import { CheckCircle2, CircleDashed, ShieldCheck } from "lucide-react";
import { StatusPill } from "@/components/status-pill";
import { valueAt } from "@/lib/api";

function confidenceLabel(value: unknown): string {
  if (value == null || value === "") return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  const percent = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return `${Math.round(percent)}%`;
}

function checklistDetail(item: Record<string, unknown>): string {
  const primary = String(item.detail ?? item.reason ?? item.value ?? "").trim();
  const hint = String(item.hint ?? "").trim();
  if (primary && hint) return `${primary} · ${hint}`;
  return primary || hint;
}

export function SignalDetail({ state, stale }: { state: Record<string, unknown>; stale?: boolean }) {
  const meta = (valueAt(state, "signal_meta") as Record<string, unknown> | undefined) ?? {};
  const regimeData = (valueAt(state, "regime") as Record<string, unknown> | undefined) ?? {};
  const position = (valueAt(state, "position") as Record<string, unknown> | undefined) ?? {};
  const positionOpen = String(valueAt(position, "state") ?? "idle").toLowerCase() === "bought";
  const rawAction = String(meta.action ?? "WAIT");
  const action = !positionOpen && rawAction.toUpperCase() === "HOLD" ? "WAIT" : rawAction;
  const checklist = Array.isArray(meta.checklist) ? meta.checklist as Array<Record<string, unknown>> : [];
  return <article className="card signal-detail" id="signal">
    <div className="section-heading"><div><span>Decision support</span><h2>Signal detail</h2></div><StatusPill label={stale ? "Delayed" : "Fresh"} tone={stale ? "warn" : "good"} /></div>
    <div className="signal-detail-lead"><div><small>Current action</small><strong>{action}</strong></div><p>{String(meta.reason ?? meta.status_summary ?? "Waiting for a confirmed strategy state.")}</p></div>
    <div className="signal-facts"><span>Confidence<strong>{confidenceLabel(meta.confidence)}</strong></span><span>Regime<strong>{String(regimeData.regime ?? "—").replaceAll("_", " ")}</strong></span><span>Trend<strong>{String(regimeData.trend ?? "—").replaceAll("_", " ")}</strong></span><span>Volatility<strong>{String(regimeData.volatility ?? "—").replaceAll("_", " ")}</strong></span></div>
    {checklist.length ? <ul className="compact-checklist">{checklist.map((item, index) => {
      const passed = typeof item.ok === "boolean" ? item.ok : Boolean(item.passed);
      return <li key={String(item.id ?? item.label ?? index)} className={passed ? "check-pass" : "check-pending"}>{passed ? <CheckCircle2 /> : <CircleDashed />}<span><strong>{String(item.label ?? item.name ?? "Strategy check")}</strong><small>{checklistDetail(item)}</small></span></li>;
    })}</ul> : <div className="signal-protection"><ShieldCheck /><span><strong>Risk gates remain active</strong><small>Position cap, feed health and Live certification are checked again before execution.</small></span></div>}
  </article>;
}
