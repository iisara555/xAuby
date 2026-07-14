/**
 * Thin transport boundary for the read-only dashboard API.
 *
 * Rendering code receives decoded payloads only; endpoint paths and browser
 * authentication recovery stay in this module.
 */
async function requestJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    throw new Error(`request failed (${response.status})`);
  }
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("unauthorized");
  }
  let body = {};
  try {
    body = await response.json();
  } catch (error) {
    body = {};
  }
  if (!response.ok) {
    throw new Error(body.error || `request failed (${response.status})`);
  }
  return body;
}

const CHART_CANDLE_COUNT = 32;

export function createDashboardClient() {
  return {
    fetchMeta: () => requestJson("/api/meta"),
    fetchCandles: (symbol, timeframe, limit = CHART_CANDLE_COUNT) => requestJson(
      `/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=${limit}`,
    ),
    fetchDashboard: () => Promise.allSettled([
      requestJson("/api/state"),
      requestJson("/api/health"),
      requestJson("/api/recent-events"),
      requestJson("/api/trades?limit=30"),
      requestJson("/api/dashboard-detail"),
    ]),
    submitManualOrder: (payload) => postJson("/api/manual-order", payload),
  };
}
