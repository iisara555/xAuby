import { CheckCircle2, CircleDashed, ShieldCheck } from "lucide-react";
import { StatusPill } from "@/components/status-pill";
import { valueAt } from "@/lib/api";

export function SignalDetail({ state, stale }: { state: Record<string, unknown>; stale?: boolean }) {
  const meta = (valueAt(state, "signal_meta") as Record<string, unknown> | undefined) ?? {};
  const regimeData = (valueAt(state, "regime") as Record<string, unknown> | undefined) ?? {};
  const checklist = Array.isArray(meta.checklist) ? meta.checklist as Array<Record<string, unknown>> : [];
  return <article className="card signal-detail" id="signal">
    <div className="section-heading"><div><span>Decision support</span><h2>Signal detail</h2></div><StatusPill label={stale ? "Delayed" : "Fresh"} tone={stale ? "warn" : "good"} /></div>
    <div className="signal-detail-lead"><div><small>Current action</small><strong>{String(meta.action ?? "WAIT")}</strong></div><p>{String(meta.reason ?? meta.status_summary ?? "Waiting for a confirmed strategy state.")}</p></div>
    <div className="signal-facts"><span>Confidence<strong>{String(meta.confidence ?? "—")}</strong></span><span>Regime<strong>{String(regimeData.regime ?? "—").replaceAll("_", " ")}</strong></span><span>Trend<strong>{String(regimeData.trend ?? "—").replaceAll("_", " ")}</strong></span><span>Volatility<strong>{String(regimeData.volatility ?? "—").replaceAll("_", " ")}</strong></span></div>
    {checklist.length ? <ul className="compact-checklist">{checklist.map((item, index) => <li key={String(item.id ?? item.label ?? index)}>{item.passed ? <CheckCircle2 /> : <CircleDashed />}<span><strong>{String(item.label ?? item.name ?? "Strategy check")}</strong><small>{String(item.detail ?? item.reason ?? "")}</small></span></li>)}</ul> : <div className="signal-protection"><ShieldCheck /><span><strong>Risk gates remain active</strong><small>Position cap, feed health and Live certification are checked again before execution.</small></span></div>}
  </article>;
}
