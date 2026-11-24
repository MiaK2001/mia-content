"""Frühwarnsystem mit Alarmfilterung (keine Dauer-Wiederholungen identischer Meldungen)."""
from __future__ import annotations

import json

from cwis.db import Database

LEVEL_RANK = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}


def event_type(s: dict) -> str:
    f = s["flows"]
    if s.get("clusters"):
        c = s["clusters"][0]
        return "CLUSTER_" + ("INFLOW" if c["flow_type"] == "EXCHANGE_INFLOW" else "OUTFLOW")
    if f["exchange_netflow_usd"] > 0 and f["exchange_inflow_usd"] >= f["whale_volume_usd"] * 0.3:
        return "EXCHANGE_INFLOW"
    if f["exchange_netflow_usd"] < 0 and f["exchange_outflow_usd"] >= f["whale_volume_usd"] * 0.3:
        return "EXCHANGE_OUTFLOW"
    if f["whale_count"]:
        return "WHALE_TRANSFER"
    return "MARKET_STRUCTURE"


def bias(s: dict) -> str:
    if s["bearish_score"] >= s["bullish_score"] + 15:
        return "bearish"
    if s["bullish_score"] >= s["bearish_score"] + 15:
        return "bullish"
    return "neutral"


class AlertManager:
    def __init__(self, db: Database, cfg):
        self.db = db
        self.cooldown = {k: v * 60_000 for k, v in cfg.get("alerts.cooldown_minutes").items()}
        self.rescore_delta = cfg.get("alerts.rescore_delta", 15)
        self._last: dict[str, dict] = {}   # dedupe_key -> {ts, score, level}
        self._last_level: dict[str, str] = {}

    def maybe_alert(self, s: dict, level: str, analysis: dict, signal: dict, now_ms: int) -> dict | None:
        sym = s["symbol"]
        prev_level = self._last_level.get(sym, "GREEN")
        self._last_level[sym] = level
        if level == "GREEN":
            return None
        et = event_type(s)
        key = f"{sym}:{level}:{et}"
        last = self._last.get(key)
        escalation = LEVEL_RANK[level] > LEVEL_RANK[prev_level]
        if last and not escalation:
            within = now_ms - last["ts"] < self.cooldown.get(level, 3_600_000)
            jump = s["whale_impact_score"] - last["score"] >= self.rescore_delta
            if within and not jump:
                return None
        # YELLOW nur bei tatsächlicher Whale-Aktivität (nicht bei reinen Marktschwankungen)
        if level == "YELLOW" and s["flows"]["whale_count"] == 0:
            return None
        self._last[key] = {"ts": now_ms, "score": s["whale_impact_score"]}
        price = (s.get("market") or {}).get("price")
        payload = {
            "symbol": sym, "level": level, "event_type": et, "bias": bias(s),
            "scores": {k: s[k] for k in ("whale_impact_score", "bullish_score", "bearish_score",
                                         "market_impact_score", "data_confidence")},
            "data_status": s["data_status"], "flows": s["flows"], "clusters": s.get("clusters", []),
            "analysis": analysis, "signal": signal, "escalation": escalation,
        }
        row = {
            "symbol": sym, "ts_ms": now_ms, "level": level, "event_type": et, "dedupe_key": key,
            "whale_impact": s["whale_impact_score"], "bullish": s["bullish_score"], "bearish": s["bearish_score"],
            "data_confidence": s["data_confidence"], "volume_usd": s["flows"]["whale_volume_usd"],
            "price_at_alert": price, "payload": json.dumps(payload, ensure_ascii=False),
        }
        row["id"] = self.db.insert("alerts", row)
        return {**row, "payload": payload}
