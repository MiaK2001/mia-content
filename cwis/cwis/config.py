"""Konfiguration laden und validieren (TOML, Standardbibliothek)."""
from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# Schlüssel, die auf Trading-Zugangsdaten hindeuten. CWIS darf so etwas nie erhalten.
FORBIDDEN_KEY_FRAGMENTS = ("api_secret", "secret_key", "passphrase", "private_key", "trading_key", "seed")


class ConfigError(ValueError):
    pass


def _walk_keys(obj: Any, prefix: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            yield path, str(k)
            yield from _walk_keys(v, path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{prefix}[{i}]")


def validate(raw: dict) -> None:
    for path, key in _walk_keys(raw):
        lk = key.lower()
        if any(f in lk for f in FORBIDDEN_KEY_FRAGMENTS):
            raise ConfigError(
                f"Unzulässiger Konfigurationsschlüssel '{path}': CWIS verarbeitet keine "
                "Trading-/Wallet-Zugangsdaten."
            )
    server = raw.get("server", {})
    if server.get("host") not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigError("server.host muss lokal sein (127.0.0.1), solange die Integration deaktiviert ist.")
    port = server.get("port")
    if not isinstance(port, int) or not (1024 <= port <= 65535):
        raise ConfigError("server.port muss eine Zahl zwischen 1024 und 65535 sein.")
    if not raw.get("symbols", {}).get("watch"):
        raise ConfigError("symbols.watch darf nicht leer sein.")


@dataclass
class Config:
    raw: dict
    source_path: Path

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def path(self, dotted: str) -> Path:
        p = Path(self.get(dotted))
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def watch(self) -> list[dict]:
        return self.get("symbols.watch", [])

    @property
    def watch_symbols(self) -> list[str]:
        return [w["symbol"] for w in self.watch]

    @property
    def stablecoins(self) -> set[str]:
        return set(self.get("symbols.stablecoins", []))


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> Config:
    """Lädt config/config.example.toml als Basis und überlagert config/config.toml (falls vorhanden)."""
    example = CONFIG_DIR / "config.example.toml"
    with open(example, "rb") as f:
        raw = tomllib.load(f)
    src = example
    user_path = Path(path) if path else CONFIG_DIR / "config.toml"
    if user_path.exists():
        with open(user_path, "rb") as f:
            raw = _deep_merge(raw, tomllib.load(f))
        src = user_path
    if overrides:
        raw = _deep_merge(raw, overrides)
    validate(raw)
    return Config(raw=raw, source_path=src)
