# Datenquellen – Prüfung

Stand der Prüfung: 24.11.2026. Erreichbarkeit wurde aus der Entwicklungsumgebung (Cloud-Rechenzentrum, USA) praktisch
getestet. Rate-Limits und Nutzungsbedingungen bitte vor dem Dauerbetrieb in der jeweils verlinkten Dokumentation
gegenprüfen – sie ändern sich gelegentlich. CWIS bleibt mit eigenen Drosseln (`cwis/http.py`) deutlich darunter.

| Quelle | Verwendet für | Kosten | Getestet | Limits / Hinweise |
|---|---|---|---|---|
| **Binance Spot** `data-api.binance.vision` | Kurs, 24h-Volumen, Orderbuch, 1m-Kerzen, Outcome-Kerzen | kostenlos, ohne Schlüssel | ✅ erreichbar | Offizieller öffentlicher Marktdaten-Endpunkt von Binance. Gewichtslimit pro IP (Header `X-MBX-USED-WEIGHT`); CWIS nutzt ≈ 600 Gewicht/Min. Doku: developers.binance.com |
| **Binance Spot** `api.binance.com` | Ausweich-URL | kostenlos | ⚠️ HTTP 451 aus US-Rechenzentrum | Geo-Sperre für bestimmte Länder. Von einem deutschen Privatanschluss normalerweise erreichbar. CWIS schaltet automatisch um. |
| **OKX** `www.okx.com/api/v5/public`, `/rubik` | Open Interest, Funding, Long/Short, Liquidationen, Kontraktwerte | kostenlos, ohne Schlüssel | ✅ erreichbar | Öffentliche Endpunkte mit Limits je Endpunkt (typisch einige bis 20 Anfragen / 2 s). CWIS: ≤ 4 Anfragen/s. Liquidationen: max. 100 letzte Einträge → Untergrenze. Doku: okx.com/docs-v5 |
| **CoinGecko** `api.coingecko.com/api/v3` | Marktkapitalisierung, marktweites Volumen, Referenzpreis | kostenlos (Public/Demo) | ✅ erreichbar | Öffentliches Limit niedrig und schwankend; CWIS fragt alle 5 Min. in einer Sammelabfrage ab. Ein kostenloser Demo-Schlüssel erhöht die Stabilität (optional, nicht eingebaut). |
| **mempool.space** `/api` | Bitcoin-Blöcke und Transaktionen | kostenlos | ✅ erreichbar | Kein fest dokumentiertes Limit; bei intensiver Nutzung wird Selbst-Hosting empfohlen. CWIS drosselt auf 1 Anfrage/s (≈ 130 Anfragen je Block, ≈ 19 000/Tag). |
| **PublicNode** `ethereum-rpc.publicnode.com` | Ethereum-Blöcke, ERC-20-Logs | kostenlos, ohne Schlüssel | ✅ erreichbar | Fair-Use, Limits nicht veröffentlicht. Liefert praktischerweise `blockTimestamp` in Logs. Alternative: eigener Node oder kostenloser Schlüssel bei Infura/Alchemy. |
| PublicNode BSC/Arbitrum/Base/Polygon/Avalanche | Token-Transfers (getLogs) | kostenlos, ohne Schlüssel | ✅ erreichbar, im Dauertest | **getLogs max. 50 Blöcke je Abfrage** (größer = „Archive requests require a personal token“). Gemessene Blockraten: Base 5,2/s, Arbitrum 3,8/s, BSC 2,3/s, Polygon 0,7/s, Avalanche 0,5/s. Token-Kontrakte on-chain geprüft (`symbol()`, `decimals()`). |
| **TronGrid** `api.trongrid.io` | USDT (TRC-20) Transfer-Events | kostenlos, ohne Schlüssel | ✅ erreichbar | ≈ 22 USDT-Transfers/s; Seiten à 200 Events; CWIS ≤ 1 Anfrage/s. Ohne Schlüssel niedrige Limits – Rückstand wird als `lag_s` angezeigt. |
| **Binance Proof-of-Reserves** `www.binance.com/bapi/.../por/address` | Börsen-Wallet-Labels (Import-Skript) | kostenlos | ✅ erreichbar (1 104 Adressen) | Öffentlicher Endpunkt der Binance-Website (wird auch von DefiLlama genutzt). Nur beim manuellen Label-Import abgefragt, nicht im Dauerbetrieb. |
| **DefiLlama-Adapters** (GitHub, MIT) | Börsen-Wallet-Labels (Import-Skript) | kostenlos | ✅ | Reserven-Adressen von OKX, KuCoin, HTX, Gate.io, Bitget, Bitfinex, Gemini u. a. |
| **Blockchair** | – (nicht verwendet) | ohne Schlüssel 1 440 Anfragen/Tag | ❌ HTTP 430 „IP blacklisted“ | Aus geteilten Rechenzentrums-IPs gesperrt; für Dauerbetrieb nur mit (kostenpflichtigem) Schlüssel sinnvoll. |
| **Etherscan API v2** | – (nicht verwendet) | kostenloser Schlüssel nötig | nicht getestet | Nützlich für Wallet-Historien/Labels. Bewusst nicht eingebaut, um ohne Schlüssel auszukommen. |
| **DefiLlama** `stablecoins.llama.fi` | – (vorgesehen) | kostenlos | ✅ erreichbar | Stablecoin-Umlauf je Chain (Tageswerte) – Erweiterungskandidat für Phase 10. |
| Solana public RPC | – (nicht verwendet) | kostenlos | nicht verwendet | Große Transfers nur durch Auswertung jedes Blocks (~2,5/s, je mehrere MB) auffindbar; öffentlicher Endpunkt laut Anbieter nicht für Produktion. Sinnvoll erst mit kostenpflichtigem Anbieter (z. B. Helius) – nur nach deiner Zustimmung. |

## Bewertung

* **Echtzeit-Abdeckung kostenlos gut:** BTC und Ethereum (inkl. USDT/USDC) lassen sich ohne Schlüssel lückenlos und
  zeitnah verfolgen. Marktdaten und Derivate sind über Binance/OKX vollständig öffentlich.
* **Wallet-Zuordnung:** Umgesetzt über die Proof-of-Reserves-Listen der Börsen (≈ 1 650 Adressen). Die Abgleichprüfung
  bestätigte 11 von 11 überprüfbaren Handlabels, kein Widerspruch. Lücken: Kunden-Einzahlungsadressen und Börsen ohne
  Reserveliste (Coinbase, Kraken). Aktualisierung: `python tools/import_cex_labels.py <DefiLlama-Adapters-Klon>`.
* **Historie:** Binance-Kerzen sind historisch kostenlos verfügbar (für Outcome-Auswertung ausreichend). Historische
  On-Chain-Whale-Daten müssten selbst aufgebaut werden – CWIS speichert ab dem ersten Tag alles dauerhaft.
