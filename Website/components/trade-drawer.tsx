"use client";

import { FormEvent, useState } from "react";
import { ArrowDownToLine, ArrowUpFromLine, X } from "lucide-react";
import { api, csrfHeaders, formatNumber, OrderPreview, User } from "@/lib/api";

type Intent = "OPEN_LONG" | "OPEN_SHORT" | "CLOSE_POSITION";

function cleanPin(value: FormDataEntryValue | null): string {
  return typeof value === "string" ? value.trim() : "";
}

export function TradeDrawer({
  user,
  symbol,
  positionOpen,
  enabled,
  live,
  shortLiveCertified,
  engineRunning,
  profileReady,
}: {
  user: User;
  symbol: string;
  positionOpen: boolean;
  enabled: boolean;
  live: boolean;
  shortLiveCertified: boolean;
  engineRunning: boolean;
  profileReady: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [intent, setIntent] = useState<Intent>(positionOpen ? "CLOSE_POSITION" : "OPEN_LONG");
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const selectedIntent: Intent = positionOpen
    ? "CLOSE_POSITION"
    : intent === "CLOSE_POSITION" ? "OPEN_LONG" : intent;
  const canReview = engineRunning && profileReady;

  async function createPreview() {
    if (!canReview) return;
    setBusy(true); setMessage("");
    try {
      const result = await api<OrderPreview>("/api/v1/orders/preview", {
        method: "POST", headers: csrfHeaders(user), body: JSON.stringify({ symbol, intent: selectedIntent }),
      });
      setPreview(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Preview failed");
    } finally { setBusy(false); }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preview) return;
    setBusy(true); setMessage("");
    const data = new FormData(event.currentTarget);
    const tradePin = cleanPin(data.get("tradePin"));
    if (!/^[0-9]{8,12}$/.test(tradePin)) {
      setMessage("Trade PIN must contain 8–12 digits. Do not use the 6-digit Authenticator code.");
      setBusy(false);
      return;
    }
    try {
      await api(`/api/v1/orders/${preview.challenge_id}/confirm`, {
        method: "POST", headers: csrfHeaders(user),
        body: JSON.stringify({ trade_pin: tradePin, idempotency_key: crypto.randomUUID() }),
      });
      setMessage("Order accepted by the tenant engine.");
      setPreview(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Confirmation failed");
    } finally { setBusy(false); }
  }

  if (!enabled) return null;
  return <>
    <button className="button-primary trade-launch" onClick={() => setOpen(true)}><ArrowUpFromLine size={17} />Trade</button>
    {open && <div className="trade-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setOpen(false)}>
      <aside className="trade-drawer" role="dialog" aria-modal="true" aria-labelledby="trade-title">
        <div className="trade-drawer-head"><div><span>{live ? "LIVE" : "SIMULATION"}</span><h2 id="trade-title">Manual order · {symbol}</h2></div><button onClick={() => setOpen(false)} aria-label="Close"><X /></button></div>
        {!preview ? <>
          <div className="order-intents">
            <button className={selectedIntent === "OPEN_LONG" ? "selected" : ""} disabled={positionOpen} onClick={() => setIntent("OPEN_LONG")}><ArrowUpFromLine />Open long</button>
            <button className={selectedIntent === "OPEN_SHORT" ? "selected" : ""} disabled={positionOpen || (live && !shortLiveCertified)} onClick={() => setIntent("OPEN_SHORT")}><ArrowDownToLine />Open short<small>{live && !shortLiveCertified ? "Not live-certified" : "Perpetual only"}</small></button>
            <button className={selectedIntent === "CLOSE_POSITION" ? "selected" : ""} disabled={!positionOpen} onClick={() => setIntent("CLOSE_POSITION")}><X />Close position</button>
          </div>
          <p className="drawer-note">Quantity is calculated from the active certified profile. You will review the exact estimate before entering your Trade PIN.</p>
          {!profileReady && <p className="form-error" role="status">Save a trading preset before reviewing a manual order.</p>}
          {profileReady && !engineRunning && <p className="form-error" role="status">Start the engine before reviewing a manual order.</p>}
          <button className="button-primary wide" disabled={busy || !canReview} onClick={createPreview}>{busy ? "Calculating…" : "Review order"}</button>
        </> : <form className="form-stack order-confirm" onSubmit={confirm} noValidate>
          <div className="preview-grid"><span>Intent<strong>{preview.preview.intent.replaceAll("_", " ")}</strong></span><span>Mode<strong>{preview.preview.mode}</strong></span><span>Mark price<strong>{formatNumber(preview.preview.mark_price)}</strong></span><span>Quantity<strong>{formatNumber(preview.preview.estimated_quantity, 6)}</strong></span><span>Est. notional<strong>{formatNumber(preview.preview.estimated_notional)} USDT</strong></span><span>Sizing<strong>{preview.preview.sizing_mode === "cdc_pure" ? `${formatNumber(preview.preview.allocation_pct ?? 0, 0)}% equity` : preview.preview.sizing_mode === "risk_based" ? "Risk-based" : "Position"}</strong></span><span>Expires<strong>60 seconds</strong></span></div>
          <label>Trade PIN (8–12 digits)<input name="tradePin" inputMode="numeric" minLength={8} maxLength={12} type="password" required autoComplete="off" /></label>
          <div className="dialog-actions"><button type="button" className="button-secondary" onClick={() => setPreview(null)}>Back</button><button className="button-primary" disabled={busy}>{busy ? "Queuing…" : "Confirm order"}</button></div>
        </form>}
        {message && <p className="form-error" role="status">{message}</p>}
      </aside>
    </div>}
  </>;
}
