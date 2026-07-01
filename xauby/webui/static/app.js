const fmtMoney = (value, digits = 2) => {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "--";
  return `${n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })} USDT`;
};

const fmtNum = (value, digits = 2) => {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "--";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
};

const text = (id, value) => {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
};

const cls = (id, value) => {
  const el = document.getElementById(id);
  if (el) el.className = value;
};

const addStateClass = (id, baseClass, value) => {
  const el = document.getElementById(id);
  if (!el) return;
  const state = String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  el.className = `${baseClass} ${state}`.trim();
};

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
}[ch]));

let lastCandles = [];
let currentSymbol = "";
let currentTimeframe = "4h";
let latestMarketPrice = null;

function timeframeToMinutes(timeframe) {
  const match = /^([0-9]+)\s*([mhdw])$/i.exec(String(timeframe || "").trim());
  if (!match) return 240;
  const amount = Number(match[1]);
  const unitMinutes = { m: 1, h: 60, d: 1440, w: 10080 }[match[2].toLowerCase()] || 60;
  return amount * unitMinutes;
}

function formatDurationAgo(minutes) {
  if (!Number.isFinite(minutes) || minutes <= 0) return "0H";
  if (minutes < 60) return `${Math.round(minutes)}M`;
  const hours = minutes / 60;
  if (hours < 48) return `${Math.round(hours)}H`;
  return `${Math.round(hours / 24)}D`;
}

function chartAxisLabels(timeframe, candleCount) {
  const totalMinutes = timeframeToMinutes(timeframe) * Math.max(0, candleCount - 1);
  return [0, 1, 2, 3].map((index) => (
    index === 3 ? "Now" : formatDurationAgo((totalMinutes * (3 - index)) / 3)
  ));
}

function asNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function compactIndicatorValue(value) {
  const n = Number(value);
  if (Number.isFinite(n)) return fmtNum(n, n >= 100 ? 0 : 2);
  return String(value ?? "--");
}

function compactState(value) {
  const raw = String(value || "--").toUpperCase();
  return raw
    .replace("BEAR_TREND_", "BEAR ")
    .replace("BULL_TREND_", "BULL ")
    .replace("RANGE_", "RANGE ")
    .replace("RISK_", "")
    .replace(/_/g, " ");
}

function normalizeCandles(values) {
  const raw = Array.isArray(values) ? values : [];
  const candles = [];
  raw.forEach((item, index) => {
    let open = null;
    let high = null;
    let low = null;
    let close = null;
    if (Array.isArray(item)) {
      if (item.length >= 5) {
        open = asNumber(item[1]);
        high = asNumber(item[2]);
        low = asNumber(item[3]);
        close = asNumber(item[4]);
      } else if (item.length >= 4) {
        open = asNumber(item[0]);
        high = asNumber(item[1]);
        low = asNumber(item[2]);
        close = asNumber(item[3]);
      }
    } else if (item && typeof item === "object") {
      open = asNumber(item.open ?? item.o);
      high = asNumber(item.high ?? item.h);
      low = asNumber(item.low ?? item.l);
      close = asNumber(item.close ?? item.c);
    } else {
      close = asNumber(item);
    }

    if (close == null) return;
    const prevClose = candles.length ? candles[candles.length - 1].close : close;
    if (open == null) open = index === 0 ? close * 0.999 : prevClose;
    const bodyTop = Math.max(open, close);
    const bodyBottom = Math.min(open, close);
    const body = Math.max(Math.abs(open - close), Math.abs(close) * 0.00035);
    if (high == null) high = bodyTop + body * 0.7;
    if (low == null) low = bodyBottom - body * 0.7;
    candles.push({ open, high, low, close });
  });
  return candles.slice(-28);
}

function emaSeries(candles, period) {
  const multiplier = 2 / (period + 1);
  let ema = null;
  return candles.map((c) => {
    const close = asNumber(c.close);
    if (close == null) return null;
    ema = ema == null ? close : close * multiplier + ema * (1 - multiplier);
    return ema;
  });
}

async function getJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  return res.json();
}

function focusSnapshot(state) {
  const bySymbol = state.by_symbol || {};
  const keys = Object.keys(bySymbol);
  if (keys.length) {
    const focus = (state.focus_symbol || state.symbol || keys[0] || "").toUpperCase();
    return bySymbol[focus] || bySymbol[keys[0]] || state;
  }
  return state;
}

function drawChart(values, referencePrice = latestMarketPrice) {
  const canvas = document.getElementById("priceCanvas");
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const cssWidth = Math.max(280, rect.width);
  const cssHeight = Math.max(170, rect.height || 184);
  canvas.width = Math.floor(cssWidth * ratio);
  canvas.height = Math.floor(cssHeight * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const w = cssWidth;
  const h = cssHeight;
  const leftPad = 4;
  const rightPad = 46;
  const topPad = 18;
  const bottomPad = 26;
  const plotW = w - leftPad - rightPad;
  const plotH = h - topPad - bottomPad;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.075)";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 5]);
  for (let i = 0; i < 5; i += 1) {
    const y = topPad + i * (plotH / 4);
    ctx.beginPath();
    ctx.moveTo(leftPad, y);
    ctx.lineTo(leftPad + plotW, y);
    ctx.stroke();
  }
  ctx.setLineDash([]);

  const candles = normalizeCandles(values);
  if (candles.length < 2) {
    text("chartMeta", "no candles");
    return;
  }
  text("chartMeta", `${candles.length} candles`);
  const emaFast = emaSeries(candles, 12);
  const emaSlow = emaSeries(candles, 26);
  const livePrice = referencePrice == null ? null : asNumber(referencePrice);
  const scaleValues = [
    ...candles.flatMap((c) => [c.low, c.high]),
    ...emaFast,
    ...emaSlow,
    livePrice,
  ].filter(Number.isFinite);
  const rawMin = Math.min(...scaleValues);
  const rawMax = Math.max(...scaleValues);
  const rawSpan = rawMax - rawMin || Math.max(Math.abs(rawMax) * 0.002, 1);
  const pad = rawSpan * 0.12;
  const min = rawMin - pad;
  const max = rawMax + pad;
  const span = max - min || 1;

  const yFor = (value) => topPad + (max - value) / span * plotH;
  const slot = plotW / candles.length;
  const bodyW = Math.max(5, Math.min(15, slot * 0.52));
  candles.forEach((c, i) => {
    const x = leftPad + slot * i + slot / 2;
    const up = c.close >= c.open;
    const color = up ? "#31d07f" : "#ff6678";
    const yOpen = yFor(c.open);
    const yClose = yFor(c.close);
    const yHigh = yFor(c.high);
    const yLow = yFor(c.low);
    const bodyY = Math.min(yOpen, yClose);
    const bodyH = Math.max(3, Math.abs(yClose - yOpen));
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, yHigh);
    ctx.lineTo(x, yLow);
    ctx.stroke();
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(x - bodyW / 2, bodyY, bodyW, bodyH, 2);
      ctx.fill();
    } else {
      ctx.fillRect(x - bodyW / 2, bodyY, bodyW, bodyH);
    }
  });

  const drawEma = (series, color) => {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.7;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.shadowColor = "rgba(0, 0, 0, 0.35)";
    ctx.shadowBlur = 3;
    ctx.beginPath();
    let started = false;
    series.forEach((value, i) => {
      if (!Number.isFinite(value)) return;
      const x = leftPad + slot * i + slot / 2;
      const y = yFor(value);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    if (started) ctx.stroke();
    ctx.restore();
  };
  drawEma(emaSlow, "#38bdf8");
  drawEma(emaFast, "#a855f7");

  ctx.fillStyle = "rgba(246, 243, 255, 0.62)";
  ctx.font = "11px Inter, system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let i = 0; i < 5; i += 1) {
    const value = max - (span * i) / 4;
    const y = topPad + i * (plotH / 4);
    ctx.fillText(fmtNum(value, 0), w - rightPad + 8, y);
  }

  const latest = livePrice ?? candles[candles.length - 1].close;
  const latestY = yFor(latest);
  ctx.fillStyle = "#7357ff";
  const label = fmtNum(latest, 0);
  const labelW = ctx.measureText(label).width + 16;
  ctx.beginPath();
  if (ctx.roundRect) {
    ctx.roundRect(w - labelW - 3, latestY - 12, labelW, 24, 12);
    ctx.fill();
  } else {
    ctx.fillRect(w - labelW - 3, latestY - 12, labelW, 24);
  }
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.fillText(label, w - labelW / 2 - 3, latestY);

  ctx.fillStyle = "rgba(246, 243, 255, 0.56)";
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  chartAxisLabels(currentTimeframe, candles.length).forEach((labelText, index) => {
    const x = leftPad + (plotW * index) / 3;
    ctx.fillText(labelText, x, h - 4);
  });
}

function renderChecklist(items) {
  const root = document.getElementById("checklist");
  if (!root) return;
  root.innerHTML = "";
  if (!Array.isArray(items) || !items.length) {
    root.innerHTML = `<div class="check-item"><span class="dot"></span><strong>No checklist</strong><span>--</span></div>`;
    return;
  }
  items.slice(0, 8).forEach((item) => {
    const row = document.createElement("div");
    row.className = `check-item ${item.ok ? "ok" : ""}`;
    row.innerHTML = `
      <span class="dot"></span>
      <strong>${escapeHtml(item.label || "--")}</strong>
      <span>${escapeHtml(item.value || item.hint || "--")}</span>
    `;
    root.appendChild(row);
  });
}

function renderCdcDetail(indicatorDisplay) {
  const root = document.getElementById("cdcDetail");
  if (!root) return;
  const items = Array.isArray(indicatorDisplay.panel_items) ? indicatorDisplay.panel_items : [];
  root.innerHTML = "";
  if (!items.length) {
    root.innerHTML = `<div><span>CDC</span><strong>--</strong></div>`;
    return;
  }
  items.slice(0, 6).forEach((item) => {
    const card = document.createElement("div");
    card.innerHTML = `
      <span>${escapeHtml(item.label || item.key || "--")}</span>
      <strong>${escapeHtml(compactIndicatorValue(item.value))}</strong>
    `;
    root.appendChild(card);
  });
}

function renderEvents(events) {
  const root = document.getElementById("events");
  if (!root) return;
  const rows = Array.isArray(events) ? events.slice(-12).reverse() : [];
  text("eventCount", `${rows.length}`);
  root.innerHTML = rows.length ? "" : `<div class="event-row"><small>--</small><strong>No events</strong></div>`;
  rows.forEach((event) => {
    const row = document.createElement("div");
    row.className = "event-row";
    const label = event.event_type || event.event || "--";
    const ts = event.ts || "";
    const payload = event.payload || {};
    const detail = payload.reason || payload.action || payload.regime || payload.price || "";
    row.innerHTML = `<small>${escapeHtml(ts)}</small><strong>${escapeHtml(label)}${detail ? `: ${escapeHtml(detail)}` : ""}</strong>`;
    root.appendChild(row);
  });
}

function renderTrades(trades) {
  const root = document.getElementById("trades");
  if (!root) return;
  const rows = Array.isArray(trades) ? trades.slice(0, 10) : [];
  text("tradeCount", `${rows.length}`);
  root.innerHTML = rows.length ? "" : `<div class="trade-row"><small>--</small><strong>No closed trades</strong></div>`;
  rows.forEach((trade) => {
    const pnl = Number(trade.net_pnl || 0);
    const pnlClass = pnl >= 0 ? "green" : "red";
    const row = document.createElement("div");
    row.className = "trade-row";
    row.innerHTML = `
      <small>${escapeHtml(trade.closed_at || trade.opened_at || "--")}</small>
      <strong>${escapeHtml(trade.symbol || "--")} <span class="${pnlClass}">${fmtMoney(pnl)}</span> <small>${escapeHtml(trade.trigger || "")}</small></strong>
    `;
    root.appendChild(row);
  });
}

function updateState(payload) {
  if (!payload.ok) {
    text("symbolTitle", "State unavailable");
    text("signalReason", payload.error || "Waiting for state file");
    cls("modePill", "status-pill warn");
    text("modePill", "No state");
    return;
  }
  const state = payload.state || {};
  const snap = focusSnapshot(state);
  const exchange = snap.exchange || state.exchange || {};
  const symbol = snap.symbol || state.focus_symbol || state.symbol || "xAuby";
  const mode = snap.execution_mode || (snap.simulate_only || state.simulate_only ? "sim" : "live");
  const modeText = String(mode || "").toUpperCase();
  const rawPrice = snap.current_price ?? state.current_price;
  const price = asNumber(rawPrice) ?? 0;
  latestMarketPrice = rawPrice == null ? null : asNumber(rawPrice);
  const pct24h = Number(
    snap.percent_change_24h
    ?? snap.price_change_24h_pct
    ?? state.percent_change_24h
    ?? state.price_change_24h_pct
    ?? 0
  );
  const equity = snap.total_equity_usdt || state.total_equity_usdt || (state.aggregate || {}).total_equity_usdt || 0;
  const bd = snap.equity_breakdown || {};
  const pos = snap.position || {};
  const sig = snap.signal_meta || {};
  const regime = snap.regime || {};
  const latency = snap.latency || state.latency || {};
  const indicators = snap.indicators || {};
  const indicatorDisplay = snap.indicator_display || {};
  const wsAge = latency.ws_tick_age_ms == null ? "--" : `${Math.round(latency.ws_tick_age_ms / 1000)}s`;
  const confidence = `${fmtNum((regime.confidence || sig.confidence || 0) * 100, 0)}%`;
  const zoneItem = (indicatorDisplay.panel_items || []).find((item) => item.label === "Zone");
  const cdcZone = indicators.cdc_zone_4h || (zoneItem || {}).value || "--";
  const redStreak = Number(indicators.cdc_zone_4h_red_streak || 0);
  const greenStreak = Number(indicators.cdc_zone_4h_green_streak || 0);
  const streak = redStreak || greenStreak;

  text("exchangeLabel", `${(exchange.id || "exchange").toUpperCase()} ${(exchange.market_type || "").toUpperCase()}`);
  text("symbolTitle", symbol);
  currentSymbol = symbol;
  text("indicatorLabel", "Indicator");
  text("modePill", modeText);
  cls("modePill", `status-pill ${modeText === "LIVE" ? "live" : "warn"}`);
  text("equityValue", fmtMoney(equity));
  text("cashValue", `Trading cash ${fmtMoney(bd.usdt_balance_usdt || (snap.portfolio || {}).USDT || 0)}`);
  text("priceValue", fmtNum(price, 2));
  text("changeValue", `24h ${fmtNum(pct24h, 2)}%`);
  cls("changeValue", `change-value ${pct24h > 0 ? "positive" : pct24h < 0 ? "negative" : "neutral"}`);
  text("positionValue", `${pos.state || "idle"} ${(pos.position_side || "").toUpperCase()}`.trim());
  text("pnlValue", `PnL ${fmtMoney(pos.unrealized_pnl || 0)}`);
  text("signalAction", sig.action || "--");
  text("overviewSignal", sig.action || "--");
  text("signalReason", sig.reason || "No signal reason");
  text("regimeConfidence", confidence);
  text("regimeState", compactState(regime.regime));
  text("regimeStateDetail", regime.regime || "--");
  text("trendState", regime.trend || "--");
  text("trendStateDetail", regime.trend || "--");
  text("riskState", regime.risk_state || "--");
  text("overviewRisk", compactState(regime.risk_state));
  text("feedState", pos.feed_health || (snap.degraded ? "DEGRADED" : "OK"));
  text("feedStateDetail", pos.feed_health || (snap.degraded ? "DEGRADED" : "OK"));
  text("stateAge", payload.age_sec == null ? "--" : `${payload.age_sec}s`);
  text("overviewStateAge", payload.age_sec == null ? "--" : `${payload.age_sec}s`);
  text("wsAge", wsAge);
  text("apiLatency", latency.api_latency_ms == null ? "--" : `${latency.api_latency_ms}ms`);
  text("overviewApiLatency", latency.api_latency_ms == null ? "--" : `${latency.api_latency_ms}ms`);
  text("cdcZone", cdcZone);
  addStateClass("cdcZone", "zone-value", cdcZone);
  text("cdcEmaFast", compactIndicatorValue(indicators.ema_fast_4h));
  text("cdcEmaSlow", compactIndicatorValue(indicators.ema_slow_4h));
  text("cdcStreak", streak ? `${cdcZone} ${streak}` : "--");
  renderCdcDetail(indicatorDisplay);
  renderChecklist(sig.checklist || []);
  if (!lastCandles.length) {
    lastCandles = indicators.recent_candles_4h || indicators.candles_4h || indicators.ohlcv_4h || indicators.recent_ohlcv_4h || indicators.recent_closes_4h || [];
    drawChart(lastCandles, latestMarketPrice);
  }
}

function updateHealth(payload) {
  text("healthStatus", payload.status || "--");
  text("overviewHealth", payload.status || "--");
  text("engineStatus", (payload.process_status || {}).status || "--");
  const root = document.getElementById("healthList");
  if (!root) return;
  const anomalies = payload.anomalies || [];
  root.innerHTML = "";
  anomalies.slice(0, 5).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    root.appendChild(li);
  });
}

function setView(view) {
  document.body.dataset.view = view;
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.classList.toggle("active", button.dataset.viewTarget === view);
  });
  if (location.hash !== `#${view}`) {
    history.replaceState(null, "", `#${view}`);
  }
  requestAnimationFrame(() => drawChart(lastCandles, latestMarketPrice));
}

function initNavigation() {
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.viewTarget));
  });
  const initial = location.hash.replace("#", "");
  if (["overview", "signal", "health", "activity"].includes(initial)) {
    setView(initial);
  }
}

async function refresh() {
  try {
    const [state, health, events, trades] = await Promise.all([
      getJson("/api/state"),
      getJson("/api/health"),
      getJson("/api/recent-events"),
      getJson("/api/trades?limit=10"),
    ]);
    updateState(state);
    const stateBody = state.state || {};
    const snap = focusSnapshot(stateBody);
    const symbol = snap.symbol || stateBody.focus_symbol || stateBody.symbol || currentSymbol;
    currentTimeframe = snap.primary_timeframe || currentTimeframe;
    if (symbol) {
      const candles = await getJson(
        `/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(currentTimeframe)}&limit=24`
      );
      if (candles.ok && Array.isArray(candles.candles) && candles.candles.length) {
        lastCandles = candles.candles;
        drawChart(lastCandles, latestMarketPrice);
      }
    }
    updateHealth(health);
    renderEvents(events.events || []);
    renderTrades(trades.trades || []);
  } catch (err) {
    text("signalReason", `WebUI refresh failed: ${err}`);
    cls("modePill", "status-pill warn");
    text("modePill", "Offline");
  }
}

async function applyMeta() {
  try {
    const meta = await getJson("/api/meta");
    if (!meta.ok) return;
    if (meta.display_name) text("botTitle", meta.display_name);
    if (meta.avatar_url) {
      const avatar = document.getElementById("avatarImg");
      if (avatar) avatar.src = meta.avatar_url;
    }
  } catch (err) {
    // Non-fatal: keep the default title/avatar if the meta endpoint fails.
  }
}

initNavigation();
applyMeta();
refresh();
setInterval(refresh, 5000);
window.addEventListener("resize", () => drawChart(lastCandles, latestMarketPrice));
