"""Startpunkt: python -m cwis [--check-sources] [--config PFAD]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from logging.handlers import RotatingFileHandler

from cwis import __version__
from cwis.config import ConfigError, load_config


def setup_logging(cfg) -> None:
    log_dir = cfg.path("storage.log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(log_dir / "cwis.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [fh, sh]
    logging.getLogger("httpx").setLevel(logging.WARNING)


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


async def check_sources(cfg) -> int:
    """Phase 2: tatsächliche Erreichbarkeit jeder konfigurierten Quelle prüfen."""
    from cwis.health import HealthRegistry
    from cwis.http import HttpClient
    h = HealthRegistry()
    http = HttpClient(h)
    checks = []
    for base in cfg.get("sources.market.base_urls"):
        checks.append((f"Binance Spot {base}", http.get(f"{base}/api/v3/ticker/price", params={"symbol": "BTCUSDT"}, source="check", retries=0)))
    checks.append(("CoinGecko", http.get(f"{cfg.get('sources.coingecko.base_url')}/ping", source="check", retries=0)))
    checks.append(("OKX Derivate", http.get(f"{cfg.get('sources.derivatives.base_url')}/api/v5/public/open-interest",
                                            params={"instType": "SWAP", "instId": "BTC-USDT-SWAP"}, source="check", retries=0)))
    checks.append(("mempool.space (BTC)", http.get(f"{cfg.get('sources.bitcoin.base_url')}/blocks", source="check", retries=0)))
    for chain, cc in (cfg.get("sources.evm") or {}).items():
        checks.append((f"EVM {chain}{'' if cc.get('enabled') else ' (deaktiviert)'}", http.post(
            cc["rpc_url"], json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}, source="check", retries=0)))
    if cfg.get("sources.tron.base_url"):
        checks.append((f"Tron (TronGrid){'' if cfg.get('sources.tron.enabled') else ' (deaktiviert)'}",
                       http.post(f"{cfg.get('sources.tron.base_url')}/wallet/getnowblock", source="check", retries=0)))
    ok = 0
    for name, coro in checks:
        try:
            await coro
            print(f"  [OK]     {name}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  [FEHLER] {name}: {e}")
    await http.aclose()
    print(f"{ok}/{len(checks)} Quellen erreichbar.")
    return 0 if ok else 1


def run() -> None:
    ap = argparse.ArgumentParser(description=f"CWIS {__version__} – Crypto Whale Intelligence System (nur Beobachtung)")
    ap.add_argument("--config", help="Pfad zu config.toml")
    ap.add_argument("--check-sources", action="store_true", help="Erreichbarkeit aller Datenquellen prüfen")
    args = ap.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"Konfigurationsfehler: {e}")
        sys.exit(2)
    if args.check_sources:
        sys.exit(asyncio.run(check_sources(cfg)))
    setup_logging(cfg)
    log = logging.getLogger("cwis")
    host, port = cfg.get("server.host"), cfg.get("server.port")
    if not port_free(host, port):
        log.error("Port %s ist belegt (evtl. vom Trading-Bot oder einer laufenden CWIS-Instanz). "
                  "Bitte server.port in config/config.toml ändern. Abbruch ohne Änderungen.", port)
        sys.exit(3)
    import uvicorn

    from cwis.api.app import create_app
    from cwis.engine import Engine
    engine = Engine(cfg)
    app = create_app(engine)
    log.info("Dashboard: http://%s:%s  (Konfiguration: %s)", host, port, cfg.source_path)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
