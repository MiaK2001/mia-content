"""Nachvollziehbare Analyse in deutscher Sprache – trennt Beobachtung, Interpretation und Unsicherheit."""
from __future__ import annotations


def usd(v: float | None) -> str:
    if v is None:
        return "n/v"
    a = abs(v)
    if a >= 1e9:
        s = f"{a / 1e9:.2f} Mrd."
    elif a >= 1e6:
        s = f"{a / 1e6:.2f} Mio."
    elif a >= 1e3:
        s = f"{a / 1e3:.0f} Tsd."
    else:
        s = f"{a:.0f}"
    return ("-" if v < 0 else "") + s + " USD"


ALTERNATIVES = {
    "inflow": [
        "Sammel-Transfers (Sweeps) von Kunden-Einzahlungsadressen in Börsen-Hot-Wallets – keine neue Verkaufsabsicht.",
        "Hinterlegung als Sicherheit für Derivate- oder Margin-Positionen (auch Absicherung/Hedging).",
        "OTC- oder Market-Maker-Bestandsverschiebung ohne Handelsabsicht.",
        "Fehlerhafte oder veraltete Wallet-Zuordnung.",
    ],
    "outflow": [
        "Umschichtung in Cold Storage oder zu einem Verwahrer (Custody) derselben Börse – nicht zwingend Akkumulation.",
        "Auszahlungen an Market Maker, OTC-Desks oder andere Börsen.",
        "Bündelung vieler kleiner Kundenauszahlungen.",
        "Fehlerhafte oder veraltete Wallet-Zuordnung.",
    ],
    "unknown": [
        "Interne Umbuchung eines einzelnen Akteurs zwischen eigenen Wallets.",
        "Unbekannte Börsen- oder Verwahrer-Wallet (nicht in der Label-Datenbank).",
    ],
    "derivatives": [
        "Liquidationsdaten stammen nur von OKX und sind in volatilen Phasen eine Untergrenze.",
        "Orderbuch-Orders können jederzeit storniert werden (Spoofing möglich).",
    ],
}


def build_analysis(s: dict, level: str, history: dict | None, sources: list[str]) -> dict:
    f, m, d = s["flows"], s.get("market") or {}, s.get("derivatives") or {}
    observed, impacts, alternatives, uncertainties = [], [], [], list(s.get("missing_data", []))
    if f["exchange_inflow_usd"]:
        observed.append(f"Börsenzuflüsse: {usd(f['exchange_inflow_usd'])}")
    if f["exchange_outflow_usd"]:
        observed.append(f"Börsenabflüsse: {usd(f['exchange_outflow_usd'])}")
    if f["exchange_inflow_usd"] or f["exchange_outflow_usd"]:
        observed.append(f"Nettofluss: {usd(f['exchange_netflow_usd'])} (positiv = netto auf Börsen)")
    if f["internal_exchange_usd"]:
        observed.append(f"Interne/zwischenbörsliche Umbuchungen (nicht im Nettofluss): {usd(f['internal_exchange_usd'])}")
    if f["whale_count"]:
        observed.append(f"{f['whale_count']} Whale-Transaktion(en), Gesamt {usd(f['whale_volume_usd'])}, größte {usd(f['largest_transfer_usd'])}")
    for c in s.get("clusters", []):
        direction = "auf Börsen" if c["flow_type"] == "EXCHANGE_INFLOW" else "von Börsen"
        observed.append(
            f"Häufung: {c['wallets']} Wallets/Entitäten ({c['unknown_wallets']} unbekannt) bewegten {usd(c['total_usd'])} {direction}"
            f" – betroffen: {', '.join(list(c['exchanges'])[:4])}")
    if d.get("oi_change_1h_pct") is not None:
        observed.append(f"Open Interest 1h: {d['oi_change_1h_pct']:+.2f} %")
    if d.get("funding_rate") is not None:
        observed.append(f"Funding Rate: {d['funding_rate'] * 100:.4f} %")
    if d.get("long_liq_usd_1h") or d.get("short_liq_usd_1h"):
        observed.append(f"Liquidationen 1h (OKX, Untergrenze): Long {usd(d.get('long_liq_usd_1h'))}, Short {usd(d.get('short_liq_usd_1h'))}")
    if m.get("ret_1h") is not None:
        observed.append(f"Kursveränderung 1h: {m['ret_1h'] * 100:+.2f} %")
    if m.get("book_imbalance") is not None:
        observed.append(f"Orderbuch-Ungleichgewicht ±1 %: {m['book_imbalance'] * 100:+.1f} % (positiv = mehr Kaufaufträge)")

    bull, bear = s["bullish_score"], s["bearish_score"]
    if bear >= bull + 15:
        impacts.append("Die Indizien deuten auf möglicherweise erhöhten Verkaufsdruck bzw. Abwärtsrisiko hin.")
    elif bull >= bear + 15:
        impacts.append("Die Indizien deuten auf möglicherweise sinkendes Börsenangebot bzw. Aufwärtsdruck hin.")
    else:
        impacts.append("Keine eindeutige Richtung erkennbar – die Indizien sind gemischt oder schwach.")
    if s["market_impact_score"] >= 50:
        impacts.append("Die Größenordnung ist relativ zu Volumen/Liquidität erheblich; erhöhte Volatilität ist möglich.")

    if f["exchange_inflow_usd"]:
        alternatives += ALTERNATIVES["inflow"]
    if f["exchange_outflow_usd"]:
        alternatives += ALTERNATIVES["outflow"]
    if f["other_transfer_usd"]:
        alternatives += ALTERNATIVES["unknown"]
    alternatives += ALTERNATIVES["derivatives"]
    uncertainties.append("Transfers sind keine Käufe/Verkäufe; ob gehandelt wird, ist on-chain nicht sichtbar.")
    uncertainties.append("Wallet-Zuordnungen stammen aus öffentlichen Labels und sind nicht von CWIS verifiziert.")
    uncertainties.append("Scores sind regelbasierte Indikatoren, keine Wahrscheinlichkeiten.")

    hist_text = "Noch keine ausreichende historische Vergleichsbasis (Mindestanzahl n = 10)."
    if history and history.get("n", 0) >= 10:
        hist_text = (
            f"In {history['n']} vergleichbaren früheren Situationen ({history['label']}) betrug die mittlere Kursveränderung "
            f"nach {history['horizon_min']} Min. {history['mean_ret'] * 100:+.2f} % "
            f"(Referenz ohne Signal: {history['baseline_mean_ret'] * 100:+.2f} %). Vergangene Muster garantieren keine Wiederholung.")
    return {
        "level": level,
        "observed_events": observed or ["Keine auffälligen Whale-Ereignisse im Betrachtungsfenster."],
        "possible_market_impact": impacts,
        "alternative_explanations": list(dict.fromkeys(alternatives)),
        "uncertainties": uncertainties,
        "historical_comparison": hist_text,
        "data_sources": sources,
        "data_freshness": {
            "market_age_s": None if m.get("age_s") is None else round(m["age_s"], 1),
            "derivatives_age_s": None if d.get("age_s") is None else round(d["age_s"], 1),
        },
    }
