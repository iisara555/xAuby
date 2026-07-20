"use client";

import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { Check, KeyRound, Radio, ShieldAlert, SlidersHorizontal, UserRound } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { PageHeading } from "@/components/page-heading";
import { StatusPill } from "@/components/status-pill";
import { useCurrentUser } from "@/components/app-shell";
import { api, csrfHeaders } from "@/lib/api";
import { useBot, useCatalog, useProfile } from "@/lib/hooks";
import { ProfileSettings } from "@/components/profile-settings";

const PENDING_BACKTEST = {
  status: "pending" as const,
  score_label: "Pending",
  period: "Not published",
  duration: "—",
  win_rate_pct: null,
  max_drawdown_pct: null,
  trades: null,
  source: "Certified backtest evidence is not available yet",
};

function cleanPin(value: FormDataEntryValue | null): string {
  return typeof value === "string" ? value.trim() : "";
}

function validatePin(value: string, minimumLength = 8, label = "Trade PIN", digitsOnly = true, maximumLength = 12): string {
  if (!value || (digitsOnly && !/^[0-9]+$/.test(value)) || value.length < minimumLength || value.length > maximumLength) {
    return digitsOnly
      ? `${label} must contain 8–12 digits. Do not use the 6-digit Authenticator code.`
      : `${label} must be the previous value, 1–128 characters.`;
  }
  return "";
}

export default function SettingsPage() {
  const user = useCurrentUser();
  const { data: catalog } = useCatalog();
  const { data: bot, mutate } = useBot();
  const { data: profile, mutate: mutateProfile } = useProfile();
  const [targetId, setTargetId] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [focusId, setFocusId] = useState("");
  const [seeded, setSeeded] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveOpen, setLiveOpen] = useState(false);
  const [liveError, setLiveError] = useState("");
  const [nowSeconds, setNowSeconds] = useState<number | null>(null);
  const selectedTarget = useMemo(() => catalog?.targets.find((item) => item.id === targetId), [catalog, targetId]);
  const targetPresets = useMemo(
    () => catalog?.presets.filter((item) => item.target_id === targetId) ?? [],
    [catalog, targetId],
  );
  const maxPairs = catalog?.limits?.configured_pairs ?? 3;
  const savedProfile = profile?.profile ?? null;
  const savedIds = useMemo(
    () => savedProfile?.preset_ids ?? savedProfile?.presets?.map((item) => item.id) ?? [],
    [savedProfile],
  );
  const savedTargetId = savedProfile?.target_id ?? null;
  const addedPresets = useMemo(
    () => catalog?.presets.filter((item) => selectedIds.includes(item.id) && !savedIds.includes(item.id)) ?? [],
    [catalog, savedIds, selectedIds],
  );
  const removedIds = useMemo(
    () => savedIds.filter((id) => !selectedIds.includes(id)),
    [savedIds, selectedIds],
  );
  const focusPreset = useMemo(() => catalog?.presets.find((item) => item.id === focusId), [catalog, focusId]);
  const selectionDirty = useMemo(() => {
    if (!savedProfile) return selectedIds.length > 0;
    const a = [...selectedIds].sort().join(",");
    const b = [...savedIds].sort().join(",");
    return a !== b || focusId !== savedProfile.active_preset_id;
  }, [savedProfile, savedIds, selectedIds, focusId]);
  const exchangeSwitchPending = Boolean(savedTargetId && targetId && targetId !== savedTargetId);
  const livePairAddition = Boolean(
    bot?.tenant.live_status === "active"
    && savedProfile
    && targetId === savedTargetId
    && addedPresets.length > 0
    && removedIds.length === 0
    && addedPresets.every((item) => item.live_certified),
  );
  const addedPairNames = addedPresets.map((item) => item.asset ?? item.symbol.replace(/USDT$/, ""));
  const savedCertified = Boolean(savedProfile?.presets?.some((item) => item.live_certified));
  const selectedPresets = useMemo(
    () => catalog?.presets.filter((item) => selectedIds.includes(item.id)) ?? [],
    [catalog, selectedIds],
  );
  const compiledPairAllocations = useMemo(() => {
    const value = profile?.compiled?.pair_allocations;
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown>
      : {};
  }, [profile?.compiled]);
  const canUseCompiledAllocations = Boolean(
    targetId && targetId === savedTargetId && savedIds.length > 0,
  );
  const allocationForPreset = (item: (typeof selectedPresets)[number]) => {
    const symbol = item.symbol.toUpperCase().replace(/_/g, "");
    const compiled = canUseCompiledAllocations && savedIds.includes(item.id)
      ? compiledPairAllocations[symbol]
      : undefined;
    if (compiled == null) return item.allocation_pct;
    const numeric = Number(compiled);
    return Number.isFinite(numeric) ? numeric : item.allocation_pct;
  };
  const pairAllocationLabel = selectedPresets.length > 0
    ? selectedPresets.map((item) => `${item.asset ?? item.symbol.replace(/USDT$/, "")} ${allocationForPreset(item) ?? "—"}%`).join(" · ")
    : "—";
  const selectedAllocationPct = selectedPresets.reduce(
    (total, item) => total + (Number(allocationForPreset(item)) || 0),
    0,
  );
  const unallocatedCashPct = Math.max(0, 100 - selectedAllocationPct);
  const maxOpenPositions = Number(
    profile?.compiled?.max_open_positions
      ?? profile?.profile?.risk?.max_open_positions
      ?? Math.max(selectedIds.length, 1),
  );
  const dailyLossCapPct = Number(
    profile?.compiled?.max_daily_loss_pct
      ?? profile?.profile?.risk?.max_daily_loss_pct
      ?? 6,
  );
  const dailyTradeCap = Number(profile?.compiled?.max_daily_trades ?? 3);
  const drawdownGuardEnabled = profile?.compiled?.drawdown_guard_enabled !== false;
  const maxDrawdownPct = Number(profile?.compiled?.max_drawdown_pct ?? 25);
  const cdcPure = Boolean(savedProfile?.presets?.some((item) => item.cdc_pure_certified) ?? focusPreset?.cdc_pure_certified);
  const connectionMismatch = Boolean(
    bot?.exchange_connection?.target_id && savedTargetId
    && bot.exchange_connection.target_id !== savedTargetId,
  );
  const exchangeTestFresh = bot?.exchange_connection?.status === "tested"
    && (nowSeconds === null || (bot.exchange_connection.tested_at != null && nowSeconds - bot.exchange_connection.tested_at < 1800));
  const exchangeTestExpired = bot?.exchange_connection?.status === "tested" && nowSeconds !== null && !exchangeTestFresh;

  useEffect(() => {
    if (seeded || !catalog) return;
    const seedTarget = savedTargetId ?? bot?.exchange_connection?.target_id ?? catalog.targets[0]?.id ?? "";
    setTargetId(seedTarget);
    if (savedProfile) {
      setSelectedIds(savedIds);
      setFocusId(savedProfile.active_preset_id ?? savedIds[0] ?? "");
    }
    setSeeded(true);
  }, [bot?.exchange_connection?.target_id, catalog, savedIds, savedProfile, savedTargetId, seeded]);

  function chooseTarget(nextId: string) {
    if (nextId === targetId) return;
    setTargetId(nextId);
    if (nextId === savedTargetId) {
      setSelectedIds(savedIds);
      setFocusId(savedProfile?.active_preset_id ?? savedIds[0] ?? "");
    } else {
      setSelectedIds([]);
      setFocusId("");
    }
  }

  function togglePreset(presetId: string) {
    setSelectedIds((current) => {
      if (current.includes(presetId)) {
        const next = current.filter((id) => id !== presetId);
        if (focusId === presetId) setFocusId(next[0] ?? "");
        return next;
      }
      if (current.length >= maxPairs) return current;
      const next = [...current, presetId];
      if (!focusId) setFocusId(presetId);
      return next;
    });
  }

  useEffect(() => {
    const updateClock = () => setNowSeconds(Math.floor(Date.now() / 1000));
    updateClock();
    const timer = window.setInterval(updateClock, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  function begin() { setBusy(true); setError(""); setMessage(""); setLiveError(""); }
  function fail(reason: unknown) { setError(reason instanceof Error ? reason.message : "Action failed"); setBusy(false); }

  function openLiveDialog() {
    setLiveError("");
    setError("");
    setMessage("");
    setLiveOpen(true);
  }

  async function saveProfile() {
    if (selectedIds.length === 0 || !focusId || !selectedIds.includes(focusId)) return;
    const liveProfileChange = bot?.tenant.live_status === "active" && selectionDirty;
    if (liveProfileChange && !livePairAddition && !window.confirm("This change will stop Live mode and require Live approval again. Continue?")) return;
    if (exchangeSwitchPending && !window.confirm("Switching the exchange replaces your configured pairs and requires connecting new API keys. Continue?")) return;
    begin();
    try {
      const result = await api<{
        mode: "live" | "simulation";
        live_reapproval_required: boolean;
        profile_changed: boolean;
        live_preserved?: boolean;
        hot_reload_eta_seconds?: number | null;
        exchange_switched?: boolean;
        reconnect_required?: boolean;
      }>("/api/v1/profile", {
        method: "PUT", headers: csrfHeaders(user),
        body: JSON.stringify({
          preset_ids: selectedIds,
          active_preset_id: focusId,
          risk: profile?.profile?.risk ?? {},
        }),
      });
      await Promise.all([mutateProfile(), mutate()]);
      if (!result.profile_changed && result.mode === "live") {
        setMessage("Pairs are unchanged. Live mode remains active.");
      } else if (result.live_preserved) {
        setMessage(`${addedPairNames.join(" + ")} activated. Existing pairs stay Live; the new pair joins within about ${result.hot_reload_eta_seconds ?? 30} seconds.`);
      } else if (result.exchange_switched || result.reconnect_required) {
        setMessage("Pairs saved on the new exchange. Connect and test API keys for it before going Live.");
      } else if (result.live_reapproval_required) {
        setMessage("Pairs changed. Live mode was stopped; review and activate Live again when ready.");
      } else {
        setMessage("Pairs saved. The current Simulation/Live mode was kept.");
      }
      setBusy(false);
    } catch (reason) { fail(reason); }
  }

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    begin();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api("/api/v1/exchange/connect", {
        method: "POST", headers: csrfHeaders(user),
        body: JSON.stringify({
          target_id: targetId, api_key: data.get("apiKey"), api_secret: data.get("apiSecret"),
          passphrase: data.get("passphrase") ?? "", withdraw_disabled_attested: data.get("attest") === "on",
        }),
      });
      await mutate();
      setMessage("Credentials encrypted and saved. Run a connection test next.");
      setBusy(false);
      form.reset();
    } catch (reason) { fail(reason); }
  }

  async function testConnection() {
    begin();
    try {
      await api("/api/v1/exchange/test", { method: "POST", headers: csrfHeaders(user) });
      await mutate();
      setNowSeconds(Math.floor(Date.now() / 1000));
      setMessage("Connection test passed. Live activation is available for 30 minutes.");
      setBusy(false);
    } catch (reason) { fail(reason); }
  }

  async function activateLive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    begin();
    const data = new FormData(event.currentTarget);
    const tradePin = cleanPin(data.get("tradePin"));
    const validationError = validatePin(tradePin);
    if (validationError) {
      setLiveError(validationError);
      setBusy(false);
      return;
    }
    try {
      await api("/api/v1/live/activate", {
        method: "POST", headers: csrfHeaders(user),
        body: JSON.stringify({ trade_pin: tradePin, risk_acknowledged: true }),
      });
      await mutate();
      setLiveOpen(false);
      setLiveError("");
      setMessage("Live mode activated. The 1× and stop-loss gates remain enforced.");
      setBusy(false);
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Live activation failed";
      setLiveError(detail.toLowerCase().includes("test the exchange connection again")
        ? "The exchange connection test has expired. Close this window, tap “Test connection”, then review and activate Live again."
        : detail);
      setBusy(false);
    }
  }

  async function deactivateLive() {
    begin();
    try {
      await api("/api/v1/live/deactivate", { method: "POST", headers: csrfHeaders(user) });
      await mutate();
      setMessage("Live mode stopped and credentials removed from runtime memory.");
      setBusy(false);
    } catch (reason) { fail(reason); }
  }

  return (
    <div className="page-wrap narrow-page">
      <PageHeading eyebrow="Workspace" title="Settings" aside={<StatusPill label={bot?.tenant.live_status === "active" ? "Live" : "Simulation"} tone={bot?.tenant.live_status === "active" ? "warn" : "neutral"} />} />
      <Tabs.Root className="settings-tabs" defaultValue="trading">
        <Tabs.List className="tab-list" aria-label="Settings sections">
          <Tabs.Trigger value="profile"><UserRound size={17} />Profile</Tabs.Trigger>
          <Tabs.Trigger value="trading"><SlidersHorizontal size={17} />Trading</Tabs.Trigger>
          <Tabs.Trigger value="exchange"><Radio size={17} />Exchange</Tabs.Trigger>
          <Tabs.Trigger value="security"><KeyRound size={17} />Security</Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="profile" className="settings-panel card">
          <div className="section-heading"><div><span>Personal workspace</span><h2>Name and profile image</h2></div></div>
          <ProfileSettings />
        </Tabs.Content>

        <Tabs.Content value="trading" className="settings-panel card">
          <div className="section-heading"><div><span>Exchange</span><h2>Choose your trading venue</h2></div><StatusPill label={savedTargetId ? `Saved: ${catalog?.targets.find((item) => item.id === savedTargetId)?.label ?? savedTargetId}` : "No exchange saved yet"} tone={savedTargetId ? "good" : "warn"} /></div>
          <p className="section-copy">All configured pairs trade on one exchange. Switching venue replaces your pairs and requires connecting API keys for the new exchange.</p>
          <div className="exchange-grid">
            {catalog?.targets.map((target) => (
              <button type="button" className={targetId === target.id ? "exchange-option selected" : "exchange-option"} onClick={() => chooseTarget(target.id)} key={target.id}>
                <span className="radio-dot">{targetId === target.id && <Check size={13} />}</span>
                <span className="preset-copy"><strong>{target.label}</strong><small>{target.market_type === "swap" ? "Perpetual" : "Spot"} · {catalog.presets.filter((item) => item.target_id === target.id).length} certified presets</small></span>
                <StatusPill label={target.live_certified === false ? "SIM only" : "Live ready"} tone={target.live_certified === false ? "warn" : "good"} />
              </button>
            ))}
          </div>
          {exchangeSwitchPending && <p className="field-help preset-switch-hint" role="status">Switching to <strong>{selectedTarget?.label}</strong> replaces your configured pairs and requires new API keys. Nothing changes until you save.</p>}

          <div className="section-heading pairs-heading"><div><span>Certified pairs</span><h2>Pick up to {maxPairs} pairs</h2></div><StatusPill label={`${selectedIds.length} / ${maxPairs} pairs`} tone={selectedIds.length > 0 ? "good" : "warn"} /></div>
          <p className="section-copy">Each pair runs its own certified strategy preset. Pairs marked <strong>SIM only</strong> trade in simulation even while Live is on. The <strong>Focus</strong> pair is the dashboard default.</p>
          <div className="preset-grid">
            {targetPresets.map((preset) => {
              const backtest = preset.backtest ?? PENDING_BACKTEST;
              const checked = selectedIds.includes(preset.id);
              const saved = savedIds.includes(preset.id) && targetId === savedTargetId;
              return (
                <button type="button" className={checked ? "preset-option selected" : "preset-option"} onClick={() => togglePreset(preset.id)} key={preset.id}>
                  <span className="preset-option-head">
                    <span className="radio-dot">{checked && <Check size={13} />}</span>
                    <span className="preset-copy"><strong>{preset.label}</strong><small>{preset.symbol} · {preset.strategy.replaceAll("_", " ")}</small></span>
                    {preset.live_certified === false ? <StatusPill label="SIM only" tone="warn" /> : saved ? <StatusPill label={bot?.tenant.live_status === "active" ? "Active" : "Saved"} tone="good" /> : checked ? <StatusPill label="Ready" tone="good" /> : null}
                    <em>{preset.confirm_timeframe ? `${preset.primary_timeframe} / ${preset.confirm_timeframe}` : `${preset.primary_timeframe} · single TF`}</em>
                  </span>
                  <span className="preset-backtest">
                    <span><small>Backtest score</small><strong className={`backtest-${backtest.status}`}>{backtest.score_label}</strong></span>
                    <span><small>Tested period</small><strong>{backtest.duration}</strong></span>
                    <span><small>Data window</small><strong>{backtest.period}</strong></span>
                  </span>
                  <span className="preset-evidence">
                    <span>{backtest.win_rate_pct == null ? "WR —" : `WR ${backtest.win_rate_pct}%`}</span>
                    <span>{backtest.max_drawdown_pct == null ? "Max DD —" : `Max DD ${backtest.max_drawdown_pct}%`}</span>
                    <span>{backtest.trades == null ? "Trades —" : `${backtest.trades} trades`}</span>
                    <small>{backtest.source}</small>
                  </span>
                  {preset.strategy_traits && preset.strategy_traits.length > 0 && (
                    <span className="preset-traits">
                      {preset.strategy_traits.map((trait) => <span key={trait}>{trait}</span>)}
                    </span>
                  )}
                  {checked && selectedIds.length > 1 && (
                    <span
                      className={focusId === preset.id ? "focus-toggle active" : "focus-toggle"}
                      role="button"
                      tabIndex={0}
                      onClick={(event) => { event.stopPropagation(); setFocusId(preset.id); }}
                      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.stopPropagation(); setFocusId(preset.id); } }}
                    >
                      {focusId === preset.id ? "Focus pair" : "Set as focus"}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="risk-summary">
            <div><span>Strategy sides</span><strong>{focusPreset?.cdc_pure_certified ? "CDC Pure · " : ""}{(focusPreset?.allowed_sides ?? ["long"]).map((side) => side.toUpperCase()).join(" / ")}</strong></div><div><span>D1 filter</span><strong>{focusPreset?.execution_profile?.use_d1_regime_filter === true ? "On" : focusPreset?.execution_profile?.use_d1_regime_filter === false ? "Off" : "—"}</strong></div><div><span>Partial TP</span><strong>{typeof focusPreset?.execution_profile?.partial_tp_pct === "number" && Number(focusPreset.execution_profile.partial_tp_pct) > 0 ? `${Number(focusPreset.execution_profile.partial_tp_fraction ?? 0) * 100}% @ +${focusPreset.execution_profile.partial_tp_pct}%` : "—"}</strong></div><div><span>Manual sides</span><strong>{selectedTarget?.manual_allowed_sides.map((side) => side.toUpperCase()).join(" / ") ?? "—"}</strong></div><div><span>Exit protection</span><strong>{focusPreset?.cdc_pure_certified ? "CDC signal / ROI" : "Stop loss"}</strong></div><div><span>Pair allocation</span><strong>{pairAllocationLabel}</strong></div><div><span>Unallocated cash</span><strong>{unallocatedCashPct}%</strong></div><div><span>Open positions</span><strong>{maxOpenPositions} max</strong></div><div><span>Daily loss cap</span><strong>{dailyLossCapPct}%</strong></div><div><span>Daily trade cap</span><strong>{dailyTradeCap} trades</strong></div><div><span>Drawdown guard</span><strong>{drawdownGuardEnabled ? `${maxDrawdownPct}%` : "Off"}</strong></div>
          </div>
          {selectionDirty && selectedIds.length > 0 && (
            <p className="field-help preset-switch-hint" role="status">
              {livePairAddition
                ? `${addedPairNames.join(" + ")} is ready to join Live. Existing pairs keep running; activation takes up to about 30 seconds.`
                : `Your pair selection is not saved yet${bot?.tenant.live_status === "active" ? " — this change will stop Live mode until you re-approve it" : ""}.`}
            </p>
          )}
          <button className="button-primary" onClick={saveProfile} disabled={busy || selectedIds.length === 0 || !focusId || !selectionDirty}>
            {selectedIds.length === 0
              ? "Select at least one pair"
              : !selectionDirty
                ? "These pairs are already active"
                : livePairAddition
                  ? `Activate ${addedPairNames.join(" + ")}`
                  : `Save ${selectedIds.length} pair${selectedIds.length > 1 ? "s" : ""}`}
          </button>
        </Tabs.Content>

        <Tabs.Content value="exchange" className="settings-panel card">
          <div className="section-heading"><div><span>API connection</span><h2>{selectedTarget?.label ?? "Select an exchange"}</h2></div>{bot?.exchange_connection && <StatusPill label={bot.exchange_connection.status} tone={bot.exchange_connection.status === "tested" ? "good" : "warn"} />}</div>
          <p className="section-copy">Create a key with read and trade permissions only. Withdraw permission must remain disabled.</p>
          {bot?.api_whitelist_ips && bot.api_whitelist_ips.length > 0 ? (
            <p className="field-help" role="note">Restrict the key to this bot&apos;s server IP{bot.api_whitelist_ips.length > 1 ? "s" : ""}: <strong>{bot.api_whitelist_ips.join(", ")}</strong>. On OKX, add {bot.api_whitelist_ips.length > 1 ? "them" : "it"} under the key&apos;s <strong>Link IP address</strong> restriction so a leaked key still cannot trade from anywhere else.</p>
          ) : (
            <p className="field-help" role="note">If your exchange supports IP restriction, restrict the key to this bot&apos;s egress IP. Ask your operator for the server IP to whitelist.</p>
          )}
          {connectionMismatch && <p className="form-error" role="status">Saved credentials belong to {catalog?.targets.find((item) => item.id === bot?.exchange_connection?.target_id)?.label ?? "another exchange"}. Connect and test keys for {catalog?.targets.find((item) => item.id === savedTargetId)?.label ?? "the new exchange"} before going Live.</p>}
          <form className="form-stack two-column-form" onSubmit={connect}>
            <label>API key<input name="apiKey" required autoComplete="off" /></label>
            <label>API secret<input name="apiSecret" type="password" required autoComplete="new-password" /></label>
            {selectedTarget?.credential_fields.includes("passphrase") && <label className="full-field">Passphrase<input name="passphrase" type="password" required autoComplete="new-password" /></label>}
            <label className="checkbox-field full-field"><input name="attest" type="checkbox" required /><span>I confirm withdraw permission is disabled.</span></label>
            <button className="button-primary" disabled={busy}>Encrypt & save</button>
            <button className="button-secondary" type="button" onClick={testConnection} disabled={busy || !bot?.exchange_connection}>Test connection</button>
          </form>
          <div className="live-zone">
            <div><ShieldAlert size={21} /><span><strong>Live execution</strong><small>Requires a test from the last 30 minutes, TOTP and your Trade PIN. {cdcPure ? "CDC Pure exits by signal/ROI; no exchange stop-loss." : "Stop-loss protection is required."}</small></span></div>
            {exchangeTestExpired && <p className="form-error" role="status">The last exchange test expired. Tap “Test connection” before activating Live.</p>}
            {!savedCertified && savedProfile && <p className="form-error" role="status">All saved pairs are SIM-only. Live activation needs at least one live-certified preset.</p>}
            {bot?.tenant.live_status === "active" ? <button className="button-danger" onClick={deactivateLive} disabled={busy}>Stop Live</button> : <button className="button-secondary" onClick={openLiveDialog} disabled={busy || !exchangeTestFresh || !savedCertified}>Review & activate</button>}
          </div>
        </Tabs.Content>

        <Tabs.Content value="security" className="settings-panel card">
          <div className="section-heading"><div><span>Account protection</span><h2>Security gates</h2></div></div>
          <div className="security-list">
            <div><span><KeyRound size={19} /><span><strong>Trade PIN</strong><small>New PIN: 8–12 digits. Legacy current values can be rotated once.</small></span></span><StatusPill label={user.trade_pin_configured ? "Configured" : "Required"} tone={user.trade_pin_configured ? "good" : "warn"} /></div>
            <div><span><ShieldAlert size={19} /><span><strong>Authenticator</strong><small>Time-based one-time codes protect sensitive actions.</small></span></span><StatusPill label={user.totp_enabled ? "Enabled" : "Required for Live"} tone={user.totp_enabled ? "good" : "warn"} /></div>
          </div>
          <TradePinForm />
          {!user.totp_enabled && <TotpSetup />}
        </Tabs.Content>
      </Tabs.Root>
      {message && <div className="toast-message" role="status">{message}</div>}
      {error && <div className="toast-message error" role="alert">{error}</div>}

      <AlertDialog.Root open={liveOpen} onOpenChange={setLiveOpen}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="dialog-overlay" />
          <AlertDialog.Content className="dialog-content">
            <AlertDialog.Title>Activate Live execution?</AlertDialog.Title>
            <AlertDialog.Description>Orders may be sent to {selectedTarget?.label}. Selected pair allocation is {pairAllocationLabel}, leaving {unallocatedCashPct}% unallocated cash. The backend allows up to {maxOpenPositions} open positions, limits daily loss to {dailyLossCapPct}% ({dailyTradeCap} trades), and uses a {drawdownGuardEnabled ? `${maxDrawdownPct}% drawdown` : "disabled drawdown"} guard. {cdcPure ? "CDC Pure exits by its certified signal and ROI schedule; no exchange stop-loss is placed." : "xAuby enforces a stop-loss exit."} Live trading can still lose money.</AlertDialog.Description>
            <form className="form-stack" onSubmit={activateLive} noValidate>
              {liveError && <p className="form-error" role="alert">{liveError}</p>}
              <label>Trade PIN (8–12 digits)<input name="tradePin" type="password" inputMode="numeric" minLength={8} maxLength={12} required autoComplete="off" /></label>
              <label className="checkbox-field"><input type="checkbox" required /><span>I understand the risk and want to activate Live execution.</span></label>
              <div className="dialog-actions"><AlertDialog.Cancel className="button-secondary" type="button">Cancel</AlertDialog.Cancel><button className="button-danger" disabled={busy}>Activate Live</button></div>
            </form>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </div>
  );
}

function TradePinForm() {
  const user = useCurrentUser();
  const [status, setStatus] = useState("");
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const currentPin = cleanPin(data.get("current"));
    const newPin = cleanPin(data.get("pin"));
    if (user.trade_pin_configured) {
      const currentError = validatePin(currentPin, 1, "Current PIN", false, 128);
      if (currentError) { setStatus(currentError); return; }
    }
    const newError = validatePin(newPin, 8, "New Trade PIN");
    if (newError) { setStatus(newError); return; }
    try {
      await api("/api/v1/trade-pin", { method: "POST", headers: csrfHeaders(user), body: JSON.stringify({ pin: newPin, current_pin: currentPin || null }) });
      setStatus("Trade PIN saved. Refresh to see the updated gate.");
      form.reset();
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : "Could not save PIN"); }
  }

  async function reset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const currentPassword = typeof data.get("currentPassword") === "string" ? String(data.get("currentPassword")) : "";
    const totpCode = cleanPin(data.get("totpCode"));
    const newPin = cleanPin(data.get("resetPin"));
    const confirmPin = cleanPin(data.get("confirmPin"));
    if (user.password_configured && !currentPassword) {
      setStatus("Current account password is required.");
      return;
    }
    if (!user.totp_enabled || !totpCode) {
      setStatus("A current Authenticator or recovery code is required.");
      return;
    }
    const newError = validatePin(newPin, 8, "New Trade PIN");
    if (newError) { setStatus(newError); return; }
    if (newPin !== confirmPin) {
      setStatus("New PIN and confirmation do not match.");
      return;
    }
    setResetBusy(true);
    try {
      await api("/api/v1/trade-pin/reset", {
        method: "POST", headers: csrfHeaders(user),
        body: JSON.stringify({ new_pin: newPin, current_password: currentPassword, totp_code: totpCode }),
      });
      setStatus("Trade PIN reset. Use the new 8–12 digit PIN for Live activation.");
      setResetOpen(false);
      form.reset();
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : "Could not reset Trade PIN");
    } finally { setResetBusy(false); }
  }

  return (
    <>
      <form className="form-stack inline-security-form" onSubmit={submit} noValidate>
        {user.trade_pin_configured && <label>Current PIN (legacy value)<input name="current" type="password" minLength={1} maxLength={128} required autoComplete="off" /></label>}
        <label>{user.trade_pin_configured ? "New PIN (8–12 digits)" : "Create Trade PIN (8–12 digits)"}<input name="pin" type="password" inputMode="numeric" minLength={8} maxLength={12} required autoComplete="new-password" /></label>
        <button className="button-secondary">Save Trade PIN</button>
        {user.trade_pin_configured && <button type="button" className="button-secondary" onClick={() => setResetOpen(true)} disabled={!user.totp_enabled}>Forgot Trade PIN?</button>}
        {!user.totp_enabled && <p className="field-help">Enable Authenticator before using PIN reset.</p>}
        {status && <p className="field-help">{status}</p>}
      </form>
      <AlertDialog.Root open={resetOpen} onOpenChange={setResetOpen}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="dialog-overlay" />
          <AlertDialog.Content className="dialog-content">
            <AlertDialog.Title>Reset forgotten Trade PIN?</AlertDialog.Title>
            <AlertDialog.Description>Confirm your account password and a fresh Authenticator or recovery code. This only changes the PIN; it does not activate Live.</AlertDialog.Description>
            <form className="form-stack" onSubmit={reset} noValidate>
              {user.password_configured && <label>Current account password<input name="currentPassword" type="password" required autoComplete="current-password" /></label>}
              <label>Authenticator or recovery code<input name="totpCode" type="password" required autoComplete="one-time-code" /></label>
              <label>New Trade PIN (8–12 digits)<input name="resetPin" type="password" inputMode="numeric" minLength={8} maxLength={12} required autoComplete="new-password" /></label>
              <label>Confirm new PIN<input name="confirmPin" type="password" inputMode="numeric" minLength={8} maxLength={12} required autoComplete="new-password" /></label>
              <div className="dialog-actions"><AlertDialog.Cancel className="button-secondary" type="button">Cancel</AlertDialog.Cancel><button className="button-primary" disabled={resetBusy}>{resetBusy ? "Resetting…" : "Reset Trade PIN"}</button></div>
            </form>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </>
  );
}

function TotpSetup() {
  const user = useCurrentUser();
  const [secret, setSecret] = useState("");
  const [status, setStatus] = useState("");
  async function setup() {
    try {
      const result = await api<{ secret: string }>("/auth/totp/setup", {
        method: "POST", headers: csrfHeaders(user),
      });
      setSecret(result.secret);
      setStatus("Add this key to your authenticator, then enter its 6-digit code.");
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : "Setup failed"); }
  }
  async function enable(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const result = await api<{ recovery_codes: string[] }>("/auth/totp/enable", {
        method: "POST", headers: csrfHeaders(user), body: JSON.stringify({ code: data.get("code") }),
      });
      setStatus(`Enabled. Save these recovery codes: ${result.recovery_codes.join(" · ")}`);
      setSecret("");
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : "Could not enable authenticator"); }
  }
  return (
    <div className="inline-security-form totp-setup">
      <strong>Set up authenticator</strong>
      {!secret ? <button className="button-secondary" onClick={setup}>Generate setup key</button> : (
        <form className="form-stack" onSubmit={enable}>
          <code>{secret}</code>
          <label>6-digit code<input name="code" inputMode="numeric" pattern="[0-9]{6}" required /></label>
          <button className="button-primary">Enable authenticator</button>
        </form>
      )}
      {status && <p className="field-help">{status}</p>}
    </div>
  );
}
