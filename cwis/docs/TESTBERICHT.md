# Testbericht – überwachter Testbetrieb (Phase 9/10)

**Datum:** 24.11.2026 · **Umgebung:** Linux-Cloud-Container (Entwicklung), Python 3.13, echte öffentliche Daten.
Der Testbetrieb auf deinem Windows-PC steht noch aus.

## Automatische Tests

`pytest`: **53 bestanden, 0 fehlgeschlagen.** Dabei gefundene und behobene Fehler:
* Ein Datenalter von exakt 0 Sekunden wurde als „unbekannt“ und damit als veraltet gewertet.
* Uvicorn hatte ohne das Paket `websockets` keine WebSocket-Unterstützung (Live-Stream gab 404).
* Collectors verarbeiteten beim Start Blöcke, bevor Preise vorlagen → 142 Transfers gingen verloren. Jetzt warten sie.
* PowerShell-Skripte ohne UTF-8-BOM wären unter Windows PowerShell 5.1 an Sonderzeichen gescheitert (statische Prüfung).
* `--binance-only` im Label-Import hätte andere Börsen gelöscht.

## Testläufe mit Echtdaten

| Lauf | Dauer | Inhalt | Ergebnis |
|---|---|---|---|
| 2 | 16 Min. | BTC + ETH, kleine Labelliste | 37 130 Transfers geprüft, 549 gespeichert, 0 Fehler, 70 MB RAM |
| 3 | 10 Min. | alle Chains, 1 650 Labels | 149 578 Transfers geprüft; Börsenflüsse auf allen Chains erkannt; Arbitrum- und Rückstandsproblem gefunden |
| 4 | 12 Min. | Standardkonfiguration | 189 460 Transfers geprüft, 1 543 gespeichert, 0 ohne verlässlichen Preis, 80 MB RAM |
| 6 | 6 Min. | mit Stablecoin-Börsenfilter | alle aktiven Quellen OK, kein Rückstand |

Beispiele echter Treffer: 26,8 und 23,5 Mio. USD USDC von Coinbase abgehoben; 14,4 Mio. USD ETH auf Coinbase;
Binance-, OKX-, KuCoin-, Bitfinex- und Gemini-Bewegungen auf Bitcoin; Binance-Flüsse auf BNB Chain, Tron und Avalanche.

**Wallet-Abgleich:** 11 von 11 überprüfbaren Handlabels durch offizielle Reservelisten bestätigt, kein Widerspruch.

## Datenverbrauch (gemessen, Lauf 6)

Netzwerk gesamt: **≈ 7,5 GB/Tag** (komprimiert). Anteile (entpackt): Ethereum 5,5 GB (davon ≈ 4 GB komplette Blöcke für
native ETH-Transfers), Bitcoin 2,5 GB, BNB 1,4 GB, Base 1,3 GB, Tron 1,0 GB, Binance/OKX je 0,3 GB, Polygon/Avalanche < 0,1 GB.
Vor dem Stablecoin-Börsenfilter: ≈ 8,9 GB/Tag komprimiert (BNB allein 7,6 GB entpackt).

**Sparmodus** (in `config\config.toml`): `[sources.evm.ethereum] scan_native = false` spart ≈ 4 GB entpackt
(native ETH-Transfers entfallen, WETH bleibt), `[sources.tron] enabled = false` spart ≈ 1 GB.
CPU-Last war durchgehend nahe 0 %, RAM 65–80 MB.

## Beobachtungen und offene Punkte

* **Arbitrum** ist standardmäßig aus: Der kostenlose Endpunkt liefert nur die letzten ~50 Blöcke (≈ 13 s); im Test
  wurden über 2 400 Blöcke übersprungen. Mit eigenem (kostenlosem) RPC-Schlüssel nutzbar.
* **Stablecoins** werden auf allen EVM-Chains nur noch für Börsen-Wallets und Mint/Burn erfasst (Ursache: Datenvolumen).
  Große Stablecoin-Transfers zwischen unbekannten Wallets erscheinen dort nicht mehr (Tron: weiterhin alle).
* **Label-Abdeckung:** 4,8 % der gespeicherten Transfers haben Börsenbezug (vorher < 1 %). Kunden-Einzahlungsadressen
  und Coinbase/Kraken bleiben weitgehend unbekannt.
* **CoinGecko** drosselte im Rechenzentrum zeitweise (HTTP 429); CWIS fällt dann auf Binance-Paarvolumen zurück.
* **BTC-Warnstufe:** Mit Regelwerk v1.1 (unbekannt→unbekannt halb gewichtet) lag BTC meist bei GRÜN, mit echten
  Binance-Abflüssen zeitweise bei GELB.
* **Nicht getestet:** Ausführung der Windows-Skripte, Dauerbetrieb über mehrere Stunden, `api.binance.com` (hier geo-gesperrt).
* Aussagen über einen **Zusatznutzen** für den Trading-Bot sind frühestens nach mehreren Wochen möglich (n ≥ 30 je Gruppe).
