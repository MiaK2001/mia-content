"""Wallet-Zuordnungen. Unbekannte Adressen bleiben ausdrücklich 'unknown'."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EVM_CHAINS = {"ethereum", "bsc", "arbitrum", "base", "polygon", "avalanche"}


@dataclass(frozen=True)
class Label:
    label: str | None
    entity: str | None
    category: str            # exchange | dex | issuer | null | unknown | ...
    confidence: str          # high | medium | low | unknown
    source: str | None

    @property
    def is_exchange(self) -> bool:
        return self.category == "exchange"


UNKNOWN = Label(None, None, "unknown", "unknown", None)


def norm_address(chain: str, address: str | None) -> str | None:
    if address is None:
        return None
    return address.lower() if chain in EVM_CHAINS else address


class WalletRegistry:
    def __init__(self, entries: list[dict] | None = None):
        self._map: dict[tuple[str, str], Label] = {}
        for e in entries or []:
            self.add(e)

    def add(self, e: dict) -> None:
        chain = e["chain"]
        self._map[(chain, norm_address(chain, e["address"]))] = Label(
            e.get("label"), e.get("entity"), e.get("category", "unknown"),
            e.get("confidence", "low"), e.get("source"),
        )

    def lookup(self, chain: str, address: str | None) -> Label:
        if not address:
            return UNKNOWN
        return self._map.get((chain, norm_address(chain, address)), UNKNOWN)

    def addresses(self, chain: str, categories: set[str]) -> list[str]:
        return sorted(a for (c, a), lbl in self._map.items() if c == chain and lbl.category in categories)

    def __len__(self) -> int:
        return len(self._map)

    def entries(self) -> list[dict]:
        return [
            {"chain": c, "address": a, **lbl.__dict__} for (c, a), lbl in self._map.items()
        ]

    @classmethod
    def from_files(cls, *paths: Path) -> "WalletRegistry":
        reg = cls()
        for p in paths:
            if p.exists():
                with open(p, encoding="utf-8") as f:
                    for e in json.load(f).get("labels", []):
                        reg.add(e)
        return reg
