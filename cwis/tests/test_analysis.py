import math

from cwis.alerts import AlertManager
from cwis.analysis import narrative, scoring
from cwis.processing.clustering import find_clusters
from cwis.state import DerivData

from .conftest import NOW, make_market


def tr(i, flow="EXCHANGE_INFLOW", value=5e6, frm=None, ent=None, symbol="ETH", tx=None, whale=1, conf="medium"):
    return {"chain": "ethereum", "tx_hash": tx or f"0x{i}", "symbol": symbol, "flow_type": flow, "value_usd": value,
            "from_address": frm or f"0xw{i}", "to_address": "0xex", "from_entity": ent,
            "from_category": "exchange" if ent else "unknown", "to_entity": "Binance", "to_category": "exchange",
            "to_confidence": conf, "from_confidence": "unknown", "exchange": "Binance", "is_whale": whale, "size_tier": 4,
            "detected_ms": NOW}


def score(rows, md=None, dd=None, status="OK", hist=None, clusters=None, onchain=True):
    agg = scoring.aggregate(rows)
    return scoring.compute_scores("ETH", agg, md, dd, price_status=status, onchain_fresh=onchain, deriv_expected=True,
                                  stable_netflow_usd=None, whale_volume_history=hist or [], clusters=clusters or [],
                                  stale_after_s=180, now_ms=NOW)


def test_clustering_dedupes_tx_and_merges_entities():
    rows = [tr(1), tr(2), tr(3), tr(3, tx="0x3"),                      # gleiche Tx zweimal (2 Logs)
            tr(4, frm="0xa", ent="Wintermute"), tr(5, frm="0xb", ent="Wintermute")]
    rows[3] = dict(rows[2])
    cl = find_clusters(rows, min_wallets=3)
    assert len(cl) == 1
    c = cl[0]
    assert c["transactions"] == 5            # 0x3 nur einmal
    assert c["wallets"] == 4                 # w1, w2, w3, Wintermute (2 Adressen = 1 Entität)
    assert c["merged_by_entity"] == 1
    assert c["total_usd"] == 6 * 5e6         # Werte der Logs einer Tx werden addiert, Teilnehmer einmal gezählt
    assert find_clusters(rows[:2], min_wallets=3) == []


def test_internal_transfers_excluded_from_netflow():
    agg = scoring.aggregate([tr(1, flow="EXCHANGE_INTERNAL", value=1e9)])
    assert agg.netflow_usd == 0 and agg.internal_usd == 1e9


def test_scores_bounded_and_inflow_bearish():
    md = make_market(ret_1h=-0.03, realized_vol_1h=0.01, volume_ratio_1h=3.0, bid=2e6, ask=8e6)
    dd = DerivData("ETH", NOW, "t", open_interest_usd=5e9, funding_rate=0.001, long_liq_usd_1h=5e7,
                   short_liq_usd_1h=1e6, oi_change_1h_pct=6)
    s = score([tr(i, value=5e7) for i in range(10)], md, dd)
    for k in ("whale_impact_score", "bullish_score", "bearish_score", "market_impact_score", "data_confidence"):
        assert 0 <= s[k] <= 100 and not math.isnan(s[k])
    assert s["bearish_score"] > 60 > s["bullish_score"]
    level, cond = scoring.classify_level(s)
    assert level in ("ORANGE", "RED")


def test_outflows_bullish():
    md = make_market(ret_1h=0.02, realized_vol_1h=0.01, volume_ratio_1h=2.0, bid=8e6, ask=2e6)
    s = score([tr(i, flow="EXCHANGE_OUTFLOW", value=3e7) for i in range(5)], md)
    assert s["bullish_score"] > s["bearish_score"]


def test_missing_data_lowers_confidence_and_blocks_escalation():
    s = score([tr(i, value=5e8) for i in range(20)], md=None, dd=None, status="MISSING", onchain=False)
    assert s["data_status"] == "INSUFFICIENT" and s["data_confidence"] < 40
    assert "Handelsvolumen" in s["missing_data"]
    level, _ = scoring.classify_level(s)
    assert level in ("GREEN", "YELLOW")                       # nie ORANGE/ROT bei fehlenden Daten
    assert scoring.recommended_action(s, level) == "NO_SIGNAL_DATA_INSUFFICIENT"


def test_stale_market_data_detected():
    md = make_market(ts=NOW - 3_600_000)
    s = score([tr(1)], md)
    assert s["data_status"] == "STALE"


def test_no_activity_green():
    s = score([], make_market(ret_1h=0.0, realized_vol_1h=0.01, volume_ratio_1h=1.0),
              DerivData("ETH", NOW, "t", open_interest_usd=1, funding_rate=0.0, oi_change_1h_pct=0))
    assert s["whale_impact_score"] == 0
    assert scoring.classify_level(s)[0] == "GREEN"


def test_rarity_uses_history_percentile():
    md = make_market()
    rows = [tr(1, value=1e7)]
    low = score(rows, md, hist=[1e9] * 30)
    high = score(rows, md, hist=[1e3] * 30)
    assert high["whale_impact_score"] > low["whale_impact_score"]


def test_extreme_values_do_not_break_scores():
    md = make_market(price=1e-8, vol=1.0, bid=0.0, ask=0.0, ret_1h=-0.99, realized_vol_1h=0.0, volume_ratio_1h=1e9)
    s = score([tr(1, value=1e12)], md)
    assert all(0 <= s[k] <= 100 for k in ("whale_impact_score", "bearish_score", "market_impact_score"))


def test_narrative_lists_alternatives_and_uncertainty():
    s = score([tr(1, value=5e7)], make_market())
    a = narrative.build_analysis(s, "YELLOW", None, ["Test"])
    assert any("Sweeps" in x for x in a["alternative_explanations"])
    assert any("keine Käufe" in x for x in a["uncertainties"])
    assert "Noch keine" in a["historical_comparison"]


def test_alert_filtering(db, cfg):
    am = AlertManager(db, cfg)
    s = score([tr(i, value=5e7) for i in range(10)], make_market(ret_1h=-0.02, realized_vol_1h=0.01, volume_ratio_1h=2))
    s["whale_impact_score"] = 40
    assert am.maybe_alert(s, "YELLOW", {}, {}, NOW) is not None
    assert am.maybe_alert(s, "YELLOW", {}, {}, NOW + 60_000) is None          # identisch -> unterdrückt
    s2 = dict(s, whale_impact_score=60)
    assert am.maybe_alert(s2, "ORANGE", {}, {}, NOW + 120_000) is not None    # Eskalation
    assert am.maybe_alert(s2, "ORANGE", {}, {}, NOW + 180_000) is None
    assert am.maybe_alert(s2, "ORANGE", {}, {}, NOW + 33 * 60_000) is not None  # nach Cooldown (ab Eskalation)
    assert db.one("SELECT COUNT(*) n FROM alerts")["n"] == 3


def test_no_yellow_alert_without_whales(db, cfg):
    am = AlertManager(db, cfg)
    s = score([], make_market())
    s["whale_impact_score"] = 30
    assert am.maybe_alert(s, "YELLOW", {}, {}, NOW) is None
