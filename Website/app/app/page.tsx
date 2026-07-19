"use client";

import { Activity as ActivityIcon, ArrowRight, BarChart3, Bot as BotIcon, Pause, Play, Radio, ShieldCheck, TrendingUp, WalletCards, WifiOff } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { CandleChart } from "@/components/candle-chart";
import { MarketContext } from "@/components/market-context";
import { SignalDetail } from "@/components/signal-detail";
import { TradeDrawer } from "@/components/trade-drawer";
import { PageHeading } from "@/components/page-heading";
import { StatusPill } from "@/components/status-pill";
import { useCurrentUser } from "@/components/app-shell";
import { useWorkspacePair } from "@/components/workspace-pair";
import { api, csrfHeaders, formatNumber, valueAt } from "@/lib/api";
import { useBot, useCatalog, useSnapshot } from "@/lib/hooks";
import { runtimePairState } from "@/lib/runtime-pair-state";
import { marketSummary, strategyFacts, strategyMarker, strategyName } from "@/lib/strategy-presentation";

function numberOrNull(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatBaht(value: unknown, signed = false, digits = 0): string {
  const amount = numberOrNull(value);
  if (amount == null) return "— THB";
  const formatted = Math.abs(amount).toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
  const sign = amount < 0 ? "−" : signed && amount > 0 ? "+" : "";
  return `${sign}฿${formatted} THB`;
}

function formatApproxBaht(value: unknown, signed = false, digits = 0): string {
  const formatted = formatBaht(value, signed, digits);
  return formatted.startsWith("—") ? formatted : `≈ ${formatted}`;
}

function formatSigned(value: unknown, digits = 2): string {
  const parsed = numberOrNull(value);
  if (parsed == null) return "—";
  return `${parsed > 0 ? "+" : ""}${formatNumber(parsed, digits)}`;
}

function formatSignedPercent(value: unknown): string {
  const parsed = numberOrNull(value);
  if (parsed == null) return "—";
  return `${parsed > 0 ? "+" : ""}${formatNumber(parsed, 2)}%`;
}

function optionalPrice(value: unknown): string {
  const parsed = numberOrNull(value);
  return parsed == null || parsed <= 0 ? "—" : formatNumber(parsed);
}

export default function DashboardPage() {
  const user = useCurrentUser();
  const { data: bot, mutate: mutateBot } = useBot();
  const { data: snapshot } = useSnapshot();
  const { data: catalog } = useCatalog();
  const { pairs, selectedPair, selectedSymbol: symbol } = useWorkspacePair();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const running = bot?.tenant.status === "running";
  const state = snapshot?.state ?? {};
  const focus = runtimePairState(state, symbol);
  const pairReady = Object.keys(focus).length > 0;
  const position = (valueAt(focus, "position") as Record<string, unknown> | undefined) ?? {};
  const currency = snapshot?.currency ?? {};
  const equity = valueAt(focus, "total_equity_usdt") ?? valueAt(focus, "equity_breakdown", "portfolio_total_usdt") ?? snapshot?.currency?.equity_usdt;
  const cash = valueAt(focus, "equity_breakdown", "usdt_balance_usdt") ?? valueAt(focus, "portfolio", "USDT") ?? snapshot?.currency?.usdt_balance_usdt;
  const exposure = valueAt(focus, "equity_breakdown", "symbol_exposure_usdt") ?? snapshot?.currency?.symbol_exposure_usdt;
  const positionOpen = String(valueAt(position, "state") ?? "idle") === "bought";
  const lastClosed = valueAt(focus, "last_closed_trade") as Record<string, unknown> | undefined;
  const lastPnlConfirmed = Boolean(lastClosed && Number(valueAt(lastClosed, "pnl_confirmed") ?? 0));
  const pnl = Number(positionOpen ? valueAt(position, "unrealized_pnl") ?? 0 : valueAt(lastClosed ?? {}, "net_pnl") ?? 0);
  const pnlPct = positionOpen
    ? numberOrNull(valueAt(position, "unrealized_pnl_pct") ?? valueAt(position, "pnl_pct"))
    : numberOrNull(valueAt(lastClosed ?? {}, "net_pnl_pct") ?? valueAt(lastClosed ?? {}, "pnl_pct"));
  const side = positionOpen ? String(valueAt(position, "position_side") ?? "FLAT").toUpperCase() : "FLAT";
  const managementMode = String(valueAt(position, "management_mode") ?? "strategy").toLowerCase();
  const signal = String(valueAt(focus, "signal_meta", "action") ?? "WAIT");
  const displaySignal = !positionOpen && signal.toUpperCase() === "HOLD" ? "WAIT" : signal;
  const reason = String(valueAt(focus, "signal_meta", "reason") ?? "Waiting for the next confirmed strategy state.");
  const regime = String(valueAt(focus, "regime", "regime") ?? "UNKNOWN");
  const activeStrategy = strategyName(focus) || selectedPair?.strategy || "";
  const strategyIndicatorFacts = strategyFacts(focus);
  const marketMarker = strategyMarker(focus);
  const pairPreset = catalog?.presets.find((item) => item.id === selectedPair?.id);
  const price = valueAt(focus, "current_price") ?? valueAt(position, "mark_price");
  const pct24h = Number(valueAt(focus, "price_change_24h_pct") ?? valueAt(focus, "percent_change_24h") ?? 0);
  const riskState = String(valueAt(focus, "regime", "risk_state") ?? "—");
  const events = Array.isArray(valueAt(focus, "recent_events")) ? valueAt(focus, "recent_events") as Array<Record<string, unknown>> : [];
  const equityNumber = Number(equity);
  const cashNumber = Number(cash);
  const exposureNumber = Number(exposure);
  const usdThbRate = numberOrNull(valueAt(currency, "usd_thb_rate"));
  const equityThb = valueAt(currency, "equity_thb") ?? (usdThbRate && Number.isFinite(equityNumber) ? equityNumber * usdThbRate : null);
  const cashThb = valueAt(currency, "usdt_balance_thb") ?? (usdThbRate && Number.isFinite(cashNumber) ? cashNumber * usdThbRate : null);
  const exposureThb = valueAt(currency, "symbol_exposure_thb") ?? (usdThbRate && Number.isFinite(exposureNumber) ? exposureNumber * usdThbRate : null);
  const pnlThb = usdThbRate && Number.isFinite(pnl)
    ? pnl * usdThbRate
    : positionOpen
      ? valueAt(currency, "unrealized_pnl_thb")
      : valueAt(lastClosed ?? {}, "net_pnl_thb") ?? valueAt(lastClosed ?? {}, "pnl_thb");
  const totalForAllocation = Number.isFinite(equityNumber) && equityNumber > 0 ? equityNumber : 0;
  const exposurePct = positionOpen && totalForAllocation > 0 && Number.isFinite(exposureNumber)
    ? Math.min(100, Math.max(2, (Math.abs(exposureNumber) / totalForAllocation) * 100))
    : 0;
  const cashPct = totalForAllocation > 0 ? Math.max(0, 100 - exposurePct) : 0;
  const pnlTone = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "neutral";
  const fees = valueAt(position, "estimated_total_fees");
  const funding = valueAt(position, "funding_paid");
  const target = catalog?.targets.find((item) => item.id === bot?.exchange_connection?.target_id);
  const live = bot?.tenant.live_status === "active";
  const shortAvailable = Boolean(
    target?.manual_allowed_sides.includes("short")
    && (!live || target.manual_short_live_certified)
  );

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
        title={<>Good {new Date().getHours() < 12 ? "morning" : new Date().getHours() < 18 ? "afternoon" : "evening"}, {user.display_name || user.email.split("@")[0]}. <span className="page-heading-market">{marketSummary(focus, Boolean(snapshot?.stale), !snapshot)}</span></>}
        aside={<div className="heading-actions"><StatusPill label={snapshot?.stale ? "Data delayed" : running ? "Engine online" : "Engine stopped"} tone={snapshot?.stale ? "warn" : running ? "good" : "neutral"} /><TradeDrawer user={user} symbol={symbol} positionOpen={positionOpen} enabled={Boolean(catalog?.features.manual_trading && bot?.exchange_connection)} live={live} shortAvailable={shortAvailable} marketZone={marketMarker} engineRunning={running} profileReady={pairs.length > 0} /></div>}
      />

      {!bot?.exchange_connection && (
        <Link className="setup-banner" href="/app/settings">
          <span><ShieldCheck size={20} /><span><strong>Finish your pilot setup</strong><small>Choose a preset and connect a read/trade-only API key.</small></span></span>
          <ArrowRight size={20} />
        </Link>
      )}

      <section className={`dashboard-grid pair-view${pairReady ? "" : " pair-pending"}`} id="workspace-pair-view" key={symbol || "workspace"}>
        {!pairReady && (
          <article className="card pair-sync-card" role="status" aria-live="polite">
            <span className="pair-sync-pulse" aria-hidden="true" />
            <div><small>Preparing market workspace</small><h2>Syncing {symbol || "selected pair"}</h2><p>The engine is loading this pair’s own price, position and strategy state. Existing pairs keep running.</p></div>
          </article>
        )}
        <article className="card hero-card">
          <div className="card-kicker"><span>Portfolio equity</span><span>Live estimate</span></div>
          <div className="hero-value">{Number.isFinite(equityNumber) ? `$${formatNumber(equityNumber)}` : "—"}<small>USDT</small></div>
          <div className="hero-secondary-value">
            <strong>{formatApproxBaht(equityThb)}</strong>
            <span>{usdThbRate == null ? "FX rate unavailable" : `FX ${formatNumber(usdThbRate, 2)} THB / USDT`}</span>
          </div>
          <div className={`hero-pnl ${pnlTone}`}>
            <div><span>{positionOpen ? "Unrealized PnL" : "Last realized PnL"}</span><strong>{positionOpen || lastPnlConfirmed ? `${formatSigned(pnl)} USDT` : "—"}</strong></div>
            <div><span>Return</span><strong>{positionOpen || lastPnlConfirmed ? formatSignedPercent(pnlPct) : "—"}</strong></div>
            <small>{positionOpen ? formatApproxBaht(pnlThb, true, 2) : lastPnlConfirmed ? `${target?.label ?? "Exchange"} verified` : "No verified realized PnL yet"}</small>
          </div>
          <div className="hero-allocation">
            <div className="hero-allocation-head"><span>Capital allocation</span><strong>{totalForAllocation ? `${formatNumber(exposurePct, 1)}% deployed` : "Waiting for equity"}</strong></div>
            <div className="hero-allocation-track" role="meter" aria-label="Capital deployed" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(exposurePct)}><span style={{ width: `${exposurePct}%` }} /></div>
            <div className="hero-allocation-legend"><span>Available <b>{totalForAllocation ? `${formatNumber(cashPct, 1)}%` : "—"}</b></span><span>Exposure <b>{positionOpen ? `${formatNumber(exposurePct, 1)}%` : "0%"}</b></span></div>
          </div>
          <div className="hero-foot">
            <div><span>USDT balance</span><strong>{formatNumber(cash)} <small>{formatApproxBaht(cashThb)}</small></strong></div>
            <div><span>24h market move</span><strong className={pct24h >= 0 ? "positive" : "negative"}>{pct24h >= 0 ? "+" : ""}{formatNumber(pct24h)}%</strong></div>
          </div>
        </article>

        <article className="card position-card">
          <div className="card-kicker"><span>Active position</span><Radio size={16} /></div>
          <div className="position-symbol"><strong>{symbol}</strong><StatusPill label={side} tone={side === "FLAT" ? "neutral" : "good"} /></div>
          <div className={`position-pnl ${pnlTone}`}>
            <div><span>{positionOpen ? "Unrealized PnL" : "No open position"}</span><strong>{positionOpen ? `${formatSigned(pnl)} USDT` : "FLAT"}</strong><small>{positionOpen ? formatApproxBaht(pnlThb, true, 2) : "— THB"}</small></div>
            <div className="position-return"><span>Return</span><b>{positionOpen ? formatSignedPercent(pnlPct) : "—"}</b></div>
          </div>
          {positionOpen && managementMode === "strategy_handoff" && <p className="position-management-note">Waiting for the active strategy to align before strategy exits are enabled.</p>}
          <dl className="metric-list">
            <div><dt>Entry</dt><dd>{positionOpen ? formatNumber(valueAt(position, "entry_price")) : "—"}</dd></div>
            <div><dt>Mark</dt><dd>{positionOpen ? formatNumber(valueAt(position, "mark_price")) : "—"}</dd></div>
            <div><dt>Quantity</dt><dd>{positionOpen ? formatNumber(valueAt(position, "quantity"), 4) : "—"}</dd></div>
            <div><dt>Leverage</dt><dd>{positionOpen ? `${formatNumber(valueAt(position, "leverage"), 1)}×` : "—"}</dd></div>
          </dl>
          <div className="position-protection">
            <span><small>Stop loss</small><strong>{positionOpen ? optionalPrice(valueAt(position, "stop_loss")) : "—"}</strong></span>
            <span><small>Take profit</small><strong>{positionOpen ? optionalPrice(valueAt(position, "take_profit")) : "—"}</strong></span>
            <span><small>Fees + funding</small><strong>{positionOpen ? `${formatNumber(fees)} · ${formatNumber(funding)}` : "—"}</strong></span>
            <span><small>Margin</small><strong>{positionOpen ? String(valueAt(position, "margin_mode") ?? "—") : "—"}</strong></span>
          </div>
        </article>

        <CandleChart
          symbol={symbol}
          currentPrice={price}
          strategyName={activeStrategy}
          primaryTimeframe={pairPreset?.primary_timeframe ?? String(valueAt(focus, "primary_timeframe") ?? "4h")}
          confirmTimeframe={pairPreset?.confirm_timeframe ?? String(valueAt(focus, "confirm_timeframe") ?? "")}
          marketMarker={marketMarker}
        />

        <SignalDetail state={focus} stale={snapshot?.stale} />

        <MarketContext state={focus} stale={snapshot?.stale} />

        <article className="card portfolio-card">
          <div className="card-kicker"><span><WalletCards size={15} /> Portfolio</span><span>Live balance</span></div>
          <div className="portfolio-total">{Number.isFinite(equityNumber) ? formatNumber(equityNumber) : "—"}<small>USDT equity</small><strong>{formatApproxBaht(equityThb)}</strong></div>
          <div className="allocation-track" aria-label="Portfolio allocation">
            <span className="allocation-cash" style={{ width: `${cashPct}%` }} />
            <span className="allocation-exposure" style={{ width: `${exposurePct}%` }} />
          </div>
          <div className="allocation-legend"><span><i className="allocation-cash-dot" />USDT cash <strong>{formatNumber(cash)} <small>{formatApproxBaht(cashThb)}</small></strong></span><span><i className="allocation-exposure-dot" />Open exposure <strong>{positionOpen ? `${formatNumber(exposure)} · ${formatApproxBaht(exposureThb)}` : "—"}</strong></span></div>
          <div className="portfolio-stat-grid">
            <div><span>{positionOpen ? "Unrealized PnL" : "Last realized PnL"}</span><strong className={pnlTone}>{lastPnlConfirmed || positionOpen ? `${formatSigned(pnl)} · ${formatSignedPercent(pnlPct)}` : "—"}</strong><small>{positionOpen ? formatApproxBaht(pnlThb, true, 2) : ""}</small></div>
            <div><span>{positionOpen ? "Fees estimated" : "Realized fees"}</span><strong>{positionOpen ? formatNumber(valueAt(position, "estimated_total_fees")) : lastPnlConfirmed ? formatNumber(valueAt(lastClosed ?? {}, "total_fees")) : "—"}</strong></div>
            <div><span>Margin</span><strong>{positionOpen ? String(valueAt(position, "margin_mode") ?? "—") : "—"}</strong></div>
            <div><span>PnL source</span><strong>{lastPnlConfirmed && !positionOpen ? `${target?.label ?? "Exchange"} verified` : "Tenant"}</strong></div>
          </div>
        </article>

        <article className="card signal-card">
          <div className="card-kicker"><span><BotIcon size={15} /> Strategy pulse</span><span>{symbol}</span></div>
          <div className="signal-orb"><BotIcon size={28} /><span>{displaySignal}</span></div>
          <p>{reason}</p>
          <div className="strategy-mini-grid">{strategyIndicatorFacts.length
            ? strategyIndicatorFacts.map((fact) => <span key={fact.label}><small>{fact.label}</small><strong>{fact.value}</strong></span>)
            : <><span><small>Regime</small><strong>{regime.replaceAll("_", " ")}</strong></span><span><small>Risk</small><strong>{riskState.replaceAll("_", " ")}</strong></span></>}
          </div>
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
          <div className="recent-list">{events.slice(-4).reverse().map((item, index) => <div key={`${String(item.tick_id ?? "tick")}-${String(item.timestamp ?? "time")}-${String(item.event ?? item.event_type ?? "event")}-${index}`}><i /><span><strong>{String(item.event ?? item.event_type ?? "Engine update").replaceAll("_", " ")}</strong><small>{String(item.action ?? item.reason ?? "Strategy state refreshed")}</small></span></div>)}</div>
          {!events.length && <p className="section-copy">Events will appear as the tenant engine evaluates the market.</p>}
        </article>
      </section>
    </div>
  );
}
