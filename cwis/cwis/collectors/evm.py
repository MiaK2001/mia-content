"""Generischer EVM-Collector (Ethereum, BNB Chain, Arbitrum, Base, Polygon, Avalanche …).

Nutzt nur Standard-JSON-RPC (eth_blockNumber, eth_getLogs, eth_getBlockByNumber).
* ERC-20-Transfers über Transfer-Events der konfigurierten Token-Kontrakte.
* Native Transfers (optional) aus dem Feld tx.value ganzer Blöcke.
  Einschränkung: Interne Kontrakt-Transfers (Traces) sind so nicht sichtbar.
* Reorg-Schutz: Es werden nur Blöcke mit >= `confirmations` Bestätigungen verarbeitet.
"""
from __future__ import annotations

import itertools
import time

from cwis.collectors.base import Collector
from cwis.db import Database
from cwis.http import HttpClient, HttpError
from cwis.processing.pipeline import RawTransfer, TransferPipeline

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def topic_to_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def parse_transfer_log(chain: str, log: dict, tokens: dict[str, dict], block_ts: dict[int, int]) -> RawTransfer | None:
    topics = log.get("topics") or []
    if len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC:
        return None  # ERC-721 o. Ä. (4 Topics) ignorieren
    tok = tokens.get(log["address"].lower())
    if tok is None:
        return None
    data = log.get("data") or "0x"
    try:
        raw_amount = int(data, 16) if data != "0x" else 0
    except ValueError:
        return None
    if raw_amount <= 0:
        return None
    bn = int(log["blockNumber"], 16)
    ts = int(log["blockTimestamp"], 16) * 1000 if log.get("blockTimestamp") else block_ts.get(bn)
    return RawTransfer(
        chain=chain, tx_hash=log["transactionHash"], log_index=int(log["logIndex"], 16), block_number=bn,
        symbol=tok["symbol"], token_address=log["address"].lower(),
        from_address=topic_to_address(topics[1]), to_address=topic_to_address(topics[2]),
        amount=raw_amount / (10 ** int(tok["decimals"])), observed_ms=ts or int(time.time() * 1000),
        source=f"rpc:{chain}")


class EvmCollector(Collector):
    def __init__(self, chain: str, health, http: HttpClient, db: Database, pipeline: TransferPipeline, cfg_chain: dict):
        self.name = f"evm_{chain}"
        super().__init__(health)
        self.chain = chain
        self.http, self.db, self.pipeline = http, db, pipeline
        self.rpc = cfg_chain["rpc_url"]
        self.native = cfg_chain["native_symbol"]
        self.interval_s = cfg_chain.get("interval_seconds", 12)
        self.confirmations = cfg_chain.get("confirmations", 2)
        self.scan_native = cfg_chain.get("scan_native", False)
        self.max_blocks = cfg_chain.get("max_blocks_per_cycle", 5)
        self.tokens = {t["address"].lower(): t for t in cfg_chain.get("tokens", [])}
        self._ids = itertools.count(1)
        self.note = f"{chain}: " + ("native + " if self.scan_native else "") + f"{len(self.tokens)} Token-Kontrakte"

    async def rpc_call(self, method: str, params: list):
        r = await self.http.post(self.rpc, source=self.name,
                                 json={"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params})
        if "error" in r:
            raise HttpError(f"RPC-Fehler {method}: {r['error']}")
        return r.get("result")

    async def run_once(self) -> None:
        if not self.pipeline.price_ready(self.native):
            # Blöcke erst verarbeiten, wenn Preise vorliegen – sonst gingen Transfers verloren
            raise RuntimeError(f"Kein verlässlicher {self.native}-Preis – warte auf Marktdaten")
        latest = int(await self.rpc_call("eth_blockNumber", []), 16)
        safe = latest - self.confirmations
        state_key = f"evm_last_block:{self.chain}"
        last = self.db.get_state(state_key)
        if last is None or safe - last > 1000:
            last = safe - 1   # Erststart oder lange Pause: nicht rückwirkend alles nachladen
        if safe <= last:
            return
        frm, to = last + 1, min(safe, last + self.max_blocks)
        block_ts: dict[int, int] = {}
        if self.scan_native:
            for bn in range(frm, to + 1):
                blk = await self.rpc_call("eth_getBlockByNumber", [hex(bn), True])
                if not blk:
                    raise HttpError(f"Block {bn} nicht verfügbar")
                ts = int(blk["timestamp"], 16) * 1000
                block_ts[bn] = ts
                for tx in blk.get("transactions", []):
                    val = int(tx.get("value", "0x0"), 16)
                    if val <= 0 or not tx.get("to"):
                        continue
                    self.pipeline.process(RawTransfer(
                        chain=self.chain, tx_hash=tx["hash"], log_index=-1, block_number=bn, symbol=self.native,
                        token_address=None, from_address=tx["from"], to_address=tx["to"],
                        amount=val / 1e18, observed_ms=ts, source=f"rpc:{self.chain}"))
        if self.tokens:
            logs = await self.rpc_call("eth_getLogs", [{
                "fromBlock": hex(frm), "toBlock": hex(to),
                "address": list(self.tokens), "topics": [TRANSFER_TOPIC]}]) or []
            missing = {int(l["blockNumber"], 16) for l in logs if not l.get("blockTimestamp")} - set(block_ts)
            for bn in sorted(missing):
                hdr = await self.rpc_call("eth_getBlockByNumber", [hex(bn), False])
                if hdr:
                    block_ts[bn] = int(hdr["timestamp"], 16) * 1000
            for lg in logs:
                if lg.get("removed"):
                    continue
                raw = parse_transfer_log(self.chain, lg, self.tokens, block_ts)
                if raw:
                    self.pipeline.process(raw)
        self.db.set_state(state_key, to)
        self.health.ok(self.name, block=to, lag_blocks=latest - to)
