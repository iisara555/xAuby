import { formatNumber, valueAt } from "@/lib/api";

type DataRecord = Record<string, unknown>;

function rows(value: unknown): DataRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is DataRecord => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

function sentence(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "";
  return /[.!?…]$/.test(text) ? text : `${text}.`;
}

function displayValue(value: unknown): string {
  if (typeof value === "number") return formatNumber(value, 2);
  const text = String(value ?? "").trim();
  return text || "—";
}

export type StrategyFact = {
  label: string;
  value: string;
  ok?: boolean;
};

export function strategyName(state: DataRecord): string {
  return String(
    valueAt(state, "signal_meta", "strategy_name")
      ?? valueAt(state, "strategy_name")
      ?? valueAt(state, "indicator_display", "strategy")
      ?? "",
  ).toLowerCase();
}

export function strategyFacts(state: DataRecord): StrategyFact[] {
  const panel = rows(valueAt(state, "indicator_display", "panel_items"));
  if (panel.length) {
    return panel.slice(0, 3).map((item) => ({
      label: String(item.label ?? item.key ?? "Indicator"),
      value: displayValue(item.value),
      ...(typeof item.ok === "boolean" ? { ok: item.ok } : {}),
    }));
  }

  const name = strategyName(state);
  if (name === "xauby_actionzone" || name === "cdc_action_zone") {
    return [
      { label: "Zone", value: displayValue(valueAt(state, "indicators", "cdc_zone_4h")) },
      { label: "EMA12", value: displayValue(valueAt(state, "indicators", "ema_fast_4h")) },
      { label: "EMA26", value: displayValue(valueAt(state, "indicators", "ema_slow_4h")) },
    ];
  }
  if (name === "supertrend_ema200") {
    return [
      { label: "ST Zone", value: Boolean(valueAt(state, "indicators", "supertrend_bull")) ? "BULL" : "BEAR" },
      { label: "SuperTrend", value: displayValue(valueAt(state, "indicators", "supertrend_line")) },
      { label: "EMA200", value: displayValue(valueAt(state, "indicators", "ema")) },
    ];
  }
  return [];
}

export function strategyMarker(state: DataRecord): string {
  const fact = strategyFacts(state).find((item) => item.label.toLowerCase().includes("zone"));
  return fact?.value ?? "—";
}

export function marketSummary(state: DataRecord, stale: boolean, loading: boolean): string {
  if (loading || !Object.keys(state).length) return "Reading the market…";
  if (stale) return "Market data delayed.";

  const name = strategyName(state);
  if (name === "xauby_actionzone" || name === "cdc_action_zone") {
    switch (String(valueAt(state, "indicators", "cdc_zone_4h") ?? "").toUpperCase()) {
      case "GREEN": return "Bullish momentum.";
      case "BLUE":
      case "LBLUE": return "Recovery forming.";
      case "YELLOW":
      case "ORANGE": return "Momentum fading.";
      case "RED": return "Bearish pressure.";
    }
  }

  if (name === "supertrend_ema200") {
    const bullish = Boolean(valueAt(state, "indicators", "supertrend_bull"));
    const price = Number(valueAt(state, "current_price"));
    const ema200 = Number(valueAt(state, "indicators", "ema"));
    const hasTrendFilter = Number.isFinite(price) && Number.isFinite(ema200);
    if (bullish && hasTrendFilter && price > ema200) return "Bullish above EMA200.";
    if (!bullish && hasTrendFilter && price < ema200) return "Bearish below EMA200.";
    if (bullish) return "SuperTrend bullish; EMA200 unconfirmed.";
    return "SuperTrend bearish; EMA200 unconfirmed.";
  }

  const status = valueAt(state, "signal_meta", "status_summary");
  if (String(status ?? "").trim()) return sentence(status);
  const trend = String(valueAt(state, "regime", "trend") ?? "").toUpperCase();
  if (trend.includes("BULL")) return "Bullish market structure.";
  if (trend.includes("BEAR")) return "Bearish market structure.";
  return "Waiting for strategy confirmation.";
}
