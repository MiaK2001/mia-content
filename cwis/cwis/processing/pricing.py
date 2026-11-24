"""USD-Bewertung von Transfers mit Plausibilitätsprüfung. Keine Ersatzwerte bei fehlenden Daten."""
from __future__ import annotations

from dataclasses import dataclass

from cwis.state import MarketState, age_s

STABLE_PEG = {"USDT": 1.0, "USDC": 1.0, "DAI": 1.0}
MAX_REF_DEVIATION = 0.03   # 3 % Abweichung Binance vs. CoinGecko -> Preis unsicher


def _age(ts_ms, now_ms) -> float:
    """Alter in Sekunden; unbekannt = unendlich alt (0 s ist ein gültiges Alter)."""
    a = age_s(ts_ms, now_ms)
    return float("inf") if a is None else a


@dataclass
class PriceQuote:
    price: float | None
    status: str      # OK | STALE | INCONSISTENT | MISSING | PEGGED
    source: str | None


def quote(state: MarketState, symbol: str, stale_after_s: float, now_ms: int | None = None) -> PriceQuote:
    if symbol in STABLE_PEG:
        ref = state.reference.get(symbol)
        if ref and abs(ref.price - 1.0) > MAX_REF_DEVIATION:
            # Depeg: echten Referenzpreis verwenden und kennzeichnen
            return PriceQuote(ref.price, "INCONSISTENT", "coingecko")
        return PriceQuote(STABLE_PEG[symbol], "PEGGED", "peg")
    md = state.market.get(symbol)
    if md is None or md.price is None or md.price <= 0:
        ref = state.reference.get(symbol)
        if ref and _age(ref.ts_ms, now_ms) <= max(stale_after_s, 600):
            return PriceQuote(ref.price, "OK", "coingecko")
        return PriceQuote(None, "MISSING", None)
    if _age(md.ts_ms, now_ms) > stale_after_s:
        return PriceQuote(md.price, "STALE", md.source)
    ref = state.reference.get(symbol)
    if ref and ref.price > 0 and abs(md.price / ref.price - 1) > MAX_REF_DEVIATION:
        return PriceQuote(md.price, "INCONSISTENT", md.source)
    return PriceQuote(md.price, "OK", md.source)


def usable(status: str) -> bool:
    """Nur verlässliche Preise fließen in USD-Werte ein."""
    return status in ("OK", "PEGGED")
