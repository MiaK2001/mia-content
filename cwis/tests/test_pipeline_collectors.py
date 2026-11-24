from cwis.collectors.bitcoin import extract_transfers
from cwis.collectors.derivatives import liquidation_sums
from cwis.collectors.evm import TRANSFER_TOPIC, parse_transfer_log
from cwis.collectors.market import book_metrics, kline_metrics
from cwis.processing.pipeline import RawTransfer, TransferPipeline

from .conftest import NOW

EX = "0xaaaa000000000000000000000000000000000001"


def raw(**kw):
    d = dict(chain="ethereum", tx_hash="0xabc", log_index=1, block_number=1, symbol="ETH", token_address=None,
             from_address="0x1111111111111111111111111111111111111111", to_address=EX, amount=1000.0,
             observed_ms=NOW, source="test")
    d.update(kw)
    return RawTransfer(**d)


def test_pipeline_stores_classifies_and_dedupes(db, registry, state, cfg):
    p = TransferPipeline(db, registry, state, cfg)
    row = p.process(raw())
    assert row["flow_type"] == "EXCHANGE_INFLOW" and row["exchange"] == "Binance"
    assert row["value_usd"] == 2_000_000 and row["price_status"] == "OK"
    assert p.process(raw()) is None and p.stats["duplicates"] == 1          # doppelte Transaktion
    assert db.one("SELECT COUNT(*) n FROM transfers")["n"] == 1
    w = db.one("SELECT * FROM wallets WHERE address=?", ("0x1111111111111111111111111111111111111111",))
    assert w["to_exchange_usd"] == 2_000_000 and w["category"] == "unknown" and w["confidence"] == "unknown"


def test_pipeline_never_invents_prices(db, registry, state, cfg):
    p = TransferPipeline(db, registry, state, cfg)
    assert p.process(raw(symbol="UNKNOWNCOIN")) is None
    assert p.stats["unpriced_skipped"] == 1
    state.market["ETH"].ts_ms = NOW - 3_600_000   # veralteter Preis
    assert p.process(raw(tx_hash="0xdef")) is None
    assert db.one("SELECT COUNT(*) n FROM transfers")["n"] == 0


def test_pipeline_rejects_invalid_amounts(db, registry, state, cfg):
    p = TransferPipeline(db, registry, state, cfg)
    for amt in (0, -5, float("nan")):
        assert p.process(raw(amount=amt, tx_hash=f"0x{amt}")) is None
    assert p.stats["invalid"] == 3


def test_pipeline_minimum_size(db, registry, state, cfg):
    p = TransferPipeline(db, registry, state, cfg)
    assert p.process(raw(amount=1)) is None and p.stats["below_min"] == 1


def test_btc_extract_skips_change_and_coinbase():
    tx = {"txid": "t1", "status": {"block_height": 5, "block_time": 1700000000},
          "vin": [{"prevout": {"scriptpubkey_address": "A", "value": 50 * 10**8}}],
          "vout": [{"scriptpubkey_address": "B", "value": 30 * 10**8},
                   {"scriptpubkey_address": "A", "value": 19 * 10**8},       # Wechselgeld
                   {"scriptpubkey_address": "C", "value": 1000}]}            # zu klein
    out = extract_transfers(tx, min_btc=1.0)
    assert len(out) == 1 and out[0].to_address == "B" and out[0].amount == 30 and out[0].log_index == 0
    assert extract_transfers({"txid": "cb", "vin": [{"is_coinbase": True}], "vout": []}, 1.0) == []


def test_evm_log_parsing():
    tokens = {"0xdac17f958d2ee523a2206206994597c13d831ec7": {"symbol": "USDT", "decimals": 6}}
    log = {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
           "topics": [TRANSFER_TOPIC, "0x" + "0" * 24 + "11" * 20, "0x" + "0" * 24 + "22" * 20],
           "data": hex(5_000_000 * 10**6), "blockNumber": "0x10", "transactionHash": "0xh", "logIndex": "0x3"}
    r = parse_transfer_log("ethereum", log, tokens, {16: NOW})
    assert r.amount == 5_000_000 and r.symbol == "USDT" and r.from_address == "0x" + "11" * 20 and r.log_index == 3
    nft = dict(log, topics=log["topics"] + ["0x01"])
    assert parse_transfer_log("ethereum", nft, tokens, {}) is None
    assert parse_transfer_log("ethereum", dict(log, address="0xother"), tokens, {}) is None


def test_book_metrics_and_coverage():
    bids = [["99.5", "10"], ["98.5", "10"]]
    asks = [["100.5", "10"], ["101.5", "10"]]
    m = book_metrics(bids, asks)
    assert m["bid_depth_1pct_usd"] == 995 and m["ask_depth_1pct_usd"] == 1005 and m["depth_complete"]
    m2 = book_metrics([["99.9", "1"]], [["100.1", "1"]])
    assert m2["depth_complete"] is False
    assert book_metrics([], asks) == {}
    assert book_metrics([["101", "1"]], [["100", "1"]]) == {}   # gekreuztes Buch = fehlerhafte Daten


def test_kline_metrics_requires_enough_data():
    assert kline_metrics([[0, "1", "1", "1", "1", 0, 0, "10"]] * 5) == {}


def test_liquidation_sums():
    d = [{"ts": str(NOW), "sz": "2", "bkPx": "60000", "posSide": "long"},
         {"ts": str(NOW), "sz": "1", "bkPx": "60000", "posSide": "short"},
         {"ts": str(NOW - 7_200_000), "sz": "100", "bkPx": "60000", "posSide": "long"},
         {"ts": "x", "sz": "1"}]
    l, s = liquidation_sums(d, 0.01, NOW - 3_600_000)
    assert l == 1200 and s == 600


def test_price_ready_gate(db, registry, state, cfg):
    p = TransferPipeline(db, registry, state, cfg)
    assert p.price_ready("ETH")
    assert p.price_ready("BNB")              # nicht überwacht -> blockiert nicht
    assert not p.price_ready("LINK")         # überwacht, aber noch kein Preis
