import asyncio

import pytest
from fastapi.testclient import TestClient

from cwis.api.app import create_app
from cwis.engine import Engine
from cwis.history.outcomes import OutcomeTracker, evaluate_klines, summarize
from cwis.integration.bot_interface import advisory_for_bot_signal, build_signal
from cwis.maintenance import run_retention
from cwis.ml.validation import assert_no_leakage, walk_forward_splits

from .conftest import NOW, make_market


def k(ts, o, h, l, c, qv=1000):
    return [ts, str(o), str(h), str(l), str(c), "0", ts + 59_999, str(qv)]


def test_evaluate_klines_no_lookahead():
    t0 = 1_000_000
    kl = [k(t0 - 60_000, 100, 200, 50, 150),   # VOR dem Ereignis – darf nicht zählen
          k(t0, 100, 105, 99, 104), k(t0 + 60_000, 104, 106, 95, 96)]
    r = evaluate_klines(kl, 100.0, t0, t0 + 120_000)
    assert r["ret"] == pytest.approx(-0.04)
    assert r["max_up"] == pytest.approx(0.06) and r["max_down"] == pytest.approx(-0.05)
    assert evaluate_klines([], 100.0, t0, t0 + 1) is None


def test_outcome_tracker_and_summary(db, cfg):
    async def fake_klines(market, interval, start, end, limit):
        return [k(start, 100, 101, 99, 99)]
    tr = OutcomeTracker(db, cfg, fake_klines)
    assert tr.schedule("alert", 1, "BTC", "EXCHANGE_INFLOW", "bearish", NOW - 3 * 86_400_000, 100.0) == 8
    assert tr.schedule("baseline", None, "BTC", None, "neutral", NOW - 3 * 86_400_000, 100.0) == 8
    assert tr.schedule("alert", 2, "BTC", "X", "bearish", NOW, None) == 0     # ohne Startpreis keine Messung
    n = asyncio.run(tr.process_due(100))
    assert n == 16
    s = summarize(db)
    g = [x for x in s["groups"] if x["horizon_min"] == 60][0]
    assert g["hit_rate"] == 1.0 and g["baseline_n"] == 1 and g["sufficient_sample"] is False


def test_walk_forward_has_no_overlap():
    splits = walk_forward_splits(1000, 5, 300, embargo=10)
    assert splits
    for s in splits:
        assert s.train_end + 10 <= s.test_start < s.test_end
        assert_no_leakage(list(range(s.train_start, s.train_end)), list(range(s.test_start, s.test_end)), 9)
    with pytest.raises(ValueError):
        assert_no_leakage([1, 5], [4, 6])


def test_signal_without_data_is_not_a_permission():
    sig = build_signal(None, symbol="BTC", market_symbol="BTCUSDT", level=None, market_condition=None,
                       action=None, ts_ms=NOW, validity_min=5)
    assert sig["data_status"] == "INSUFFICIENT" and sig["whale_impact_score"] is None
    assert advisory_for_bot_signal("LONG", sig)["advice"] == "NO_CONFIRMATION"


def test_bot_advisory_logic_concept():
    base = {"data_status": "VALID", "signal_valid_until": "2099-01-01T00:00:00Z", "alert_level": "YELLOW"}
    assert advisory_for_bot_signal("LONG", {**base, "bullish_score": 70, "bearish_score": 10})["advice"] == "CONFIRM"
    assert advisory_for_bot_signal("LONG", {**base, "bullish_score": 10, "bearish_score": 80, "alert_level": "RED"})["advice"] == "REJECT_OR_WARN"
    assert advisory_for_bot_signal("SHORT", {**base, "bullish_score": 30, "bearish_score": 35})["advice"] == "NO_CONFIRMATION"
    expired = {**base, "signal_valid_until": "2000-01-01T00:00:00Z", "bullish_score": 90, "bearish_score": 0}
    assert advisory_for_bot_signal("LONG", expired, now_iso="2026-01-01T00:00:00Z")["advice"] == "NO_CONFIRMATION"


@pytest.fixture
def client(cfg, db):
    eng = Engine(cfg, db=db)
    eng.state.set_market(make_market("BTC", 60000, vol=2e10, ret_1h=0.0, realized_vol_1h=0.01, volume_ratio_1h=1))
    eng.health.ok("bitcoin_mempool")
    eng.health.ok("evm_ethereum")
    app = create_app(eng, start_engine=False)
    return TestClient(app), eng


def test_api_signal_endpoints(client):
    c, eng = client
    r = c.get("/api/v1/signal/BTCUSDT").json()
    assert r["data_status"] == "INSUFFICIENT" and r["recommended_action"] == "NO_SIGNAL_DATA_INSUFFICIENT"
    eng.analyze()
    r = c.get("/api/v1/signal/btc").json()
    assert r["schema_version"] == "1.0" and r["symbol"] == "BTCUSDT"
    assert r["integration_status"].startswith("NICHT VERBUNDEN")
    for key in ("whale_impact_score", "bullish_score", "bearish_score", "data_confidence", "market_condition",
                "exchange_netflow_usd", "unusual_whale_activity", "data_status", "signal_valid_until", "recommended_action"):
        assert key in r
    eth = c.get("/api/v1/signal/ETH").json()
    assert eth["data_status"] in ("INSUFFICIENT", "STALE")       # keine ETH-Marktdaten -> kein Signal
    assert c.get("/api/v1/signal/DOGE").status_code == 404


def test_api_read_only_and_dashboard(client):
    c, eng = client
    assert c.post("/api/v1/signals").status_code == 405
    assert c.delete("/api/v1/alerts").status_code == 405
    assert c.get("/").status_code == 200 and "CWIS" in c.get("/").text
    eng.analyze()
    for path in ("/api/v1/overview", "/api/v1/ranking", "/api/v1/transfers", "/api/v1/flows", "/api/v1/alerts",
                 "/api/v1/wallets", "/api/v1/history", "/api/v1/health", "/api/v1/integration",
                 "/api/v1/score-history/BTC", "/api/v1/analysis/BTC", "/api/v1/smart-money"):
        assert c.get(path).status_code == 200, path
    assert c.get("/api/v1/integration").json()["enabled"] is False


def test_websocket_stream(client):
    c, eng = client
    with c.websocket_connect("/ws/v1/signals") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and "NICHT VERBUNDEN" in hello["integration_status"]


def test_retention(db, cfg):
    old = NOW - 400 * 86_400_000
    db.insert("market_snapshots", {"symbol": "BTC", "ts_ms": old, "source": "t", "price": 1})
    db.insert("market_snapshots", {"symbol": "BTC", "ts_ms": NOW, "source": "t", "price": 1})
    res = run_retention(db, cfg)
    assert res["market_snapshots"] == 1
    assert db.one("SELECT COUNT(*) n FROM market_snapshots")["n"] == 1
