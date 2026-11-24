from cwis.labels import UNKNOWN, Label
from cwis.processing.classify import classify, directional_lean
from cwis.processing.pricing import quote, usable
from cwis.processing.thresholds import evaluate
from cwis.state import MarketState, ReferencePrice

from .conftest import NOW, make_market

EX_A = Label("Binance 14", "Binance", "exchange", "medium", None)
EX_A2 = Label("Binance 15", "Binance", "exchange", "medium", None)
EX_B = Label("Coinbase 1", "Coinbase", "exchange", "medium", None)
NULL = Label("Null", "null", "null", "high", None)


def test_flow_classification():
    assert classify(UNKNOWN, EX_A) == ("EXCHANGE_INFLOW", "Binance")
    assert classify(EX_A, UNKNOWN) == ("EXCHANGE_OUTFLOW", "Binance")
    assert classify(EX_A, EX_A2)[0] == "EXCHANGE_INTERNAL"     # Reorganisation, kein Nettofluss
    assert classify(EX_A, EX_B)[0] == "INTER_EXCHANGE"
    assert classify(NULL, UNKNOWN)[0] == "MINT"
    assert classify(UNKNOWN, NULL)[0] == "BURN"
    assert classify(UNKNOWN, UNKNOWN)[0] == "UNKNOWN_TRANSFER"   # nie Kauf/Verkauf


def test_directional_lean_stablecoins_inverted():
    st = {"USDT"}
    assert directional_lean("ETH", "EXCHANGE_INFLOW", st) == -1
    assert directional_lean("USDT", "EXCHANGE_INFLOW", st) == 1
    assert directional_lean("ETH", "UNKNOWN_TRANSFER", st) == 0


TH = {"tiers_usd": [1e5, 2.5e5, 5e5, 1e6, 5e6, 1e7, 5e7, 1e8], "volume_factor": 0.0005,
      "exchange_factor_multiplier": 0.5, "floor_usd": 1e5, "ceiling_usd": 5e7, "stablecoin_threshold_usd": 5e6}


def test_dynamic_threshold_small_cap_more_sensitive():
    big = make_market("BTC", vol=3e10, bid=1e8, ask=1e8)
    small = make_market("ALT", vol=2e7, bid=2e5, ask=2e5)
    r_big = evaluate(TH, 5e5, big, exchange_related=False, stablecoin=False)
    r_small = evaluate(TH, 5e5, small, exchange_related=False, stablecoin=False)
    assert not r_big.is_whale and r_small.is_whale
    assert r_small.rel_volume > r_big.rel_volume


def test_threshold_respects_floor_ceiling_and_exchange_factor():
    md = make_market(vol=1e12, bid=1e12, ask=1e12)
    assert evaluate(TH, 1, md, exchange_related=False, stablecoin=False).threshold_usd == 5e7
    tiny = make_market(vol=1e5, bid=1e3, ask=1e3)
    assert evaluate(TH, 1, tiny, exchange_related=False, stablecoin=False).threshold_usd == 1e5
    md2 = make_market(vol=1e10, bid=1e9, ask=1e9)
    a = evaluate(TH, 1, md2, exchange_related=False, stablecoin=False).threshold_usd
    b = evaluate(TH, 1, md2, exchange_related=True, stablecoin=False).threshold_usd
    assert b == a * 0.5


def test_incomplete_depth_not_used_as_cap():
    md = make_market(vol=1e10, bid=1e5, ask=1e5)
    md.depth_complete = False
    assert evaluate(TH, 1, md, exchange_related=False, stablecoin=False).threshold_usd == 5e6


def test_missing_market_data_conservative_default():
    r = evaluate(TH, 2e6, None, exchange_related=False, stablecoin=False)
    assert r.threshold_usd == 1e6 and r.rel_volume is None


def test_price_quotes():
    s = MarketState()
    assert quote(s, "ETH", 180, NOW).status == "MISSING"
    s.set_market(make_market("ETH", 2000, ts=NOW - 600_000))
    assert quote(s, "ETH", 180, NOW).status == "STALE"
    s.set_market(make_market("ETH", 2000, ts=NOW))
    assert quote(s, "ETH", 180, NOW).status == "OK"
    s.set_reference(ReferencePrice("ETH", NOW, 2500, None, None))   # 20 % Abweichung = fehlerhafter Preis
    q = quote(s, "ETH", 180, NOW)
    assert q.status == "INCONSISTENT" and not usable(q.status)


def test_stablecoin_depeg_flagged():
    s = MarketState()
    assert quote(s, "USDT", 180, NOW).status == "PEGGED"
    s.set_reference(ReferencePrice("USDT", NOW, 0.90, None, None))
    assert quote(s, "USDT", 180, NOW).status == "INCONSISTENT"
