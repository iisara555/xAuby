"use client";

import { Activity as ActivityIcon, ArrowRight, BarChart3, Bot as BotIcon, Pause, Play, Radio, ShieldCheck, TrendingUp, WalletCards, WifiOff } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { CandleChart } from "@/components/candle-chart";
import { SignalDetail } from "@/components/signal-detail";
import { TradeDrawer } from "@/components/trade-drawer";
import { PageHeading } from "@/components/page-heading";
import { StatusPill } from "@/components/status-pill";
import { useCurrentUser } from "@/components/app-shell";
import { api, csrfHeaders, formatNumber, valueAt } from "@/lib/api";
import { useBot, useCatalog, useProfile, useSnapshot } from "@/lib/hooks";

export default function DashboardPage() {
  const user = useCurrentUser();
  const { data: bot, mutate: mutateBot } = useBot();
  const { data: snapshot } = useSnapshot();
  const { data: catalog } = useCatalog();
  const { data: profile } = useProfile();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const running = bot?.tenant.status === "running";
  const state = snapshot?.state ?? {};
  const symbol = String(valueAt(state, "focus_symbol") ?? valueAt(state, "symbol") ?? "XAUUSDT");
  const focus = (valueAt(state, "by_symbol", symbol) as Record<string, unknown> | undefined) ?? state;
  const position = (valueAt(focus, "position") as Record<string, unknown> | undefined) ?? (valueAt(state, "position") as Record<string, unknown> | undefined) ?? {};
  const equity = valueAt(focus, "total_equity_usdt") ?? valueAt(focus, "equity_breakdown", "portfolio_total_usdt") ?? snapshot?.currency?.equity_usdt;
  const cash = valueAt(focus, "equity_breakdown", "usdt_balance_usdt") ?? valueAt(focus, "portfolio", "USDT") ?? snapshot?.currency?.usdt_balance_usdt;
  const exposure = valueAt(focus, "equity_breakdown", "symbol_exposure_usdt") ?? snapshot?.currency?.symbol_exposure_usdt;
  const positionOpen = String(valueAt(position, "state") ?? "idle") === "bought";
  const lastClosed = (valueAt(focus, "last_closed_trade") as Record<string, unknown> | undefined) ?? (valueAt(state, "last_closed_trade") as Record<string, unknown> | undefined);
  const lastPnlConfirmed = Boolean(lastClosed && Number(valueAt(lastClosed, "pnl_confirmed") ?? 0));
  const pnl = Number(positionOpen ? valueAt(position, "unrealized_pnl") ?? 0 : valueAt(lastClosed ?? {}, "net_pnl") ?? 0);
  const side = positionOpen ? String(valueAt(position, "position_side") ?? "FLAT").toUpperCase() : "FLAT";
  const signal = String(valueAt(focus, "signal_meta", "action") ?? "WAIT");
  const displaySignal = !positionOpen && signal.toUpperCase() === "HOLD" ? "WAIT" : signal;
  const reason = String(valueAt(focus, "signal_meta", "reason") ?? "Waiting for the next confirmed strategy state.");
  const regime = String(valueAt(focus, "regime", "regime") ?? "UNKNOWN");
  const zone = String(valueAt(focus, "indicators", "cdc_zone_4h") ?? "—");
  const price = valueAt(focus, "current_price") ?? valueAt(position, "mark_price");
  const pct24h = Number(valueAt(focus, "price_change_24h_pct") ?? valueAt(focus, "percent_change_24h") ?? 0);
  const riskState = String(valueAt(focus, "regime", "risk_state") ?? "—");
  const events = Array.isArray(valueAt(focus, "recent_events")) ? valueAt(focus, "recent_events") as Array<Record<string, unknown>> : [];
  const equityNumber = Number(equity);
  const cashNumber = Number(cash);
  const exposureNumber = Number(exposure);
  const totalForAllocation = Number.isFinite(equityNumber) && equityNumber > 0 ? equityNumber : 0;
  const cashPct = totalForAllocation ? Math.min(100, Math.max(0, (cashNumber / totalForAllocation) * 100)) : 0;
  const target = catalog?.targets.find((item) => item.id === bot?.exchange_connection?.target_id);

  async function toggleEngine() {
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/bot/${running ? "stop" : "start"}`, {
        method: "POST",
        headers: csrfHeaders(user),
      });
      await mutateBot();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-wrap">
      <PageHeading
        eyebrow="Pilot workspace"
        title={`Good ${new Date().getHours() < 12 ? "morning" : new Date().getHours() < 18 ? "afternoon" : "evening"}, ${user.display_name || user.email.split("@")[0]}`}
        aside={<div className="heading-actions"><StatusPill label={snapshot?.stale ? "Data delayed" : running ? "Engine online" : "Engine stopped"} tone={snapshot?.stale ? "warn" : running ? "good" : "neutral"} /><TradeDrawer user={user} symbol={symbol} positionOpen={positionOpen} enabled={Boolean(catalog?.features.manual_trading && bot?.exchange_connection)} live={bot?.tenant.live_status === "active"} shortLiveCertified={Boolean(target?.manual_short_live_certified)} engineRunning={running} profileReady={Boolean(profile?.profile)} /></div>}
      />

      {!bot?.exchange_connection && (
        <Link className="setup-banner" href="/app/settings">
          <span><ShieldCheck size={20} /><span><strong>Finish your pilot setup</strong><small>Choose a preset and connect a read/trade-only API key.</small></span></span>
          <ArrowRight size={20} />
        </Link>
      )}

      <section className="dashboard-grid">
        <article className="card hero-card">
          <div className="card-kicker"><span>Portfolio equity</span><span>Live estimate</span></div>
          <div className="hero-value">{Number.isFinite(equityNumber) ? `$${formatNumber(equityNumber)}` : "—"}<small>USDT</small></div>
          <div className={pnl >= 0 ? "metric-change positive" : "metric-change negative"}>
            {positionOpen
              ? `${pnl >= 0 ? "+" : ""}${formatNumber(pnl)} unrealized`
              : lastPnlConfirmed
                ? `${pnl >= 0 ? "+" : ""}${formatNumber(pnl)} realized · OKX verified`
                : "No verified realized PnL yet"}
          </div>
          <div className="hero-foot">
            <div><span>USDT balance</span><strong>{formatNumber(cash)}</strong></div>
            <div><span>24h market move</span><strong className={pct24h >= 0 ? "positive" : "negative"}>{pct24h >= 0 ? "+" : ""}{formatNumber(pct24h)}%</strong></div>
          </div>
        </article>

        <article className="card position-card">
          <div className="card-kicker"><span>Active position</span><Radio size={16} /></div>
          <div className="position-symbol"><strong>{symbol}</strong><StatusPill label={side} tone={side === "FLAT" ? "neutral" : "good"} /></div>
          <dl className="metric-list">
            <div><dt>Entry</dt><dd>{positionOpen ? formatNumber(valueAt(position, "entry_price")) : "—"}</dd></div>
            <div><dt>Mark</dt><dd>{positionOpen ? formatNumber(valueAt(position, "mark_price")) : "—"}</dd></div>
            <div><dt>Quantity</dt><dd>{positionOpen ? formatNumber(valueAt(position, "quantity"), 4) : "—"}</dd></div>
            <div><dt>Leverage</dt><dd>{positionOpen ? `${formatNumber(valueAt(position, "leverage"), 1)}×` : "—"}</dd></div>
          </dl>
        </article>

        <CandleChart symbol={symbol} currentPrice={price} zone={zone} />

        <SignalDetail state={focus} stale={snapshot?.stale} />

        <article className="card portfolio-card">
          <div className="card-kicker"><span><WalletCards size={15} /> Portfolio</span><span>Live balance</span></div>
          <div className="portfolio-total">{Number.isFinite(equityNumber) ? formatNumber(equityNumber) : "—"}<small>USDT equity</small></div>
          <div className="allocation-track" aria-label="Portfolio allocation">
            <span className="allocation-cash" style={{ width: `${cashPct}%` }} />
            <span className="allocation-exposure" style={{ width: positionOpen ? `${Math.max(2, Math.min(100 - cashPct, totalForAllocation ? (exposureNumber / totalForAllocation) * 100 : 0))}%` : "0%" }} />
          </div>
          <div className="allocation-legend"><span><i className="allocation-cash-dot" />USDT cash <strong>{formatNumber(cash)}</strong></span><span><i className="allocation-exposure-dot" />Open exposure <strong>{positionOpen ? formatNumber(exposure) : "—"}</strong></span></div>
          <div className="portfolio-stat-grid">
            <div><span>{positionOpen ? "Unrealized PnL" : "Last realized PnL"}</span><strong className={pnl >= 0 ? "positive" : "negative"}>{lastPnlConfirmed || positionOpen ? `${pnl >= 0 ? "+" : ""}${formatNumber(pnl)}` : "—"}</strong></div>
            <div><span>{positionOpen ? "Fees estimated" : "Realized fees"}</span><strong>{positionOpen ? formatNumber(valueAt(position, "estimated_total_fees")) : lastPnlConfirmed ? formatNumber(valueAt(lastClosed ?? {}, "total_fees")) : "—"}</strong></div>
            <div><span>Margin</span><strong>{positionOpen ? String(valueAt(position, "margin_mode") ?? "—") : "—"}</strong></div>
            <div><span>PnL source</span><strong>{lastPnlConfirmed && !positionOpen ? "OKX verified" : "Tenant"}</strong></div>
          </div>
        </article>

        <article className="card signal-card">
          <div className="card-kicker"><span><BotIcon size={15} /> Strategy pulse</span><span>{symbol}</span></div>
          <div className="signal-orb"><BotIcon size={28} /><span>{displaySignal}</span></div>
          <p>{reason}</p>
          <div className="strategy-mini-grid"><span><small>4H zone</small><strong>{zone}</strong></span><span><small>Regime</small><strong>{regime.replaceAll("_", " ")}</strong></span><span><small>Risk</small><strong>{riskState.replaceAll("_", " ")}</strong></span></div>
          <a href="#signal">Open signal detail <ArrowRight size={16} /></a>
        </article>

        <article className="card market-card">
          <div className="card-kicker"><span><BarChart3 size={15} /> Market snapshot</span><span>{snapshot?.stale ? "Delayed" : "Live"}</span></div>
          <div className="market-price">{formatNumber(price)}<small>USDT</small></div>
          <div className="market-change"><TrendingUp size={16} /><strong className={pct24h >= 0 ? "positive" : "negative"}>{pct24h >= 0 ? "+" : ""}{formatNumber(pct24h)}%</strong><span>24h change</span></div>
          <div className="market-levels"><span><small>24h high</small><strong>{formatNumber(valueAt(focus, "high_24h"))}</strong></span><span><small>24h low</small><strong>{formatNumber(valueAt(focus, "low_24h"))}</strong></span></div>
        </article>

        <article className="card control-card">
          <div className="card-kicker"><span>Engine control</span>{snapshot?.ok ? <StatusPill label="Connected" tone="good" /> : <WifiOff size={17} />}</div>
          <h2>{running ? "Automation is running" : bot?.tenant.status === "queued" ? "Waiting for capacity" : "Automation is paused"}</h2>
          <p>Starting the engine monitors your selected preset. Live orders remain gated separately.</p>
          <button className={running ? "button-secondary wide" : "button-primary wide"} onClick={toggleEngine} disabled={busy || !bot?.exchange_connection}>
            {running ? <Pause size={18} /> : <Play size={18} />}{busy ? "Working…" : running ? "Stop engine" : "Start engine"}
          </button>
          {message && <p className="form-error" role="alert">{message}</p>}
        </article>

        <article className="card recent-card">
          <div className="card-kicker"><span><ActivityIcon size={15} /> Recent activity</span><Link href="/app/activity">View all <ArrowRight size={14} /></Link></div>
          <div className="recent-list">{events.slice(-4).reverse().map((item, index) => <div key={String(item.tick_id ?? item.timestamp ?? index)}><i /><span><strong>{String(item.event ?? item.event_type ?? "Engine update").replaceAll("_", " ")}</strong><small>{String(item.action ?? item.reason ?? "Strategy state refreshed")}</small></span></div>)}</div>
          {!events.length && <p className="section-copy">Events will appear as the tenant engine evaluates the market.</p>}
        </article>
      </section>
    </div>
  );
}
