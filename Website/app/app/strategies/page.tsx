"use client";

import { ArrowRight, CheckCircle2, FlaskConical, LockKeyhole, Plus, Trophy } from "lucide-react";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { PageHeading } from "@/components/page-heading";
import { StatusPill } from "@/components/status-pill";
import { api, csrfHeaders, Catalog, formatNumber, StrategyCandidate, StrategyPool, StrategyPoolsResponse } from "@/lib/api";
import { useCatalog, useMe } from "@/lib/hooks";

export default function StrategiesPage() {
  const { data: user } = useMe();
  const { data: catalog } = useCatalog();
  const { data, error, isLoading, mutate } = useSWR<StrategyPoolsResponse>(
    "/api/v1/strategy-pools",
    api,
    { refreshInterval: 30000 },
  );
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [addFor, setAddFor] = useState<string | null>(null);
  const [promotionFor, setPromotionFor] = useState<string | null>(null);
  const [tradePin, setTradePin] = useState("");

  const addableByPair = useMemo(() => {
    const result = new Map<string, Catalog["presets"]>();
    for (const pool of data?.pools ?? []) {
      const existing = new Set(pool.candidates.map((candidate) => candidate.preset_id));
      result.set(
        pool.symbol,
        (catalog?.presets ?? []).filter((preset) =>
          normalizeSymbol(preset.symbol) === pool.symbol
          && preset.target_id === pool.target_id
          && preset.certification_status === "certified"
          && !existing.has(preset.id),
        ),
      );
    }
    return result;
  }, [catalog?.presets, data?.pools]);

  async function addCandidate(pool: StrategyPool, presetId: string) {
    if (!user || !presetId) return;
    setBusy(`add:${pool.symbol}`);
    setMessage("");
    try {
      await api(`/api/v1/strategy-pools/${encodeURIComponent(pool.symbol)}/candidates`, {
        method: "POST",
        headers: csrfHeaders(user),
        body: JSON.stringify({ preset_id: presetId }),
      });
      setAddFor(null);
      setMessage(`${presetId} joined the ${pool.symbol} arena.`);
      await mutate();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Unable to add strategy");
    } finally {
      setBusy("");
    }
  }

  async function promote(pool: StrategyPool, challenger: StrategyCandidate) {
    if (!user || !tradePin) return;
    setBusy(`promote:${pool.symbol}`);
    setMessage("");
    try {
      const result = await api<{ requires_live_reapproval?: boolean }>(
        `/api/v1/strategy-pools/${encodeURIComponent(pool.symbol)}/promote`,
        {
          method: "POST",
          headers: csrfHeaders(user),
          body: JSON.stringify({ challenger_id: challenger.preset_id, trade_pin: tradePin }),
        },
      );
      setPromotionFor(null);
      setTradePin("");
      setMessage(result.requires_live_reapproval
        ? "Champion changed safely. Review and activate Live again when ready."
        : `${challenger.label} is now Champion for ${pool.symbol}.`);
      await mutate();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Promotion failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="page-wrap">
      <PageHeading
        eyebrow="Strategy arena"
        title="One champion per pair"
        description="Certified strategies are tracked per pair. Evaluator results can nominate a Challenger; only the Champion can be Live."
      />
      {message && <div className="setup-banner" role="status"><span><CheckCircle2 size={20} /><span><strong>{message}</strong><small>Promotion is manual and waits for a safe, flat handoff.</small></span></span></div>}
      {isLoading && <ArenaEmpty title="Loading strategy pools" copy="Reading the certified candidates for this workspace." />}
      {error && <ArenaEmpty title="Strategy arena unavailable" copy="The control plane could not load the current pools." />}
      {!isLoading && !error && !(data?.pools.length) && <ArenaEmpty title="No strategy pools yet" copy="Save a certified pair in Settings to create its first arena." />}
      <div className="strategy-arena-grid">
        {data?.pools.map((pool) => {
          const champion = pool.candidates.find((candidate) => candidate.preset_id === pool.champion_id) ?? null;
          const challengers = pool.candidates.filter((candidate) => candidate.preset_id !== pool.champion_id);
          const addable = addableByPair.get(pool.symbol) ?? [];
          return <section className="strategy-arena-pool card" key={pool.symbol}>
            <div className="strategy-arena-heading">
              <div><span className="eyebrow">Pair arena</span><h2>{pool.symbol}</h2><p>{pool.target_id} · {pool.candidates.length}/{data.max_candidates} strategies</p></div>
              <StatusPill label="Manual promotion" tone="neutral" />
            </div>
            {champion && <CandidateCard candidate={champion} champion liveEnabled={data.tenant_live_status === "active"} />}
            <div className="strategy-arena-divider"><span>Challengers</span><span>same pair · paper mode</span></div>
            <div className="strategy-challenger-list">
              {challengers.map((candidate) => <CandidateCard key={candidate.preset_id} candidate={candidate} />)}
              {!challengers.length && <p className="strategy-arena-empty">Add another certified strategy to start the comparison.</p>}
            </div>
            {addable.length > 0 && <div className="strategy-arena-add">
              {addFor === pool.symbol ? <div className="strategy-arena-add-form"><select aria-label={`Certified strategy for ${pool.symbol}`} defaultValue="" onChange={(event) => { void addCandidate(pool, event.target.value); }} disabled={busy === `add:${pool.symbol}`}><option value="" disabled>Choose a certified strategy</option>{addable.map((preset) => <option value={preset.id} key={preset.id}>{preset.label}</option>)}</select><button type="button" className="button-secondary" onClick={() => setAddFor(null)}>Cancel</button></div> : <button type="button" className="button-secondary" onClick={() => setAddFor(pool.symbol)}><Plus size={15} />Add certified challenger</button>}
            </div>}
            {challengers.map((challenger) => challenger.eligible_for_promotion && <div className="strategy-promotion-row" key={`promotion-${challenger.preset_id}`}>
              <div><strong>{challenger.label}</strong><span>is ready to challenge the current Champion</span></div>
              {promotionFor === `${pool.symbol}:${challenger.preset_id}` ? <div className="strategy-promotion-form"><input aria-label="Trade PIN" type="password" inputMode="numeric" maxLength={12} placeholder="Trade PIN" value={tradePin} onChange={(event) => setTradePin(event.target.value)} /><button type="button" className="button-primary" disabled={busy === `promote:${pool.symbol}` || !tradePin} onClick={() => void promote(pool, challenger)}>Confirm</button><button type="button" className="button-secondary" onClick={() => setPromotionFor(null)}>Cancel</button></div> : <button type="button" className="button-primary" onClick={() => setPromotionFor(`${pool.symbol}:${challenger.preset_id}`)}><Trophy size={15} />Promote</button>}
            </div>)}
            {pool.promotion && <p className="field-help strategy-promotion-note"><ArrowRight size={14} /> Last change: {String(pool.promotion.from ?? "—")} → {String(pool.promotion.to ?? "—")} ({String(pool.promotion.status ?? "recorded").replaceAll("_", " ")})</p>}
          </section>;
        })}
      </div>
    </div>
  );
}

function CandidateCard({ candidate, champion = false, liveEnabled = false }: { candidate: StrategyCandidate; champion?: boolean; liveEnabled?: boolean }) {
  const evaluation = candidate.evaluation ?? {};
  const score = Number(evaluation.score);
  const scoreLabel = Number.isFinite(score) && score > -999 ? formatNumber(score, 1) : "—";
  const tone = candidate.certification_status === "certified" ? "good" : "warn";
  const liveLabel = champion && candidate.mode === "live"
    ? (liveEnabled ? "LIVE" : "LIVE READY")
    : candidate.mode === "shadow" && candidate.shadow_runtime_status === "not_connected"
      ? "NOT RUNNING"
      : candidate.status;
  return <article className={champion ? "strategy-candidate champion" : "strategy-candidate"}>
    <div className="strategy-candidate-top"><div><span className="strategy-candidate-role">{champion ? <Trophy size={14} /> : <FlaskConical size={14} />} {champion ? "Champion" : "Challenger"}</span><h3>{candidate.label}</h3><p>{candidate.strategy.replaceAll("_", " ")} · {candidate.mode === "live" ? "Live lane" : "Shadow SIM"}</p></div><StatusPill label={liveLabel} tone={champion && candidate.mode === "live" ? "warn" : tone} /></div>
    <div className="strategy-candidate-metrics"><span><small>Score</small><strong>{scoreLabel}</strong></span><span><small>PF</small><strong>{formatNumber(evaluation.profit_factor, 2)}</strong></span><span><small>Max DD</small><strong>{formatNumber(evaluation.max_drawdown_pct, 1)}%</strong></span><span><small>Trades</small><strong>{formatNumber(evaluation.trades, 0)}</strong></span></div>
    <div className="strategy-candidate-footer"><span className={candidate.certification_status === "certified" ? "positive" : "negative"}>{candidate.certification_status === "certified" ? "Certificate passed" : "Certificate gate required"}</span><span>{evaluation.source === "forward_sim" ? `${formatNumber(evaluation.forward_days, 0)}d forward SIM` : "Certificate baseline"}</span></div>
    {candidate.mode === "shadow" && candidate.shadow_runtime_status === "not_connected" && <p className="field-help strategy-candidate-reason">Shadow runner is not connected; this card is registry state only.</p>}
    {!champion && candidate.eligibility_reasons.length > 0 && <p className="strategy-candidate-reason"><LockKeyhole size={13} /> {candidate.eligibility_reasons[0]}</p>}
  </article>;
}

function ArenaEmpty({ title, copy }: { title: string; copy: string }) {
  return <section className="card strategy-arena-empty-state"><FlaskConical size={25} /><div><h2>{title}</h2><p>{copy}</p></div></section>;
}

function normalizeSymbol(symbol: string) {
  return symbol.toUpperCase().replace(/[^A-Z0-9]/g, "");
}
