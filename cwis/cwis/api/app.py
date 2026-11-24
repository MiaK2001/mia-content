"""Lokale, rein lesende REST-/WebSocket-API und Dashboard-Auslieferung.

Sicherheit:
  * Server bindet ausschließlich an 127.0.0.1 (siehe config.validate).
  * Zusätzlich werden Anfragen von anderen Hosts abgewiesen.
  * Es existieren nur GET-Endpunkte – keine Schreib- oder Handelsfunktionen.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cwis import API_SCHEMA_VERSION, __version__
from cwis.history import outcomes
from cwis.integration import bot_interface

STATIC = Path(__file__).resolve().parent.parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def create_app(engine, start_engine: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_engine:
            await engine.start()
        yield
        if start_engine:
            await engine.stop()

    app = FastAPI(title="CWIS – Crypto Whale Intelligence System", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.client.host if request.client else None
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Nur lokaler Zugriff erlaubt"}, status_code=403)
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "CWIS-API ist rein lesend"}, status_code=405)
        return await call_next(request)

    cfg = engine.cfg
    markets = {w["symbol"]: w["market"] for w in cfg.watch}
    by_market = {v: k for k, v in markets.items()}

    def resolve(symbol: str) -> str:
        s = symbol.upper()
        s = by_market.get(s, s)
        if s not in markets:
            raise HTTPException(404, f"Symbol {symbol} wird nicht überwacht")
        return s

    def signal_for(sym: str) -> dict:
        cur = engine.latest.get(sym)
        if cur is None:
            return bot_interface.build_signal(None, symbol=sym, market_symbol=markets[sym], level=None,
                                              market_condition=None, action=None, ts_ms=int(time.time() * 1000),
                                              validity_min=0)
        sig = dict(cur["signal"])
        if sig.get("signal_valid_until", "") < bot_interface.iso(int(time.time() * 1000)):
            sig["data_status"] = "STALE" if sig["data_status"] == "VALID" else sig["data_status"]
            sig["recommended_action"] = "NO_SIGNAL_DATA_INSUFFICIENT"
        return sig

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    # ---------- Trading-Bot-Schnittstelle (v1, nur lesend) ----------
    @app.get("/api/v1/signal/{symbol}")
    async def get_signal(symbol: str):
        return signal_for(resolve(symbol))

    @app.get("/api/v1/signals")
    async def get_signals():
        return {"schema_version": API_SCHEMA_VERSION, "integration_status": bot_interface.INTEGRATION_STATUS,
                "signals": [signal_for(s) for s in markets]}

    @app.get("/api/v1/integration")
    async def integration():
        return {"enabled": bot_interface.INTEGRATION_ENABLED, "status": bot_interface.INTEGRATION_STATUS,
                "schema_version": API_SCHEMA_VERSION, "mode": "read-only",
                "endpoints": ["/api/v1/signal/{symbol}", "/api/v1/signals", "/ws/v1/signals"],
                "hinweis": "Keine Verbindung zum Trading-Bot. Aktivierung nur nach ausdrücklicher Freigabe."}

    @app.websocket("/ws/v1/signals")
    async def ws_signals(ws: WebSocket):
        host = ws.client.host if ws.client else None
        if host not in LOCAL_HOSTS:
            await ws.close(code=1008)
            return
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        engine.subscribers.add(q)
        try:
            await ws.send_json({"type": "hello", "schema_version": API_SCHEMA_VERSION,
                                "integration_status": bot_interface.INTEGRATION_STATUS})
            while True:
                msg = await q.get()
                await ws.send_text(json.dumps(msg, ensure_ascii=False, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            engine.subscribers.discard(q)

    # ---------- Dashboard-Daten ----------
    @app.get("/api/v1/analysis/{symbol}")
    async def analysis(symbol: str):
        sym = resolve(symbol)
        cur = engine.latest.get(sym)
        if not cur:
            raise HTTPException(503, "Noch keine Analyse verfügbar")
        return {k: cur[k] for k in ("score", "level", "condition", "action", "analysis", "ts_ms")}

    @app.get("/api/v1/overview")
    async def overview():
        rows = []
        for sym in markets:
            cur = engine.latest.get(sym)
            if not cur:
                rows.append({"symbol": sym, "level": None})
                continue
            s = cur["score"]
            rows.append({"symbol": sym, "level": cur["level"], "condition": cur["condition"], "action": cur["action"],
                         "whale_impact": s["whale_impact_score"], "bullish": s["bullish_score"],
                         "bearish": s["bearish_score"], "market_impact": s["market_impact_score"],
                         "confidence": s["data_confidence"], "data_status": s["data_status"],
                         "flows": s["flows"], "market": s["market"], "derivatives": s["derivatives"],
                         "missing": s["missing_data"]})
        return {"ts_ms": engine.market_overview.get("ts_ms"), "market": engine.market_overview, "symbols": rows}

    @app.get("/api/v1/ranking")
    async def ranking():
        items = [{"symbol": k, "level": v["level"], **{x: v["score"][x] for x in (
            "whale_impact_score", "bullish_score", "bearish_score", "market_impact_score", "data_confidence", "data_status")},
                  "netflow_usd": v["score"]["flows"]["exchange_netflow_usd"],
                  "inflow_usd": v["score"]["flows"]["exchange_inflow_usd"],
                  "outflow_usd": v["score"]["flows"]["exchange_outflow_usd"]} for k, v in engine.latest.items()]
        valid = [i for i in items if i["data_status"] in ("VALID", "DEGRADED")]
        def top(key, rev=True):
            return sorted(valid, key=lambda i: i[key], reverse=rev)[:10]
        return {"hinweis": "Nur Beobachtung – löst keine Trades aus.",
                "bullish": top("bullish_score"), "bearish": top("bearish_score"),
                "whale_activity": top("whale_impact_score"), "market_change": top("market_impact_score"),
                "exchange_inflows": top("inflow_usd"), "exchange_outflows": top("outflow_usd"),
                "excluded_insufficient_data": [i["symbol"] for i in items if i not in valid]}

    @app.get("/api/v1/transfers")
    async def transfers(limit: int = Query(100, le=1000), symbol: str | None = None, whale_only: bool = False,
                        flow_type: str | None = None):
        sql, p = "SELECT * FROM transfers WHERE 1=1", []
        if symbol:
            sql += " AND symbol=?"; p.append(symbol.upper())
        if whale_only:
            sql += " AND is_whale=1"
        if flow_type:
            sql += " AND flow_type=?"; p.append(flow_type)
        sql += " ORDER BY detected_ms DESC LIMIT ?"; p.append(limit)
        return engine.db.query(sql, p)

    @app.get("/api/v1/flows")
    async def flows(hours: int = Query(24, le=720), symbol: str | None = None):
        since = int(time.time() * 1000) - hours * 3_600_000
        sql = ("SELECT symbol, (detected_ms/3600000)*3600000 AS hour, exchange, flow_type, SUM(value_usd) AS usd, "
               "COUNT(*) AS n FROM transfers WHERE detected_ms>=? AND flow_type IN ('EXCHANGE_INFLOW','EXCHANGE_OUTFLOW')")
        p: list = [since]
        if symbol:
            sql += " AND symbol=?"; p.append(symbol.upper())
        sql += " GROUP BY symbol, hour, exchange, flow_type ORDER BY hour"
        return engine.db.query(sql, p)

    @app.get("/api/v1/alerts")
    async def alerts(limit: int = Query(100, le=1000), level: str | None = None):
        sql, p = "SELECT * FROM alerts", []
        if level:
            sql += " WHERE level=?"; p.append(level.upper())
        sql += " ORDER BY ts_ms DESC LIMIT ?"; p.append(limit)
        rows = engine.db.query(sql, p)
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    @app.get("/api/v1/wallets")
    async def wallets(limit: int = Query(50, le=500), category: str | None = None):
        sql, p = "SELECT * FROM wallets WHERE category != 'null'", []
        if category:
            sql += " AND category=?"; p.append(category)
        sql += " ORDER BY volume_usd DESC LIMIT ?"; p.append(limit)
        return engine.db.query(sql, p)

    @app.get("/api/v1/smart-money")
    async def smart_money():
        return {"hinweis": "Geschrumpfte Mittelwerte (k=20) gegen Selektions-Bias; erst ab vielen Beobachtungen aussagekräftig.",
                "wallets": await asyncio.to_thread(outcomes.wallet_stats, engine.db)}

    @app.get("/api/v1/history")
    async def history():
        summary = outcomes.summarize(engine.db)
        recent = engine.db.query(
            "SELECT a.id, a.symbol, a.ts_ms, a.level, a.event_type, a.price_at_alert, o.horizon_min, o.ret, o.max_up, "
            "o.max_down, o.status FROM alerts a LEFT JOIN outcomes o ON o.ref_type='alert' AND o.ref_id=a.id "
            "ORDER BY a.ts_ms DESC LIMIT 400")
        return {**summary, "recent": recent}

    @app.get("/api/v1/score-history/{symbol}")
    async def score_history(symbol: str, hours: int = Query(24, le=720)):
        sym = resolve(symbol)
        since = int(time.time() * 1000) - hours * 3_600_000
        return engine.db.query("SELECT ts_ms, whale_impact, bullish, bearish, market_impact, data_confidence, level "
                               "FROM scores WHERE symbol=? AND ts_ms>=? ORDER BY ts_ms", (sym, since))

    @app.get("/api/v1/health")
    async def health():
        sysinfo = {}
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            sysinfo = {"cpu_percent_system": psutil.cpu_percent(interval=None),
                       "memory_percent_system": psutil.virtual_memory().percent,
                       "process_rss_mb": round(proc.memory_info().rss / 1_048_576, 1),
                       "process_cpu_percent": proc.cpu_percent(interval=None),
                       "disk_free_gb": round(psutil.disk_usage(str(Path(engine.db.path).parent if engine.db.path != ':memory:' else '.')).free / 1e9, 1)}
        except Exception as e:  # noqa: BLE001
            sysinfo = {"error": str(e)}
        counts = engine.db.one("SELECT (SELECT COUNT(*) FROM transfers) AS transfers, (SELECT COUNT(*) FROM alerts) AS alerts, "
                               "(SELECT COUNT(*) FROM wallets) AS wallets, (SELECT COUNT(*) FROM outcomes) AS outcomes, "
                               "(SELECT COUNT(*) FROM market_snapshots) AS market_snapshots")
        return {"version": __version__, "uptime_s": round((time.time() * 1000 - engine.health.started_ms) / 1000),
                "sources": engine.health.snapshot(), "pipeline": engine.pipeline.stats,
                "database": {"path": engine.db.path, "size_mb": round(engine.db.size_mb(), 2), **counts},
                "system": sysinfo, "wallet_labels": len(engine.registry),
                "integration": bot_interface.INTEGRATION_STATUS}

    return app
