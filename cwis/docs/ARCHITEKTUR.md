# Architektur

Ein einzelner Python-Prozess (asyncio) – ressourcenschonend (im Test ≈ 70 MB RAM, CPU nahe 0 %), keine externen Dienste.

```
 Datenerfassung (collectors/, je eigene Endlosschleife mit Backoff + Health)
   mempool.space ─┐   Ethereum-RPC ─┐   Binance Spot ─┐   CoinGecko ─┐   OKX ─┐
                  ▼                 ▼                 ▼              ▼        ▼
           RawTransfer ──► TransferPipeline        MarketState (Kurs, Tiefe, Volumen, OI, Funding …)
                           │  Preisprüfung (pricing) ◄──────────────┘
                           │  Wallet-Labels (labels) → Klassifizierung (classify)
                           │  Dynamische Schwelle (thresholds)
                           ▼
                        SQLite (WAL): transfers · wallets · snapshots · scores · alerts · outcomes
                           │
 Analyse-Schleife (60 s)   ▼
   Fenster 60 Min. → aggregate → Clustering (10 Min.) → compute_scores → classify_level
     → narrative (Analyse-Text) → AlertManager (Filter/Cooldown) → OutcomeTracker (1 Min. … 24 Std.)
     → build_signal (Bot-Schnittstelle v1, DEAKTIVIERT)
                           │
 FastAPI (127.0.0.1, nur GET) ─► Dashboard · REST /api/v1/* · WebSocket /ws/v1/signals
```

## Scores (Regelwerk `rules-v1.1`, `cwis/analysis/scoring.py`)

Alle Komponenten sind auf 0..1 normiert (Sättigung `x/(x+h)`), Score = gewichteter Mittelwert × 100.
Bezugsgrößen: *Stundenvolumen* = marktweites 24h-Volumen / 24 (CoinGecko, sonst Binance-Paar); *Tiefe* = Orderbuch ±1 %.

| Score | Komponenten (Gewicht) |
|---|---|
| **Bearish** | Börsen-Nettozufluss (30) · Long-Liquidationen (15) · verkaufsseitiges Orderbuch (10) · Abwärtsdynamik mit Volumen (15) · überhitzte Long-Positionierung (Funding > 0, OI ↑) (10) · OI ↑ bei fallendem Kurs (10) · Stablecoins verlassen Börsen (10) |
| **Bullish** | spiegelbildlich: Nettoabfluss (30) · Short-Liquidationen (15) · kaufseitiges Buch (10) · Aufwärtsdynamik (15) · überhitzte Shorts (10) · OI ↑ bei steigendem Kurs (10) · Stablecoins fließen auf Börsen (10) |
| **Market Impact** | Börsenflüsse rel. zum Volumen (35) · größter Flow rel. zur Tiefe (25) · Liquidationen (15) · Volumenanomalie (15) · \|OI-Veränderung\| (10) |
| **Whale Impact** | Whale-Volumen rel. zum Volumen (35, Transfers unbekannt→unbekannt mit Faktor 0,5) · Anzahl Whale-Wallets/Cluster (20, unbekannte Wallets ohne Börsenbezug × 0,5) · Börsenbezug (15) · Größenklasse (10) · Seltenheit vs. 30-Tage-Historie (20); anschließend × (0,6 + 0,4 × Confidence/100) |
| **Data Confidence** | Preis frisch & plausibel (30) · Orderbuch (15) · Derivate (20) · On-Chain-Collector aktuell (25) · Anteil gut gelabelter Börsenflüsse (10) |

**Warnstufen:** ROT: Whale ≥ 70 ∧ Market ≥ 60 ∧ max(Bull, Bear) ≥ 55 ∧ Confidence ≥ 60 · ORANGE: Whale ≥ 50 ∧
(Market ≥ 40 ∨ Cluster) ∧ Confidence ≥ 50 · GELB: Whale ≥ 25 · sonst GRÜN. Bei Datenstatus INSUFFICIENT/STALE
höchstens GELB.

**Alarmfilter:** gleicher Schlüssel (Symbol, Stufe, Ereignistyp) innerhalb Cooldown (GELB 60, ORANGE 30, ROT 15 Min.)
wird unterdrückt – außer bei Eskalation oder Whale-Score-Sprung ≥ 15. GELB nur bei tatsächlicher Whale-Aktivität.

Die Gewichte sind begründete Startwerte, nicht optimiert. Änderungen nur versioniert (`SCORING_VERSION`) und nach
Prüfung mit der historischen Auswertung.

## Historische Auswertung und Verzerrungsschutz

| Risiko | Gegenmaßnahme |
|---|---|
| Look-ahead-Bias | Start = `detected_ms`; nur Kerzen mit `openTime ≥ t0`; Messung erst nach Abschluss der letzten Kerze |
| Data Leakage | `ml/validation.py`: Walk-Forward mit Embargo, `assert_no_leakage` |
| Survivorship-/Selektions-Bias | Unabhängige stündliche Referenzmessungen je Symbol; Signalgruppen werden immer gegen die Referenz verglichen; Smart-Money-Werte mit Shrinkage (k = 20) |
| Überoptimierung | Feste Regeln, Mindeststichprobe n ≥ 30 als Kennzeichen, kein automatisches Nachjustieren |
| Fehlerhafte Wallet-Zuordnung | Vertrauensstufe je Label, Anteil gut gelabelter Flows fließt in Confidence ein |
| Doppelzählung | UNIQUE (Chain, Tx, Index); Cluster pro Tx und Entität; interne Umbuchungen getrennt |

## Speicher und Wartung

SQLite im WAL-Modus. Stündlich: Snapshots > 14 Tage, Scores > 90 Tage, Transfers > 365 Tage, Alerts > 730 Tage
löschen; Größenlimit (Standard 2 GB) entfernt zuerst älteste Snapshots. Logs rotieren (5 × 10 MB).

## Maschinelles Lernen (vorbereitet, nicht aktiv)

Reihenfolge: (1) mehrere Wochen Daten sammeln → (2) Regel-Score gegen Referenz auswerten → (3) einfache Modelle
(logistische Regression) mit Walk-Forward gegen die Baselines „neutral“ und `rules-v1` → (4) Kalibrierung
(Brier-Score) → (5) manuelle, versionierte Übernahme. Kein Modell verändert automatisch Produktivwerte.
