"""Importiert Börsen-Wallets aus dem Open-Source-Projekt DefiLlama-Adapters (MIT-Lizenz).

Quelle: https://github.com/DefiLlama/DefiLlama-Adapters  (projects/<börse>/index.js)
Diese Adressen stammen überwiegend aus den Proof-of-Reserves-Veröffentlichungen der Börsen
selbst (die Börse erklärt, dass ihr diese Wallets gehören). Ergebnis: data/wallet_labels_cex.json

Zusätzlich: offizielle Proof-of-Reserves-Adressliste von Binance (öffentlicher Endpunkt der Binance-Website).

Aufruf:  python tools/import_cex_labels.py <Pfad zum geklonten DefiLlama-Adapters-Repo> [--no-binance]
         python tools/import_cex_labels.py --binance-only
Das Skript liest die JavaScript-Dateien nur als Text (kein Ausführen fremden Codes).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# DefiLlama-Projektordner -> Anzeigename der Börse
EXCHANGES = {
    "okex": "OKX", "bitfinex": "Bitfinex", "bitget": "Bitget", "bitstamp": "Bitstamp", "gate-io": "Gate.io",
    "huobi": "HTX", "kucoin": "KuCoin", "gemini": "Gemini", "backpack": "Backpack", "coinbase-btc": "Coinbase",
    "binance": "Binance", "bybit": "Bybit", "kraken": "Kraken", "crypto-com": "Crypto.com", "deribit": "Deribit",
    "mexc": "MEXC", "bitmex": "BitMEX",
}
# DefiLlama-Chain-Schlüssel -> CWIS-Chain
CHAINS = {"bitcoin": "bitcoin", "ethereum": "ethereum", "bsc": "bsc", "arbitrum": "arbitrum", "base": "base",
          "polygon": "polygon", "avax": "avalanche", "tron": "tron"}

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BTC_RE = re.compile(r"^(bc1[0-9a-z]{25,87}|[13][1-9A-HJ-NP-Za-km-z]{25,34})$")
TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
STR_RE = re.compile(r"""["'`]([0-9A-Za-z]{25,90})["'`]""")


def valid(chain: str, a: str) -> bool:
    if chain == "bitcoin":
        return bool(BTC_RE.match(a))
    if chain == "tron":
        return bool(TRON_RE.match(a))
    return bool(EVM_RE.match(a))


def block_after(text: str, start: int, open_ch: str, close_ch: str) -> str:
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return text[start:]


def strip_comments(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[,\[\s])//[^\n]*", "", t)


def btc_book(repo: Path) -> dict[str, list[str]]:
    book: dict[str, list[str]] = {}
    d = repo / "projects/helper/bitcoin-book"
    for f in d.glob("*.js"):
        t = strip_comments(f.read_text(encoding="utf-8"))
        addrs = [a for a in STR_RE.findall(t) if valid("bitcoin", a)]
        if addrs:
            book[f.stem.replace("-cex", "")] = addrs
        idx = f.name == "index.js"
        if idx:  # Einträge der Form  "name": [ ... ]  oder  name: [ ... ]
            for m in re.finditer(r"""["']?([\w-]+)["']?\s*:\s*\[""", t):
                arr = block_after(t, m.end() - 1, "[", "]")
                a2 = [a for a in STR_RE.findall(arr) if valid("bitcoin", a)]
                if a2:
                    book[m.group(1).replace("-cex", "")] = a2
    return book


def parse_project(text: str, book: dict) -> dict[str, set[str]]:
    text = strip_comments(text)
    out: dict[str, set[str]] = {}
    for key, chain in CHAINS.items():
        for m in re.finditer(rf"(?<![\w.])[\"']?{key}[\"']?\s*:\s*\{{", text):
            blk = block_after(text, m.end() - 1, "{", "}")
            om = re.search(r"[\"']?owners[\"']?\s*:\s*", blk)
            if not om:
                continue
            rest = blk[om.end():]
            if rest.startswith("["):
                arr = block_after(rest, 0, "[", "]")
                addrs = {a for a in STR_RE.findall(arr) if valid(chain, a)}
            elif re.match(r"\w+\s*[,}\n]", rest) and (cm := re.search(
                    rf"(?:const|let|var)\s+{re.match(r'\w+', rest).group(0)}\s*=\s*\[", text)):
                arr = block_after(text, cm.end() - 1, "[", "]")   # Verweis auf Array-Konstante, z. B. evmOwners
                addrs = {a for a in STR_RE.findall(arr) if valid(chain, a)}
            else:
                ref = re.match(r"bitcoinAddressBook\.([\w]+)|bitcoinAddressBook\[['\"]([\w-]+)['\"]\]", rest)
                name = (ref.group(1) or ref.group(2)).replace("-cex", "") if ref else None
                addrs = set(book.get(name, [])) if chain == "bitcoin" and name else set()
            if addrs:
                out.setdefault(chain, set()).update(addrs)
    return out


BINANCE_POR = "https://www.binance.com/bapi/apex/v1/public/apex/market/por/address"
BINANCE_NET = {"BTC": "bitcoin", "ETH": "ethereum", "BSC": "bsc", "BEP20": "bsc", "ARBITRUM": "arbitrum",
               "BASE": "base", "MATIC": "polygon", "AVAXC": "avalanche", "TRX": "tron", "TRON": "tron"}


def binance_labels() -> list[dict]:
    import httpx
    data = httpx.get(BINANCE_POR, timeout=30, headers={"User-Agent": "CWIS label import"}).json().get("data") or []
    out = []
    for item in data:
        chain = BINANCE_NET.get(str(item.get("network", "")).upper())
        a = str(item.get("address", "")).strip()
        if not chain or not valid(chain, a):
            continue
        cust = (item.get("thirdPartyCustodianName") or "").strip()
        out.append({"chain": chain, "address": a,
                    "label": f"Binance (Reserven-Wallet{', Verwahrer ' + cust if cust else ''})",
                    "entity": "Binance", "category": "exchange", "confidence": "high",
                    "source": f"Offizielle Binance-Proof-of-Reserves-Liste ({BINANCE_POR}), abgerufen {date.today().isoformat()}"})
    return out


def main(repo: Path | None, with_binance: bool = True) -> None:
    book = btc_book(repo) if repo else {}
    try:
        commit = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%H %cs"], capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = "unbekannt"
    labels, seen, stats = [], set(), {}
    if with_binance:
        try:
            for lb in binance_labels():
                k = (lb["chain"], lb["address"].lower() if lb["chain"] not in ("bitcoin", "tron") else lb["address"])
                if k not in seen:
                    seen.add(k)
                    labels.append(lb)
                    stats[f"Binance/{lb['chain']}"] = stats.get(f"Binance/{lb['chain']}", 0) + 1
        except Exception as e:  # noqa: BLE001
            print(f"Binance-Liste nicht abrufbar: {e}")
    out = Path(__file__).resolve().parent.parent / "data" / "wallet_labels_cex.json"
    if repo is None and out.exists():
        # Nur Binance aktualisieren: übrige Börsen aus der bestehenden Datei übernehmen
        for lb in json.loads(out.read_text(encoding="utf-8")).get("labels", []):
            k = (lb["chain"], lb["address"].lower() if lb["chain"] not in ("bitcoin", "tron") else lb["address"])
            if lb.get("entity") != "Binance" and k not in seen:
                seen.add(k)
                labels.append(lb)
    for folder, name in (EXCHANGES.items() if repo else []):
        f = repo / "projects" / folder / "index.js"
        if not f.exists():
            continue
        parsed = parse_project(f.read_text(encoding="utf-8"), book)
        if folder == "bitmex" and "bitmex" in book:
            parsed.setdefault("bitcoin", set()).update(book["bitmex"])
        for chain, addrs in parsed.items():
            for a in sorted(addrs):
                k = (chain, a.lower() if chain not in ("bitcoin", "tron") else a)
                if k in seen:
                    continue   # Adresse doppelt gelistet -> erste Zuordnung behalten
                seen.add(k)
                labels.append({"chain": chain, "address": a, "label": f"{name} (Reserven-Wallet)", "entity": name,
                               "category": "exchange", "confidence": "high",
                               "source": f"Proof-of-Reserves-Liste via DefiLlama-Adapters (MIT), Commit {commit}"})
                stats[f"{name}/{chain}"] = stats.get(f"{name}/{chain}", 0) + 1
    if not labels:
        print("Keine Labels erhalten – bestehende Datei bleibt unverändert.")
        return
    out.write_text(json.dumps({
        "_hinweis": "Automatisch erzeugt von tools/import_cex_labels.py am " + date.today().isoformat() +
                    ". Quellen: offizielle Binance-Proof-of-Reserves-Liste und DefiLlama-Adapters (MIT License, "
                    "Copyright DefiLlama). Adressen aus den Reservenachweisen der Börsen; nicht unabhängig von CWIS "
                    "verifiziert.",
        "labels": labels}, ensure_ascii=False, indent=0), encoding="utf-8")
    for k in sorted(stats):
        print(f"{k:28} {stats[k]}")
    print(f"Gesamt: {len(labels)} Adressen -> {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--binance-only" in args:
        main(None, True)
    else:
        main(Path(args[0]), "--no-binance" not in args)
