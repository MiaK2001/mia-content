# Testbericht – überwachter Testbetrieb (Phase 9)

**Datum:** 24.11.2026 · **Umgebung:** Linux-Cloud-Container (Entwicklung), Python 3.13, echte öffentliche Marktdaten.
Der Testbetrieb auf deinem Windows-PC steht noch aus.

## Automatische Tests

`pytest`: **49 bestanden, 0 fehlgeschlagen.** Dabei gefundene und behobene Fehler:
* Ein Datenalter von exakt 0 Sekunden wurde als „unbekannt“ und damit als veraltet gewertet (`or 1e9`-Muster).
* Uvicorn hatte ohne das Paket `websockets` keine WebSocket-Unterstützung (Live-Stream gab 404) → als Abhängigkeit ergänzt.

## Testbetrieb mit Echtdaten (Lauf 2, 20:23–20:39 UTC, sauberer Start)

| Messgröße | Ergebnis |
|---|---|
| Datenquellen | Binance Spot, CoinGecko, OKX, mempool.space, Ethereum-RPC: alle **OK**, 0 Fehler nach Start |
| Fehler beim Start | je 1 beabsichtigte Wartemeldung (BTC/ETH-Collector wartet auf erste Preise) |
| Geprüfte Transfers | 37 130 |
| Gespeichert (≥ 100 000 USD) | 549 · davon 19 über der dynamischen Whale-Schwelle |
| Ohne verlässlichen Preis verworfen | 0 (vor der Korrektur: 142 beim Start) |
| Duplikate | 0 in Lauf 2; nach einem Neustart in Lauf 1 wurden 28 Duplikate korrekt verworfen |
| Erkannte Börsenflüsse | 5 Zuflüsse (7,1 Mio. USD), 5 Abflüsse (58,0 Mio. USD) |
| Stablecoin-Mint/-Burn | 16 Mints / 6 Burns erkannt |
| Warnungen | 1 (BTC GELB) – Filter verhinderte Wiederholungen bei 17 Analysezyklen |
| Outcome-Tracking | 1- und 5-Minuten-Messungen erfolgreich abgeschlossen, längere Horizonte laufen |
| RAM / CPU | ≈ 70 MB / nahe 0 % |
| Datenbankwachstum | ≈ 0,04 MB pro Minute in der Startphase |
| Port-Schutz | Belegter Port erkannt → Abbruch ohne Änderungen (Exit-Code 3) |
| Dashboard | alle 11 Bereiche im Browser (Chromium) geprüft, keine JavaScript-Fehler |

Beispiel eines echten Treffers (Lauf 1): **14,38 Mio. USD ETH → Coinbase 10** als Börsenzufluss klassifiziert.

## Beobachtungen und offene Punkte

* **Label-Abdeckung ist der Engpass:** 94 % der gespeicherten Transfers sind „Unbekannt → Unbekannt“. Ohne mehr
  verifizierte Börsenadressen bleiben die Börsenfluss-Scores meist bei 0.
* **BTC dauerhaft GELB:** Routinemäßige große BTC-Bewegungen zwischen unbekannten Wallets ergeben einen Whale Impact
  von ≈ 26 (Schwelle GELB = 25). Nach einigen Tagen Historie greift die Komponente „Seltenheit“; danach Schwelle prüfen.
* **OI-Veränderung** und **Seltenheit** brauchen 1 Stunde bzw. 24 Stunden Historie; bis dahin als fehlend gekennzeichnet.
* Aussagen über einen **Zusatznutzen** der Whale-Daten sind frühestens nach mehreren Wochen möglich (n ≥ 30 je Gruppe).
* **Nicht getestet:** Windows-Skripte (PowerShell/Batch), BSC/Arbitrum/Base/Polygon/Avalanche, `api.binance.com`
  (hier geo-gesperrt), Dauerbetrieb über mehr als 30 Minuten.
