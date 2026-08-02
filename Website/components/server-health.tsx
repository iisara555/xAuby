"use client";

import { useBot, useSnapshot } from "@/lib/hooks";
import { valueAt } from "@/lib/api";
import { useWorkspacePair } from "@/components/workspace-pair";
import { runtimePairState } from "@/lib/runtime-pair-state";

type Tone = "good" | "warn" | "bad" | "neutral";

function metricTone(value: number | null, warn: number, bad: number): Tone {
  if (value == null) return "neutral";
  if (value >= bad) return "bad";
  if (value >= warn) return "warn";
  return "good";
}

function compactMs(value: number | null): string {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}

export function ServerHealth({ compact = false }: { compact?: boolean }) {
  const { data: bot } = useBot();
  const { data: snapshot } = useSnapshot();
  const { selectedSymbol } = useWorkspacePair();
  const state = snapshot?.state ?? {};
  const symbol = selectedSymbol || String(valueAt(state, "focus_symbol") ?? valueAt(state, "symbol") ?? "");
  const focus = runtimePairState(state, symbol);
  const latency = (valueAt(focus, "latency") as Record<string, unknown> | undefined) ?? {};
  const wsAge = finite(valueAt(latency, "ws_tick_age_ms"));
  const restLatency = finite(valueAt(latency, "api_latency_ms") ?? valueAt(focus, "api_latency_ms"));
  const tickDuration = finite(valueAt(latency, "tick_duration_ms"));
  const engineOnline = bot?.service_status === "active" && bot?.tenant.status === "running";
  const items = [
    { label: "Engine", short: "E", value: engineOnline ? "Online" : bot?.tenant.status ?? "—", tone: engineOnline ? "good" as Tone : "bad" as Tone },
    { label: "WS", short: "WS", value: compactMs(wsAge), tone: metricTone(wsAge, 5000, 15000) },
    { label: "REST", short: "R", value: compactMs(restLatency), tone: metricTone(restLatency, 500, 1200) },
    { label: "Tick", short: "T", value: compactMs(tickDuration), tone: metricTone(tickDuration, 2000, 10000) },
  ];
  return <section className={compact ? "server-health compact" : "server-health"} aria-label="Server status">
    {!compact && <div className="server-health-title"><span>Server status</span><small>State {snapshot?.age_sec == null ? "—" : `${Math.round(snapshot.age_sec)}s`}</small></div>}
    <div className="server-health-grid">{items.map((item) => <span className={`server-health-item health-${item.tone}`} title={`${item.label}: ${item.value}`} key={item.label}><i /><small>{compact ? item.short : item.label}</small><strong>{item.value}</strong></span>)}</div>
  </section>;
}

function finite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
