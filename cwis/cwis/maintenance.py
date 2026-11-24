"""Aufbewahrungsregeln und Speicherbegrenzung."""
from __future__ import annotations

import logging

from cwis.db import Database, now_ms

log = logging.getLogger("cwis.maintenance")
DAY = 86_400_000


def run_retention(db: Database, cfg) -> dict:
    now = now_ms()
    snap_days = cfg.get("storage.retention_days_snapshots", 14)
    res = {}
    for table, col, days in (
        ("market_snapshots", "ts_ms", snap_days),
        ("derivatives_snapshots", "ts_ms", snap_days),
        ("scores", "ts_ms", max(snap_days, 90)),
        ("transfers", "detected_ms", cfg.get("storage.retention_days_transfers", 365)),
        ("alerts", "ts_ms", cfg.get("storage.retention_days_alerts", 730)),
        ("outcomes", "t0_ms", cfg.get("storage.retention_days_alerts", 730)),
    ):
        cur = db.execute(f"DELETE FROM {table} WHERE {col} < ?", (now - days * DAY,))
        res[table] = cur.rowcount
    # Größenlimit: älteste Snapshots/Scores zuerst entfernen (Ereignisse bleiben erhalten)
    max_mb = cfg.get("storage.max_db_size_mb", 2048)
    rounds = 0
    while db.size_mb() > max_mb and rounds < 10:
        rounds += 1
        for table in ("market_snapshots", "derivatives_snapshots", "scores"):
            oldest = db.one(f"SELECT MIN(ts_ms) AS t FROM {table}")
            if oldest and oldest["t"]:
                db.execute(f"DELETE FROM {table} WHERE ts_ms < ?", (oldest["t"] + DAY,))
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("VACUUM")
        log.warning("Datenbank über %s MB – älteste Snapshots entfernt (Runde %s)", max_mb, rounds)
    db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    db.execute("PRAGMA optimize")
    res["size_mb"] = round(db.size_mb(), 2)
    return res
