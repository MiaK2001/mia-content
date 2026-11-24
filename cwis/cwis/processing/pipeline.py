"""Verarbeitung roher On-Chain-Transfers: Bewertung, Zuordnung, Klassifizierung, Speicherung."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from cwis.db import Database, now_ms
from cwis.labels import WalletRegistry, norm_address
from cwis.processing import classify as cls
from cwis.processing import pricing, thresholds
from cwis.state import MarketState

log = logging.getLogger("cwis.pipeline")


@dataclass
class RawTransfer:
    chain: str
    tx_hash: str
    log_index: int          # -1 für native Transfers / BTC
    block_number: int | None
    symbol: str
    token_address: str | None
    from_address: str | None
    to_address: str | None
    amount: float
    observed_ms: int
    source: str


class TransferPipeline:
    def __init__(self, db: Database, registry: WalletRegistry, state: MarketState, cfg, listeners=None):
        self.db = db
        self.registry = registry
        self.state = state
        self.cfg = cfg
        self.th_cfg = cfg.get("thresholds")
        self.stale_s = cfg.get("analysis.stale_after_seconds", 180)
        self.listeners = listeners if listeners is not None else []
        self.stats = {"seen": 0, "stored": 0, "duplicates": 0, "unpriced_skipped": 0, "below_min": 0, "invalid": 0}

    def price_ready(self, symbol: str) -> bool:
        """True, wenn für ein überwachtes Asset ein verlässlicher Preis vorliegt (nicht überwachte: immer True)."""
        if symbol not in self.cfg.watch_symbols:
            return True
        return pricing.usable(pricing.quote(self.state, symbol, self.stale_s).status)

    def process(self, raw: RawTransfer, detected_ms: int | None = None) -> dict | None:
        self.stats["seen"] += 1
        if not raw.tx_hash or raw.amount is None or raw.amount <= 0 or raw.amount != raw.amount:  # NaN-Schutz
            self.stats["invalid"] += 1
            return None
        detected_ms = detected_ms or now_ms()
        q = pricing.quote(self.state, raw.symbol, self.stale_s, detected_ms)
        if not pricing.usable(q.status):
            # Kein verlässlicher Preis -> kein USD-Wert -> nicht bewerten (keine erfundenen Werte)
            self.stats["unpriced_skipped"] += 1
            return None
        value = raw.amount * q.price
        if value < self.th_cfg["min_store_usd"]:
            self.stats["below_min"] += 1
            return None
        src = self.registry.lookup(raw.chain, raw.from_address)
        dst = self.registry.lookup(raw.chain, raw.to_address)
        flow, exchange = cls.classify(src, dst)
        md = self.state.market.get(raw.symbol)
        stable = raw.symbol in self.cfg.stablecoins
        th = thresholds.evaluate(
            self.th_cfg, value, md,
            exchange_related=flow in cls.NETFLOW_TYPES or flow == "INTER_EXCHANGE",
            stablecoin=stable,
        )
        row = {
            "chain": raw.chain, "tx_hash": raw.tx_hash, "log_index": raw.log_index,
            "block_number": raw.block_number, "symbol": raw.symbol, "token_address": raw.token_address,
            "from_address": norm_address(raw.chain, raw.from_address),
            "to_address": norm_address(raw.chain, raw.to_address),
            "amount": raw.amount, "price_usd": q.price, "value_usd": value, "price_status": q.status,
            "from_label": src.label, "from_entity": src.entity, "from_category": src.category, "from_confidence": src.confidence,
            "to_label": dst.label, "to_entity": dst.entity, "to_category": dst.category, "to_confidence": dst.confidence,
            "flow_type": flow, "exchange": exchange, "is_whale": int(th.is_whale),
            "threshold_usd": th.threshold_usd, "size_tier": th.size_tier,
            "rel_volume": th.rel_volume, "rel_depth": th.rel_depth,
            "observed_ms": raw.observed_ms, "detected_ms": detected_ms, "source": raw.source,
        }
        rid = self.db.insert("transfers", row, or_ignore=True)
        if rid is None:
            self.stats["duplicates"] += 1
            return None
        self.stats["stored"] += 1
        row["id"] = rid
        self._update_wallets(row)
        for fn in self.listeners:
            try:
                fn(row)
            except Exception:  # Listener dürfen die Pipeline nie stoppen
                log.exception("Listener-Fehler")
        return row

    def _update_wallets(self, row: dict) -> None:
        v = row["value_usd"]
        ts = row["detected_ms"]
        for side, addr in (("from", row["from_address"]), ("to", row["to_address"])):
            if not addr or row[f"{side}_category"] == "null":
                continue
            sent = v if side == "from" else 0.0
            recv = v if side == "to" else 0.0
            to_ex = v if side == "from" and row["flow_type"] == "EXCHANGE_INFLOW" else 0.0
            from_ex = v if side == "to" and row["flow_type"] == "EXCHANGE_OUTFLOW" else 0.0
            self.db.execute(
                """INSERT INTO wallets(chain,address,label,entity,category,confidence,label_source,
                       first_seen_ms,last_seen_ms,tx_count,volume_usd,sent_usd,received_usd,
                       to_exchange_usd,from_exchange_usd,updated_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)
                   ON CONFLICT(chain,address) DO UPDATE SET
                       last_seen_ms=excluded.last_seen_ms, tx_count=tx_count+1,
                       volume_usd=volume_usd+excluded.volume_usd, sent_usd=sent_usd+excluded.sent_usd,
                       received_usd=received_usd+excluded.received_usd,
                       to_exchange_usd=to_exchange_usd+excluded.to_exchange_usd,
                       from_exchange_usd=from_exchange_usd+excluded.from_exchange_usd,
                       updated_ms=excluded.updated_ms""",
                (row["chain"], addr, row[f"{side}_label"], row[f"{side}_entity"], row[f"{side}_category"],
                 row[f"{side}_confidence"], None, ts, ts, v, sent, recv, to_ex, from_ex, ts),
            )
