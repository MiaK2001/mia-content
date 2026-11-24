"""Orchestrierung: Collectors, Analyse-Schleife, Warnsystem, historische Auswertung, Wartung."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque

from cwis.alerts import AlertManager, bias, event_type
from cwis.analysis import narrative, scoring
from cwis.collectors.bitcoin import BitcoinCollector
from cwis.collectors.derivatives import OkxDerivativesCollector
from cwis.collectors.evm import EvmCollector
from cwis.collectors.market import BinanceSpotCollector, CoinGeckoCollector
from cwis.collectors.tron import TronCollector
from cwis.config import PROJECT_ROOT, Config
from cwis.db import Database, now_ms
from cwis.health import HealthRegistry
from cwis.history import outcomes
from cwis.http import HttpClient
from cwis.integration import bot_interface
from cwis.labels import WalletRegistry
from cwis.maintenance import run_retention
from cwis.processing.clustering import find_clusters
from cwis.processing.pipeline import TransferPipeline
from cwis.processing.pricing import quote
from cwis.state import DerivData, MarketState

log = logging.getLogger("cwis.engine")


class Engine:
    def __init__(self, cfg: Config, db: Database | None = None, http: HttpClient | None = None):
        self.cfg = cfg
        self.db = db or Database(cfg.path("storage.db_path"))
        self.health = HealthRegistry()
        self.http = http or HttpClient(self.health)
        self.state = MarketState()
        self.registry = WalletRegistry.from_files(
            PROJECT_ROOT / "data" / "wallet_labels.json",
            PROJECT_ROOT / "data" / "wallet_labels_cex.json",     # Reserven-Wallets der Börsen (tools/import_cex_labels.py)
            PROJECT_ROOT / "data" / "wallet_labels.local.json")   # eigene Ergänzungen haben Vorrang
        self.recent_transfers: deque = deque(maxlen=300)
        self.subscribers: set[asyncio.Queue] = set()
        self.pipeline = TransferPipeline(self.db, self.registry, self.state, cfg, listeners=[self._on_transfer])
        self.alerts = AlertManager(self.db, cfg)
        self.latest: dict[str, dict] = {}      # symbol -> {score, level, condition, action, analysis, signal}
        self.market_overview: dict = {}
        self.collectors = []
        self.tasks: list[asyncio.Task] = []
        self._baseline_last: dict[str, int] = {}
        self._build_collectors()
        self.tracker = outcomes.OutcomeTracker(self.db, cfg, self._klines)
        self._seed_oi_history()

    # ---------- Aufbau ----------
    def _build_collectors(self) -> None:
        c = self.cfg
        self.spot = BinanceSpotCollector(self.health, self.http, self.db, self.state, c)
        if c.get("sources.market.enabled", True):
            self.collectors.append(self.spot)
        if c.get("sources.coingecko.enabled", True):
            self.collectors.append(CoinGeckoCollector(self.health, self.http, self.state, c))
        if c.get("sources.derivatives.enabled", True):
            self.collectors.append(OkxDerivativesCollector(self.health, self.http, self.db, self.state, c))
        if c.get("sources.bitcoin.enabled", True):
            self.collectors.append(BitcoinCollector(self.health, self.http, self.db, self.pipeline, self.state, c))
        for chain, cc in (c.get("sources.evm") or {}).items():
            if cc.get("enabled"):
                self.collectors.append(EvmCollector(chain, self.health, self.http, self.db, self.pipeline, cc,
                                                    registry=self.registry))
            else:
                self.health.register(f"evm_{chain}", cc.get("interval_seconds", 10), "deaktiviert (Konfiguration)")
                self.health.sources[f"evm_{chain}"].extra["disabled"] = True
        if c.get("sources.tron.enabled", False):
            self.collectors.append(TronCollector(self.health, self.http, self.db, self.pipeline, c))
        for name, note in (("solana", "nicht implementiert – Erfassung großer Transfers erfordert das Auswerten jedes "
                                      "Blocks; öffentliche RPCs sind dafür zu stark limitiert (kostenpflichtige Anbieter nötig)"),):
            self.health.register(name, 60, note)
            self.health.sources[name].extra["disabled"] = True

    def _seed_oi_history(self) -> None:
        since = now_ms() - 2 * 3_600_000
        for r in self.db.query("SELECT * FROM derivatives_snapshots WHERE ts_ms>=? ORDER BY ts_ms", (since,)):
            if r["open_interest_usd"] is not None:
                self.state.oi_history.setdefault(r["symbol"], deque(maxlen=240)).append((r["ts_ms"], r["open_interest_usd"]))

    async def _klines(self, market, interval, start, end, limit):
        return await self.spot.klines(market, interval, start, end, limit)

    # ---------- Ereignisse ----------
    def _on_transfer(self, row: dict) -> None:
        self.recent_transfers.appendleft(row)
        self.broadcast({"type": "transfer", "data": row})

    def broadcast(self, msg: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # langsamer Client – Nachricht verwerfen statt System zu blockieren

    # ---------- Analyse ----------
    def _coverage(self) -> dict[str, list[tuple[str, float, str]]]:
        """Welche Collectors liefern On-Chain-Daten für welches Asset (Name, max. Alter, Anzeigename)."""
        cov: dict[str, list] = {}
        for c in self.collectors:
            for sym, label in getattr(c, "covers", {}).items():
                cov.setdefault(sym, []).append((c.name, c.max_age_s, label))
        return cov

    def _onchain_fresh(self, symbol: str) -> bool:
        return any(self.health.is_fresh(n, age) for n, age, _ in self._coverage().get(symbol, []))

    def _onchain_sources(self, symbol: str) -> list[str]:
        return sorted({label for _, _, label in self._coverage().get(symbol, [])})

    def _whale_history(self, symbol: str) -> list[float]:
        rows = self.db.query(
            "SELECT whale_volume_usd FROM scores WHERE symbol=? AND ts_ms>? AND ((ts_ms/60000) % 60)=0",
            (symbol, now_ms() - 30 * 86_400_000))
        return [r["whale_volume_usd"] or 0.0 for r in rows]

    def analyze(self, now: int | None = None) -> dict:
        now = now or now_ms()
        win = self.cfg.get("analysis.window_minutes", 60) * 60_000
        cwin = self.cfg.get("analysis.cluster_window_minutes", 10) * 60_000
        stale = self.cfg.get("analysis.stale_after_seconds", 180)
        rows = self.db.query("SELECT * FROM transfers WHERE detected_ms>=? AND detected_ms<=?", (now - win, now))
        stables = self.cfg.stablecoins
        stable_rows = [r for r in rows if r["symbol"] in stables]
        stable_agg = scoring.aggregate(stable_rows)
        stable_net = stable_agg.netflow_usd if any(self._onchain_fresh(x) for x in stables) else None
        clusters_all = find_clusters([r for r in rows if r["detected_ms"] >= now - cwin],
                                     self.cfg.get("analysis.cluster_min_wallets", 3))
        sources_base = ["Binance Spot (öffentlich)", "CoinGecko", "OKX Derivate (öffentlich)"]
        for w in self.cfg.watch:
            sym = w["symbol"]
            srows = [r for r in rows if r["symbol"] == sym]
            agg = scoring.aggregate(srows)
            md = self.state.market.get(sym)
            dd: DerivData | None = self.state.deriv.get(sym)
            pq = quote(self.state, sym, stale, now)
            s = scoring.compute_scores(
                sym, agg, md, dd, price_status=pq.status, onchain_fresh=self._onchain_fresh(sym),
                deriv_expected=bool(w.get("deriv")), stable_netflow_usd=stable_net,
                whale_volume_history=self._whale_history(sym),
                clusters=[c for c in clusters_all if c["symbol"] == sym], stale_after_s=stale, now_ms=now)
            level, cond = scoring.classify_level(s)
            action = scoring.recommended_action(s, level)
            srcs = sources_base + self._onchain_sources(sym)
            hist = outcomes.comparable(self.db, sym, event_type(s))
            analysis = narrative.build_analysis(s, level, hist, srcs)
            signal = bot_interface.build_signal(
                s, symbol=sym, market_symbol=w["market"], level=level, market_condition=cond, action=action,
                ts_ms=now, validity_min=self.cfg.get("analysis.signal_validity_minutes", 5))
            self.latest[sym] = {"score": s, "level": level, "condition": cond, "action": action,
                                "analysis": analysis, "signal": signal, "ts_ms": now}
            self.db.insert("scores", {
                "symbol": sym, "ts_ms": now, "whale_impact": s["whale_impact_score"], "bullish": s["bullish_score"],
                "bearish": s["bearish_score"], "market_impact": s["market_impact_score"],
                "data_confidence": s["data_confidence"], "whale_volume_usd": s["flows"]["whale_volume_usd"],
                "level": level, "market_condition": cond, "data_status": s["data_status"],
                # Details nur bei auffälliger Lage oder stündlich speichern (Speicherbedarf)
                "payload": json.dumps({"flows": s["flows"], "components": s["components"]}, ensure_ascii=False)
                if level != "GREEN" or (now // 60_000) % 60 == 0 else "{}"})
            alert = self.alerts.maybe_alert(s, level, analysis, signal, now)
            if alert:
                self.tracker.schedule("alert", alert["id"], sym, alert["event_type"], bias(s), now,
                                      (s.get("market") or {}).get("price"))
                self.broadcast({"type": "alert", "data": alert})
                log.info("ALERT %s %s %s WI=%.0f", sym, alert["level"], alert["event_type"], s["whale_impact_score"])
        levels = [v["level"] for v in self.latest.values()]
        order = ["GREEN", "YELLOW", "ORANGE", "RED"]
        self.market_overview = {
            "ts_ms": now,
            "overall_level": max(levels, key=order.index) if levels else "GREEN",
            "stablecoin_exchange_netflow_usd": stable_net,
            "stablecoin_inflow_usd": stable_agg.inflow_usd, "stablecoin_outflow_usd": stable_agg.outflow_usd,
            "clusters": clusters_all,
        }
        self.broadcast({"type": "signals", "data": {k: v["signal"] for k, v in self.latest.items()}})
        return self.latest

    # ---------- Schleifen ----------
    async def _analysis_loop(self) -> None:
        interval = self.cfg.get("analysis.interval_seconds", 60)
        self.health.register("analysis_engine", interval, "Scores, Warnungen, Ranking")
        await asyncio.sleep(min(20, interval))  # erste Marktdaten abwarten
        while True:
            t0 = time.perf_counter()
            try:
                self.analyze()  # im Event-Loop-Thread (Broadcast ist nicht threadsicher); dauert nur Millisekunden
                self.health.ok("analysis_engine", latency_ms=round((time.perf_counter() - t0) * 1000, 1))
            except Exception as e:  # noqa: BLE001
                log.exception("Analysefehler")
                self.health.error("analysis_engine", str(e))
            await asyncio.sleep(interval)

    async def _history_loop(self) -> None:
        self.health.register("history_tracker", 60, "Marktreaktion nach Ereignissen (1 Min. – 24 Std.)")
        await asyncio.sleep(30)
        while True:
            try:
                outcomes.schedule_baselines(self.tracker, self.state, self.cfg.get("history.baseline_probe_minutes", 60),
                                            self._baseline_last)
                n = await self.tracker.process_due()
                self.health.ok("history_tracker", evaluated=n)
            except Exception as e:  # noqa: BLE001
                self.health.error("history_tracker", str(e))
            await asyncio.sleep(60)

    async def _maintenance_loop(self) -> None:
        self.health.register("maintenance", 3600, "Aufbewahrungsregeln, Speicherlimit")
        while True:
            try:
                res = await asyncio.to_thread(run_retention, self.db, self.cfg)
                self.health.ok("maintenance", **res)
            except Exception as e:  # noqa: BLE001
                self.health.error("maintenance", str(e))
            await asyncio.sleep(3600)

    async def start(self) -> None:
        for c in self.collectors:
            self.tasks.append(asyncio.create_task(c.run_forever(), name=c.name))
        self.tasks.append(asyncio.create_task(self._analysis_loop(), name="analysis"))
        self.tasks.append(asyncio.create_task(self._history_loop(), name="history"))
        self.tasks.append(asyncio.create_task(self._maintenance_loop(), name="maintenance"))
        log.info("CWIS gestartet: %d Collectors, Integration: %s", len(self.collectors), bot_interface.INTEGRATION_STATUS)

    async def stop(self) -> None:
        for c in self.collectors:
            c.stop()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.http.aclose()
        self.db.close()
