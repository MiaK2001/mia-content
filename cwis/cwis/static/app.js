"use strict";
// CWIS Dashboard – reines Vanilla-JS, keine externen Bibliotheken (offline-fähig)
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const LEVEL_DE = { GREEN: "GRÜN – NORMAL", YELLOW: "GELB – ERHÖHT", ORANGE: "ORANGE – HOCH", RED: "ROT – KRITISCH" };
const FLOW_DE = {
  EXCHANGE_INFLOW: "Börsenzufluss", EXCHANGE_OUTFLOW: "Börsenabfluss", EXCHANGE_INTERNAL: "Börsenintern",
  INTER_EXCHANGE: "Zwischen Börsen", MINT: "Mint", BURN: "Burn", ISSUER_TREASURY: "Emittent",
  DEX_INTERACTION: "DEX-Interaktion", KNOWN_TRANSFER: "Bekannt", UNKNOWN_TRANSFER: "Unbekannt → Unbekannt",
};
const ACTION_DE = {
  NONE: "keine", RISK_WARNING: "Risikohinweis", BULLISH_CONTEXT: "bullisher Kontext",
  CRITICAL_NO_NEW_POSITIONS_ADVISORY: "kritisch – Hinweis: keine neuen Positionen", NO_SIGNAL_DATA_INSUFFICIENT: "kein Signal (Daten unzureichend)",
};
function usd(v) {
  if (v === null || v === undefined) return "n/v";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  if (a >= 1e9) return s + nf.format(a / 1e9) + " Mrd. $";
  if (a >= 1e6) return s + nf.format(a / 1e6) + " Mio. $";
  if (a >= 1e3) return s + nf.format(Math.round(a / 1e3)) + " Tsd. $";
  return s + nf.format(a) + " $";
}
const price = (v) => (v ? (v >= 1 ? nf.format(v) : new Intl.NumberFormat("de-DE", { maximumSignificantDigits: 4 }).format(v)) + " $" : "n/v");
const pct = (v, d = 2) => (v === null || v === undefined ? "n/v" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + " %");
const t = (ms) => (ms ? new Date(ms).toLocaleString("de-DE") : "–");
const tShort = (ms) => (ms ? new Date(ms).toLocaleTimeString("de-DE") : "–");
const short = (a) => (a ? (a.length > 16 ? a.slice(0, 8) + "…" + a.slice(-6) : a) : "–");
const lvl = (l) => (l ? `<span class="lvl ${l}">${esc(l)}</span>` : '<span class="muted">–</span>');
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(path + " " + r.status);
  return r.json();
}
function explorer(chain, tx) {
  if (chain === "bitcoin") return `https://mempool.space/tx/${tx}`;
  const m = { ethereum: "https://etherscan.io/tx/", bsc: "https://bscscan.com/tx/", arbitrum: "https://arbiscan.io/tx/", base: "https://basescan.org/tx/", polygon: "https://polygonscan.com/tx/", avalanche: "https://snowtrace.io/tx/" };
  return (m[chain] || "#") + tx;
}
function meter(label, v, cls) {
  const val = v === null || v === undefined ? 0 : v;
  return `<div class="meter"><span>${label}</span><div class="bar ${cls}"><i style="width:${Math.max(0, Math.min(100, val))}%"></i></div><b>${v === null || v === undefined ? "–" : Math.round(v)}</b></div>`;
}
function party(r, side) {
  const lab = r[side + "_label"], conf = r[side + "_confidence"];
  if (lab) return `${esc(lab)} <span class="muted">(${esc(conf)})</span>`;
  return `<span class="muted" title="${esc(r[side + "_address"])}">unbekannt ${esc(short(r[side + "_address"]))}</span>`;
}

// ---------- Tabs ----------
let activeTab = "overview";
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  activeTab = name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  refreshTab();
}

// ---------- Übersicht ----------
let overview = null;
let symbols = [];
async function loadOverview() {
  overview = await api("/api/v1/overview");
  const syms = overview.symbols.map((s) => s.symbol);
  if (syms.join() !== symbols.join()) {
    symbols = syms;
    for (const id of ["liveSym", "flowSym", "impactSym"]) {
      const sel = $("#" + id), keep = sel.value;
      sel.innerHTML = (id === "liveSym" ? '<option value="">alle Assets</option>' : "") + syms.map((s) => `<option>${s}</option>`).join("");
      if (keep) sel.value = keep;
    }
  }
  const ov = overview.market || {};
  const o = $("#overall");
  o.className = "badge " + (ov.overall_level || "NONE");
  o.textContent = LEVEL_DE[ov.overall_level] || "Warte auf Daten";
  $("#updated").textContent = "Analyse: " + tShort(overview.ts_ms);
  if (activeTab === "overview") renderOverview();
}
function renderOverview() {
  $("#cards").innerHTML = overview.symbols.map((s) => {
    if (!s.level) return `<div class="card"><div class="sym">${s.symbol}</div><p class="muted">Noch keine Analyse</p></div>`;
    const m = s.market || {}, f = s.flows || {};
    return `<div class="card ${s.level}" data-sym="${s.symbol}">
      <div class="top"><span class="sym">${s.symbol}</span><span class="price">${price(m.price)} <span class="${(m.ret_1h || 0) >= 0 ? "pos" : "neg"}">${pct(m.ret_1h)}</span></span></div>
      <div style="margin:6px 0">${lvl(s.level)} <span class="muted">${esc(s.data_status)}</span></div>
      ${meter("Whale Impact", s.whale_impact, "")}${meter("Bullish", s.bullish, "bull")}${meter("Bearish", s.bearish, "bear")}
      ${meter("Market Impact", s.market_impact, "mkt")}${meter("Datenqualität", s.confidence, "conf")}
      <div class="muted" style="font-size:12px;margin-top:6px">Netto Börse 60 Min.: <b class="${f.exchange_netflow_usd > 0 ? "neg" : f.exchange_netflow_usd < 0 ? "pos" : ""}">${usd(f.exchange_netflow_usd)}</b> · Whales: ${f.whale_count ?? 0}</div>
      <div class="muted" style="font-size:12px">Hinweis: ${esc(ACTION_DE[s.action] || s.action)}</div></div>`;
  }).join("");
  document.querySelectorAll(".card[data-sym]").forEach((c) => c.addEventListener("click", () => { $("#impactSym").value = c.dataset.sym; showTab("impact"); }));
  const m = overview.market || {};
  $("#stable").innerHTML = `<div class="kv"><div>Zuflüsse auf Börsen</div><div>${usd(m.stablecoin_inflow_usd)}</div><div>Abflüsse von Börsen</div><div>${usd(m.stablecoin_outflow_usd)}</div>
    <div>Netto</div><div><b>${usd(m.stablecoin_exchange_netflow_usd)}</b> <span class="muted">(positiv = mögliche Kaufkraft auf Börsen)</span></div></div>`;
  const cl = m.clusters || [];
  $("#clusters").innerHTML = cl.length ? cl.map((c) => `<div style="margin-bottom:8px"><b>${c.symbol}</b> · ${c.flow_type === "EXCHANGE_INFLOW" ? "auf Börsen" : "von Börsen"} ·
      ${c.wallets} Wallets/Entitäten (${c.unknown_wallets} unbekannt) · ${usd(c.total_usd)} · ${Object.keys(c.exchanges).slice(0, 4).map(esc).join(", ")}
      <div class="muted" style="font-size:12px">${esc(c.note)}</div></div>`).join("") : '<p class="muted">Keine Häufung im aktuellen Fenster.</p>';
}

// ---------- Live Whale Monitor ----------
let liveRows = [];
async function loadLive() {
  const w = $("#whaleOnly").checked, s = $("#liveSym").value;
  liveRows = await api(`/api/v1/transfers?limit=200&whale_only=${w}${s ? "&symbol=" + s : ""}`);
  renderLive();
}
function renderLive() {
  $("#liveTable tbody").innerHTML = liveRows.map((r) => `<tr class="${r.is_whale ? "whale" : ""}">
    <td>${tShort(r.detected_ms)}</td><td>${esc(r.chain)}</td><td><b>${esc(r.symbol)}</b></td>
    <td class="r">${nf.format(r.amount)}</td><td class="r"><b>${usd(r.value_usd)}</b></td>
    <td><span class="ft ${r.flow_type}">${esc(FLOW_DE[r.flow_type] || r.flow_type)}</span>${r.exchange ? " " + esc(r.exchange) : ""}</td>
    <td>${party(r, "from")}</td><td>${party(r, "to")}</td><td class="r muted">${usd(r.threshold_usd)}</td>
    <td><a href="${explorer(r.chain, r.tx_hash)}" target="_blank" rel="noopener">${short(r.tx_hash)}</a></td></tr>`).join("") ||
    '<tr><td colspan="10" class="muted">Noch keine Transfers erfasst.</td></tr>';
}
$("#whaleOnly").addEventListener("change", loadLive);
$("#liveSym").addEventListener("change", loadLive);

// ---------- Whale Activity Map ----------
async function loadMap() {
  const [wallets, tr] = await Promise.all([api("/api/v1/wallets?limit=40"), api("/api/v1/transfers?limit=1000")]);
  $("#walletTable tbody").innerHTML = wallets.map((w) => `<tr><td title="${esc(w.address)}">${esc(w.chain)}: ${esc(short(w.address))}</td>
    <td>${w.label ? esc(w.label) : '<span class="muted">unbekannt</span>'}</td><td>${esc(w.confidence)}</td><td class="r">${w.tx_count}</td>
    <td class="r">${usd(w.volume_usd)}</td><td class="r">${usd(w.to_exchange_usd)}</td><td class="r">${usd(w.from_exchange_usd)}</td><td>${tShort(w.last_seen_ms)}</td></tr>`).join("");
  const since = Date.now() - 864e5, agg = {};
  tr.filter((r) => r.detected_ms >= since).forEach((r) => { agg[r.flow_type] = (agg[r.flow_type] || 0) + r.value_usd; });
  const max = Math.max(1, ...Object.values(agg));
  $("#flowTypes").innerHTML = Object.entries(agg).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
    `<div class="comp"><span>${esc(FLOW_DE[k] || k)}</span><div class="bar"><i style="width:${(v / max) * 100}%"></i></div><span>${usd(v)}</span></div>`).join("") ||
    '<p class="muted">Keine Daten.</p>';
}

// ---------- Exchange Flow Monitor ----------
async function loadFlows() {
  const sym = $("#flowSym").value || symbols[0], hours = +$("#flowHours").value;
  if (!sym) return;
  const rows = await api(`/api/v1/flows?hours=${hours}&symbol=${sym}`);
  const byHour = {}, byEx = {};
  for (const r of rows) {
    const h = (byHour[r.hour] ||= { in: 0, out: 0 });
    const e = (byEx[r.exchange || "unbekannt"] ||= { in: 0, out: 0, n: 0 });
    if (r.flow_type === "EXCHANGE_INFLOW") { h.in += r.usd; e.in += r.usd; } else { h.out += r.usd; e.out += r.usd; }
    e.n += r.n;
  }
  const now = Date.now(), start = Math.floor((now - hours * 36e5) / 36e5) * 36e5, hs = [];
  for (let x = start; x <= now; x += 36e5) hs.push(x);
  const max = Math.max(1, ...hs.map((h) => Math.max(byHour[h]?.in || 0, byHour[h]?.out || 0)));
  const W = 1000, H = 260, mid = H / 2, bw = W / hs.length;
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="0" y1="${mid}" x2="${W}" y2="${mid}" stroke="#2a3240"/>`;
  hs.forEach((h, i) => {
    const d = byHour[h] || { in: 0, out: 0 }, hi = (d.in / max) * (mid - 10), ho = (d.out / max) * (mid - 10);
    svg += `<rect x="${i * bw + 1}" y="${mid - hi}" width="${Math.max(1, bw - 2)}" height="${hi}" fill="var(--in)"><title>${new Date(h).toLocaleString("de-DE")} Zufluss ${usd(d.in)}</title></rect>`;
    svg += `<rect x="${i * bw + 1}" y="${mid}" width="${Math.max(1, bw - 2)}" height="${ho}" fill="var(--out)"><title>${new Date(h).toLocaleString("de-DE")} Abfluss ${usd(d.out)}</title></rect>`;
  });
  svg += `<text x="4" y="14" fill="#8b98a8" font-size="12">Zufluss max ${usd(max)}</text><text x="4" y="${H - 4}" fill="#8b98a8" font-size="12">Abfluss</text></svg>`;
  $("#flowChart").innerHTML = rows.length ? svg : '<p class="muted">Noch keine Börsenflüsse für dieses Asset (nur bekannte Börsenadressen werden erkannt).</p>';
  $("#exTable tbody").innerHTML = Object.entries(byEx).sort((a, b) => (b[1].in + b[1].out) - (a[1].in + a[1].out)).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td class="r neg">${usd(v.in)}</td><td class="r pos">${usd(v.out)}</td><td class="r"><b>${usd(v.in - v.out)}</b></td><td class="r">${v.n}</td></tr>`).join("");
}
$("#flowSym").addEventListener("change", loadFlows);
$("#flowHours").addEventListener("change", loadFlows);

// ---------- Market Impact Analysis ----------
function lineChart(rows, keys) {
  if (rows.length < 2) return '<p class="muted">Noch zu wenig Verlauf.</p>';
  const W = 1000, H = 220, t0 = rows[0].ts_ms, t1 = rows[rows.length - 1].ts_ms;
  const x = (ts) => ((ts - t0) / Math.max(1, t1 - t0)) * W, y = (v) => H - 10 - (v / 100) * (H - 20);
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`;
  for (const g of [25, 50, 75]) svg += `<line x1="0" x2="${W}" y1="${y(g)}" y2="${y(g)}" stroke="#2a3240" stroke-dasharray="4 4"/>`;
  for (const [k, color] of keys) svg += `<polyline fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" points="${rows.map((r) => `${x(r.ts_ms).toFixed(1)},${y(r[k] || 0).toFixed(1)}`).join(" ")}"/>`;
  return svg + "</svg>";
}
function comps(title, values, weights) {
  return `<h4>${title}</h4>` + Object.entries(values).map(([k, v]) =>
    `<div class="comp"><span>${esc(k)} <span class="muted">(${weights?.[k] ?? "-"})</span></span><div class="bar"><i style="width:${v * 100}%"></i></div><span>${(v * 100).toFixed(0)} %</span></div>`).join("");
}
const list = (xs) => "<ul>" + (xs || []).map((x) => `<li>${esc(x)}</li>`).join("") + "</ul>";
async function loadImpact() {
  const sym = $("#impactSym").value || symbols[0];
  if (!sym) return;
  let a;
  try { a = await api("/api/v1/analysis/" + sym); } catch { $("#impactBody").innerHTML = '<p class="muted">Noch keine Analyse.</p>'; return; }
  const hist = await api(`/api/v1/score-history/${sym}?hours=24`);
  const s = a.score, c = s.components, an = a.analysis, m = s.market || {}, d = s.derivatives || {};
  $("#impactBody").innerHTML = `
    <div class="grid2">
      <div class="panel"><h3>${sym} · ${lvl(a.level)} · ${esc(a.condition)}</h3>
        ${meter("Whale Impact", s.whale_impact_score, "")}${meter("Bullish", s.bullish_score, "bull")}${meter("Bearish", s.bearish_score, "bear")}
        ${meter("Market Impact", s.market_impact_score, "mkt")}${meter("Datenqualität", s.data_confidence, "conf")}
        <p class="muted">Status: ${esc(s.data_status)} · Hinweis: ${esc(ACTION_DE[a.action] || a.action)} · Regelwerk ${esc(s.scoring_version)}</p>
        <h4>Beobachtete Ereignisse</h4>${list(an.observed_events)}
        <h4>Mögliche Marktauswirkungen</h4>${list(an.possible_market_impact)}
        <h4>Alternative Erklärungen</h4>${list(an.alternative_explanations)}
        <h4>Unsicherheiten / fehlende Daten</h4>${list(an.uncertainties)}
        <h4>Historischer Vergleich</h4><p>${esc(an.historical_comparison)}</p>
        <h4>Quellen & Aktualität</h4><p class="muted">${an.data_sources.map(esc).join(" · ")} · Markt ${an.data_freshness.market_age_s ?? "–"} s, Derivate ${an.data_freshness.derivatives_age_s ?? "–"} s</p></div>
      <div class="panel"><h3>Score-Komponenten (Gewicht)</h3>
        ${comps("Whale Impact", c.whale, c.weights.whale)}${comps("Bearish", c.bearish, c.weights.bearish)}
        ${comps("Bullish", c.bullish, c.weights.bullish)}${comps("Market Impact", c.market, c.weights.market)}
        <h4>Datenqualität</h4><div class="kv">${Object.entries(c.confidence).map(([k, v]) => `<div>${esc(k)}</div><div>${v}</div>`).join("")}</div></div>
    </div>
    <div class="panel"><h3>Verlauf 24 Std. <span style="color:#4ea8ff">Whale</span> · <span style="color:#2ea043">Bullish</span> · <span style="color:#e5484d">Bearish</span> · <span style="color:#e8792b">Market</span></h3>
      <div class="chart">${lineChart(hist, [["whale_impact", "#4ea8ff"], ["bullish", "#2ea043"], ["bearish", "#e5484d"], ["market_impact", "#e8792b"]])}</div></div>
    <div class="grid2">
      <div class="panel"><h3>Marktdaten (Binance Spot)</h3><div class="kv">
        <div>Kurs</div><div>${price(m.price)}</div><div>24h-Volumen Paar / Markt</div><div>${usd(m.volume_24h_usd)} / ${usd(m.global_volume_24h_usd)}</div>
        <div>Orderbuch-Tiefe ±1 %</div><div>${usd(m.depth_1pct_usd)} ${m.depth_complete === false ? '<span class="muted">(Untergrenze)</span>' : ""}</div>
        <div>Ungleichgewicht</div><div>${pct(m.book_imbalance, 1)}</div><div>Größte Kauf-/Verkaufsorder</div><div>${usd(m.largest_bid_usd)} / ${usd(m.largest_ask_usd)} <span class="muted">(storniertbar)</span></div>
        <div>Spread</div><div>${m.spread_bps?.toFixed(2) ?? "n/v"} bps</div><div>Volatilität 1h</div><div>${pct(m.realized_vol_1h)}</div><div>Volumen 1h vs. Ø</div><div>${m.volume_ratio_1h?.toFixed(2) ?? "n/v"}×</div>
        <div>Marktkapitalisierung</div><div>${usd(m.market_cap_usd)}</div></div></div>
      <div class="panel"><h3>Derivate (OKX Perpetual)</h3><div class="kv">
        <div>Open Interest</div><div>${usd(d.open_interest_usd)}</div><div>OI-Veränderung 1h</div><div>${d.oi_change_1h_pct == null ? "n/v (Historie fehlt)" : d.oi_change_1h_pct.toFixed(2) + " %"}</div>
        <div>Funding Rate</div><div>${d.funding_rate == null ? "n/v" : (d.funding_rate * 100).toFixed(4) + " %"}</div><div>Long/Short-Konten</div><div>${d.long_short_ratio ?? "n/v"}</div>
        <div>Long-Liquidationen 1h</div><div>${usd(d.long_liq_usd_1h)} <span class="muted">(Untergrenze)</span></div><div>Short-Liquidationen 1h</div><div>${usd(d.short_liq_usd_1h)}</div></div></div>
    </div>`;
}
$("#impactSym").addEventListener("change", loadImpact);

// ---------- Ranking ----------
async function loadRanking() {
  const r = await api("/api/v1/ranking");
  const blocks = [["Potenziell bullishe Aktivität", "bullish", "bullish_score"], ["Potenziell bearishe Aktivität", "bearish", "bearish_score"],
    ["Höchste Whale-Aktivität", "whale_activity", "whale_impact_score"], ["Außergewöhnliche Marktveränderungen", "market_change", "market_impact_score"],
    ["Größte Börsenzuflüsse", "exchange_inflows", "inflow_usd"], ["Größte Börsenabflüsse", "exchange_outflows", "outflow_usd"]];
  $("#rankGrid").innerHTML = blocks.map(([title, k, f]) => `<div class="panel"><h3>${title}</h3><table><tbody>${r[k].map((x, i) =>
    `<tr><td>${i + 1}.</td><td><b>${x.symbol}</b></td><td>${lvl(x.level)}</td><td class="r">${f.endsWith("usd") ? usd(x[f]) : x[f].toFixed(1)}</td></tr>`).join("")}</tbody></table></div>`).join("") +
    (r.excluded_insufficient_data.length ? `<p class="note">Ausgeschlossen (Daten unzureichend): ${r.excluded_insufficient_data.join(", ")}</p>` : "");
}

// ---------- Smart Money ----------
async function loadSmart() {
  const r = await api("/api/v1/smart-money");
  $("#smartNote").textContent = r.hinweis;
  $("#smartTable tbody").innerHTML = r.wallets.map((w) => `<tr><td>${esc(w.chain)}</td><td title="${esc(w.address)}">${esc(short(w.address))}</td><td class="r">${w.n}</td>
    <td class="r">${pct(w.raw_mean_ret_60m)}</td><td class="r"><b>${pct(w.shrunk_mean_ret_60m)}</b></td></tr>`).join("") ||
    '<tr><td colspan="5" class="muted">Noch keine Wallet mit ≥ 5 auswertbaren Whale-Transfers.</td></tr>';
}

// ---------- Alerts ----------
async function loadAlerts() {
  const l = $("#alertLevel").value;
  const rows = await api("/api/v1/alerts?limit=100" + (l ? "&level=" + l : ""));
  $("#alertList").innerHTML = rows.map((a) => {
    const p = a.payload, an = p.analysis || {}, s = p.scores;
    return `<details class="alert ${a.level}"><summary>${lvl(a.level)} <b>${esc(a.symbol)}</b> <span>${esc(p.event_type)}</span>
      <span class="muted">${t(a.ts_ms)}</span> <span>WI ${s.whale_impact_score} · Bull ${s.bullish_score} · Bear ${s.bearish_score} · Daten ${s.data_confidence}</span>
      <span class="muted">Volumen ${usd(a.volume_usd)}</span></summary>
      <h4>Beobachtet</h4>${list(an.observed_events)}<h4>Mögliche Auswirkungen</h4>${list(an.possible_market_impact)}
      <h4>Alternative Erklärungen</h4>${list(an.alternative_explanations)}<h4>Unsicherheiten</h4>${list(an.uncertainties)}
      <h4>Quellen</h4><p class="muted">${(an.data_sources || []).map(esc).join(" · ")}</p></details>`;
  }).join("") || '<p class="muted">Noch keine Warnungen.</p>';
}
$("#alertLevel").addEventListener("change", loadAlerts);

// ---------- Historie ----------
async function loadHistory() {
  const h = await api("/api/v1/history");
  $("#histTable tbody").innerHTML = h.groups.map((g) => `<tr><td>${esc(g.event_type)}</td><td>${esc(g.bias)}</td><td class="r">${g.horizon_min} Min.</td>
    <td class="r">${g.n}${g.sufficient_sample ? "" : ' <span class="muted">*</span>'}</td><td class="r">${pct(g.mean_ret, 3)}</td><td class="r">${pct(g.baseline_mean_ret, 3)}</td>
    <td class="r"><b>${pct(g.excess_ret, 3)}</b></td><td class="r">${g.hit_rate == null ? "–" : (g.hit_rate * 100).toFixed(0) + " %"}</td>
    <td class="r">${g.baseline_hit_rate == null ? "–" : (g.baseline_hit_rate * 100).toFixed(0) + " %"}</td><td class="r">${pct(g.mean_max_up)}</td><td class="r">${pct(g.mean_max_down)}</td></tr>`).join("") ||
    '<tr><td colspan="11" class="muted">Noch keine abgeschlossenen Auswertungen.</td></tr>';
  $("#baseline").innerHTML = `<p class="muted">${esc(h.hinweis)} · Offene Messungen: ${h.pending}</p><div class="kv">` +
    h.baselines.map((b) => `<div>${b.horizon_min} Min. (n=${b.n})</div><div>Ø ${pct(b.mean_ret, 3)} · Ø |Bewegung| ${pct(b.mean_abs_ret, 3)}</div>`).join("") + "</div>";
  const by = {};
  for (const r of h.recent) (by[r.id] ||= { ...r, o: {} }).o[r.horizon_min] = r;
  const hz = [1, 5, 15, 30, 60, 240, 720, 1440];
  $("#histRecent").innerHTML = `<table><thead><tr><th>Zeit</th><th>Asset</th><th>Stufe</th><th>Typ</th>${hz.map((x) => `<th class="r">${x < 60 ? x + "m" : x / 60 + "h"}</th>`).join("")}</tr></thead><tbody>` +
    Object.values(by).slice(0, 50).map((a) => `<tr><td>${t(a.ts_ms)}</td><td>${esc(a.symbol)}</td><td>${lvl(a.level)}</td><td>${esc(a.event_type)}</td>${hz.map((x) => {
      const o = a.o[x]; return `<td class="r ${o?.ret > 0 ? "pos" : o?.ret < 0 ? "neg" : "muted"}">${o?.status === "done" ? pct(o.ret) : o?.status === "failed" ? "Fehler" : "…"}</td>`;
    }).join("")}</tr>`).join("") + "</tbody></table>";
}

// ---------- Bot / Health ----------
async function loadBot() { $("#botJson").textContent = JSON.stringify(await api("/api/v1/signals"), null, 2); }
async function loadHealth() {
  const h = await api("/api/v1/health");
  $("#srcTable tbody").innerHTML = h.sources.map((s) => `<tr><td>${esc(s.name)}</td><td class="st-${s.state}"><b>${s.state}</b></td><td class="r">${s.success_count}</td>
    <td class="r">${s.error_count}</td><td class="r">${s.last_latency_ms ?? "–"} ms</td><td>${tShort(s.last_success_ms)}</td>
    <td class="muted" style="white-space:normal;max-width:420px">${esc(s.last_error || s.note || "")}</td></tr>`).join("");
  const sys = h.system || {}, db = h.database || {}, p = h.pipeline || {};
  $("#sysInfo").innerHTML = `<div class="kv"><div>Version / Laufzeit</div><div>${esc(h.version)} · ${Math.round(h.uptime_s / 60)} Min.</div>
    <div>CPU System / Prozess</div><div>${sys.cpu_percent_system ?? "–"} % / ${sys.process_cpu_percent ?? "–"} %</div>
    <div>RAM System / Prozess</div><div>${sys.memory_percent_system ?? "–"} % / ${sys.process_rss_mb ?? "–"} MB</div><div>Freier Speicher</div><div>${sys.disk_free_gb ?? "–"} GB</div>
    <div>Datenbank</div><div>${db.size_mb} MB · ${db.transfers} Transfers · ${db.alerts} Warnungen · ${db.wallets} Wallets</div>
    <div>Pipeline</div><div>gesehen ${p.seen} · gespeichert ${p.stored} · Duplikate ${p.duplicates} · ohne verlässlichen Preis ${p.unpriced_skipped} · ungültig ${p.invalid}</div>
    <div>Wallet-Labels</div><div>${h.wallet_labels}</div><div>Integration</div><div>${esc(h.integration)}</div></div>`;
}

// ---------- Aktualisierung ----------
const loaders = { overview: async () => {}, live: loadLive, map: loadMap, flows: loadFlows, impact: loadImpact, ranking: loadRanking, smart: loadSmart, alerts: loadAlerts, history: loadHistory, bot: loadBot, health: loadHealth };
async function refreshTab() {
  try { await loadOverview(); await loaders[activeTab](); } catch (e) { console.warn(e); }
}
setInterval(refreshTab, 15000);
refreshTab();

// WebSocket: Live-Transfers und Warnungen sofort anzeigen
function connectWs() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/v1/signals");
  ws.onopen = () => { $("#conn").textContent = "Live verbunden"; $("#conn").className = "pill ok"; };
  ws.onclose = () => { $("#conn").textContent = "Getrennt – verbinde neu…"; $("#conn").className = "pill err"; setTimeout(connectWs, 3000); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "transfer" && activeTab === "live") {
      const r = m.data, s = $("#liveSym").value;
      if ((!$("#whaleOnly").checked || r.is_whale) && (!s || r.symbol === s)) { liveRows.unshift(r); liveRows = liveRows.slice(0, 200); renderLive(); }
    } else if (m.type === "alert" && activeTab === "alerts") loadAlerts();
  };
}
connectWs();
