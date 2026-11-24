"""Erkennung zeitlich gehäufter Whale-Aktivität.

Regeln gegen Doppelzählung:
* Mehrere Outputs/Logs derselben Transaktion zählen als EIN Teilnehmer-Ereignis.
* Teilnehmer = bekannte Entität (falls zugeordnet), sonst die Adresse.
  Mehrere Adressen derselben Entität (z. B. Binance 14 und 15) zählen als EINE.
* Interne Börsen-Umbuchungen werden nicht mitgezählt.
Eine zeitliche Häufung ist KEIN Beweis für koordiniertes Handeln.
"""
from __future__ import annotations

from collections import defaultdict


def participant_key(row: dict, side: str) -> str:
    ent = row.get(f"{side}_entity")
    if ent and row.get(f"{side}_category") not in (None, "unknown"):
        return f"entity:{ent}"
    return f"addr:{row.get(f'{side}_address')}"


def find_clusters(rows: list[dict], min_wallets: int) -> list[dict]:
    """rows: Transfers EINES Zeitfensters. Gruppiert nach (Symbol, Richtung)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["flow_type"] in ("EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW"):
            groups[(r["symbol"], r["flow_type"])].append(r)
    clusters = []
    for (symbol, flow), items in groups.items():
        side = "from" if flow == "EXCHANGE_INFLOW" else "to"   # die Nicht-Börsen-Seite
        by_tx: dict[tuple, dict] = {}
        for r in items:
            k = (r["chain"], r["tx_hash"])
            agg = by_tx.setdefault(k, {"value": 0.0, "participant": participant_key(r, side),
                                       "exchange": r.get("exchange"), "address": r.get(f"{side}_address")})
            agg["value"] += r["value_usd"] or 0.0
        participants: dict[str, float] = defaultdict(float)
        exchanges: dict[str, float] = defaultdict(float)
        addresses: set = set()
        for agg in by_tx.values():
            participants[agg["participant"]] += agg["value"]
            exchanges[agg["exchange"] or "unbekannt"] += agg["value"]
            addresses.add(agg["address"])
        if len(participants) < min_wallets:
            continue
        unknown = sum(1 for p in participants if p.startswith("addr:"))
        clusters.append({
            "symbol": symbol,
            "flow_type": flow,
            "wallets": len(participants),
            "unknown_wallets": unknown,
            "transactions": len(by_tx),
            "total_usd": sum(participants.values()),
            "exchanges": dict(sorted(exchanges.items(), key=lambda kv: -kv[1])),
            "addresses": len(addresses),
            "merged_by_entity": len(addresses) - len(participants),
            "note": "Zeitliche Häufung – kein Nachweis koordinierten Handelns.",
        })
    return sorted(clusters, key=lambda c: -c["total_usd"])
