"""Dynamische Whale-Schwellenwerte.

Die Relevanz einer Transaktion hängt von der Liquidität des Assets ab:
500 000 USD in einem kleinen Altcoin können bedeutender sein als 20 Mio. USD in BTC.

    schwelle = clamp(volume_factor * 24h-Volumen, floor, ceiling)
    Börsen-Transfers: schwelle * exchange_factor_multiplier
    Zusätzlich: Ist die Orderbuch-Tiefe (±1 %) klein, wird die Schwelle auf
    höchstens 50 % dieser Tiefe begrenzt (aber nie unter floor).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from cwis.state import MarketData


@dataclass
class ThresholdResult:
    threshold_usd: float
    is_whale: bool
    size_tier: int            # Index der höchsten erreichten absoluten Größenklasse (-1 = keine)
    rel_volume: float | None  # Wert / 24h-Volumen
    rel_depth: float | None   # Wert / Orderbuch-Tiefe ±1 %
    basis: str


def dynamic_threshold(cfg: dict, md: MarketData | None, *, exchange_related: bool, stablecoin: bool) -> tuple[float, str]:
    floor = float(cfg["floor_usd"])
    ceiling = float(cfg["ceiling_usd"])
    if stablecoin:
        return float(cfg["stablecoin_threshold_usd"]), "fester Stablecoin-Schwellenwert"
    vol = md.liquidity_volume_24h_usd if md else None
    if not vol:
        return max(floor, 1_000_000.0), "keine Volumendaten – konservativer Standard 1 Mio. USD"
    t = float(cfg["volume_factor"]) * vol
    basis = f"{cfg['volume_factor'] * 100:.3f} % des 24h-Volumens"
    depth = md.depth_1pct_usd if md.depth_complete else None   # Untergrenzen nicht als Deckel verwenden
    if depth:
        cap = 0.5 * depth
        if cap < t:
            t, basis = cap, "50 % der Orderbuch-Tiefe ±1 %"
    if exchange_related:
        t *= float(cfg["exchange_factor_multiplier"])
        basis += " × Börsenfaktor"
    return min(max(t, floor), ceiling), basis


def evaluate(cfg: dict, value_usd: float, md: MarketData | None, *, exchange_related: bool, stablecoin: bool) -> ThresholdResult:
    t, basis = dynamic_threshold(cfg, md, exchange_related=exchange_related, stablecoin=stablecoin)
    tiers = cfg["tiers_usd"]
    tier = bisect_right(tiers, value_usd) - 1
    vol = md.liquidity_volume_24h_usd if md else None
    rel_v = value_usd / vol if vol else None
    depth = md.depth_1pct_usd if md else None
    rel_d = value_usd / depth if depth else None
    return ThresholdResult(t, value_usd >= t, tier, rel_v, rel_d, basis)
