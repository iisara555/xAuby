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

export function createDashboardClient() {
  return {
    fetchMeta: () => requestJson("/api/meta"),
    fetchCandles: (symbol, timeframe, limit = 24) => requestJson(
      `/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=${limit}`,
    ),
    fetchDashboard: () => Promise.allSettled([
      requestJson("/api/state"),
      requestJson("/api/health"),
      requestJson("/api/recent-events"),
      requestJson("/api/trades?limit=30"),
      requestJson("/api/dashboard-detail"),
    ]),
  };
}
