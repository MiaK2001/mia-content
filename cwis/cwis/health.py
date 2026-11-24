"""Zustandsüberwachung aller Datenquellen."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class SourceStatus:
    name: str
    last_success_ms: int | None = None
    last_error_ms: int | None = None
    last_error: str | None = None
    success_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    last_latency_ms: float | None = None
    note: str | None = None
    expected_interval_s: float = 60.0
    extra: dict = field(default_factory=dict)

    def state(self, now_ms: int | None = None) -> str:
        now_ms = now_ms or int(time.time() * 1000)
        if self.extra.get("disabled"):
            return "DISABLED"
        if self.last_success_ms is None:
            return "ERROR" if self.error_count else "STARTING"
        age = (now_ms - self.last_success_ms) / 1000
        if age > max(3 * self.expected_interval_s, 180):
            return "STALE"
        if self.consecutive_errors >= 3:
            return "DEGRADED"
        return "OK"


class HealthRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sources: dict[str, SourceStatus] = {}
        self.started_ms = int(time.time() * 1000)

    def register(self, name: str, expected_interval_s: float, note: str | None = None) -> SourceStatus:
        with self._lock:
            st = self.sources.setdefault(name, SourceStatus(name=name))
            st.expected_interval_s = expected_interval_s
            if note:
                st.note = note
            return st

    def ok(self, name: str, latency_ms: float | None = None, **extra) -> None:
        with self._lock:
            st = self.sources.setdefault(name, SourceStatus(name=name))
            st.last_success_ms = int(time.time() * 1000)
            st.success_count += 1
            st.consecutive_errors = 0
            st.last_latency_ms = latency_ms
            st.extra.update(extra)

    def error(self, name: str, err: str) -> None:
        with self._lock:
            st = self.sources.setdefault(name, SourceStatus(name=name))
            st.last_error_ms = int(time.time() * 1000)
            st.last_error = err[:300]
            st.error_count += 1
            st.consecutive_errors += 1

    def is_fresh(self, name: str, max_age_s: float) -> bool:
        st = self.sources.get(name)
        if not st or st.last_success_ms is None:
            return False
        return (time.time() * 1000 - st.last_success_ms) / 1000 <= max_age_s

    def snapshot(self) -> list[dict]:
        now = int(time.time() * 1000)
        with self._lock:
            return [
                {**{k: v for k, v in s.__dict__.items()}, "state": s.state(now)}
                for s in self.sources.values()
            ]
