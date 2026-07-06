const fmtMoney = (value, digits = 2) => {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "--";
  return `${n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })} USDT`;
};

const fmtThb = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "฿--";
  return `฿${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};

const fmtThbLine = (currency, equity) => {
  const baht = fmtThb(equityBaht(currency, equity));
  const rate = Number((currency || {}).usd_thb_rate);
  if (!Number.isFinite(rate) || rate <= 0 || baht === "฿--") return baht;
  return `${baht} · ${rate.toFixed(2)}/USDT`;
};

function splitTagline(value) {
  const raw = String(value || "").replace(/\s+/g, " ").trim();
  if (!raw) return ["Alternative Store of Value", "Trading System"];
  const match = /^(.*?)(?:\s+)?(Trading System)$/i.exec(raw);
  if (match) return [match[1].trim(), match[2].trim()];
  return [raw, ""];
}

const equityBaht = (currency, equity) => {
  const direct = Number((currency || {}).equity_thb);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const rate = Number((currency || {}).usd_thb_rate);
  const usdt = Number(equity);
  if (Number.isFinite(rate) && rate > 0 && Number.isFinite(usdt) && usdt > 0) return usdt * rate;
  return NaN;
};

const fmtNum = (value, digits = 2) => {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "--";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
};

const fmtSignedPct = (value, digits = 1) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })}%`;
};

const text = (id, value) => {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
};

const prefersReducedMotion = () => (
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
);

function markValueUpdated(el) {
  if (!el || prefersReducedMotion()) return;
  el.classList.remove("value-updated");
  void el.offsetWidth;
  el.classList.add("value-updated");
  window.setTimeout(() => el.classList.remove("value-updated"), 380);
}

function liveText(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  const next = String(value);
  const previous = el.dataset.liveValue;
  if (previous !== undefined && previous !== next) {
    markValueUpdated(el);
  }
  el.dataset.liveValue = next;
  el.textContent = value;
}

function noteLiveValue(el, value) {
  if (!el) return;
  const next = String(value);
  const previous = el.dataset.liveValue;
  if (previous !== undefined && previous !== next) {
    markValueUpdated(el);
  }
  el.dataset.liveValue = next;
}

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
let lastPortfolioSegments = [];
let currentSymbol = "";
let currentTimeframe = "4h";
let latestMarketPrice = null;

const PORTFOLIO_COLORS = ["#ff431a", "#97aef0", "#ff7040", "#f8f4ee", "#70e0c2", "#c6b7ff"];

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

function relativeTime(value) {
  if (!value) return "--";
  const ts = parseUtcish(value);
  if (!Number.isFinite(ts)) return "--";
  const diffSec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (diffSec < 45) return "Just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 48) return `${diffHour}h ago`;
  return `${Math.round(diffHour / 24)}d ago`;
}

function compactSecs(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n < 0) return "--";
  if (n < 90) return `${Math.round(n)}s`;
  const minutes = n / 60;
  if (minutes < 90) return `${Math.round(minutes)}m`;
  const hours = minutes / 60;
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

function shortStatus(value, fallback = "--") {
  const raw = String(value || fallback).toUpperCase();
  if (raw === "RUNNING") return "RUN";
  if (raw === "DEGRADED") return "DEG";
  if (raw === "STOPPED") return "STOP";
  return raw;
}

function compactTrigger(trigger) {
  const raw = String(trigger || "").trim();
  if (!raw) return "closed";
  const lower = raw.toLowerCase();
  if (lower.includes("fixed tp") || lower.includes("tp reached") || lower.includes("take profit")) return "target";
  if (lower.includes("exchange-side stop") || lower.includes("stop loss") || lower.includes("sl")) return "stop";
  return raw.replace(/\s+/g, " ").slice(0, 42);
}

function compactEventDetail(value) {
  const raw = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!raw) return "";
  return raw.length > 58 ? `${raw.slice(0, 55)}...` : raw;
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

function baseFromSymbol(symbol) {
  const normalized = String(symbol || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  const quotes = ["USDT", "USDC", "USD", "THB", "BTC", "ETH"];
  const quote = quotes.find((suffix) => normalized.endsWith(suffix));
  return quote ? normalized.slice(0, -quote.length) || normalized : normalized;
}

function mergeSegment(map, label, value, includeZero = false) {
  const amount = Number(value);
  if (!label || !Number.isFinite(amount) || amount < 0) return;
  if (amount === 0 && !includeZero && !map.has(label)) return;
  map.set(label, (map.get(label) || 0) + amount);
}

function portfolioSegmentsFromState(state, snap, fallbackEquity) {
  const bySymbol = state.by_symbol && typeof state.by_symbol === "object" ? state.by_symbol : {};
  const entries = Object.keys(bySymbol).length
    ? Object.entries(bySymbol)
    : [[snap.symbol || state.symbol || currentSymbol || "PORTFOLIO", snap || state]];
  const merged = new Map();
  let cashAdded = false;

  entries.forEach(([symbol, rawSnap]) => {
    const item = rawSnap && typeof rawSnap === "object" ? rawSnap : {};
    const breakdown = item.equity_breakdown || {};
    const portfolio = item.portfolio || {};
    const base = String(breakdown.base_asset || baseFromSymbol(symbol || item.symbol)).toUpperCase();
    const price = asNumber(item.current_price ?? item.mark_price ?? snap.current_price ?? state.current_price);

    if (!cashAdded) {
      const cash = asNumber(breakdown.usdt_balance_usdt ?? portfolio.USDT ?? portfolio.USD);
      if (cash != null) mergeSegment(merged, "USDT", cash);
      cashAdded = true;
    }

    const explicitBaseValue = asNumber(breakdown.base_value_usdt);
    if (explicitBaseValue != null) {
      mergeSegment(merged, base, explicitBaseValue, true);
      return;
    }

    const baseQty = asNumber(portfolio[base] ?? breakdown.base_quantity);
    if (baseQty != null && price != null && price > 0) {
      mergeSegment(merged, base, baseQty * price, true);
      return;
    }

    mergeSegment(merged, base, 0, true);
  });

  const segments = Array.from(merged.entries())
    .map(([label, value], index) => ({
      label,
      value,
      color: PORTFOLIO_COLORS[index % PORTFOLIO_COLORS.length],
    }))
    .sort((a, b) => (a.label === "USDT" ? -1 : b.label === "USDT" ? 1 : b.value - a.value));

  if (!segments.length && Number(fallbackEquity) > 0) {
    return [{ label: "USDT", value: Number(fallbackEquity), color: PORTFOLIO_COLORS[0] }];
  }
  return segments;
}

function fmtCompactUsdt(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "--";
  const digits = Math.abs(n) >= 100 ? 0 : 2;
  return `${fmtNum(n, digits)} U`;
}

function partialTpSummary(pos, positionOpen) {
  if (!positionOpen) return "";
  const pct = Number(pos.partial_tp_pct || 0);
  const fraction = Number(pos.partial_tp_fraction || 0);
  if (!(pct > 0 && fraction > 0 && fraction < 1)) return "";
  if (pos.partial_tp_taken) return "PTP banked";
  const trigger = Number(pos.partial_tp_trigger_price || 0);
  const target = trigger > 0 ? fmtNum(trigger, trigger >= 100 ? 0 : 2) : `${fmtNum(pct, 1)}%`;
  return `PTP ${fmtNum(fraction * 100, 0)}% @ ${target}`;
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

async function loadMeta() {
  try {
    const meta = await getJson("/api/meta");
    if (!meta.ok) return;
    if (meta.avatar_url) {
      const avatar = document.getElementById("userAvatar");
      if (avatar) avatar.src = meta.avatar_url;
    }
    if (meta.display_name) {
      const name = document.getElementById("botName");
      const subtitleMain = document.getElementById("botSubtitleMain");
      const subtitleFine = document.getElementById("botSubtitleFine");
      const parts = String(meta.display_name).split(/\s*:\s*/);
      if (name) {
        name.textContent = parts[0] || "xAuby";
        name.title = meta.display_name;
      }
      const [main, fine] = splitTagline(parts.slice(1).join(" : "));
      if (subtitleMain) subtitleMain.textContent = main;
      if (subtitleFine) subtitleFine.textContent = fine;
    }
  } catch (err) {
    /* header falls back to the bundled defaults */
  }
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
  ctx.strokeStyle = "rgba(248, 244, 238, 0.07)";
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 8]);
  for (let i = 1; i < 5; i += 1) {
    const y = topPad + i * (plotH / 5);
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
  text("chartMeta", String(currentTimeframe || "4h").toUpperCase());
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
  const bodyW = Math.max(6, Math.min(18, slot * 0.64));
  candles.forEach((c, i) => {
    const x = leftPad + slot * i + slot / 2;
    const up = c.close >= c.open;
    const color = up ? "#ff7040" : "#97aef0";
    const yOpen = yFor(c.open);
    const yClose = yFor(c.close);
    const yHigh = yFor(c.high);
    const yLow = yFor(c.low);
    const bodyY = Math.min(yOpen, yClose);
    const bodyH = Math.max(4, Math.abs(yClose - yOpen));
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.8;
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
  drawEma(emaSlow, "rgba(151, 174, 240, 0.76)");
  drawEma(emaFast, "#ff7040");

  ctx.fillStyle = "rgba(248, 244, 238, 0.52)";
  ctx.font = "11px -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let i = 0; i < 5; i += 1) {
    const value = max - (span * i) / 4;
    const y = topPad + i * (plotH / 4);
    ctx.fillText(fmtNum(value, 0), w - rightPad + 8, y);
  }

  const latest = livePrice ?? candles[candles.length - 1].close;
  const latestY = yFor(latest);
  const label = fmtNum(latest, 0);
  const labelW = ctx.measureText(label).width + 16;
  const labelX = w - labelW - 3;

  ctx.save();
  ctx.strokeStyle = "rgba(255, 67, 26, 0.78)";
  ctx.lineWidth = 1.4;
  ctx.setLineDash([6, 6]);
  ctx.beginPath();
  ctx.moveTo(leftPad, latestY);
  ctx.lineTo(Math.max(leftPad, labelX - 8), latestY);
  ctx.stroke();
  ctx.restore();

  ctx.fillStyle = "#ff7040";
  ctx.beginPath();
  if (ctx.roundRect) {
    ctx.roundRect(labelX, latestY - 12, labelW, 24, 12);
    ctx.fill();
  } else {
    ctx.fillRect(labelX, latestY - 12, labelW, 24);
  }
  ctx.fillStyle = "#161618";
  ctx.textAlign = "center";
  ctx.fillText(label, w - labelW / 2 - 3, latestY);

  ctx.fillStyle = "rgba(248, 244, 238, 0.5)";
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  chartAxisLabels(currentTimeframe, candles.length).forEach((labelText, index) => {
    const x = leftPad + (plotW * index) / 3;
    ctx.fillText(labelText, x, h - 4);
  });
}

function drawPortfolio(segments = lastPortfolioSegments) {
  const canvas = document.getElementById("portfolioCanvas");
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const cssWidth = Math.max(96, rect.width || 128);
  const cssHeight = Math.max(96, rect.height || 128);
  canvas.width = Math.floor(cssWidth * ratio);
  canvas.height = Math.floor(cssHeight * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const cx = cssWidth / 2;
  const cy = cssHeight / 2;
  const radius = Math.min(cssWidth, cssHeight) / 2 - 9;
  const lineWidth = Math.max(14, radius * 0.3);
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  ctx.lineWidth = lineWidth;
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.stroke();

  if (total > 0) {
    let start = -Math.PI / 2;
    const gap = Math.min(0.05, (Math.PI * 2) / Math.max(segments.length, 1) * 0.16);
    segments.forEach((segment) => {
      const angle = (segment.value / total) * Math.PI * 2;
      const end = start + Math.max(0, angle - gap);
      ctx.strokeStyle = segment.color;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, start, end);
      ctx.stroke();
      start += angle;
    });
  }

  ctx.fillStyle = "rgba(248, 244, 238, 0.54)";
  ctx.font = "10px -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("Portfolio", cx, cy - 7);
  ctx.fillStyle = "#f8f4ee";
  ctx.font = "600 13px -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
  ctx.fillText(total > 0 ? `${segments.length} Asset${segments.length === 1 ? "" : "s"}` : "--", cx, cy + 9);
}

function renderPortfolio(state, snap, equity) {
  const root = document.getElementById("portfolioLegend");
  const header = document.getElementById("portfolioTotal");
  const statsRoot = document.getElementById("portfolioStats");
  const segments = portfolioSegmentsFromState(state, snap, equity);
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);
  lastPortfolioSegments = segments;
  if (header) header.textContent = fmtMoney(equity || total, 2);
  if (root) {
    root.innerHTML = "";
    if (!segments.length || total <= 0) {
      root.innerHTML = `<div class="empty-state">No portfolio data</div>`;
    } else {
      segments.slice(0, 5).forEach((segment) => {
        const pct = total > 0 ? (segment.value / total) * 100 : 0;
        const row = document.createElement("div");
        row.className = "portfolio-row";
        row.innerHTML = `
          <i style="background:${escapeHtml(segment.color)}"></i>
          <strong>${escapeHtml(segment.label)}</strong>
          <span>${fmtNum(pct, 0)}%</span>
        `;
        root.appendChild(row);
      });
    }
  }
  if (statsRoot) {
    const cash = segments.find((segment) => segment.label === "USDT")?.value || 0;
    const base = Math.max(0, total - cash);
    const top = segments.reduce((best, segment) => (
      segment.value > (best?.value ?? -1) ? segment : best
    ), null);
    const topPct = top && total > 0 ? (top.value / total) * 100 : 0;
    const stats = [
      ["Cash", fmtCompactUsdt(cash)],
      ["Base", fmtCompactUsdt(base)],
      ["Top", top ? `${top.label} ${fmtNum(topPct, 0)}%` : "--"],
    ];
    statsRoot.innerHTML = stats.map(([label, value]) => `
      <div class="portfolio-stat">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
    `).join("");
  }
  drawPortfolio(segments);
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
  const rows = Array.isArray(events) ? events.slice(-40).reverse() : [];
  text("eventCount", `${rows.length}`);
  root.innerHTML = rows.length ? "" : `<div class="empty-state">No recent events</div>`;
  rows.forEach((event) => {
    const row = document.createElement("div");
    row.className = "event-row";
    const label = event.event_type || event.event || "--";
    const ts = event.ts || "";
    const payload = event.payload || {};
    const detail = compactEventDetail(payload.reason || payload.action || payload.regime || payload.price || "");
    const chip = eventChip(label);
    row.innerHTML = `
      <span class="ev-chip ${chip.cls}">${escapeHtml(chip.label)}</span>
      <div class="ev-body">
        <small>${escapeHtml(relativeTime(ts))}</small>
        <strong>${escapeHtml(label)}${detail ? `: ${escapeHtml(detail)}` : ""}</strong>
      </div>
    `;
    root.appendChild(row);
  });
}

function renderTrades(trades) {
  const root = document.getElementById("trades");
  if (!root) return;
  const rows = Array.isArray(trades) ? trades.slice(0, 30) : [];
  text("tradeCount", `${rows.length}`);
  root.innerHTML = rows.length ? "" : `<div class="empty-state">No closed trades</div>`;
  rows.forEach((trade) => {
    const pnl = Number(trade.net_pnl || 0);
    const pnlClass = pnl >= 0 ? "green" : "red";
    const sym = String(trade.symbol || "--");
    const short = sym.replace("USDT", "").slice(0, 3) || "--";
    const trig = String(trade.trigger || "").toLowerCase();
    const trigCls = /tp|take|target/.test(trig) ? "chip-pos" : /stop|sl|liq/.test(trig) ? "chip-neg" : "chip-muted";
    const row = document.createElement("div");
    row.className = "trade-row";
    row.innerHTML = `
      <span class="ev-chip ${pnl >= 0 ? "chip-pos" : "chip-neg"}">${escapeHtml(short)}</span>
      <div class="ev-body">
        <small>${escapeHtml(relativeTime(trade.closed_at || trade.opened_at))}</small>
        <strong>${escapeHtml(sym)} <span class="${pnlClass}">${fmtMoney(pnl)}</span>${trade.trigger ? ` <span class="ev-tag ${trigCls}">${escapeHtml(compactTrigger(trade.trigger))}</span>` : ""}</strong>
      </div>
    `;
    root.appendChild(row);
  });
}

function parseUtcish(ts) {
  // Engine writes opened_at as a tz-naive UTC ISO string; treat a missing
  // timezone as UTC so held-for doesn't drift by the viewer's offset.
  if (!ts) return NaN;
  const s = String(ts);
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s);
  return Date.parse(hasTz ? s : `${s}Z`);
}

const STATE_CLASSES = ["state-pos", "state-neg", "state-warn", "state-info", "state-muted"];

function setStateClass(id, semantic) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove(...STATE_CLASSES);
  if (semantic) el.classList.add(`state-${semantic}`);
}

function signalSemantic(action) {
  const a = String(action || "").toUpperCase();
  if (/BUY|OPEN|LONG|COVER/.test(a)) return "pos";
  if (/SELL|CLOSE|SHORT/.test(a)) return "neg";
  return "muted";
}

function regimeSemantic(value) {
  const r = String(value || "").toUpperCase();
  if (r.includes("BULL")) return "pos";
  if (r.includes("BEAR") || r.includes("PANIC") || r.includes("BREAKDOWN")) return "neg";
  if (r.includes("RANGE") || r.includes("NEUTRAL")) return "warn";
  return "muted";
}

function trendSemantic(value) {
  const s = String(value || "").toUpperCase();
  if (s.includes("UP") || s.includes("BULL")) return "pos";
  if (s.includes("DOWN") || s.includes("BEAR")) return "neg";
  return "muted";
}

function riskSemantic(value) {
  const r = String(value || "").toUpperCase();
  if (r.includes("PANIC")) return "neg";
  if (r.includes("DEFENSIVE") || r.includes("ELEVATED") || r.includes("CAUTION")) return "warn";
  if (r.includes("NORMAL") || r.includes("RISK_ON") || r === "OK") return "pos";
  return "muted";
}

function healthSemantic(value) {
  const s = String(value || "").toUpperCase();
  if (s.includes("OFFLINE") || s.includes("CRIT") || s.includes("ERROR") || s.includes("FAIL")) return "neg";
  if (s.includes("WARN") || s.includes("DEGRAD")) return "warn";
  if (s.includes("OK") || s.includes("ONLINE") || s.includes("RUN")) return "pos";
  return "muted";
}

function eventChip(type) {
  const t = String(type || "").toLowerCase();
  if (t.includes("position_open") || t.includes("opened")) return { label: "OPN", cls: "chip-pos" };
  if (t.includes("position_clos") || t.includes("closed")) return { label: "CLS", cls: "chip-neg" };
  if (t.includes("order") || t.includes("fill")) return { label: "ORD", cls: "chip-info" };
  if (t.includes("risk") || t.includes("reject")) return { label: "RSK", cls: "chip-warn" };
  if (t.includes("signal")) return { label: "SIG", cls: "chip-info" };
  if (t.includes("regime")) return { label: "REG", cls: "chip-cyan" };
  if (t.includes("tick") || t.includes("price")) return { label: "PX", cls: "chip-muted" };
  return { label: (t.slice(0, 3).toUpperCase() || "EVT"), cls: "chip-muted" };
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
  const pos = snap.position || {};
  const sig = snap.signal_meta || {};
  const regime = snap.regime || {};
  const latency = snap.latency || state.latency || {};
  const indicators = snap.indicators || {};
  const indicatorDisplay = snap.indicator_display || {};
  const wsAge = latency.ws_tick_age_ms == null ? "--" : compactSecs(latency.ws_tick_age_ms / 1000);
  const confidence = `${fmtNum((regime.confidence || sig.confidence || 0) * 100, 0)}%`;
  const zoneItem = (indicatorDisplay.panel_items || []).find((item) => item.label === "Zone");
  const cdcZone = indicators.cdc_zone_4h || (zoneItem || {}).value || "--";
  const redStreak = Number(indicators.cdc_zone_4h_red_streak || 0);
  const greenStreak = Number(indicators.cdc_zone_4h_green_streak || 0);
  const streak = redStreak || greenStreak;

  text("exchangeLabel", `${(exchange.id || "exchange").toUpperCase()} ${(exchange.market_type || "").toUpperCase()}`);
  text("symbolTitle", symbol);
  currentSymbol = symbol;
  const stale = Boolean(payload.stale);
  text("modePill", stale ? "STALE" : modeText);
  cls("modePill", `status-pill ${!stale && modeText === "LIVE" ? "live" : "warn"}`);
  text("signalRegimeLabel", stale ? "STALE" : modeText);
  setStateClass("signalRegimeLabel", stale ? "warn" : healthSemantic(modeText));
  const equityEl = document.getElementById("equityValue");
  if (equityEl) {
    const equityText = fmtNum(equity, 2);
    equityEl.dataset.liveValue = equityText;
    equityEl.textContent = "";
    const number = document.createElement("span");
    number.className = "equity-number";
    number.textContent = equityText;
    equityEl.appendChild(number);
    const unit = document.createElement("span");
    unit.className = "unit";
    unit.textContent = "USDT";
    equityEl.appendChild(unit);
  }
  text("cashValue", fmtThbLine(payload.currency, equity));
  renderPortfolio(state, snap, equity);
  liveText("priceValue", fmtNum(price, 2));
  text("changeValue", `24h ${fmtNum(pct24h, 2)}%`);
  cls("changeValue", `change-value ${pct24h > 0 ? "positive" : pct24h < 0 ? "negative" : "neutral"}`);
  const positionOpen = String(pos.state || "").toLowerCase() === "bought";
  const positionSide = String(pos.position_side || "LONG").toUpperCase();
  const positionPnlPct = asNumber(pos.unrealized_pnl_pct);
  const positionLabel = positionOpen
    ? `${positionSide}${positionPnlPct == null ? "" : ` ${fmtSignedPct(positionPnlPct, 1)}`}`
    : "FLAT";
  cls("positionValue", `position-status ${positionOpen ? (positionSide === "SHORT" ? "state-neg" : "state-pos") : "state-muted"}`);
  liveText("positionValue", positionLabel);
  const positionPnl = Number(pos.unrealized_pnl || 0);
  cls("pnlValue", positionOpen ? (positionPnl > 0 ? "positive" : positionPnl < 0 ? "negative" : "neutral") : "neutral");
  const partialTp = partialTpSummary(pos, positionOpen);
  liveText(
    "pnlValue",
    positionOpen
      ? `PnL ${fmtMoney(positionPnl)}${partialTp ? ` · ${partialTp}` : ""}`
      : "PnL --"
  );
  liveText("signalAction", sig.action || "--");
  liveText("overviewSignal", sig.action || "--");
  setStateClass("signalAction", signalSemantic(sig.action));
  setStateClass("overviewSignal", signalSemantic(sig.action));
  text("signalReason", sig.reason || "No signal reason");
  text("regimeConfidence", confidence);
  setStateClass("regimeConfidence", signalSemantic(sig.action));
  text("regimeState", compactState(regime.regime));
  text("regimeStateDetail", regime.regime || "--");
  setStateClass("regimeState", regimeSemantic(regime.regime));
  setStateClass("regimeStateDetail", regimeSemantic(regime.regime));
  text("trendState", regime.trend || "--");
  text("trendStateDetail", regime.trend || "--");
  setStateClass("trendState", trendSemantic(regime.trend));
  setStateClass("trendStateDetail", trendSemantic(regime.trend));
  text("riskState", regime.risk_state || "--");
  text("overviewRisk", compactState(regime.risk_state));
  setStateClass("riskState", riskSemantic(regime.risk_state));
  setStateClass("overviewRisk", riskSemantic(regime.risk_state));
  const feedText = pos.feed_health || (snap.degraded ? "DEGRADED" : "OK");
  text("feedState", feedText);
  text("feedStateDetail", feedText);
  setStateClass("feedState", healthSemantic(feedText));
  setStateClass("feedStateDetail", healthSemantic(feedText));
  text("stateAge", payload.age_sec == null ? "S --" : `S ${compactSecs(payload.age_sec)}`);
  text("wsAge", `W ${wsAge}`);
  text("apiLatency", latency.api_latency_ms == null ? "A --" : `A ${latency.api_latency_ms}`);
  text("chartMeta", String(snap.primary_timeframe || currentTimeframe || "4h").toUpperCase());
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
  const engine = (payload.process_status || {}).status || "--";
  text("engineStatus", `E ${shortStatus(engine)}`);
  setStateClass("engineStatus", healthSemantic(engine));
}

function setView(view, animateNav = true) {
  const previousView = document.body.dataset.view;
  const shouldAnimateNav = animateNav && previousView !== view && !prefersReducedMotion();
  document.body.dataset.view = view;
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    const active = button.dataset.viewTarget === view;
    button.classList.toggle("active", active);
    button.classList.remove("nav-pressed");
    if (active && shouldAnimateNav) {
      void button.offsetWidth;
      button.classList.add("nav-pressed");
      window.setTimeout(() => button.classList.remove("nav-pressed"), 180);
    }
  });
  if (location.hash !== `#${view}`) {
    history.replaceState(null, "", `#${view}`);
  }
  requestAnimationFrame(() => {
    drawChart(lastCandles, latestMarketPrice);
    drawPortfolio(lastPortfolioSegments);
  });
}

function initNavigation() {
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.viewTarget));
  });
  const initial = location.hash.replace("#", "");
  if (["overview", "signal", "activity"].includes(initial)) {
    setView(initial, false);
  } else if (initial === "health") {
    setView("overview", false);
  }
}

async function refresh() {
  const [stateResult, healthResult, eventsResult, tradesResult] = await Promise.allSettled([
    getJson("/api/state"),
    getJson("/api/health"),
    getJson("/api/recent-events"),
    getJson("/api/trades?limit=30"),
  ]);

  if (stateResult.status === "fulfilled") {
    const state = stateResult.value;
    updateState(state);
    const stateBody = state.state || {};
    const snap = focusSnapshot(stateBody);
    const symbol = snap.symbol || stateBody.focus_symbol || stateBody.symbol || currentSymbol;
    currentTimeframe = snap.primary_timeframe || currentTimeframe;
    if (symbol) {
      try {
        const candles = await getJson(
          `/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(currentTimeframe)}&limit=24`
        );
        if (candles.ok && Array.isArray(candles.candles) && candles.candles.length) {
          lastCandles = candles.candles;
          drawChart(lastCandles, latestMarketPrice);
        }
      } catch (err) {
        /* Candle refresh is non-critical; keep the last rendered chart. */
      }
    }
  } else {
    text("signalReason", `WebUI state refresh failed: ${stateResult.reason}`);
    cls("modePill", "status-pill warn");
    text("modePill", "Offline");
  }

  if (healthResult.status === "fulfilled") updateHealth(healthResult.value);
  if (eventsResult.status === "fulfilled") renderEvents(eventsResult.value.events || []);
  if (tradesResult.status === "fulfilled") renderTrades(tradesResult.value.trades || []);
}

initNavigation();
loadMeta();
refresh();
setInterval(refresh, 5000);
window.addEventListener("resize", () => {
  drawChart(lastCandles, latestMarketPrice);
  drawPortfolio(lastPortfolioSegments);
});
