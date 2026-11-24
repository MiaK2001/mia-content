"""Gemeinsamer In-Memory-Zustand der letzten Markt- und Derivatedaten."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class MarketData:
    symbol: str
    ts_ms: int
    source: str
    price: float | None = None
    volume_24h_usd: float | None = None
    price_change_24h_pct: float | None = None
    bid_depth_1pct_usd: float | None = None
    ask_depth_1pct_usd: float | None = None
    spread_bps: float | None = None
    largest_bid_usd: float | None = None
    largest_ask_usd: float | None = None
    ret_1h: float | None = None
    realized_vol_1h: float | None = None
    volume_ratio_1h: float | None = None
    market_cap_usd: float | None = None
    global_volume_24h_usd: float | None = None   # CoinGecko, alle Handelsplätze
    depth_complete: bool | None = None          # False = Orderbuch-Ausschnitt deckt ±1 % nicht ab (Untergrenze)

    @property
    def liquidity_volume_24h_usd(self) -> float | None:
        """Bevorzugt marktweites Volumen (CoinGecko), sonst Volumen des Binance-Paares."""
        return self.global_volume_24h_usd or self.volume_24h_usd

    @property
    def depth_1pct_usd(self) -> float | None:
        if self.bid_depth_1pct_usd is None or self.ask_depth_1pct_usd is None:
            return None
        return self.bid_depth_1pct_usd + self.ask_depth_1pct_usd

    @property
    def book_imbalance(self) -> float | None:
        d = self.depth_1pct_usd
        if not d:
            return None
        return (self.bid_depth_1pct_usd - self.ask_depth_1pct_usd) / d


@dataclass
class DerivData:
    symbol: str
    ts_ms: int
    source: str
    open_interest_usd: float | None = None
    funding_rate: float | None = None
    long_short_ratio: float | None = None
    long_liq_usd_1h: float | None = None
    short_liq_usd_1h: float | None = None
    oi_change_1h_pct: float | None = None


@dataclass
class ReferencePrice:
    symbol: str
    ts_ms: int
    price: float
    market_cap_usd: float | None
    volume_24h_usd: float | None


@dataclass
class MarketState:
    market: dict[str, MarketData] = field(default_factory=dict)
    deriv: dict[str, DerivData] = field(default_factory=dict)
    reference: dict[str, ReferencePrice] = field(default_factory=dict)  # CoinGecko
    oi_history: dict[str, deque] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def set_market(self, md: MarketData) -> None:
        with self.lock:
            ref = self.reference.get(md.symbol)
            if ref:
                md.market_cap_usd = md.market_cap_usd or ref.market_cap_usd
                md.global_volume_24h_usd = md.global_volume_24h_usd or ref.volume_24h_usd
            self.market[md.symbol] = md

    def set_deriv(self, dd: DerivData) -> None:
        with self.lock:
            hist = self.oi_history.setdefault(dd.symbol, deque(maxlen=240))
            if dd.open_interest_usd is not None:
                hist.append((dd.ts_ms, dd.open_interest_usd))
                # Basis: jüngster Wert, der mindestens 55 Minuten alt ist (sonst: unbekannt)
                cutoff = dd.ts_ms - 55 * 60_000
                past = [v for t, v in hist if t <= cutoff]
                if past and past[-1]:
                    dd.oi_change_1h_pct = (dd.open_interest_usd / past[-1] - 1) * 100
            self.deriv[dd.symbol] = dd

    def set_reference(self, rp: ReferencePrice) -> None:
        with self.lock:
            self.reference[rp.symbol] = rp
            md = self.market.get(rp.symbol)
            if md is not None:
                md.market_cap_usd = rp.market_cap_usd
                md.global_volume_24h_usd = rp.volume_24h_usd


def age_s(ts_ms: int | None, now_ms: int | None = None) -> float | None:
    if ts_ms is None:
        return None
    now_ms = now_ms or int(time.time() * 1000)
    return (now_ms - ts_ms) / 1000
