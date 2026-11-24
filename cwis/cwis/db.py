"""SQLite-Datenbank (WAL-Modus). Alle Zeitstempel als UTC-Epoch-Millisekunden.

Jede Beobachtung hat zwei Zeitpunkte:
  * observed_ms – Zeitpunkt laut Quelle (z. B. Blockzeit)
  * detected_ms – Zeitpunkt, an dem CWIS die Information tatsächlich hatte
Historische Auswertungen verwenden ausschließlich detected_ms als Startpunkt,
um Look-ahead-Bias zu vermeiden.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY,
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL DEFAULT -1,
    block_number INTEGER,
    symbol TEXT NOT NULL,
    token_address TEXT,
    from_address TEXT,
    to_address TEXT,
    amount REAL NOT NULL,
    price_usd REAL,
    value_usd REAL,
    price_status TEXT NOT NULL,
    from_label TEXT, from_entity TEXT, from_category TEXT, from_confidence TEXT,
    to_label TEXT, to_entity TEXT, to_category TEXT, to_confidence TEXT,
    flow_type TEXT NOT NULL,
    exchange TEXT,
    is_whale INTEGER NOT NULL DEFAULT 0,
    threshold_usd REAL,
    size_tier INTEGER,
    rel_volume REAL,
    rel_depth REAL,
    observed_ms INTEGER NOT NULL,
    detected_ms INTEGER NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(chain, tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS ix_transfers_sym_time ON transfers(symbol, detected_ms);
CREATE INDEX IF NOT EXISTS ix_transfers_time ON transfers(detected_ms);

CREATE TABLE IF NOT EXISTS wallets (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    label TEXT, entity TEXT, category TEXT,
    confidence TEXT NOT NULL DEFAULT 'unknown',
    label_source TEXT,
    first_seen_ms INTEGER, last_seen_ms INTEGER,
    tx_count INTEGER NOT NULL DEFAULT 0,
    volume_usd REAL NOT NULL DEFAULT 0,
    sent_usd REAL NOT NULL DEFAULT 0,
    received_usd REAL NOT NULL DEFAULT 0,
    to_exchange_usd REAL NOT NULL DEFAULT 0,
    from_exchange_usd REAL NOT NULL DEFAULT 0,
    watch INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    updated_ms INTEGER,
    PRIMARY KEY(chain, address)
);
CREATE INDEX IF NOT EXISTS ix_wallets_volume ON wallets(volume_usd);

CREATE TABLE IF NOT EXISTS market_snapshots (
    symbol TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    source TEXT NOT NULL,
    price REAL, volume_24h_usd REAL, price_change_24h_pct REAL,
    bid_depth_1pct_usd REAL, ask_depth_1pct_usd REAL, spread_bps REAL,
    largest_bid_usd REAL, largest_ask_usd REAL,
    ret_1h REAL, realized_vol_1h REAL, volume_ratio_1h REAL,
    market_cap_usd REAL,
    PRIMARY KEY(symbol, ts_ms, source)
);
CREATE TABLE IF NOT EXISTS derivatives_snapshots (
    symbol TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    source TEXT NOT NULL,
    open_interest_usd REAL, funding_rate REAL, long_short_ratio REAL,
    long_liq_usd_1h REAL, short_liq_usd_1h REAL,
    PRIMARY KEY(symbol, ts_ms, source)
);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    whale_impact REAL, bullish REAL, bearish REAL, market_impact REAL, data_confidence REAL,
    whale_volume_usd REAL,
    level TEXT NOT NULL,
    market_condition TEXT NOT NULL,
    data_status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scores_sym_time ON scores(symbol, ts_ms);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    whale_impact REAL, bullish REAL, bearish REAL, data_confidence REAL,
    volume_usd REAL,
    price_at_alert REAL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alerts_time ON alerts(ts_ms);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY,
    ref_type TEXT NOT NULL,          -- 'alert' | 'baseline'
    ref_id INTEGER,
    symbol TEXT NOT NULL,
    event_type TEXT,
    bias TEXT,                       -- 'bullish' | 'bearish' | 'neutral'
    t0_ms INTEGER NOT NULL,          -- Erkennungszeitpunkt (kein Look-ahead)
    price0 REAL NOT NULL,
    horizon_min INTEGER NOT NULL,
    due_ms INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
    ret REAL, max_up REAL, max_down REAL, realized_vol REAL, quote_volume REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    UNIQUE(ref_type, ref_id, symbol, t0_ms, horizon_min)
);
CREATE INDEX IF NOT EXISTS ix_outcomes_due ON outcomes(status, due_ms);

CREATE TABLE IF NOT EXISTS collector_state (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ms INTEGER NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Database:
    """Dünner, threadsicherer Wrapper. Schreibzugriffe sind serialisiert."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    # ---- generische Helfer ----
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, row: dict, or_ignore: bool = False) -> int | None:
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
        with self._lock:
            cur = self.conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({qs})", tuple(row.values()))
            return cur.lastrowid if cur.rowcount else None

    # ---- Zustand für Collector (z. B. letzter verarbeiteter Block) ----
    def get_state(self, name: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM collector_state WHERE name=?", (name,))
        return json.loads(r["value"]) if r else default

    def set_state(self, name: str, value: Any) -> None:
        self.execute(
            "INSERT INTO collector_state(name,value,updated_ms) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_ms=excluded.updated_ms",
            (name, json.dumps(value), now_ms()),
        )

    def size_mb(self) -> float:
        if self.path == ":memory:":
            return 0.0
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.path + suffix)
            if p.exists():
                total += p.stat().st_size
        return total / 1_048_576

    def close(self) -> None:
        with self._lock:
            self.conn.close()
