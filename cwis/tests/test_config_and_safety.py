"""Konfiguration und verbindliche Sicherheitsregeln."""
import re
from pathlib import Path

import pytest

from cwis.config import ConfigError, load_config, validate
from cwis.integration import bot_interface

SRC = Path(__file__).resolve().parent.parent / "cwis"


def test_default_config_loads(cfg):
    assert cfg.get("server.host") == "127.0.0.1"
    assert "BTC" in cfg.watch_symbols


@pytest.mark.parametrize("key", ["api_secret", "binance_secret_key", "passphrase", "private_key"])
def test_trading_credentials_rejected(key, tmp_path):
    with pytest.raises(ConfigError):
        load_config(path=tmp_path / "x.toml", overrides={"sources": {"market": {key: "abc"}}})


def test_non_local_host_rejected():
    with pytest.raises(ConfigError):
        validate({"server": {"host": "0.0.0.0", "port": 8787}, "symbols": {"watch": [{"symbol": "BTC"}]}})


def test_integration_hard_disabled():
    assert bot_interface.INTEGRATION_ENABLED is False
    assert "NICHT VERBUNDEN" in bot_interface.INTEGRATION_STATUS


def test_no_trading_endpoints_or_order_code_in_source():
    """Kein Code darf Order-/Trade-/Konto-Endpunkte einer Börse ansprechen."""
    forbidden = [r"/api/v3/order", r"/fapi/v1/order", r"/api/v5/trade", r"/api/v5/account", r"/v5/order",
                 r"create_order", r"place_order", r"new_order", r"X-MBX-APIKEY", r"OK-ACCESS-KEY", r"hmac"]
    hits = []
    for p in SRC.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            if re.search(pat, text, re.IGNORECASE):
                hits.append(f"{p.name}: {pat}")
    assert not hits, hits


def test_api_has_only_get_routes(cfg, db):
    from cwis.api.app import create_app
    from cwis.engine import Engine
    eng = Engine(cfg, db=db)
    app = create_app(eng, start_engine=False)
    for r in app.routes:
        methods = getattr(r, "methods", None)
        if methods:
            assert methods <= {"GET", "HEAD"}, (r.path, methods)
