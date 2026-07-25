"use client";

import { Check, Send, Trash2, TriangleAlert } from "lucide-react";
import { FormEvent, useState } from "react";
import { api, csrfHeaders, TelegramPreferences } from "@/lib/api";
import { useCurrentUser } from "@/components/app-shell";
import { StatusPill } from "@/components/status-pill";
import { useBot, useTelegramPreferences } from "@/lib/hooks";

const CATEGORIES: Array<{ key: keyof TelegramPreferences; label: string; help: string }> = [
  { key: "trade_lifecycle", label: "Trade lifecycle", help: "Entries, exits, partial take-profit and trailing-stop updates." },
  { key: "risk_safety", label: "Risk & safety", help: "Guard blocks, drawdown protection and regime changes." },
  { key: "system_health", label: "System health", help: "A periodic heartbeat so silence always means something is wrong." },
  { key: "periodic_reports", label: "Periodic reports", help: "The daily digest and the weekly review." },
];

export function TelegramSettings() {
  const user = useCurrentUser();
  const { data: bot, mutate: mutateBot } = useBot();
  const { data: preferences, mutate: mutatePreferences } = useTelegramPreferences();
  const [busy, setBusy] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [restartNeeded, setRestartNeeded] = useState(false);

  const connection = bot?.telegram_connection ?? null;
  const isChannel = connection?.chat_id.startsWith("@") ?? false;
  const isGroup = connection?.chat_id.startsWith("-") ?? false;

  async function run(label: string, action: () => Promise<{ restart_required?: boolean }>) {
    setBusy(label); setStatus(""); setError("");
    try {
      const result = await action();
      await Promise.all([mutateBot(), mutatePreferences()]);
      if (result?.restart_required) setRestartNeeded(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Something went wrong");
    } finally {
      setBusy("");
    }
  }

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await run("connect", async () => {
      const result = await api<{ restart_required: boolean; commands_supported: boolean }>(
        "/api/v1/telegram/connect",
        {
          method: "POST",
          headers: csrfHeaders(user),
          body: JSON.stringify({
            bot_token: String(data.get("botToken") ?? "").trim(),
            chat_id: String(data.get("chatId") ?? "").trim(),
          }),
        },
      );
      form.reset();
      setStatus("Bot token encrypted and saved. Send a test message next.");
      return result;
    });
  }

  async function sendTest() {
    await run("test", async () => {
      await api("/api/v1/telegram/test", { method: "POST", headers: csrfHeaders(user) });
      setStatus("Test message sent. Check your Telegram chat.");
      return {};
    });
  }

  async function disconnect() {
    await run("disconnect", async () => {
      const result = await api<{ restart_required: boolean }>("/api/v1/telegram", {
        method: "DELETE", headers: csrfHeaders(user),
      });
      setStatus("Telegram disconnected. Alerts are off.");
      return result;
    });
  }

  async function toggle(key: keyof TelegramPreferences, value: boolean) {
    await run(key, () =>
      api<{ restart_required: boolean }>("/api/v1/telegram/preferences", {
        method: "PATCH", headers: csrfHeaders(user), body: JSON.stringify({ [key]: value }),
      }),
    );
  }

  const pill = !connection
    ? { label: "Not connected", tone: "neutral" as const }
    : connection.status === "tested"
      ? { label: `Connected · @${connection.bot_username}`, tone: "good" as const }
      : connection.status === "failed"
        ? { label: "Test failed", tone: "warn" as const }
        : { label: "Saved · not tested", tone: "warn" as const };

  return <div className="telegram-settings">
    <div className="section-heading">
      <div><span>Notifications</span><h2>Telegram alerts</h2></div>
      <StatusPill label={pill.label} tone={pill.tone} />
    </div>

    {!connection && <div className="field-help telegram-onboarding">
      <p>Alerts are delivered through your own Telegram bot, so nobody else can read them.</p>
      <ol>
        <li>Message <strong>@BotFather</strong> on Telegram and send <code>/newbot</code>.</li>
        <li>Copy the token it gives you and paste it below.</li>
        <li>Send <code>/start</code> to your new bot, then get your chat ID from <strong>@userinfobot</strong>.</li>
      </ol>
      <p>For a channel, add the bot as an administrator and use its <code>-100…</code> ID.</p>
    </div>}

    <form className="form-stack" onSubmit={connect}>
      <label>Bot token
        <input name="botToken" type="password" autoComplete="new-password"
               placeholder="123456789:AA…" required disabled={busy !== ""} />
      </label>
      <label>Chat ID
        <input name="chatId" type="text" placeholder="123456789 or @mychannel"
               required disabled={busy !== ""} />
      </label>
      <div className="form-actions">
        <button className="button-primary" disabled={busy !== ""}>
          {busy === "connect" ? "Saving…" : connection ? "Replace credentials" : "Encrypt & save"}
        </button>
        {connection && <button type="button" className="button-secondary" onClick={sendTest}
                               disabled={busy !== ""}>
          <Send size={16} />{busy === "test" ? "Sending…" : "Send test message"}
        </button>}
        {connection && <button type="button" className="button-danger" onClick={disconnect}
                               disabled={busy !== ""}>
          <Trash2 size={16} />Disconnect
        </button>}
      </div>
    </form>

    {status && <p className="field-help" role="status">{status}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}

    {connection && isChannel && <p className="field-help">
      <TriangleAlert size={14} /> Telegram commands are unavailable for channel IDs — the bot can
      only send to a channel, not take instructions from it.
    </p>}
    {connection && isGroup && <p className="field-help">
      <TriangleAlert size={14} /> This is a group chat. Any member of that group can run bot
      commands, including pausing your bot.
    </p>}

    {connection && preferences && <div className="telegram-preferences">
      <h3>What to send</h3>
      {CATEGORIES.map((category) => <label key={category.key} className="checkbox-field">
        <input type="checkbox" checked={Boolean(preferences[category.key])}
               disabled={busy !== ""}
               onChange={(event) => toggle(category.key, event.target.checked)} />
        <span><strong>{category.label}</strong><small>{category.help}</small></span>
      </label>)}
      <label className="checkbox-field">
        <input type="checkbox" checked={Boolean(preferences.commands_enabled)}
               disabled={busy !== "" || isChannel}
               onChange={(event) => toggle("commands_enabled", event.target.checked)} />
        <span><strong>Allow Telegram commands</strong><small>Run /status, /pnl and /pause from your phone.</small></span>
      </label>
      <p className="field-help">
        <Check size={14} /> Critical safety alerts are always delivered and cannot be muted.
      </p>
    </div>}

    {restartNeeded && <p className="field-help telegram-restart" role="status">
      Restart the engine from the Home page to apply these changes — the bot reads its
      notification settings at startup.
    </p>}
  </div>;
}
