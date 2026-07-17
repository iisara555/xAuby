"use client";

import { Activity, Gauge, ShieldAlert } from "lucide-react";
import { StatusPill } from "@/components/status-pill";
import { formatNumber, valueAt } from "@/lib/api";

type DataRecord = Record<string, unknown>;

function asRecord(value: unknown): DataRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as DataRecord
    : {};
}

function asList(value: unknown): DataRecord[] {
  return Array.isArray(value) ? value.filter((item): item is DataRecord => Boolean(item && typeof item === "object")) : [];
}

function label(value: unknown, fallback = "—"): string {
  const raw = String(value ?? "").trim();
  return raw ? raw.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase()) : fallback;
}

function scorePercent(value: unknown): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  const percent = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return Math.max(-100, Math.min(100, percent));
}

function scoreText(value: unknown): string {
  const percent = scorePercent(value);
  return percent == null ? "—" : `${percent > 0 ? "+" : ""}${formatNumber(percent, 0)}%`;
}

function tone(value: unknown): "good" | "warn" | "bad" | "neutral" {
  const raw = String(value ?? "").toUpperCase();
  if (raw.includes("BULL") || raw.includes("SUPPORT") || raw.includes("RISK_ON") || raw.includes("RISK ON")) return "good";
  if (raw.includes("BEAR") || raw.includes("BLOCK") || raw.includes("PANIC") || raw.includes("RISK_OFF") || raw.includes("RISK OFF")) return "warn";
  if (raw.includes("ERROR") || raw.includes("FAIL")) return "bad";
  return "neutral";
}

function metricValue(value: unknown, digits = 1): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? formatNumber(parsed, digits) : "—";
}

function factorWidth(value: unknown): string {
  const percent = scorePercent(value);
  return `${percent == null ? 0 : Math.min(100, Math.abs(percent))}%`;
}

export function MarketContext({ state, stale }: { state: DataRecord; stale?: boolean }) {
  const regime = asRecord(valueAt(state, "regime"));
  const macro = asRecord(valueAt(state, "macro_guard"));
  const strategyBias = asRecord(valueAt(regime, "strategy_bias"));
  const features = asRecord(valueAt(regime, "features"));
  const reasons = asList(valueAt(regime, "reasons"));
  const confidence = scorePercent(regime.confidence);
  const macroScore = scorePercent(macro.score);
  const macroBias = label(regime.macro_bias);
  const guardEnabled = Boolean(macro.enabled);
  const guardBlocked = Boolean(macro.blocks_buy);
  const alignment = label(features.macro_alignment);
  const conviction = scoreText(features.macro_conviction);
  const strategyPosture = label(strategyBias.posture);
  const factors = [
    { name: "DXY", score: macro.dxy_score, detail: macro.dxy_price == null ? "No price" : `Index ${metricValue(macro.dxy_price, 2)}` },
    { name: "Rates", score: macro.fred_score, detail: macro.fred_rate == null ? "No rate" : `${metricValue(macro.fred_rate, 2)}%` },
    { name: "News", score: macro.news_score, detail: String(macro.news_reason || "No headline signal") },
  ];

  return (
    <article className="card market-context-card" aria-labelledby="market-context-title">
      <div className="card-kicker">
        <span><Gauge size={15} /> Market regime &amp; macro</span>
        <span>{stale ? "Delayed" : String(valueAt(state, "primary_timeframe") ?? "Live").toUpperCase()}</span>
      </div>
      <div className="context-grid">
        <section className="context-column" aria-labelledby="market-context-title">
          <div className="context-heading">
            <div><span>Market regime</span><h2 id="market-context-title">{label(regime.regime)}</h2></div>
            <StatusPill label={confidence == null ? "No score" : `${formatNumber(confidence, 0)}%`} tone={tone(regime.regime)} />
          </div>
          <p className="context-summary">{label(regime.phase)} phase · {label(regime.trend)} trend · {label(regime.volatility_state ?? regime.volatility)} volatility.</p>
          <div className="context-facts">
            <span><small>Trend strength</small><strong>{label(regime.trend_strength)}</strong></span>
            <span><small>Liquidity</small><strong>{label(regime.liquidity_state)}</strong></span>
            <span><small>Transition risk</small><strong>{label(regime.transition_risk)}</strong></span>
            <span><small>Risk state</small><strong>{label(regime.risk_state)}</strong></span>
          </div>
          <div className="context-meter" aria-label={`Regime confidence ${confidence == null ? 0 : confidence}%`}>
            <div className="context-meter-head"><span>Regime confidence</span><strong>{confidence == null ? "—" : `${formatNumber(confidence, 0)}%`}</strong></div>
            <div className="context-meter-track"><span style={{ width: `${confidence == null ? 0 : confidence}%` }} /></div>
          </div>
          <div className="context-reasons">
            {reasons.slice(0, 4).map((item, index) => (
              <span className={item.supportive ? "supportive" : "caution"} key={String(item.label ?? index)}>
                <i />{String(item.label ?? "Market input")}
              </span>
            ))}
            {!reasons.length && <span className="context-empty">Waiting for regime inputs.</span>}
          </div>
        </section>

        <section className="context-column macro-context" aria-labelledby="macro-bias-title">
          <div className="context-heading">
            <div><span>Macro bias</span><h2 id="macro-bias-title">{macroBias}</h2></div>
            <StatusPill label={guardBlocked ? "Buy blocked" : guardEnabled ? "Monitoring" : "Disabled"} tone={guardBlocked ? "warn" : guardEnabled ? "good" : "neutral"} />
          </div>
          <p className="context-summary">{String(macro.summary || macro.news_reason || "Macro inputs are not available yet.")}</p>
          <div className="context-facts">
            <span><small>Guard</small><strong>{guardBlocked ? "Blocking" : guardEnabled ? "Watching" : "Off"}</strong></span>
            <span><small>Alignment</small><strong>{alignment}</strong></span>
            <span><small>Macro score</small><strong>{scoreText(macro.score)}</strong></span>
            <span><small>Conviction</small><strong>{conviction}</strong></span>
          </div>
          <div className="macro-factors" aria-label="Macro factor scores">
            {factors.map((factor) => (
              <div className="macro-factor" key={factor.name}>
                <div><span>{factor.name}</span><strong>{scoreText(factor.score)}</strong></div>
                <div className="macro-factor-track"><span style={{ width: factorWidth(factor.score) }} /></div>
                <small>{factor.detail}</small>
              </div>
            ))}
          </div>
          <div className="strategy-bias">
            <div><Activity size={15} /><span><small>Strategy posture</small><strong>{strategyPosture} · {label(strategyBias.family)}</strong></span></div>
            <span className="strategy-actions"><ShieldAlert size={13} />{Array.isArray(strategyBias.allowed_actions) && strategyBias.allowed_actions.length ? strategyBias.allowed_actions.join(" · ") : "No action guidance"}</span>
          </div>
        </section>
      </div>
    </article>
  );
}
