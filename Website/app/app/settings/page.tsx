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

export default function SettingsPage() {
  const user = useCurrentUser();
  const { data: catalog } = useCatalog();
  const { data: bot, mutate } = useBot();
  const { data: profile, mutate: mutateProfile } = useProfile();
  const [targetId, setTargetId] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveOpen, setLiveOpen] = useState(false);
  const selectedTarget = useMemo(() => catalog?.targets.find((item) => item.id === targetId), [catalog, targetId]);
  const selectedPreset = useMemo(() => catalog?.presets.find((item) => item.target_id === targetId), [catalog, targetId]);

  useEffect(() => {
    if (targetId || !catalog) return;
    setTargetId(profile?.profile?.target_id ?? bot?.exchange_connection?.target_id ?? catalog.targets[0]?.id ?? "");
  }, [bot?.exchange_connection?.target_id, catalog, profile?.profile?.target_id, targetId]);

  function begin() { setBusy(true); setError(""); setMessage(""); }
  function fail(reason: unknown) { setError(reason instanceof Error ? reason.message : "Action failed"); setBusy(false); }

  async function saveProfile() {
    if (!selectedPreset) return;
    begin();
    try {
      await api("/api/v1/profile", {
        method: "PUT", headers: csrfHeaders(user),
        body: JSON.stringify({ preset_ids: [selectedPreset.id], active_preset_id: selectedPreset.id, risk: {} }),
      });
      await mutateProfile();
      setMessage("Preset saved. Simulation mode remains active.");
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
      setMessage("Connection test passed. Live activation is available for 30 minutes.");
      setBusy(false);
    } catch (reason) { fail(reason); }
  }

  async function activateLive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    begin();
    const data = new FormData(event.currentTarget);
    try {
      await api("/api/v1/live/activate", {
        method: "POST", headers: csrfHeaders(user),
        body: JSON.stringify({ trade_pin: data.get("tradePin"), risk_acknowledged: true }),
      });
      await mutate();
      setLiveOpen(false);
      setMessage("Live mode activated. The 1× and stop-loss gates remain enforced.");
      setBusy(false);
    } catch (reason) { fail(reason); }
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
          <div className="section-heading"><div><span>Certified preset</span><h2>Choose one focused strategy</h2></div><StatusPill label="1 active pair" /></div>
          <div className="preset-grid">
            {catalog?.targets.map((target) => {
              const preset = catalog.presets.find((item) => item.target_id === target.id);
              if (!preset) return null;
              const backtest = preset.backtest ?? PENDING_BACKTEST;
              return (
                <button type="button" className={targetId === target.id ? "preset-option selected" : "preset-option"} onClick={() => setTargetId(target.id)} key={target.id}>
                  <span className="preset-option-head">
                    <span className="radio-dot">{targetId === target.id && <Check size={13} />}</span>
                    <span className="preset-copy"><strong>{preset.label}</strong><small>{target.label} · {preset.symbol} · {preset.strategy.replaceAll("_", " ")}</small></span>
                    <em>{preset.primary_timeframe} / {preset.confirm_timeframe}</em>
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
                </button>
              );
            })}
          </div>
          <div className="risk-summary">
            <div><span>Strategy</span><strong>{selectedPreset?.market_type === "swap" ? "Long certified" : "Spot long"}</strong></div><div><span>Manual sides</span><strong>{selectedTarget?.manual_allowed_sides.map((side) => side.toUpperCase()).join(" / ") ?? "—"}</strong></div><div><span>Stop loss</span><strong>Required</strong></div><div><span>Daily loss cap</span><strong>3%</strong></div>
          </div>
          <button className="button-primary" onClick={saveProfile} disabled={busy || !selectedPreset}>Save preset</button>
        </Tabs.Content>

        <Tabs.Content value="exchange" className="settings-panel card">
          <div className="section-heading"><div><span>API connection</span><h2>{selectedTarget?.label ?? "Select an exchange"}</h2></div>{bot?.exchange_connection && <StatusPill label={bot.exchange_connection.status} tone={bot.exchange_connection.status === "tested" ? "good" : "warn"} />}</div>
          <p className="section-copy">Create a key with read and trade permissions only. Withdraw permission must remain disabled.</p>
          <form className="form-stack two-column-form" onSubmit={connect}>
            <label>API key<input name="apiKey" required autoComplete="off" /></label>
            <label>API secret<input name="apiSecret" type="password" required autoComplete="new-password" /></label>
            {selectedTarget?.credential_fields.includes("passphrase") && <label className="full-field">Passphrase<input name="passphrase" type="password" required autoComplete="new-password" /></label>}
            <label className="checkbox-field full-field"><input name="attest" type="checkbox" required /><span>I confirm withdraw permission is disabled.</span></label>
            <button className="button-primary" disabled={busy}>Encrypt & save</button>
            <button className="button-secondary" type="button" onClick={testConnection} disabled={busy || !bot?.exchange_connection}>Test connection</button>
          </form>
          <div className="live-zone">
            <div><ShieldAlert size={21} /><span><strong>Live execution</strong><small>Requires a test from the last 30 minutes, TOTP and your Trade PIN.</small></span></div>
            {bot?.tenant.live_status === "active" ? <button className="button-danger" onClick={deactivateLive} disabled={busy}>Stop Live</button> : <button className="button-secondary" onClick={() => setLiveOpen(true)} disabled={bot?.exchange_connection?.status !== "tested"}>Review & activate</button>}
          </div>
        </Tabs.Content>

        <Tabs.Content value="security" className="settings-panel card">
          <div className="section-heading"><div><span>Account protection</span><h2>Security gates</h2></div></div>
          <div className="security-list">
            <div><span><KeyRound size={19} /><span><strong>Trade PIN</strong><small>8–12 digits, required for Live activation.</small></span></span><StatusPill label={user.trade_pin_configured ? "Configured" : "Required"} tone={user.trade_pin_configured ? "good" : "warn"} /></div>
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
            <AlertDialog.Description>Orders may be sent to {selectedTarget?.label}. xAuby enforces long-only, 1× leverage, one position and a stop loss, but trading can still lose money.</AlertDialog.Description>
            <form className="form-stack" onSubmit={activateLive}>
              <label>Trade PIN<input name="tradePin" type="password" inputMode="numeric" pattern="[0-9]{8,12}" required autoComplete="off" /></label>
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
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api("/api/v1/trade-pin", { method: "POST", headers: csrfHeaders(user), body: JSON.stringify({ pin: data.get("pin"), current_pin: data.get("current") || null }) });
      setStatus("Trade PIN saved. Refresh to see the updated gate.");
      form.reset();
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : "Could not save PIN"); }
  }
  return (
    <form className="form-stack inline-security-form" onSubmit={submit}>
      {user.trade_pin_configured && <label>Current PIN<input name="current" type="password" inputMode="numeric" pattern="[0-9]{8,12}" required /></label>}
      <label>{user.trade_pin_configured ? "New PIN" : "Create Trade PIN"}<input name="pin" type="password" inputMode="numeric" pattern="[0-9]{8,12}" required /></label>
      <button className="button-secondary">Save Trade PIN</button>
      {status && <p className="field-help">{status}</p>}
    </form>
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
