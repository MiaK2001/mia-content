"""Historische Auswertung: Was passierte nach einem Ereignis tatsächlich?

* t0 ist der ERKENNUNGSZEITPUNKT (detected_ms), nicht die Blockzeit -> kein Look-ahead.
* Es werden nur Kerzen mit openTime >= t0 verwendet.
* Neben Alerts werden regelmäßig unabhängige Referenzmessungen ('baseline') zu
  festen Zeitpunkten erfasst. Erst der Vergleich Signal vs. Referenz zeigt, ob ein
  Muster einen ZUSÄTZLICHEN Informationswert hat (Schutz vor Selektions-Bias).
"""
from __future__ import annotations

import logging
import math
import time

from cwis.db import Database, now_ms

log = logging.getLogger("cwis.outcomes")


def kline_interval(h_min: int) -> tuple[str, int]:
    if h_min <= 60:
        return "1m", 60_000
    if h_min <= 720:
        return "5m", 300_000
    return "15m", 900_000


def evaluate_klines(klines: list, price0: float, t0_ms: int, due_ms: int) -> dict | None:
    ks = [k for k in klines if int(k[0]) >= t0_ms and int(k[0]) < due_ms]
    if not ks or price0 <= 0:
        return None
    highs = [float(k[2]) for k in ks]
    lows = [float(k[3]) for k in ks]
    closes = [float(k[4]) for k in ks]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    return {
        "ret": closes[-1] / price0 - 1,
        "max_up": max(highs) / price0 - 1,
        "max_down": min(lows) / price0 - 1,
        "realized_vol": math.sqrt(sum(r * r for r in rets)) if rets else 0.0,
        "quote_volume": sum(float(k[7]) for k in ks),
    }


class OutcomeTracker:
    def __init__(self, db: Database, cfg, klines_fn):
        self.db = db
        self.horizons = cfg.get("history.horizons_minutes")
        self.markets = {w["symbol"]: w["market"] for w in cfg.watch}
        self.klines_fn = klines_fn  # async (market, interval, start_ms, end_ms, limit) -> list

    def schedule(self, ref_type: str, ref_id: int | None, symbol: str, event_type: str | None,
                 bias: str | None, t0_ms: int, price0: float | None) -> int:
        if symbol not in self.markets or not price0:
            return 0  # ohne verlässlichen Startpreis keine Auswertung
        n = 0
        for h in self.horizons:
            if self.db.insert("outcomes", {
                "ref_type": ref_type, "ref_id": ref_id, "symbol": symbol, "event_type": event_type,
                "bias": bias, "t0_ms": t0_ms, "price0": price0, "horizon_min": h,
                "due_ms": t0_ms + h * 60_000}, or_ignore=True):
                n += 1
        return n

    async def process_due(self, max_items: int = 30) -> int:
        cutoff = now_ms() - 90_000  # letzte Kerze muss abgeschlossen sein
        rows = self.db.query(
            "SELECT * FROM outcomes WHERE status='pending' AND due_ms<=? ORDER BY due_ms LIMIT ?",
            (cutoff, max_items))
        done = 0
        for r in rows:
            interval, step = kline_interval(r["horizon_min"])
            try:
                kl = await self.klines_fn(self.markets[r["symbol"]], interval, r["t0_ms"], r["due_ms"], 1000)
                res = evaluate_klines(kl, r["price0"], r["t0_ms"], r["due_ms"])
            except Exception as e:  # noqa: BLE001
                log.warning("Outcome %s fehlgeschlagen: %s", r["id"], e)
                res = None
            if res is None:
                status = "failed" if r["attempts"] >= 4 else "pending"
                self.db.execute("UPDATE outcomes SET attempts=attempts+1, status=? WHERE id=?", (status, r["id"]))
                continue
            self.db.execute(
                "UPDATE outcomes SET status='done', ret=?, max_up=?, max_down=?, realized_vol=?, quote_volume=?, "
                "attempts=attempts+1 WHERE id=?",
                (res["ret"], res["max_up"], res["max_down"], res["realized_vol"], res["quote_volume"], r["id"]))
            done += 1
        return done


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def summarize(db: Database, min_n: int = 1) -> dict:
    """Signal-Gruppen vs. Referenz je Symbol und Horizont."""
    done = db.query("SELECT * FROM outcomes WHERE status='done'")
    base: dict[tuple, list] = {}
    groups: dict[tuple, list] = {}
    for r in done:
        if r["ref_type"] == "baseline":
            base.setdefault((r["symbol"], r["horizon_min"]), []).append(r)
        else:
            groups.setdefault((r["event_type"], r["bias"], r["horizon_min"]), []).append(r)
    base_all: dict[int, list] = {}
    for (sym, h), rs in base.items():
        base_all.setdefault(h, []).extend(rs)
    out = []
    for (et, b, h), rs in sorted(groups.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "", kv[0][2])):
        if len(rs) < min_n:
            continue
        rets = [r["ret"] for r in rs]
        # Referenz: Baseline-Messungen derselben Symbole und desselben Horizonts
        syms = {r["symbol"] for r in rs}
        bl = [x["ret"] for s in syms for x in base.get((s, h), [])]
        sign = -1 if b == "bearish" else 1
        hit = [1 if sign * x > 0 else 0 for x in rets] if b in ("bullish", "bearish") else []
        m, sd = _mean(rets), _std(rets)
        bm = _mean(bl)
        out.append({
            "event_type": et, "bias": b, "horizon_min": h, "n": len(rs),
            "mean_ret": m, "std_ret": sd,
            "t_stat_vs_baseline": ((m - bm) / (sd / math.sqrt(len(rs)))) if sd and bm is not None and len(rs) > 2 else None,
            "hit_rate": _mean(hit) if hit else None,
            "mean_max_up": _mean([r["max_up"] for r in rs]),
            "mean_max_down": _mean([r["max_down"] for r in rs]),
            "baseline_n": len(bl), "baseline_mean_ret": bm,
            "baseline_hit_rate": _mean([1 if sign * x > 0 else 0 for x in bl]) if bl and hit else None,
            "excess_ret": (m - bm) if bm is not None else None,
            "sufficient_sample": len(rs) >= 30 and len(bl) >= 30,
        })
    baselines = [{"horizon_min": h, "n": len(rs), "mean_ret": _mean([r["ret"] for r in rs]),
                  "mean_abs_ret": _mean([abs(r["ret"]) for r in rs])} for h, rs in sorted(base_all.items())]
    pending = db.one("SELECT COUNT(*) AS n FROM outcomes WHERE status='pending'")["n"]
    return {"groups": out, "baselines": baselines, "pending": pending,
            "hinweis": "Statistiken erst ab n ≥ 30 je Gruppe aussagekräftig. Keine Gewinnprognose."}


def comparable(db: Database, symbol: str, event_type: str, horizon: int = 60) -> dict | None:
    rs = db.query("SELECT ret FROM outcomes WHERE status='done' AND ref_type='alert' AND symbol=? AND event_type=? "
                  "AND horizon_min=?", (symbol, event_type, horizon))
    bl = db.query("SELECT ret FROM outcomes WHERE status='done' AND ref_type='baseline' AND symbol=? AND horizon_min=?",
                  (symbol, horizon))
    if not rs or not bl:
        return {"n": len(rs)}
    return {"n": len(rs), "label": f"{symbol} {event_type}", "horizon_min": horizon,
            "mean_ret": _mean([r["ret"] for r in rs]), "baseline_mean_ret": _mean([r["ret"] for r in bl])}


def wallet_stats(db: Database, min_transfers: int = 5, shrink_k: int = 20, limit: int = 50) -> list[dict]:
    """Smart-Money-Auswertung mit Schrumpfung (Shrinkage) gegen Selektions-Bias.

    Für jede Wallet: Kursveränderung des Assets 60 Min. nach ihren Whale-Transfers
    (aus Markt-Snapshots, Start = Erkennungszeitpunkt). Der Mittelwert wird mit
    k Pseudo-Beobachtungen in Richtung des Gesamtmittels geschrumpft, damit
    Wallets mit wenigen 'Glückstreffern' nicht als Smart Money erscheinen.
    """
    rows = db.query("""
        SELECT t.chain, t.from_address AS address, t.symbol, t.flow_type, t.detected_ms,
               (SELECT price FROM market_snapshots m WHERE m.symbol=t.symbol AND m.ts_ms BETWEEN t.detected_ms - 120000 AND t.detected_ms
                ORDER BY m.ts_ms DESC LIMIT 1) AS p0,
               (SELECT price FROM market_snapshots m WHERE m.symbol=t.symbol AND m.ts_ms BETWEEN t.detected_ms + 3600000 AND t.detected_ms + 3720000
                ORDER BY m.ts_ms ASC LIMIT 1) AS p1
        FROM transfers t WHERE t.is_whale=1 AND t.detected_ms <= ?""", (now_ms() - 3_720_000,))
    per: dict[tuple, list] = {}
    for r in rows:
        if r["p0"] and r["p1"]:
            per.setdefault((r["chain"], r["address"]), []).append(r["p1"] / r["p0"] - 1)
    allr = [x for v in per.values() for x in v]
    gm = _mean(allr) or 0.0
    out = []
    for (chain, addr), rets in per.items():
        if len(rets) < min_transfers:
            continue
        n = len(rets)
        out.append({"chain": chain, "address": addr, "n": n, "raw_mean_ret_60m": _mean(rets),
                    "shrunk_mean_ret_60m": (n * _mean(rets) + shrink_k * gm) / (n + shrink_k)})
    out.sort(key=lambda x: -abs(x["shrunk_mean_ret_60m"] - gm))
    return out[:limit]


def schedule_baselines(tracker: OutcomeTracker, state, interval_min: int, last: dict) -> int:
    now = int(time.time() * 1000)
    n = 0
    for sym in tracker.markets:
        if now - last.get(sym, 0) < interval_min * 60_000:
            continue
        md = state.market.get(sym)
        if md and md.price and (now - md.ts_ms) < 120_000:
            n += tracker.schedule("baseline", None, sym, None, "neutral", now, md.price)
            last[sym] = now
    return n
