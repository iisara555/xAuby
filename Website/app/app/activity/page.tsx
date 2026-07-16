"use client";

import { Activity as ActivityIcon, CircleCheck, Info, TriangleAlert } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { PageHeading } from "@/components/page-heading";
import { api, formatNumber, TradeLog } from "@/lib/api";
import { useSnapshot } from "@/lib/hooks";

type ActivityData = { events: Array<Record<string, unknown>> };

export default function ActivityPage() {
  const [tab, setTab] = useState<"trades" | "events">("trades");
  const [outcome, setOutcome] = useState("all");
  const { data: snapshot } = useSnapshot();
  const symbol = String(snapshot?.state?.focus_symbol ?? "XAUUSDT");
  const { data: activity } = useSWR<ActivityData>(`/api/v1/runtime/activity?symbol=${symbol}&limit=50`, api, { refreshInterval: 15000 });
  const { data: trades } = useSWR<TradeLog>(`/api/v1/runtime/trades?outcome=${outcome}&limit=50`, api, { refreshInterval: 15000 });
  const events = [...(activity?.events ?? [])].reverse();
  return <div className="page-wrap">
    <PageHeading eyebrow="Audit trail" title="Activity & trades" />
    <div className="activity-tabs" role="tablist"><button className={tab === "trades" ? "selected" : ""} onClick={() => setTab("trades")}>Trade log</button><button className={tab === "events" ? "selected" : ""} onClick={() => setTab("events")}>Engine events</button></div>
    {tab === "trades" ? <>
      <section className="trade-summary"><div><span>Closed trades</span><strong>{trades?.summary.total ?? 0}</strong></div><div><span>Net PnL</span><strong className={Number(trades?.summary.net_pnl) >= 0 ? "positive" : "negative"}>{formatNumber(trades?.summary.net_pnl)} USDT</strong></div><div><span>Win rate</span><strong>{formatNumber(trades?.summary.win_rate, 1)}%</strong></div><div><span>Total fees</span><strong>{formatNumber(trades?.summary.fees)} USDT</strong></div></section>
      <article className="card trade-log-card">
        <div className="section-heading"><div><span>All configured symbols</span><h2>Closed positions</h2></div><select aria-label="Filter by outcome" value={outcome} onChange={(event) => setOutcome(event.target.value)}><option value="all">All outcomes</option><option value="win">Wins</option><option value="loss">Losses</option><option value="breakeven">Breakeven</option></select></div>
        {trades?.items.length ? <div className="trade-table-wrap"><table className="trade-table"><thead><tr><th>Closed</th><th>Symbol</th><th>Side</th><th>Entry → Exit</th><th>Quantity</th><th>Fees</th><th>Net PnL</th><th>Source</th><th>Trigger</th></tr></thead><tbody>{trades.items.map((item) => { const confirmed = Boolean(Number(item.pnl_confirmed ?? 0)); const source = String(item.pnl_source ?? "engine"); return <tr key={String(item.id)}><td>{formatTime(item.closed_at)}</td><td><strong>{String(item.symbol)}</strong></td><td>{String(item.side ?? "—")}</td><td>{formatNumber(item.entry_price)} → {formatNumber(item.exit_price)}</td><td>{formatNumber(item.amount, 6)}</td><td>{formatNumber(item.total_fees)}</td><td className={Number(item.net_pnl) >= 0 ? "positive" : "negative"}>{formatNumber(item.net_pnl)}</td><td><span className={confirmed && source.includes("positions_history") ? "source-badge verified" : "source-badge"}>{confirmed && source.includes("positions_history") ? "OKX verified" : source.replaceAll("_", " ")}</span></td><td>{String(item.trigger ?? "—")}</td></tr>; })}</tbody></table></div> : <Empty title="No closed trades yet" copy="Closed simulation and Live positions will appear here across all symbols." />}
      </article>
    </> : <article className="card activity-card">
      <div className="section-heading"><div><span>Engine timeline</span><h2>Recent decisions and events</h2></div><span className="event-count">{events.length}</span></div>
      {events.length ? <ol className="timeline">{events.map((item, index) => { const level = String(item.level ?? item.severity ?? "info").toLowerCase(); const Icon = level.includes("error") ? TriangleAlert : level.includes("success") ? CircleCheck : Info; return <li key={String(item.id ?? item.timestamp ?? index)}><span className={`timeline-icon ${level}`}><Icon size={16} /></span><div><strong>{String(item.event ?? item.type ?? item.title ?? "Engine update").replaceAll("_", " ")}</strong><p>{String(item.message ?? item.reason ?? "Strategy state refreshed")}</p><time>{formatTime(item.timestamp ?? item.created_at)}</time></div></li>; })}</ol> : <Empty title="No activity yet" copy="Events appear after the engine starts monitoring your preset." />}
    </article>}
  </div>;
}

function Empty({ title, copy }: { title: string; copy: string }) { return <div className="empty-state"><ActivityIcon size={28} /><h2>{title}</h2><p>{copy}</p></div>; }

function formatTime(value: unknown) {
  if (!value) return "—";
  const numeric = Number(value);
  const date = new Date(Number.isFinite(numeric) ? (numeric < 1e12 ? numeric * 1000 : numeric) : String(value));
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
