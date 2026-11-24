"""Derivatedaten von OKX (öffentliche Endpunkte, kein API-Schlüssel).

Es werden ausschließlich lesende Endpunkte unter /api/v5/public und /api/v5/rubik
verwendet. Liquidationsdaten: OKX liefert die letzten bis zu 100 Liquidationen –
in sehr volatilen Phasen ist die Stundensumme daher eine UNTERGRENZE.
"""
from __future__ import annotations

import time

from cwis.collectors.base import Collector
from cwis.db import Database
from cwis.http import HttpClient, HttpError
from cwis.state import DerivData, MarketState


def liquidation_sums(details: list, ct_val: float, since_ms: int) -> tuple[float, float]:
    long_usd = short_usd = 0.0
    for d in details:
        try:
            ts = int(d.get("ts") or d.get("time"))
            if ts < since_ms:
                continue
            usd = float(d["sz"]) * ct_val * float(d["bkPx"])
        except (KeyError, TypeError, ValueError):
            continue
        if d.get("posSide") == "long":
            long_usd += usd
        elif d.get("posSide") == "short":
            short_usd += usd
    return long_usd, short_usd


class OkxDerivativesCollector(Collector):
    name = "okx_derivatives"

    def __init__(self, health, http: HttpClient, db: Database, state: MarketState, cfg):
        super().__init__(health)
        self.http, self.db, self.state = http, db, state
        self.base = cfg.get("sources.derivatives.base_url")
        self.interval_s = cfg.get("sources.derivatives.interval_seconds", 60)
        self.watch = [w for w in cfg.watch if w.get("deriv")]
        self._ct_val: dict[str, float] = {}
        self.note = "Open Interest, Funding, Long/Short-Verhältnis, Liquidationen (OKX Perpetuals)"

    async def _data(self, path: str, params: dict):
        r = await self.http.get(self.base + path, params=params, source=self.name)
        if str(r.get("code")) != "0":
            raise HttpError(f"OKX-Fehler {r.get('code')}: {r.get('msg')}")
        return r.get("data") or []

    async def _contract_value(self, inst: str) -> float | None:
        if inst not in self._ct_val:
            d = await self._data("/api/v5/public/instruments", {"instType": "SWAP", "instId": inst})
            if not d:
                return None
            self._ct_val[inst] = float(d[0]["ctVal"])
        return self._ct_val[inst]

    async def run_once(self) -> None:
        now = int(time.time() * 1000)
        failures = 0
        for w in self.watch:
            inst, sym = w["deriv"], w["symbol"]
            dd = DerivData(symbol=sym, ts_ms=now, source="okx")
            try:
                oi = await self._data("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst})
                if oi:
                    dd.open_interest_usd = float(oi[0]["oiUsd"])
                fr = await self._data("/api/v5/public/funding-rate", {"instId": inst})
                if fr:
                    dd.funding_rate = float(fr[0]["fundingRate"])
                ccy = inst.split("-")[0]
                ls = await self._data("/api/v5/rubik/stat/contracts/long-short-account-ratio",
                                      {"ccy": ccy, "period": "5m"})
                if ls:
                    dd.long_short_ratio = float(ls[0][1])
                ct = await self._contract_value(inst)
                liq = await self._data("/api/v5/public/liquidation-orders",
                                       {"instType": "SWAP", "uly": inst.rsplit("-", 1)[0], "state": "filled", "limit": 100})
                if liq and ct:
                    dd.long_liq_usd_1h, dd.short_liq_usd_1h = liquidation_sums(
                        liq[0].get("details", []), ct, now - 3_600_000)
            except HttpError as e:
                failures += 1
                self.health.error(self.name, f"{inst}: {e}")
            if dd.open_interest_usd is None and dd.funding_rate is None:
                continue  # nichts Verwertbares -> keinen leeren Datensatz schreiben
            self.state.set_deriv(dd)
            self.db.insert("derivatives_snapshots", {
                "symbol": sym, "ts_ms": now, "source": "okx",
                "open_interest_usd": dd.open_interest_usd, "funding_rate": dd.funding_rate,
                "long_short_ratio": dd.long_short_ratio, "long_liq_usd_1h": dd.long_liq_usd_1h,
                "short_liq_usd_1h": dd.short_liq_usd_1h,
            }, or_ignore=True)
        if failures == len(self.watch):
            raise HttpError("alle OKX-Abfragen fehlgeschlagen")
