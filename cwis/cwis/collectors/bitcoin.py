"""Bitcoin-Blockscan über die öffentliche mempool.space-API.

Wert eines Transfers: Summe der Outputs, die NICHT an eine Input-Adresse
zurückgehen (einfache Wechselgeld-Heuristik). Jeder Output an eine andere
Adresse wird als eigener Transfer (log_index = Output-Index) gespeichert, sofern
er die Mindestgröße erreicht. Absender = Input-Adresse mit dem größten Beitrag
(bei mehreren Input-Adressen gekennzeichnet). Nur bestätigte Blöcke.
"""
from __future__ import annotations

import asyncio
import time

from cwis.collectors.base import Collector
from cwis.db import Database
from cwis.http import HttpClient
from cwis.processing.pipeline import RawTransfer, TransferPipeline

SATS = 100_000_000


def extract_transfers(tx: dict, min_btc: float) -> list[RawTransfer]:
    vin = tx.get("vin", [])
    if any(v.get("is_coinbase") for v in vin):
        return []
    in_addrs: dict[str, int] = {}
    for v in vin:
        po = v.get("prevout") or {}
        a = po.get("scriptpubkey_address")
        if a:
            in_addrs[a] = in_addrs.get(a, 0) + int(po.get("value", 0))
    if not in_addrs:
        return []
    sender = max(in_addrs, key=in_addrs.get)
    status = tx.get("status", {})
    out = []
    for i, vo in enumerate(tx.get("vout", [])):
        a = vo.get("scriptpubkey_address")
        if not a or a in in_addrs:
            continue  # Wechselgeld oder nicht adressierbar (OP_RETURN)
        btc = int(vo.get("value", 0)) / SATS
        if btc < min_btc:
            continue
        out.append(RawTransfer(
            chain="bitcoin", tx_hash=tx["txid"], log_index=i, block_number=status.get("block_height"),
            symbol="BTC", token_address=None, from_address=sender, to_address=a, amount=btc,
            observed_ms=int(status.get("block_time", time.time())) * 1000, source="mempool.space"))
    return out


class BitcoinCollector(Collector):
    name = "bitcoin_mempool"

    def __init__(self, health, http: HttpClient, db: Database, pipeline: TransferPipeline, state, cfg):
        super().__init__(health)
        self.http, self.db, self.pipeline, self.state = http, db, pipeline, state
        self.base = cfg.get("sources.bitcoin.base_url")
        self.interval_s = cfg.get("sources.bitcoin.interval_seconds", 60)
        self.delay = cfg.get("sources.bitcoin.request_delay_seconds", 1.0)
        self.min_usd = cfg.get("thresholds.min_store_usd")
        self.covers = {"BTC": "mempool.space (Bitcoin)"}
        self.max_age_s = 1800   # Blöcke im Schnitt alle 10 Min., Streuung groß
        self.note = "Bestätigte BTC-Blöcke (Wechselgeld-Heuristik, keine Mempool-Prognose)"

    async def run_once(self) -> None:
        blocks = await self.http.get(f"{self.base}/blocks", source=self.name)  # letzte 10 Blöcke
        if not blocks:
            return
        last = self.db.get_state("btc_last_height")
        newest = blocks[0]["height"]
        if last is None:
            last = newest - 1   # Erststart: nur den neuesten Block verarbeiten
        todo = sorted([b for b in blocks if b["height"] > last], key=lambda b: b["height"])
        md = self.state.market.get("BTC")
        if md is None or not md.price or not self.pipeline.price_ready("BTC"):
            raise RuntimeError("Kein verlässlicher BTC-Preis – Blockscan wartet auf Marktdaten")
        min_btc = self.min_usd / md.price
        for b in todo[:3]:
            await self._scan_block(b, min_btc)
            self.db.set_state("btc_last_height", b["height"])
        self.health.ok(self.name, height=newest)

    async def _scan_block(self, block: dict, min_btc: float) -> None:
        n = block.get("tx_count", 0)
        for start in range(0, n, 25):
            txs = await self.http.get(f"{self.base}/block/{block['id']}/txs/{start}", source=self.name)
            for tx in txs:
                for raw in extract_transfers(tx, min_btc):
                    self.pipeline.process(raw)
            await asyncio.sleep(self.delay)
