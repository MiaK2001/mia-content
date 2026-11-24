"""Basisklasse: Endlosschleife mit automatischer Wiederverbindung und Backoff."""
from __future__ import annotations

import asyncio
import logging
import random

from cwis.health import HealthRegistry

log = logging.getLogger("cwis.collector")


class Collector:
    name = "collector"
    interval_s: float = 60.0
    note: str | None = None
    covers: dict = {}          # Asset -> Anzeigename der Quelle (für Datenqualität/Quellenangaben)
    max_age_s: float = 180.0   # ab diesem Alter gelten die On-Chain-Daten als veraltet

    def __init__(self, health: HealthRegistry):
        self.health = health
        self._stop = asyncio.Event()

    async def run_once(self) -> None:  # pragma: no cover - abstrakt
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        self.health.register(self.name, self.interval_s, self.note)
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self.run_once()
                backoff = 1.0
                delay = self.interval_s
            except asyncio.CancelledError:
                raise
            except Exception as e:  # jede Störung führt zu Wiederholung, nie zum Absturz
                self.health.error(self.name, f"{type(e).__name__}: {e}")
                log.warning("%s fehlgeschlagen: %s – neuer Versuch in %.0fs", self.name, e, backoff)
                delay = backoff
                backoff = min(backoff * 2 + random.random(), 300)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
