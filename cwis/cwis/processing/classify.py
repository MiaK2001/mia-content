"""Klassifizierung von Transfers.

Grundsatz: Ein Transfer ist KEIN Kauf oder Verkauf. Wir klassifizieren nur die
Richtung relativ zu bekannten Adressen. Ob gehandelt wurde, ist on-chain bei
Börsen-Transfers nicht erkennbar.
"""
from __future__ import annotations

from cwis.labels import Label

FLOW_TYPES = {
    "EXCHANGE_INFLOW": "Transfer auf eine Börsen-Wallet",
    "EXCHANGE_OUTFLOW": "Abhebung von einer Börsen-Wallet",
    "EXCHANGE_INTERNAL": "Interner Transfer derselben Börse (Wallet-Reorganisation)",
    "INTER_EXCHANGE": "Transfer zwischen zwei Börsen",
    "MINT": "Neuausgabe (Mint) – Transfer von der Null-Adresse",
    "BURN": "Vernichtung (Burn) – Transfer zur Null-Adresse",
    "ISSUER_TREASURY": "Bewegung einer Emittenten-Treasury (z. B. Tether)",
    "DEX_INTERACTION": "Interaktion mit einem DEX-Kontrakt (potenziell handelsbezogen, nicht bestätigt)",
    "KNOWN_TRANSFER": "Transfer zwischen bekannten Nicht-Börsen-Adressen",
    "UNKNOWN_TRANSFER": "Transfer zwischen unbekannten Wallets",
}

# Flows, die als echte Börsen-Netto-Bewegung zählen (interne Umbuchungen nicht!)
NETFLOW_TYPES = {"EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW"}


def classify(src: Label, dst: Label) -> tuple[str, str | None]:
    """Gibt (flow_type, betroffene Börse) zurück."""
    if src.category == "null":
        return "MINT", None
    if dst.category == "null":
        return "BURN", None
    if src.is_exchange and dst.is_exchange:
        if src.entity and src.entity == dst.entity:
            return "EXCHANGE_INTERNAL", src.entity
        return "INTER_EXCHANGE", f"{src.entity}->{dst.entity}"
    if dst.is_exchange:
        return "EXCHANGE_INFLOW", dst.entity
    if src.is_exchange:
        return "EXCHANGE_OUTFLOW", src.entity
    if "issuer" in (src.category, dst.category):
        return "ISSUER_TREASURY", None
    if "dex" in (src.category, dst.category):
        return "DEX_INTERACTION", None
    if src.category == "unknown" and dst.category == "unknown":
        return "UNKNOWN_TRANSFER", None
    return "KNOWN_TRANSFER", None


def directional_lean(symbol: str, flow_type: str, stablecoins: set[str]) -> int:
    """Grobe, dokumentierte Tendenz: +1 = eher bullish, -1 = eher bearish, 0 = neutral.

    * Nicht-Stablecoin auf Börse  -> mögliches Verkaufsangebot   -> -1
    * Nicht-Stablecoin von Börse  -> mögliche Verwahrung/Akkumulation -> +1
    * Stablecoin auf Börse        -> mögliche Kaufkraft          -> +1
    * Stablecoin von Börse        -> Kaufkraft verlässt Börsen   -> -1
    Das ist eine Hypothese, kein Beweis (siehe Alternative Erklärungen).
    """
    stable = symbol in stablecoins
    if flow_type == "EXCHANGE_INFLOW":
        return 1 if stable else -1
    if flow_type == "EXCHANGE_OUTFLOW":
        return -1 if stable else 1
    return 0
