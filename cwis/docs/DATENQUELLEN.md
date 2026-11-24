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
| PublicNode BSC/Arbitrum/Base/Polygon/Avalanche | vorkonfiguriert, deaktiviert | kostenlos | ❌ nicht getestet (Netzwerkrichtlinie der Entwicklungsumgebung) | Vor Aktivierung mit `python -m cwis --check-sources` prüfen. Schnelle Chains: `scan_native = false`. |
| **Blockchair** | – (nicht verwendet) | ohne Schlüssel 1 440 Anfragen/Tag | ❌ HTTP 430 „IP blacklisted“ | Aus geteilten Rechenzentrums-IPs gesperrt; für Dauerbetrieb nur mit (kostenpflichtigem) Schlüssel sinnvoll. |
| **Etherscan API v2** | – (nicht verwendet) | kostenloser Schlüssel nötig | nicht getestet | Nützlich für Wallet-Historien/Labels. Bewusst nicht eingebaut, um ohne Schlüssel auszukommen. |
| **DefiLlama** `stablecoins.llama.fi` | – (vorgesehen) | kostenlos | ✅ erreichbar | Stablecoin-Umlauf je Chain (Tageswerte) – Erweiterungskandidat für Phase 10. |
| Solana public RPC | – | kostenlos | ❌ nicht freigegeben/ungetestet | Öffentlicher Endpunkt stark limitiert, laut Anbieter nicht für Produktion. |
| TronGrid | – | kostenloser Schlüssel nötig | ❌ nicht freigegeben/ungetestet | Ohne Schlüssel sehr niedrige Limits. |

## Bewertung

* **Echtzeit-Abdeckung kostenlos gut:** BTC und Ethereum (inkl. USDT/USDC) lassen sich ohne Schlüssel lückenlos und
  zeitnah verfolgen. Marktdaten und Derivate sind über Binance/OKX vollständig öffentlich.
* **Hauptlücke Wallet-Zuordnung:** Die Aussagekraft von Börsenflüssen hängt fast vollständig von der Label-Qualität ab.
  Kostenlos gibt es nur wenige gut belegte Börsenadressen (Explorer-Namensschilder, Proof-of-Reserves-Listen der
  Börsen). Hier liegt der größte Hebel für Phase 10 – z. B. Import der öffentlich veröffentlichten
  Proof-of-Reserves-Adresslisten der Börsen.
* **Historie:** Binance-Kerzen sind historisch kostenlos verfügbar (für Outcome-Auswertung ausreichend). Historische
  On-Chain-Whale-Daten müssten selbst aufgebaut werden – CWIS speichert ab dem ersten Tag alles dauerhaft.
