"""Vorbereitete Schnittstelle für den bestehenden Trading-Bot.

STATUS: DEAKTIVIERT – NICHT VERBUNDEN – TESTPHASE

Dieses Modul enthält
  * das versionierte Signalformat (build_signal), das die lokale, rein lesende
    REST-/WebSocket-API ausliefert, und
  * eine KONZEPTIONELLE Entscheidungslogik (advisory_for_bot_signal), die zeigt,
    wie der Bot die Daten später verwenden KÖNNTE.

Es gibt hier keinerlei Code zur Handelsausführung, keine Exchange-Clients mit
Schlüsseln und keine Verbindung zum Trading-Bot. Die Sperre ist eine Konstante,
die nicht über die Konfiguration geändert werden kann. Eine Aktivierung erfordert
eine bewusste Code-Änderung nach ausdrücklicher Freigabe durch den Eigentümer.
"""
from __future__ import annotations

from datetime import datetime, timezone

from cwis import API_SCHEMA_VERSION

INTEGRATION_ENABLED = False
INTEGRATION_STATUS = "NICHT VERBUNDEN – TESTPHASE"
DISCLAIMER = ("Nur Information. Keine Anlageberatung, keine Handelsfreigabe. Scores sind keine "
              "Wahrscheinlichkeiten. Fehlende/ungültige Daten bedeuten KEINE Freigabe.")


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_signal(score: dict | None, *, symbol: str, market_symbol: str, level: str | None,
                 market_condition: str | None, action: str | None, ts_ms: int, validity_min: int) -> dict:
    base = {
        "schema_version": API_SCHEMA_VERSION,
        "symbol": market_symbol,
        "asset": symbol,
        "integration_status": INTEGRATION_STATUS,
        "disclaimer": DISCLAIMER,
    }
    if score is None:
        return {**base, "timestamp": iso(ts_ms), "data_status": "INSUFFICIENT", "whale_impact_score": None,
                "bullish_score": None, "bearish_score": None, "market_impact_score": None,
                "data_confidence": 0, "market_condition": "UNKNOWN", "alert_level": None,
                "exchange_netflow_usd": None, "unusual_whale_activity": None,
                "signal_valid_until": iso(ts_ms), "recommended_action": "NO_SIGNAL_DATA_INSUFFICIENT",
                "missing_data": ["Noch keine Analyse für dieses Symbol"]}
    valid = score["data_status"] == "VALID"
    return {
        **base,
        "timestamp": iso(ts_ms),
        "scoring_version": score["scoring_version"],
        "whale_impact_score": score["whale_impact_score"],
        "bullish_score": score["bullish_score"],
        "bearish_score": score["bearish_score"],
        "market_impact_score": score["market_impact_score"],
        "data_confidence": score["data_confidence"],
        "market_condition": market_condition,
        "alert_level": level,
        "exchange_netflow_usd": score["flows"]["exchange_netflow_usd"],
        "unusual_whale_activity": level in ("ORANGE", "RED"),
        "data_status": score["data_status"],
        # Ungültige Daten verfallen sofort
        "signal_valid_until": iso(ts_ms + (validity_min * 60_000 if valid else 0)),
        "recommended_action": action,
        "missing_data": score.get("missing_data", []),
    }


def advisory_for_bot_signal(bot_side: str, cwis_signal: dict, now_iso: str | None = None) -> dict:
    """KONZEPT (nicht aktiv): Bewertung eines technischen Bot-Signals mit CWIS-Kontext.

    Gibt nur eine Empfehlung zurück. Muss vor jeder Nutzung per Backtest und
    Paper-Trading auf zusätzlichen Nutzen geprüft werden.
    """
    bot_side = bot_side.upper()
    status = cwis_signal.get("data_status")
    expired = now_iso is not None and cwis_signal.get("signal_valid_until", "") < now_iso
    if status != "VALID" or expired:
        return {"advice": "NO_CONFIRMATION", "reason": "Whale-Daten fehlen/ungültig/abgelaufen – keine automatische Freigabe."}
    bull, bear = cwis_signal["bullish_score"], cwis_signal["bearish_score"]
    critical = cwis_signal.get("alert_level") == "RED"
    if bot_side == "LONG":
        if critical and bear > bull:
            return {"advice": "REJECT_OR_WARN", "reason": "Kritische bearishe Whale-Lage."}
        if bull >= bear + 20:
            return {"advice": "CONFIRM", "reason": "Whale-Kontext unterstützt LONG."}
        if bear >= bull + 20:
            return {"advice": "WARN", "reason": "Whale-Kontext widerspricht LONG."}
    elif bot_side == "SHORT":
        if critical and bull > bear:
            return {"advice": "REJECT_OR_WARN", "reason": "Kritische bullishe Whale-Lage."}
        if bear >= bull + 20:
            return {"advice": "CONFIRM", "reason": "Whale-Kontext unterstützt SHORT."}
        if bull >= bear + 20:
            return {"advice": "WARN", "reason": "Whale-Kontext widerspricht SHORT."}
    return {"advice": "NO_CONFIRMATION", "reason": "Whale-Daten unklar."}
