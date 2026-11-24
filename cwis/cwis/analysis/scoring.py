"""Transparente, regelbasierte Scores (Version 1).

Alle Scores 0–100. Sie beschreiben, wie AUSSERGEWÖHNLICH eine Situation ist und
in welche Richtung die beobachteten Indizien zeigen. Sie sind KEINE
Wahrscheinlichkeiten: Ein Score von 80 bedeutet nicht 80 % Gewinnchance.
Wahrscheinlichkeiten dürfen erst nach Kalibrierung auf unabhängigen Testdaten
ausgegeben werden (siehe cwis/ml/validation.py).

Jede Komponente ist auf 0..1 normiert und mit einem festen Gewicht versehen.
Fehlende Daten liefern 0 Beitrag UND senken den Data Confidence Score – sie
werden nie durch Annahmen ersetzt.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from cwis.state import DerivData, MarketData, age_s

SCORING_VERSION = "rules-v1.1"
# v1.1: Transfers zwischen zwei unbekannten Wallets zählen im Whale Impact nur zur Hälfte
#       (häufig interne Umbuchungen; geringster Informationsgehalt). Begründung: docs/ARCHITEKTUR.md
UNKNOWN_WEIGHT = 0.5


def _age(ts_ms, now_ms) -> float:
    """Alter in Sekunden; unbekannt = unendlich alt (0 s ist ein gültiges Alter)."""
    a = age_s(ts_ms, now_ms)
    return float("inf") if a is None else a


def sat(x: float | None, half: float) -> float:
    """Sättigungsfunktion: 0 bei x=0, 0.5 bei x=half, -> 1 für große x."""
    if x is None or x <= 0:
        return 0.0
    return x / (x + half)


def clip01(x: float | None) -> float:
    if x is None:
        return 0.0
    return max(0.0, min(1.0, x))


@dataclass
class FlowAggregate:
    inflow_usd: float = 0.0
    outflow_usd: float = 0.0
    internal_usd: float = 0.0
    other_usd: float = 0.0
    whale_count: int = 0
    whale_volume_usd: float = 0.0
    whale_weighted_usd: float = 0.0     # gewichtet (unbekannt -> unbekannt = 0,5)
    wallet_weight: float = 0.0
    whale_exchange_usd: float = 0.0
    largest_usd: float = 0.0
    max_tier: int = -1
    wallets: set = field(default_factory=set)
    labeled_volume: float = 0.0
    classified_volume: float = 0.0
    transfers: int = 0

    @property
    def netflow_usd(self) -> float:
        """> 0: netto auf Börsen, < 0: netto von Börsen."""
        return self.inflow_usd - self.outflow_usd


def aggregate(rows: list[dict]) -> FlowAggregate:
    a = FlowAggregate()
    seen_tx = set()
    for r in rows:
        v = r.get("value_usd") or 0.0
        a.transfers += 1
        ft = r["flow_type"]
        if ft == "EXCHANGE_INFLOW":
            a.inflow_usd += v
        elif ft == "EXCHANGE_OUTFLOW":
            a.outflow_usd += v
        elif ft in ("EXCHANGE_INTERNAL", "INTER_EXCHANGE"):
            a.internal_usd += v
        else:
            a.other_usd += v
        if ft in ("EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW", "INTER_EXCHANGE"):
            a.classified_volume += v
            side_conf = r.get("to_confidence") if ft == "EXCHANGE_INFLOW" else r.get("from_confidence")
            if side_conf in ("high", "medium"):
                a.labeled_volume += v
        if r.get("is_whale"):
            key = (r["chain"], r["tx_hash"])
            if key not in seen_tx:
                seen_tx.add(key)
                a.whale_count += 1
            a.whale_volume_usd += v
            w = UNKNOWN_WEIGHT if ft == "UNKNOWN_TRANSFER" else 1.0
            a.whale_weighted_usd += v * w
            if ft in ("EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW"):
                a.whale_exchange_usd += v
            wkey = r.get("from_entity") or r.get("from_address")
            if wkey not in a.wallets:
                a.wallets.add(wkey)
                a.wallet_weight += w
            a.largest_usd = max(a.largest_usd, v)
            a.max_tier = max(a.max_tier, r.get("size_tier") if r.get("size_tier") is not None else -1)
    return a


def percentile_rank(history: list[float], value: float) -> float | None:
    if len(history) < 24:
        return None
    h = sorted(history)
    return bisect_left(h, value) / len(h)


def _weighted(components: dict[str, tuple[float, float]]) -> float:
    total_w = sum(w for w, _ in components.values())
    return round(100 * sum(w * c for w, c in components.values()) / total_w, 1) if total_w else 0.0


def compute_scores(
    symbol: str,
    agg: FlowAggregate,
    md: MarketData | None,
    dd: DerivData | None,
    *,
    price_status: str,
    onchain_fresh: bool,
    deriv_expected: bool,
    stable_netflow_usd: float | None,
    whale_volume_history: list[float],
    clusters: list[dict],
    stale_after_s: float,
    now_ms: int,
) -> dict:
    missing: list[str] = []
    md_fresh = md is not None and _age(md.ts_ms, now_ms) <= stale_after_s
    dd_fresh = dd is not None and _age(dd.ts_ms, now_ms) <= max(stale_after_s, 180)
    liq_vol = md.liquidity_volume_24h_usd if md_fresh else None
    hourly_vol = (liq_vol / 24) if liq_vol else None
    depth = md.depth_1pct_usd if md_fresh else None
    imb = md.book_imbalance if md_fresh else None
    ret = md.ret_1h if md_fresh else None
    rvol = md.realized_vol_1h if md_fresh else None
    vratio = md.volume_ratio_1h if md_fresh else None
    if hourly_vol is None:
        missing.append("Handelsvolumen")
    if depth is None:
        missing.append("Orderbuch-Tiefe")
    elif md.depth_complete is False:
        missing.append("Orderbuch-Tiefe nur teilweise erfasst (Untergrenze)")
    if ret is None:
        missing.append("Kursverlauf 1h")
    fr = dd.funding_rate if dd_fresh else None
    oi = dd.oi_change_1h_pct if dd_fresh else None
    lliq = dd.long_liq_usd_1h if dd_fresh else None
    sliq = dd.short_liq_usd_1h if dd_fresh else None
    if deriv_expected and not dd_fresh:
        missing.append("Derivatedaten")
    elif deriv_expected and oi is None:
        missing.append("OI-Veränderung (benötigt ≥ 1h Historie)")

    net = agg.netflow_usd
    def flow_pressure(x: float) -> float:
        parts = []
        if hourly_vol:
            parts.append(sat(x / hourly_vol, 0.10))
        if depth:
            parts.append(sat(x / depth, 1.0))
        return max(parts) if parts else 0.0

    liq_total = (lliq or 0) + (sliq or 0)
    long_dom = (lliq or 0) / liq_total if liq_total else 0.0
    move = abs(ret) / max(rvol, 1e-4) if ret is not None and rvol is not None else None
    vfac = min(1.0, vratio) if vratio is not None else 0.5

    bearish = {
        "Börsen-Nettozufluss": (30, flow_pressure(net) if net > 0 else 0.0),
        "Long-Liquidationen": (15, sat(lliq / hourly_vol, 0.02) * long_dom if lliq and hourly_vol else 0.0),
        "Verkaufsseitiges Orderbuch": (10, clip01(-imb * 2) if imb is not None else 0.0),
        "Abwärtsdynamik mit Volumen": (15, sat(move, 1.0) * vfac if ret is not None and ret < 0 else 0.0),
        "Überhitzte Long-Positionierung": (10, clip01(fr / 0.0005) * (0.5 + 0.5 * clip01((oi or 0) / 5)) if fr and fr > 0 else 0.0),
        "OI steigt bei fallendem Kurs": (10, sat(oi / 2, 1.0) if oi and oi > 0 and ret is not None and ret < 0 else 0.0),
        "Stablecoins verlassen Börsen": (10, sat(-stable_netflow_usd / 1e8, 1.0) if stable_netflow_usd and stable_netflow_usd < 0 else 0.0),
    }
    bullish = {
        "Börsen-Nettoabfluss": (30, flow_pressure(-net) if net < 0 else 0.0),
        "Short-Liquidationen": (15, sat(sliq / hourly_vol, 0.02) * (1 - long_dom) if sliq and hourly_vol else 0.0),
        "Kaufseitiges Orderbuch": (10, clip01(imb * 2) if imb is not None else 0.0),
        "Aufwärtsdynamik mit Volumen": (15, sat(move, 1.0) * vfac if ret is not None and ret > 0 else 0.0),
        "Überhitzte Short-Positionierung": (10, clip01(-fr / 0.0005) * (0.5 + 0.5 * clip01((oi or 0) / 5)) if fr and fr < 0 else 0.0),
        "OI steigt bei steigendem Kurs": (10, sat(oi / 2, 1.0) if oi and oi > 0 and ret is not None and ret > 0 else 0.0),
        "Stablecoins fließen auf Börsen": (10, sat(stable_netflow_usd / 1e8, 1.0) if stable_netflow_usd and stable_netflow_usd > 0 else 0.0),
    }
    gross_ex = agg.inflow_usd + agg.outflow_usd
    market = {
        "Börsenflüsse relativ zum Volumen": (35, sat(gross_ex / hourly_vol, 0.15) if hourly_vol else 0.0),
        "Größter Flow relativ zur Tiefe": (25, sat(max(agg.largest_usd, abs(net)) / depth, 1.0) if depth else 0.0),
        "Liquidationen": (15, sat(liq_total / hourly_vol, 0.03) if hourly_vol else 0.0),
        "Volumenanomalie": (15, sat(vratio - 1, 1.0) if vratio else 0.0),
        "OI-Veränderung": (10, sat(abs(oi) / 3, 1.0) if oi is not None else 0.0),
    }
    rank = percentile_rank(whale_volume_history, agg.whale_volume_usd)
    max_cluster = max((c["wallets"] for c in clusters), default=0)
    whale = {
        "Whale-Volumen relativ zum Volumen": (35, sat(agg.whale_weighted_usd / hourly_vol, 0.2) if hourly_vol else
                                              sat(agg.whale_weighted_usd / 1e8, 1.0)),
        "Anzahl aktiver Whale-Wallets": (20, sat(max(agg.wallet_weight, max_cluster), 5)),
        "Anteil Börsenbezug": (15, agg.whale_exchange_usd / agg.whale_volume_usd if agg.whale_volume_usd else 0.0),
        "Größenklasse": (10, (agg.max_tier + 1) / 8 if agg.max_tier >= 0 else 0.0),
        "Seltenheit vs. Historie": (20, rank if rank is not None else 0.0),   # ohne Historie kein Beitrag
    }
    if rank is None:
        missing.append("Historische Vergleichsbasis (< 24 Messpunkte)")

    # ---- Data Confidence ----
    conf_parts = {
        "Preis": 30 if price_status in ("OK", "PEGGED") and md_fresh else (10 if price_status == "INCONSISTENT" else 0),
        "Orderbuch": 15 if depth is not None else 0,
        "Derivate": (20 if dd_fresh else 0) if deriv_expected else 20,
        "On-Chain-Abdeckung": 25 if onchain_fresh else 0,
        "Wallet-Zuordnung": round(10 * (agg.labeled_volume / agg.classified_volume), 1) if agg.classified_volume else 10,
    }
    confidence = round(sum(conf_parts.values()), 1)
    whale_raw = _weighted(whale)
    # Datenqualität als 10. Faktor: unsichere Daten dämpfen den Impact (max. -40 %)
    whale_impact = round(whale_raw * (0.6 + 0.4 * confidence / 100), 1) if agg.whale_count else 0.0

    if price_status not in ("OK", "PEGGED") or not md_fresh:
        status = "STALE" if md is not None else "INSUFFICIENT"
    elif confidence >= 60:
        status = "VALID"
    elif confidence >= 40:
        status = "DEGRADED"
    else:
        status = "INSUFFICIENT"

    return {
        "symbol": symbol,
        "scoring_version": SCORING_VERSION,
        "whale_impact_score": whale_impact,
        "bullish_score": _weighted(bullish),
        "bearish_score": _weighted(bearish),
        "market_impact_score": _weighted(market),
        "data_confidence": confidence,
        "data_status": status,
        "components": {
            "whale": {k: round(c, 3) for k, (_, c) in whale.items()},
            "bullish": {k: round(c, 3) for k, (_, c) in bullish.items()},
            "bearish": {k: round(c, 3) for k, (_, c) in bearish.items()},
            "market": {k: round(c, 3) for k, (_, c) in market.items()},
            "confidence": conf_parts,
            "weights": {
                "whale": {k: w for k, (w, _) in whale.items()},
                "bullish": {k: w for k, (w, _) in bullish.items()},
                "bearish": {k: w for k, (w, _) in bearish.items()},
                "market": {k: w for k, (w, _) in market.items()},
            },
        },
        "missing_data": missing,
        "flows": {
            "exchange_inflow_usd": round(agg.inflow_usd, 2),
            "exchange_outflow_usd": round(agg.outflow_usd, 2),
            "exchange_netflow_usd": round(net, 2),
            "internal_exchange_usd": round(agg.internal_usd, 2),
            "other_transfer_usd": round(agg.other_usd, 2),
            "whale_count": agg.whale_count,
            "whale_volume_usd": round(agg.whale_volume_usd, 2),
            "largest_transfer_usd": round(agg.largest_usd, 2),
            "active_whale_wallets": len(agg.wallets),
            "transfers": agg.transfers,
        },
        "market": None if md is None else {
            "price": md.price, "volume_24h_usd": md.volume_24h_usd, "depth_1pct_usd": md.depth_1pct_usd,
            "book_imbalance": md.book_imbalance, "spread_bps": md.spread_bps, "ret_1h": md.ret_1h,
            "realized_vol_1h": md.realized_vol_1h, "volume_ratio_1h": md.volume_ratio_1h,
            "market_cap_usd": md.market_cap_usd, "age_s": age_s(md.ts_ms, now_ms),
            "global_volume_24h_usd": md.global_volume_24h_usd, "depth_complete": md.depth_complete,
            "largest_bid_usd": md.largest_bid_usd, "largest_ask_usd": md.largest_ask_usd,
        },
        "derivatives": None if dd is None else {
            "open_interest_usd": dd.open_interest_usd, "oi_change_1h_pct": dd.oi_change_1h_pct,
            "funding_rate": dd.funding_rate, "long_short_ratio": dd.long_short_ratio,
            "long_liq_usd_1h": dd.long_liq_usd_1h, "short_liq_usd_1h": dd.short_liq_usd_1h,
            "age_s": age_s(dd.ts_ms, now_ms),
        },
        "clusters": clusters,
    }


def classify_level(s: dict) -> tuple[str, str]:
    """Warnstufe und Marktzustand aus den Scores (feste, dokumentierte Regeln)."""
    wi, mi = s["whale_impact_score"], s["market_impact_score"]
    bull, bear, conf = s["bullish_score"], s["bearish_score"], s["data_confidence"]
    has_cluster = bool(s.get("clusters"))
    direction = max(bull, bear)
    if wi >= 70 and mi >= 60 and direction >= 55 and conf >= 60:
        level = "RED"
    elif wi >= 50 and (mi >= 40 or has_cluster) and conf >= 50:
        level = "ORANGE"
    elif wi >= 25:
        level = "YELLOW"
    else:
        level = "GREEN"
    # Unvollständige Daten dürfen nie zu einer Eskalation führen
    if s["data_status"] in ("INSUFFICIENT", "STALE") and level in ("ORANGE", "RED"):
        level = "YELLOW"
    if level == "GREEN":
        cond = "NORMAL"
    elif level == "YELLOW":
        cond = "ELEVATED_ACTIVITY"
    elif level == "ORANGE":
        cond = "ELEVATED_RISK" if bear >= bull + 15 else "HIGH_ACTIVITY"
    else:
        cond = "CRITICAL"
    return level, cond


def recommended_action(s: dict, level: str) -> str:
    """Rein informativ. Löst NIEMALS eine Handlung aus."""
    if s["data_status"] != "VALID":
        return "NO_SIGNAL_DATA_INSUFFICIENT"
    if level == "RED":
        return "CRITICAL_NO_NEW_POSITIONS_ADVISORY"
    if level in ("YELLOW", "ORANGE"):
        if s["bearish_score"] >= s["bullish_score"] + 20:
            return "RISK_WARNING"
        if s["bullish_score"] >= s["bearish_score"] + 20:
            return "BULLISH_CONTEXT"
    return "NONE"
