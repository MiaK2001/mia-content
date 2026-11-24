# CWIS – Crypto Whale Intelligence System

Ein lokales, rund um die Uhr laufendes **Beobachtungssystem** für große Kapitalbewegungen am Kryptomarkt.
CWIS erkennt Whale-Transfers on-chain, ordnet sie bekannten Börsen-Wallets zu, kombiniert sie mit Spot-, Orderbuch- und
Derivatedaten und bewertet die Lage mit transparenten Scores, Warnstufen und einer schriftlichen Analyse.

> **CWIS führt keine Trades aus.** Es gibt keinen Code zur Orderausführung, keine Exchange-API-Schlüssel und keine
> Verbindung zum bestehenden Trading-Bot. Die vorbereitete Bot-Schnittstelle ist hart deaktiviert
> (`INTEGRATION_ENABLED = False`), nur lesend und nur unter `127.0.0.1` erreichbar.
> Scores sind keine Wahrscheinlichkeiten und keine Anlageberatung.

---

## Schnellstart (Windows 11)

Voraussetzung: Python ≥ 3.11 (python.org). Kein Docker, kein WSL, keine Administratorrechte.

**Einfachster Weg:** Ordner entpacken und `INSTALLIEREN.bat` doppelklicken (siehe `INSTALLATION.txt`). Manuell:

```powershell
cd <Pfad>\cwis
powershell -ExecutionPolicy Bypass -File windows\1_bestandsaufnahme.ps1     # Phase 1: nur lesen (Ports, RAM, Python …)
powershell -ExecutionPolicy Bypass -File windows\2_installieren.ps1         # eigenes .venv, Tests, Quellencheck
windows\start_cwis.bat                                                      # manueller Start
```

Dashboard: **http://127.0.0.1:8787** · API-Dokumentation: http://127.0.0.1:8787/api/docs

24/7-Betrieb (Autostart bei Anmeldung, automatischer Neustart, niedrige Prozesspriorität):

```powershell
powershell -ExecutionPolicy Bypass -File windows\3_autostart_einrichten.ps1
powershell -ExecutionPolicy Bypass -File windows\autostart_entfernen.ps1    # rückgängig machen
```

Ist Port 8787 belegt (z. B. durch den Trading-Bot), bricht CWIS ohne Änderungen ab. Dann in `config\config.toml`
`server.port` ändern.

## Was CWIS tut

| Bereich | Umsetzung |
|---|---|
| Bitcoin | Bestätigte Blöcke via mempool.space, Wechselgeld-Heuristik, jeder relevante Output einzeln |
| Ethereum | Native ETH-Transfers + ERC-20-Transfers (USDT, USDC, DAI, WETH, WBTC, LINK, UNI, PEPE, SHIB) via öffentlichem RPC, Reorg-Schutz über Bestätigungen |
| BNB Chain, Arbitrum, Base, Polygon, Avalanche | Gleicher EVM-Collector, vorkonfiguriert, **standardmäßig deaktiviert** (nicht getestet) |
| Solana, Tron | **Nicht implementiert** – siehe [Einschränkungen](#bekannte-einschränkungen) |
| Börsenflüsse | Zuflüsse, Abflüsse, Netto; interne Umbuchungen und Transfers zwischen Börsen getrennt |
| Dynamische Whale-Schwelle | 0,05 % des marktweiten 24h-Volumens, begrenzt durch Orderbuch-Tiefe, Floor/Ceiling, Börsenfaktor |
| Clustering | Zeitliche Häufungen, Deduplizierung je Transaktion, Zusammenfassung von Adressen derselben Entität |
| Marktdaten | Binance Spot (Kurs, Volumen, Orderbuch ±1 %, 1h-Rendite/Volatilität), CoinGecko (Marktkapitalisierung, Referenzpreis) |
| Derivate | OKX: Open Interest, OI-Veränderung, Funding, Long/Short-Verhältnis, Liquidationen |
| Scores | Whale Impact, Bullish, Bearish, Market Impact, Data Confidence (0–100, regelbasiert, jede Komponente sichtbar) |
| Warnsystem | GRÜN / GELB / ORANGE / ROT mit Cooldowns, Eskalationslogik und Analyse-Text |
| Historische Auswertung | Kursreaktion nach 1 Min. – 24 Std. ab dem *Erkennungszeitpunkt*, Vergleich mit unabhängigen Referenzmessungen |
| Smart Money | Wallet-Datenbank, Auswertung mit Shrinkage gegen Selektions-Bias |
| Bot-Schnittstelle | Versioniertes JSON (`/api/v1/signal/{symbol}`), WebSocket `/ws/v1/signals` – deaktiviert |

## Projektstruktur

```
cwis/
├─ cwis/
│  ├─ collectors/      Datenerfassung (bitcoin, evm, market, derivatives) mit Auto-Reconnect
│  ├─ processing/      Preisbewertung, Klassifizierung, dynamische Schwellen, Clustering, Pipeline
│  ├─ analysis/        Scores (scoring.py) und deutschsprachige Analyse (narrative.py)
│  ├─ history/         Outcome-Tracking, Statistik, Smart-Money-Auswertung
│  ├─ ml/              Walk-Forward-Validierung, Leakage-Schutz (vorbereitet, nicht aktiv)
│  ├─ integration/     Bot-Schnittstelle (DEAKTIVIERT)
│  ├─ api/             FastAPI (nur GET, nur lokal)
│  ├─ static/          Dashboard (Vanilla-JS, offline-fähig)
│  ├─ engine.py        Orchestrierung · alerts.py · maintenance.py · db.py · config.py
├─ config/config.example.toml
├─ data/wallet_labels.json      Startliste bekannter Adressen (nicht verifiziert)
├─ tests/                       pytest-Suite
├─ windows/                     Bestandsaufnahme, Installation, Autostart
└─ docs/                        ARCHITEKTUR.md · DATENQUELLEN.md · INTEGRATION.md · TESTBERICHT.md
```

## Grundsätze der Datenverarbeitung

* **Transfers sind keine Käufe oder Verkäufe.** CWIS klassifiziert nur Richtungen relativ zu bekannten Adressen.
* **Keine erfundenen Werte.** Ohne verlässlichen Preis (veraltet, fehlend oder > 3 % Abweichung zwischen Binance und
  CoinGecko) wird ein Transfer nicht bewertet. Fehlende Marktdaten senken den Data Confidence Score und verhindern
  Eskalationen auf ORANGE/ROT.
* **Unbekannte Wallets bleiben „unbekannt“.** Labels stammen aus öffentlichen Quellen, haben eine Vertrauensstufe und
  sind von CWIS nicht verifiziert. Eigene Labels: `data/wallet_labels.local.json`.
* **Kein Look-ahead.** Jeder Datensatz hat `observed_ms` (Quelle) und `detected_ms` (Zeitpunkt, zu dem CWIS die
  Information hatte). Auswertungen starten bei `detected_ms` und verwenden nur Kerzen ab diesem Zeitpunkt.
* **Keine Doppelzählung.** Eindeutigkeit über (Chain, Tx-Hash, Log-/Output-Index); interne Börsen-Umbuchungen zählen
  nicht als Nettofluss; Cluster zählen jede Transaktion und jede Entität einmal.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Abgedeckt: Konfiguration/Sicherheitsregeln (inkl. Quellcode-Scan auf Order-Endpunkte), Klassifizierung, dynamische
Schwellen, Preisprüfung (fehlend/veraltet/inkonsistent/Depeg), Pipeline (Duplikate, ungültige Mengen, unbekannte
Wallets), BTC-/EVM-Parser, Orderbuch- und Liquidationsberechnung, Clustering, Scores inkl. Extremwerte und fehlender
Daten, Alarmfilterung, Outcome-Tracking ohne Look-ahead, Walk-Forward ohne Überlappung, API (nur lesend), WebSocket,
Aufbewahrung. Ergebnisse des Testbetriebs mit Echtdaten: [docs/TESTBERICHT.md](docs/TESTBERICHT.md).

## Bekannte Einschränkungen

* **Wallet-Labels:** Die Startliste ist klein (≈ 20 Adressen) und unverifiziert. Börsen nutzen Tausende
  Einzahlungsadressen; ohne umfangreiche Label-Datenbank (meist kostenpflichtig, z. B. Arkham, Nansen) wird nur ein
  Teil der echten Börsenflüsse erkannt. Die meisten Transfers erscheinen daher als „Unbekannt → Unbekannt“.
* **Native ETH über Kontrakte** (interne Transaktionen) ist ohne Trace-API nicht sichtbar.
* **Bitcoin:** Absender = größte Input-Adresse; die Wechselgeld-Heuristik ist einfach. Mempool (unbestätigt) wird nicht ausgewertet.
* **DEX-Swaps** werden nur als „DEX-Interaktion“ erkannt (Router-Adressen), nicht als bestätigte Käufe/Verkäufe.
* **Liquidationen** nur von OKX und nur die letzten ≤ 100 je Abfrage → Untergrenze.
* **Orderbuch:** 1 000 Level reichen bei BTC/ETH oft nicht für ±1 % → als Untergrenze markiert. Orders können storniert werden.
* **Solana/Tron:** kein zuverlässiger kostenloser Echtzeitzugang ohne Schlüssel; nicht implementiert.
* **Score-Gewichte** sind begründete Startwerte, noch **nicht** empirisch kalibriert. Ob Whale-Daten einen
  Zusatznutzen bieten, zeigt erst die historische Auswertung über Wochen (n ≥ 30 je Gruppe).
* **Datenverbrauch:** Vollständiger ETH-Blockscan ≈ 2 GB/Tag Download (abschaltbar: `scan_native = false`).
* Die Windows-Skripte wurden in einer Linux-Entwicklungsumgebung geschrieben und dort **nicht** ausgeführt.
