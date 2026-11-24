import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cwis.config import load_config  # noqa: E402
from cwis.db import Database  # noqa: E402
from cwis.labels import WalletRegistry  # noqa: E402
from cwis.state import DerivData, MarketData, MarketState  # noqa: E402

NOW = int(time.time() * 1000)


@pytest.fixture
def cfg(tmp_path):
    return load_config(path=tmp_path / "nicht_vorhanden.toml",
                       overrides={"storage": {"db_path": str(tmp_path / "t.sqlite3"), "log_dir": str(tmp_path / "logs")}})


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def registry():
    return WalletRegistry([
        {"chain": "ethereum", "address": "0x0000000000000000000000000000000000000000", "label": "Null", "entity": "null", "category": "null", "confidence": "high"},
        {"chain": "ethereum", "address": "0xAAAA000000000000000000000000000000000001", "label": "Binance 14", "entity": "Binance", "category": "exchange", "confidence": "medium"},
        {"chain": "ethereum", "address": "0xAAAA000000000000000000000000000000000002", "label": "Binance 15", "entity": "Binance", "category": "exchange", "confidence": "medium"},
        {"chain": "ethereum", "address": "0xBBBB000000000000000000000000000000000001", "label": "Coinbase 1", "entity": "Coinbase", "category": "exchange", "confidence": "medium"},
        {"chain": "bitcoin", "address": "bc1qexchange", "label": "Ex Cold", "entity": "Kraken", "category": "exchange", "confidence": "medium"},
    ])


def make_market(symbol="ETH", price=2000.0, vol=1e9, bid=5e6, ask=5e6, ts=None, **kw):
    md = MarketData(symbol=symbol, ts_ms=ts or NOW, source="test", price=price, volume_24h_usd=vol,
                    bid_depth_1pct_usd=bid, ask_depth_1pct_usd=ask, depth_complete=True, **kw)
    return md


@pytest.fixture
def state():
    s = MarketState()
    s.set_market(make_market("ETH", 2000.0))
    s.set_market(make_market("BTC", 60000.0, vol=2e10, bid=2e7, ask=2e7))
    s.set_deriv(DerivData("ETH", NOW, "test", open_interest_usd=5e9, funding_rate=0.0001, long_short_ratio=1.5,
                          long_liq_usd_1h=0, short_liq_usd_1h=0))
    return s
