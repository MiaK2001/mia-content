"""Tron: USDT-Transfers (TRC-20) über die öffentliche TronGrid-Event-API.

* Nur bestätigte Events (only_confirmed=true) plus 60 s Sicherheitsabstand.
* Verarbeitung lückenlos in Zeitfenstern; Fortschritt wird gespeichert.
* Ohne API-Schlüssel nutzbar, aber stark limitiert – CWIS fragt höchstens
  ~1 Anfrage/s ab. Bei sehr hohem Aufkommen kann CWIS zurückfallen; das zeigt
  die Health-Anzeige (lag_s). Ein kostenloser TronGrid-Schlüssel würde helfen,
  wird aber bewusst nicht verwendet.
"""
from __future__ import annotations

import hashlib
import time

from cwis.collectors.base import Collector
from cwis.db import Database
from cwis.http import HttpClient
from cwis.processing.pipeline import RawTransfer, TransferPipeline

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def hex_to_tron(addr: str) -> str:
    """0x-Hex (20 Byte) -> Tron-Base58Check-Adresse (T…)."""
    h = addr[2:] if addr.startswith("0x") else addr
    if len(h) == 40:
        h = "41" + h
    raw = bytes.fromhex(h)
    chk = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    n = int.from_bytes(raw + chk, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    pad = len(raw + chk) - len((raw + chk).lstrip(b"\0"))
    return "1" * pad + out


class TronCollector(Collector):
    name = "tron_trongrid"

    def __init__(self, health, http: HttpClient, db: Database, pipeline: TransferPipeline, cfg):
        super().__init__(health)
        self.http, self.db, self.pipeline = http, db, pipeline
        c = cfg.get("sources.tron")
        self.base = c["base_url"]
        self.interval_s = c.get("interval_seconds", 15)
        self.window_ms = c.get("window_seconds", 30) * 1000
        self.max_pages = c.get("max_pages_per_cycle", 40)
        self.tokens = {t["address"]: t for t in c.get("tokens", [])}
        self.covers = {t["symbol"]: "TronGrid (Tron)" for t in self.tokens.values()}
        self.max_age_s = 300
        self.min_usd = cfg.get("thresholds.min_store_usd")
        self.note = f"Tron TRC-20: {', '.join(t['symbol'] for t in self.tokens.values())} (TronGrid, ohne Schlüssel)"

    async def run_once(self) -> None:
        now = int(time.time() * 1000)
        safe = now - 60_000
        for contract, tok in self.tokens.items():
            if not self.pipeline.price_ready(tok["symbol"]):
                raise RuntimeError(f"Kein verlässlicher {tok['symbol']}-Preis")
            key = f"tron_last_ts:{contract}"
            last = self.db.get_state(key)
            if last is None or safe - last > 3_600_000:
                last = safe - self.window_ms   # Erststart/lange Pause: nicht rückwirkend nachladen
            pages = 0
            while last < safe and pages < self.max_pages:
                end = min(safe, last + self.window_ms)
                url = f"{self.base}/v1/contracts/{contract}/events"
                params = {"event_name": "Transfer", "only_confirmed": "true", "limit": 200,
                          "order_by": "block_timestamp,asc", "min_block_timestamp": last + 1,
                          "max_block_timestamp": end}
                while True:
                    d = await self.http.get(url, params=params, source=self.name)
                    pages += 1
                    for e in d.get("data", []):
                        self._handle(e, tok)
                    nxt = (d.get("meta") or {}).get("links", {}).get("next")
                    if not nxt or not d.get("data"):
                        break
                    url, params = nxt, None
                    if pages >= self.max_pages * 3:
                        raise RuntimeError("Zu viele Seiten in einem Zeitfenster – Fenster verkleinern")
                last = end
                self.db.set_state(key, last)
            self.health.ok(self.name, lag_s=round((now - last) / 1000))

    def _handle(self, e: dict, tok: dict) -> None:
        try:
            r = e["result"]
            amount = int(r["value"]) / 10 ** int(tok["decimals"])
        except (KeyError, ValueError, TypeError):
            return
        if amount < self.min_usd * 0.9:   # Vorfilter (Stablecoin ≈ 1 USD); genaue Prüfung in der Pipeline
            return
        self.pipeline.process(RawTransfer(
            chain="tron", tx_hash=e["transaction_id"], log_index=int(e.get("event_index", 0)),
            block_number=e.get("block_number"), symbol=tok["symbol"], token_address=e.get("contract_address"),
            from_address=hex_to_tron(r["from"]), to_address=hex_to_tron(r["to"]), amount=amount,
            observed_ms=int(e["block_timestamp"]), source="trongrid"))
