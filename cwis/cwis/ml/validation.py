"""Grundlagen für spätere statistische Modelle / Machine Learning.

Status: VORBEREITET, NICHT AKTIV. Kein Modell beeinflusst derzeit Scores oder Warnungen.
Regeln für spätere Modelle:
  1. Nur zeitlich getrennte Daten (Walk-Forward) mit Embargo zwischen Training und Test.
  2. Jedes Modell wird gegen einfache Baselines verglichen (z. B. 'immer neutral',
     'Regel-Score v1').
  3. Wahrscheinlichkeiten nur nach Kalibrierung auf unabhängigen Testdaten (Brier-Score).
  4. Übernahme in den Produktivbetrieb nur manuell, versioniert und protokolliert.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    train_start: int
    train_end: int    # exklusiv
    test_start: int
    test_end: int     # exklusiv


def walk_forward_splits(n: int, n_splits: int, min_train: int, embargo: int = 0) -> list[Split]:
    """Indizes über zeitlich sortierte Beobachtungen. Test liegt immer NACH Training (+Embargo)."""
    if n <= min_train + embargo:
        return []
    test_size = (n - min_train - embargo) // n_splits
    if test_size <= 0:
        return []
    splits = []
    for i in range(n_splits):
        train_end = min_train + i * test_size
        test_start = train_end + embargo
        test_end = min(test_start + test_size, n)
        if test_start >= test_end:
            break
        splits.append(Split(0, train_end, test_start, test_end))
    return splits


def assert_no_leakage(train_ts: list[int], test_ts: list[int], embargo_ms: int = 0) -> None:
    if train_ts and test_ts and max(train_ts) + embargo_ms >= min(test_ts):
        raise ValueError("Data Leakage: Trainingsdaten überlappen zeitlich mit Testdaten.")


def brier_score(probs: list[float], outcomes: list[int]) -> float:
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def compare_to_baseline(model_hits: list[int], baseline_hits: list[int]) -> dict:
    m = sum(model_hits) / len(model_hits) if model_hits else None
    b = sum(baseline_hits) / len(baseline_hits) if baseline_hits else None
    return {"model_hit_rate": m, "baseline_hit_rate": b,
            "improvement": None if m is None or b is None else m - b, "n": len(model_hits)}


MODEL_REGISTRY: dict[str, dict] = {
    "rules-v1": {"status": "PRODUKTIV", "type": "regelbasiert", "beschreibung": "cwis/analysis/scoring.py"},
}
