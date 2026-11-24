"""Spot-Marktdaten (Binance, öffentlich) und Referenzdaten (CoinGecko).

Hinweis: Sichtbare Orderbuch-Orders sind KEINE ausgeführten Trades und können
jederzeit storniert werden. Tiefe/Größte Order sind daher nur Momentaufnahmen.
USDT-Kurse werden als USD-Näherung verwendet.
"""
from __future__ import annotations

import json
import math
import time

from cwis.collectors.base import Collector
from cwis.db import Database
from cwis.http import HttpClient, HttpError
from cwis.state import MarketData, MarketState, ReferencePrice


def book_metrics(bids: list, asks: list, band: float = 0.01) -> dict:
    """Tiefe innerhalb ±band um den Mittelkurs in Quote-Währung (USDT)."""
    if not bids or not asks:
        return {}
    best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return {}
    mid = (best_bid + best_ask) / 2
    bd = [float(p) * float(q) for p, q in bids if float(p) >= mid * (1 - band)]
    ad = [float(p) * float(q) for p, q in asks if float(p) <= mid * (1 + band)]
    return {
        "bid_depth_1pct_usd": sum(bd),
        "ask_depth_1pct_usd": sum(ad),
        "spread_bps": (best_ask - best_bid) / mid * 10_000,
        "largest_bid_usd": max(bd) if bd else 0.0,
        "largest_ask_usd": max(ad) if ad else 0.0,
        "mid": mid,
        # Deckt der geladene Ausschnitt die volle Spanne ab? Sonst ist die Tiefe nur eine Untergrenze.
        "depth_complete": float(bids[-1][0]) <= mid * (1 - band) and float(asks[-1][0]) >= mid * (1 + band),
    }


def kline_metrics(klines: list) -> dict:
    """Aus 1m-Kerzen der letzten ~60 Minuten: Rendite, realisierte Volatilität, Volumenverhältnis."""
    if len(klines) < 30:
        return {}
    closes = [float(k[4]) for k in klines]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    vol = math.sqrt(sum(r * r for r in rets) / len(rets)) * math.sqrt(60) if rets else None
    return {
        "ret_1h": closes[-1] / float(klines[0][1]) - 1,
        "realized_vol_1h": vol,
        "quote_volume_1h": sum(float(k[7]) for k in klines),
    }


class BinanceSpotCollector(Collector):
    name = "binance_spot"

    def __init__(self, health, http: HttpClient, db: Database, state: MarketState, cfg):
        super().__init__(health)
        self.http, self.db, self.state = http, db, state
        self.watch = cfg.watch
        self.base_urls = list(cfg.get("sources.market.base_urls"))
        self.interval_s = cfg.get("sources.market.interval_seconds", 30)
        self.depth_limit = cfg.get("sources.market.depth_limit", 1000)
        self.note = "Spotkurse, 24h-Volumen, Orderbuch (öffentlich, ohne API-Schlüssel)"
        self._snap_every = 60_000
        self._last_snap: dict[str, int] = {}

    async def _get(self, path: str, params: dict):
        last = None
        for base in list(self.base_urls):
            try:
                return await self.http.get(base + path, params=params, source=self.name)
            except HttpError as e:
                last = e
                if e.status in (451, 403):  # Geo-Sperre -> diese Basis-URL nach hinten
                    self.base_urls.remove(base)
                    self.base_urls.append(base)
        raise last or HttpError("keine Binance-Basis-URL erreichbar")

    async def run_once(self) -> None:
        symbols = [w["market"] for w in self.watch]
        tickers = await self._get("/api/v3/ticker/24hr", {"symbols": json.dumps(symbols, separators=(",", ":"))})
        by_sym = {t["symbol"]: t for t in tickers}
        now = int(time.time() * 1000)
        for w in self.watch:
            t = by_sym.get(w["market"])
            if not t:
                continue
            md = MarketData(symbol=w["symbol"], ts_ms=now, source="binance")
            md.price = float(t["lastPrice"]) or None
            md.volume_24h_usd = float(t["quoteVolume"])
            md.price_change_24h_pct = float(t["priceChangePercent"])
            try:
                depth = await self._get("/api/v3/depth", {"symbol": w["market"], "limit": self.depth_limit})
                bm = book_metrics(depth.get("bids", []), depth.get("asks", []))
                for k in ("bid_depth_1pct_usd", "ask_depth_1pct_usd", "spread_bps", "largest_bid_usd", "largest_ask_usd", "depth_complete"):
                    setattr(md, k, bm.get(k))
                kl = await self._get("/api/v3/klines", {"symbol": w["market"], "interval": "1m", "limit": 60})
                km = kline_metrics(kl)
                md.ret_1h = km.get("ret_1h")
                md.realized_vol_1h = km.get("realized_vol_1h")
                if km.get("quote_volume_1h") and md.volume_24h_usd:
                    md.volume_ratio_1h = km["quote_volume_1h"] / (md.volume_24h_usd / 24)
            except HttpError as e:
                self.health.error(self.name, f"{w['market']}: {e}")
            self.state.set_market(md)
            if now - self._last_snap.get(md.symbol, 0) >= self._snap_every:
                self._last_snap[md.symbol] = now
                self.db.insert("market_snapshots", {
                    "symbol": md.symbol, "ts_ms": now, "source": "binance", "price": md.price,
                    "volume_24h_usd": md.volume_24h_usd, "price_change_24h_pct": md.price_change_24h_pct,
                    "bid_depth_1pct_usd": md.bid_depth_1pct_usd, "ask_depth_1pct_usd": md.ask_depth_1pct_usd,
                    "spread_bps": md.spread_bps, "largest_bid_usd": md.largest_bid_usd,
                    "largest_ask_usd": md.largest_ask_usd, "ret_1h": md.ret_1h,
                    "realized_vol_1h": md.realized_vol_1h, "volume_ratio_1h": md.volume_ratio_1h,
                    "market_cap_usd": md.market_cap_usd,
                }, or_ignore=True)

    async def klines(self, market: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000) -> list:
        """Für die historische Auswertung (Outcome-Tracker)."""
        return await self._get("/api/v3/klines", {
            "symbol": market, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": limit})


class CoinGeckoCollector(Collector):
    name = "coingecko"

    def __init__(self, health, http: HttpClient, state: MarketState, cfg):
        super().__init__(health)
        self.http, self.state = http, state
        self.base = cfg.get("sources.coingecko.base_url")
        self.interval_s = cfg.get("sources.coingecko.interval_seconds", 300)
        self.ids = {w["coingecko"]: w["symbol"] for w in cfg.watch if w.get("coingecko")}
        self.ids.update({"tether": "USDT", "usd-coin": "USDC", "dai": "DAI"})
        self.note = "Marktkapitalisierung, Referenzpreis zur Plausibilitätsprüfung"

    async def run_once(self) -> None:
        data = await self.http.get(f"{self.base}/coins/markets", source=self.name, params={
            "vs_currency": "usd", "ids": ",".join(self.ids), "sparkline": "false"})
        now = int(time.time() * 1000)
        for c in data:
            sym = self.ids.get(c.get("id"))
            if sym and c.get("current_price"):
                self.state.set_reference(ReferencePrice(
                    sym, now, float(c["current_price"]), c.get("market_cap"), c.get("total_volume")))
