# Trading-Bot-Integration (vorbereitet – DEAKTIVIERT)

**Status: NICHT VERBUNDEN – TESTPHASE.** CWIS stellt eine lokale, rein lesende Schnittstelle bereit. Es gibt keine
Verbindung zum Trading-Bot und keine Möglichkeit, über CWIS Orders auszulösen. Die Konstante
`INTEGRATION_ENABLED = False` in `cwis/integration/bot_interface.py` ist über die Konfiguration nicht änderbar.

## Endpunkte (Schema v1)

| Art | Pfad | Inhalt |
|---|---|---|
| REST | `GET /api/v1/signal/{symbol}` | Signal für `BTCUSDT` bzw. `BTC` |
| REST | `GET /api/v1/signals` | alle überwachten Symbole |
| WebSocket | `ws://127.0.0.1:8787/ws/v1/signals` | Push: `signals` (jede Minute), `alert`, `transfer` |
| REST | `GET /api/v1/integration` | Status der Integration |

### Beispielantwort (echte Ausgabe aus dem Testbetrieb, gekürzt)

```json
{
  "schema_version": "1.0",
  "symbol": "BTCUSDT",
  "asset": "BTC",
  "integration_status": "NICHT VERBUNDEN – TESTPHASE",
  "timestamp": "2026-11-24T20:12:00Z",
  "scoring_version": "rules-v1",
  "whale_impact_score": 57.1,
  "bullish_score": 0.0,
  "bearish_score": 13.7,
  "market_impact_score": 18.8,
  "data_confidence": 100,
  "market_condition": "ELEVATED_ACTIVITY",
  "alert_level": "YELLOW",
  "exchange_netflow_usd": 0.0,
  "unusual_whale_activity": false,
  "data_status": "VALID",
  "signal_valid_until": "2026-11-24T20:17:00Z",
  "recommended_action": "NONE",
  "missing_data": ["OI-Veränderung (benötigt ≥ 1h Historie)"]
}
```

### Felder

* `data_status`: `VALID` · `DEGRADED` (Confidence 40–59) · `STALE` (Preis/Marktdaten veraltet oder Signal abgelaufen) ·
  `INSUFFICIENT` (keine/zu wenige Daten). **Alles außer `VALID` bedeutet: keine Bestätigung.**
* `signal_valid_until`: 5 Minuten nach Berechnung; bei nicht gültigen Daten sofort abgelaufen. Abgelaufene Signale
  liefert die API als `STALE` mit `NO_SIGNAL_DATA_INSUFFICIENT`.
* `recommended_action` (rein informativ): `NONE`, `BULLISH_CONTEXT`, `RISK_WARNING`,
  `CRITICAL_NO_NEW_POSITIONS_ADVISORY`, `NO_SIGNAL_DATA_INSUFFICIENT`.
* Scores sind 0–100-Indikatoren, **keine Wahrscheinlichkeiten**. Fehlen Daten, sind Werte `null` – nie geschätzt.

## Konzept der späteren Entscheidungslogik

Implementiert als reine Funktion `advisory_for_bot_signal(bot_side, cwis_signal)` – wird nirgends aufgerufen:

| Bot-Signal | CWIS-Lage | Empfehlung |
|---|---|---|
| LONG | Bullish ≥ Bearish + 20 | CONFIRM |
| SHORT | Bearish ≥ Bullish + 20 | CONFIRM |
| LONG | ROT und Bearish > Bullish | REJECT_OR_WARN |
| SHORT | ROT und Bullish > Bearish | REJECT_OR_WARN |
| beliebig | Gegenrichtung ≥ +20 | WARN |
| beliebig | unklar | NO_CONFIRMATION |
| beliebig | Daten fehlen/ungültig/abgelaufen | NO_CONFIRMATION (keine automatische Freigabe) |

## Voraussetzungen vor einer Aktivierung (Freigabe durch dich)

1. CWIS läuft mehrere Wochen stabil; Health-Ausfälle sind dokumentiert.
2. Historische Auswertung zeigt für die genutzten Ereignistypen einen Überschuss gegenüber der Referenz
   (n ≥ 30 je Gruppe, möglichst deutlich mehr), getrennt nach Zeiträumen.
3. Backtest des Trading-Bots **mit und ohne** CWIS-Filter auf identischen Zeiträumen; danach Paper-Trading.
4. Positionsgrößen-Anpassung nur innerhalb der bestehenden Risikogrenzen des Bots.
5. Integration erfolgt ausschließlich im Bot (Lesen der CWIS-API) – CWIS selbst bleibt ohne Handelszugriff.
