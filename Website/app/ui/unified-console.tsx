"use client";

import Link from "next/link";
import Image from "next/image";
import QRCode from "qrcode";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import {
  api,
  ApiError,
  numberValue,
  record,
  setCsrfToken,
  textValue,
  type Candle,
  type Catalog,
  type Me,
  type RuntimeSnapshot,
} from "./api";

type View = "home" | "trade" | "activity" | "settings" | "admin";
type Notice = { tone: "ok" | "warn"; text: string } | null;

type RuntimeModel = {
  state: Record<string, unknown>;
  focus: Record<string, unknown>;
  position: Record<string, unknown>;
  signal: Record<string, unknown>;
  regime: Record<string, unknown>;
  risk: Record<string, unknown>;
  latency: Record<string, unknown>;
  portfolio: Record<string, unknown>;
  exchange: Record<string, unknown>;
  symbol: string;
  mode: string;
  equity: number;
  price: number;
  change24h: number;
};

function runtimeModel(snapshot: RuntimeSnapshot | null): RuntimeModel {
  const state = record(snapshot?.state);
  const symbol = textValue(state.focus_symbol || state.symbol, "XAUUSDT").toUpperCase();
  const bySymbol = record(state.by_symbol);
  const focus = record(bySymbol[symbol] || state);
  const position = record(focus.position);
  return {
    state,
    focus,
    position,
    signal: record(focus.signal_meta),
    regime: record(focus.regime),
    risk: record(focus.risk),
    latency: record(focus.latency || state.latency),
    portfolio: record(focus.portfolio || state.portfolio),
    exchange: record(focus.exchange || state.exchange),
    symbol: textValue(focus.symbol || symbol, "XAUUSDT"),
    mode: textValue(focus.execution_mode || state.execution_mode || (focus.simulate_only ? "sim" : "live"), "sim").toUpperCase(),
    equity: numberValue(focus.total_equity_usdt || state.total_equity_usdt || record(state.aggregate).total_equity_usdt),
    price: numberValue(focus.current_price || state.current_price),
    change24h: numberValue(focus.price_change_24h_pct || focus.percent_change_24h || state.price_change_24h_pct),
  };
}

function fmt(value: number, digits = 2): string {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value || 0);
}

function compactAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m`;
}

export function UnifiedConsole({ initialView }: { initialView: string }) {
  const view = initialView as View;
  const [me, setMe] = useState<Me | null>(null);
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [bot, setBot] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<Notice>(null);

  const loadSnapshot = useCallback(async () => {
    try {
      const payload = await api<RuntimeSnapshot>("/api/v1/runtime/snapshot");
      setSnapshot(payload);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        window.location.replace("/login");
        return;
      }
      setSnapshot({
        ok: false,
        source: "tenant_engine",
        read_only: false,
        as_of: Date.now() / 1000,
        age_sec: null,
        stale: true,
        state: {},
        currency: {},
        detail: {},
        error: "ไม่สามารถอ่านสถานะ Runtime ได้",
      });
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([api<Me>("/api/v1/me"), api<Record<string, unknown>>("/api/v1/bot")])
      .then(([current, botPayload]) => {
        if (!active) return;
        setCsrfToken(current.csrf_token);
        setMe(current);
        setBot(botPayload);
        return loadSnapshot();
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 401) window.location.replace("/login");
        else setNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "เปิด workspace ไม่สำเร็จ" });
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadSnapshot]);

  useEffect(() => {
    const timer = window.setInterval(() => void loadSnapshot(), 5000);
    return () => window.clearInterval(timer);
  }, [loadSnapshot]);

  const model = useMemo(() => runtimeModel(snapshot), [snapshot]);

  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setCsrfToken("");
      window.location.replace("/login");
    }
  }

  async function engineAction(action: "start" | "stop") {
    if (snapshot?.read_only) return;
    setNotice(null);
    try {
      await api(`/api/v1/bot/${action}`, { method: "POST" });
      setNotice({ tone: "ok", text: action === "start" ? "เริ่ม Engine แล้ว" : "หยุด Engine แล้ว" });
      setBot(await api<Record<string, unknown>>("/api/v1/bot"));
      await loadSnapshot();
    } catch (caught) {
      setNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "ดำเนินการไม่สำเร็จ" });
    }
  }

  if (loading) {
    return <main className="console-loading"><strong>xAuby</strong><span>กำลังเปิด secure workspace…</span></main>;
  }
  if (!me) return null;
  if (me.account_status !== "active" || !me.tenant) {
    return (
      <main className="pending-page">
        <section className="paper-card">
          <p className="eyebrow">ACCOUNT REVIEW</p>
          <h1>บัญชีอยู่ระหว่างตรวจสอบ</h1>
          <p>{me.email}</p>
          <span className="status-pill warn">{me.account_status}</span>
          <p className="muted">Owner จะอนุมัติและสร้าง workspace ก่อนเริ่มใช้งาน</p>
          <button className="secondary-button" onClick={() => void logout()}>ออกจากระบบ</button>
        </section>
      </main>
    );
  }

  return (
    <main className="console-page">
      <div className="console-shell">
        <ConsoleHeader me={me} snapshot={snapshot} model={model} />
        {snapshot?.read_only && (
          <div className="bridge-banner">
            <strong>LEGACY BRIDGE · MONITOR ONLY</strong>
            <span>ดูข้อมูล Live ได้ แต่คำสั่งควบคุมจะเปิดหลัง Owner handoff เมื่อ Position ปิดแล้ว</span>
          </div>
        )}
        {notice && <div className={`notice ${notice.tone === "warn" ? "notice-warn" : ""}`}>{notice.text}</div>}
        <section className="console-content">
          {view === "home" && (
            <HomeView
              snapshot={snapshot}
              model={model}
              bot={bot}
              onEngineAction={engineAction}
            />
          )}
          {view === "trade" && <TradeView snapshot={snapshot} model={model} onNotice={setNotice} />}
          {view === "activity" && <ActivityView model={model} />}
          {view === "settings" && <SettingsView me={me} onNotice={setNotice} />}
          {view === "admin" && me.role === "platform_admin" && <AdminView onNotice={setNotice} />}
        </section>
        <ConsoleNavigation view={view} isAdmin={me.role === "platform_admin"} />
        <button className="logout-button" onClick={() => void logout()}>ออกจากระบบ</button>
      </div>
    </main>
  );
}

function ConsoleHeader({ me, snapshot, model }: {
  me: Me;
  snapshot: RuntimeSnapshot | null;
  model: RuntimeModel;
}) {
  const wsAge = numberValue(model.latency.ws_tick_age_ms) / 1000;
  const apiLatency = numberValue(model.latency.api_latency_ms);
  return (
    <header className="console-header">
      <div className="profile-stack">
        <h1>xAuby</h1>
        <p><span>Alternative Store of Value</span><span>Trading System</span></p>
        <div className="profile-row">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <Image src="/profile-itsara.jpg" alt="" width={30} height={30} priority />
          <span>{me.email}</span>
        </div>
      </div>
      <div className="runtime-status">
        <span className={`status-pill ${model.mode === "LIVE" ? "live" : "warn"}`}>
          <i aria-hidden="true" />{model.mode}
        </span>
        <div className="health-grid" aria-label="System health">
          <span>E {snapshot?.ok ? "RUN" : "—"}</span>
          <span>S {compactAge(snapshot?.age_sec)}</span>
          <span>W {wsAge ? `${wsAge.toFixed(1)}s` : "—"}</span>
          <span>A {apiLatency ? `${Math.round(apiLatency)}` : "—"}</span>
        </div>
      </div>
    </header>
  );
}

function HomeView({ snapshot, model, bot, onEngineAction }: {
  snapshot: RuntimeSnapshot | null;
  model: RuntimeModel;
  bot: Record<string, unknown>;
  onEngineAction: (action: "start" | "stop") => Promise<void>;
}) {
  const open = String(model.position.state || "").toLowerCase() === "bought";
  const side = open ? textValue(model.position.position_side, "POSITION") : "FLAT";
  const pnlPct = numberValue(model.position.unrealized_pnl_pct);
  const pnl = numberValue(model.position.unrealized_pnl);
  const qty = numberValue(model.position.quantity);
  const exposure = model.equity > 0 ? Math.min(100, (qty * model.price / model.equity) * 100) : 0;
  const thb = numberValue(record(snapshot?.currency).equity_thb);
  const service = textValue(bot.service_status, "unknown");

  return (
    <div className="view-stack home-view">
      <article className="balance-card">
        <div>
          <span>{textValue(model.exchange.id, "OKX").toUpperCase()} {textValue(model.exchange.market_type, "SWAP").toUpperCase()}</span>
          <h2>{model.symbol}</h2>
          <strong>{fmt(model.equity)} <small>USDT</small></strong>
          <p>{thb ? `฿${fmt(thb, 0)}` : "Portfolio equity"}</p>
        </div>
        <span className={`feed-chip ${snapshot?.stale ? "warn" : ""}`}>{snapshot?.stale ? "STALE" : "OK"}</span>
      </article>
      <section className="market-grid">
        <article className="price-card">
          <span>Price</span>
          <strong>{fmt(model.price)}</strong>
          <small>24h {model.change24h >= 0 ? "+" : ""}{fmt(model.change24h)}%</small>
        </article>
        <article className="position-card">
          <span>Position</span>
          <strong>{side}{open ? ` ${pnlPct >= 0 ? "+" : ""}${fmt(pnlPct, 1)}%` : ""}</strong>
          <small>{open ? `Net PnL ${fmt(pnl)} USDT · Qty ${fmt(qty, 3)}` : "No open position"}</small>
        </article>
      </section>
      <article className="panel portfolio-panel">
        <div className="panel-head"><h2>Portfolio</h2><span>{fmt(model.equity)} USDT</span></div>
        <div className="portfolio-body">
          <div className="donut" style={{ "--exposure": `${exposure * 3.6}deg` } as CSSProperties}>
            <div><span>Portfolio</span><strong>{open ? "Open" : "Flat"}</strong></div>
          </div>
          <div className="portfolio-legend">
            <p><i className="orange-dot" />USDT <span>{fmt(Math.max(0, 100 - exposure), 0)}%</span></p>
            <p><i />Exposure <span>{fmt(exposure, 0)}%</span></p>
          </div>
        </div>
        <div className="stat-row">
          <div><span>Signal</span><strong>{textValue(model.signal.action, "WAIT")}</strong></div>
          <div><span>Regime</span><strong>{textValue(model.regime.regime, "UNKNOWN")}</strong></div>
          <div><span>Engine</span><strong>{service.toUpperCase()}</strong></div>
        </div>
      </article>
      <article className="panel engine-panel">
        <div><h2>Engine control</h2><p>{snapshot?.read_only ? "Legacy engine is monitored through a read-only bridge" : "ทุกคำสั่งบันทึกใน audit trail"}</p></div>
        <div className="button-row">
          <button className="primary-button compact" disabled={snapshot?.read_only} onClick={() => void onEngineAction("start")}>Start</button>
          <button className="secondary-button compact" disabled={snapshot?.read_only} onClick={() => void onEngineAction("stop")}>Stop</button>
        </div>
      </article>
    </div>
  );
}

function TradeView({ snapshot, model, onNotice }: {
  snapshot: RuntimeSnapshot | null;
  model: RuntimeModel;
  onNotice: (notice: Notice) => void;
}) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [tab, setTab] = useState<"signal" | "ops" | "regime">("signal");
  const [intent, setIntent] = useState("OPEN_LONG");
  const [challenge, setChallenge] = useState<{ id: string; warnings: string[] } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadCandles = useCallback(async () => {
    try {
      const payload = await api<{ candles: Candle[] }>(
        `/api/v1/runtime/candles?symbol=${encodeURIComponent(model.symbol)}&timeframe=4h&limit=64`,
      );
      setCandles(Array.isArray(payload.candles) ? payload.candles : []);
    } catch {
      setCandles([]);
    }
  }, [model.symbol]);

  useEffect(() => {
    void loadCandles();
    const timer = window.setInterval(() => void loadCandles(), 30000);
    return () => window.clearInterval(timer);
  }, [loadCandles]);

  async function preview() {
    setBusy(true);
    onNotice(null);
    try {
      const result = await api<{ challenge_id: string; warnings?: string[] }>("/api/v1/orders/preview", {
        method: "POST",
        body: JSON.stringify({ symbol: model.symbol, intent, management_mode: "strategy" }),
      });
      setChallenge({ id: result.challenge_id, warnings: result.warnings ?? [] });
      onNotice({ tone: "ok", text: "Preview พร้อมยืนยันภายใน 60 วินาที" });
    } catch (caught) {
      onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "สร้าง Preview ไม่สำเร็จ" });
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!challenge) return;
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      await api(`/api/v1/orders/${challenge.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ trade_pin: data.get("pin"), idempotency_key: crypto.randomUUID() }),
      });
      setChallenge(null);
      onNotice({ tone: "ok", text: "ส่งคำสั่งเข้า execution queue แล้ว" });
    } catch (caught) {
      onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "ยืนยันคำสั่งไม่สำเร็จ" });
    } finally {
      setBusy(false);
    }
  }

  const protection = [
    ["Entry", model.position.entry_price],
    ["Mark", model.position.mark_price || model.price],
    ["Stop", model.position.stop_loss],
    ["Take profit", model.position.take_profit],
  ];
  const reasons = Array.isArray(model.regime.reasons) ? model.regime.reasons : [];

  return (
    <div className="view-stack trade-view">
      <article className="panel chart-card">
        <div className="panel-head"><div><h2>{model.symbol}</h2><p>4H market structure</p></div><span>{fmt(model.price)}</span></div>
        <MarketChart candles={candles} />
      </article>
      <div className="detail-tabs" role="tablist" aria-label="Trading detail">
        {(["signal", "ops", "regime"] as const).map((item) => (
          <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item.toUpperCase()}</button>
        ))}
      </div>
      {tab === "signal" && (
        <article className="panel detail-panel">
          <p className="eyebrow">SIGNAL</p>
          <h2 className="detail-title">{textValue(model.signal.action, "WAIT")}</h2>
          <p>{textValue(model.signal.reason, "Waiting for runtime state")}</p>
          <div className="stat-row two">
            <div><span>Confidence</span><strong>{fmt(numberValue(model.signal.confidence) * 100, 0)}%</strong></div>
            <div><span>Strategy</span><strong>{textValue(model.focus.strategy_name, "—")}</strong></div>
          </div>
        </article>
      )}
      {tab === "ops" && (
        <article className="panel detail-panel">
          <div className="panel-head"><h2>Position protection</h2><span>{textValue(model.position.position_side, "FLAT")}</span></div>
          <div className="kv-grid">
            {protection.map(([label, value]) => <div key={String(label)}><span>{String(label)}</span><strong>{fmt(numberValue(value))}</strong></div>)}
          </div>
        </article>
      )}
      {tab === "regime" && (
        <article className="panel detail-panel">
          <div className="panel-head"><h2>Market regime</h2><span>{textValue(model.regime.regime, "UNKNOWN")}</span></div>
          <p>{textValue(model.regime.phase, "No phase data")} · {textValue(model.regime.risk_state, "—")}</p>
          <div className="reason-list">
            {reasons.map((item, index) => {
              const row = record(item);
              return <div key={`${textValue(row.label)}-${index}`}><i className={row.supportive ? "active" : ""} /><span>{textValue(row.label)}</span></div>;
            })}
          </div>
        </article>
      )}
      <article className="panel order-ticket">
        <div className="panel-head"><div><h2>Manual order</h2><p>Signal bypassed · Risk remains enforced</p></div><span>{snapshot?.read_only ? "LOCKED" : model.mode}</span></div>
        <label className="field"><span>Pair</span><input value={model.symbol} readOnly /></label>
        <label className="field"><span>Intent</span>
          <select value={intent} onChange={(event) => setIntent(event.target.value)}>
            <option>OPEN_LONG</option><option>CLOSE_LONG</option><option>OPEN_SHORT</option><option>CLOSE_SHORT</option>
          </select>
        </label>
        <button className="primary-button" disabled={busy || snapshot?.read_only || snapshot?.stale} onClick={() => void preview()}>
          Preview order
        </button>
        {snapshot?.read_only && <p className="locked-note">Manual Trade จะเปิดหลัง legacy Position ปิดและ Owner handoff เสร็จ</p>}
        {challenge && (
          <form className="confirm-box" onSubmit={confirm}>
            <strong>ตรวจ Pair, Side และ Exposure ก่อนยืนยัน</strong>
            {challenge.warnings.map((warning) => <p key={warning}>{warning}</p>)}
            <label className="field"><span>Trade PIN</span><input name="pin" type="password" inputMode="numeric" minLength={8} maxLength={12} required /></label>
            <button className="primary-button" disabled={busy}>Confirm order</button>
          </form>
        )}
      </article>
    </div>
  );
}

function MarketChart({ candles }: { candles: Candle[] }) {
  if (candles.length < 2) return <div className="chart-empty">Waiting for candle data</div>;
  const width = 720;
  const height = 260;
  const values = candles.flatMap((candle) => [numberValue(candle.high), numberValue(candle.low)]).filter(Boolean);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(0.0001, max - min);
  const step = width / candles.length;
  const y = (value: number) => 12 + ((max - value) / range) * (height - 24);
  return (
    <svg className="market-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Candlestick chart">
      {[0.25, 0.5, 0.75].map((ratio) => <line key={ratio} x1="0" x2={width} y1={height * ratio} y2={height * ratio} className="chart-grid-line" />)}
      {candles.map((candle, index) => {
        const open = numberValue(candle.open);
        const close = numberValue(candle.close);
        const high = numberValue(candle.high);
        const low = numberValue(candle.low);
        const x = index * step + step / 2;
        const bodyTop = Math.min(y(open), y(close));
        const bodyHeight = Math.max(2, Math.abs(y(open) - y(close)));
        const rising = close >= open;
        return (
          <g key={`${candle.timestamp}-${index}`} className={rising ? "candle rising" : "candle falling"}>
            <line x1={x} x2={x} y1={y(high)} y2={y(low)} />
            <rect x={x - Math.max(2, step * 0.27)} y={bodyTop} width={Math.max(4, step * 0.54)} height={bodyHeight} />
          </g>
        );
      })}
    </svg>
  );
}

function ActivityView({ model }: { model: RuntimeModel }) {
  const [payload, setPayload] = useState<{ events: unknown[]; trades: unknown[] }>({ events: [], trades: [] });
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    try {
      const result = await api<{ events: unknown[]; trades: unknown[] }>(
        `/api/v1/runtime/activity?symbol=${encodeURIComponent(model.symbol)}&limit=40`,
      );
      setPayload({ events: result.events ?? [], trades: result.trades ?? [] });
    } finally {
      setLoading(false);
    }
  }, [model.symbol]);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(timer);
  }, [load]);
  return (
    <div className="view-stack activity-view">
      <article className="panel activity-panel">
        <div className="panel-head"><div><h2>Event log</h2><p>Latest system decisions</p></div><span>{payload.events.length}</span></div>
        <div className="timeline">
          {loading && <p className="muted">กำลังโหลด…</p>}
          {!loading && payload.events.length === 0 && <p className="muted">ยังไม่มี Event</p>}
          {[...payload.events].reverse().map((item, index) => {
            const event = record(item);
            return <div className="timeline-row" key={`${textValue(event.timestamp)}-${index}`}><i /><div><strong>{textValue(event.event_type || event.type || event.action, "EVENT")}</strong><p>{textValue(event.message || event.reason || event.summary, "Runtime activity")}</p><small>{textValue(event.timestamp || event.created_at, "")}</small></div></div>;
          })}
        </div>
      </article>
      <article className="panel activity-panel">
        <div className="panel-head"><div><h2>Trade log</h2><p>Closed positions</p></div><span>{payload.trades.length}</span></div>
        <div className="trade-list">
          {payload.trades.length === 0 && <p className="muted">ยังไม่มี Closed trade</p>}
          {payload.trades.map((item, index) => {
            const trade = record(item);
            return <div key={`${textValue(trade.id || trade.closed_at)}-${index}`}><span>{textValue(trade.symbol, model.symbol)} · {textValue(trade.side || trade.position_side, "—")}</span><strong>{fmt(numberValue(trade.realized_pnl || trade.pnl))} USDT</strong><small>{textValue(trade.closed_at, "")}</small></div>;
          })}
        </div>
      </article>
    </div>
  );
}

function SettingsView({ me, onNotice }: { me: Me; onNotice: (notice: Notice) => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [totpSetup, setTotpSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [qr, setQr] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    api<Catalog>("/api/v1/catalog").then((result) => {
      if (!active) return;
      setCatalog(result);
      setSelected(result.presets[0] ? [result.presets[0].id] : []);
    }).catch((caught: unknown) => onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "โหลด Catalog ไม่สำเร็จ" }));
    return () => { active = false; };
  }, [onNotice]);

  useEffect(() => {
    if (!totpSetup) { setQr(""); return; }
    let active = true;
    QRCode.toDataURL(totpSetup.otpauth_uri, { width: 220, margin: 1, color: { dark: "#161614", light: "#ece9e2" } })
      .then((result) => { if (active) setQr(result); })
      .catch(() => { if (active) setQr(""); });
    return () => { active = false; };
  }, [totpSetup]);

  async function connectExchange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api("/api/v1/exchange/connect", {
        method: "POST",
        body: JSON.stringify({ exchange_id: "okx", market_type: "swap", api_key: data.get("key"), api_secret: data.get("secret"), passphrase: data.get("passphrase"), withdraw_disabled_attested: data.get("attest") === "on" }),
      });
      onNotice({ tone: "ok", text: "บันทึก Exchange แล้ว กรุณากด Test connection" });
      form.reset();
    } catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "บันทึก Exchange ไม่สำเร็จ" }); }
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/api/v1/profile", {
        method: "PUT",
        body: JSON.stringify({ preset_ids: selected, active_preset_id: data.get("active"), risk: { risk_pct: Number(data.get("risk")), max_position_per_trade_pct: Number(data.get("allocation")), max_daily_loss_pct: Number(data.get("daily")), max_leverage: Number(data.get("leverage")), max_open_positions: 1 } }),
      });
      onNotice({ tone: "ok", text: "บันทึก Pair, Strategy และ Risk แล้ว · Mode กลับเป็น SIM" });
    } catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "บันทึก Profile ไม่สำเร็จ" }); }
  }

  async function setupTotp() {
    try { setTotpSetup(await api<{ secret: string; otpauth_uri: string }>("/auth/totp/setup", { method: "POST" })); }
    catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "สร้าง TOTP ไม่สำเร็จ" }); }
  }

  async function enableTotp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const result = await api<{ recovery_codes: string[] }>("/auth/totp/enable", { method: "POST", body: JSON.stringify({ code: data.get("code") }) });
      setRecoveryCodes(result.recovery_codes);
      setTotpSetup(null);
      onNotice({ tone: "ok", text: "เปิด TOTP แล้ว กรุณาเก็บ Recovery Codes" });
    } catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "เปิด TOTP ไม่สำเร็จ" }); }
  }

  async function savePin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api("/api/v1/trade-pin", { method: "POST", body: JSON.stringify({ pin: data.get("pin"), current_pin: data.get("current") || null }) });
      onNotice({ tone: "ok", text: "บันทึก Trade PIN แล้ว" });
      form.reset();
    } catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "บันทึก PIN ไม่สำเร็จ" }); }
  }

  return (
    <div className="view-stack settings-view">
      <article className="panel settings-card">
        <div className="step-head"><span>01</span><div><h2>Exchange connection</h2><p>ใช้ API Key ที่เปิด Read/Trade และปิด Withdraw เท่านั้น</p></div></div>
        <form className="form-stack" onSubmit={connectExchange}>
          <label className="field"><span>OKX API Key</span><input name="key" autoComplete="off" required /></label>
          <label className="field"><span>API Secret</span><input name="secret" type="password" autoComplete="new-password" required /></label>
          <label className="field"><span>Passphrase</span><input name="passphrase" type="password" autoComplete="new-password" /></label>
          <label className="check-field"><input name="attest" type="checkbox" required /><span>ยืนยันว่าปิด Withdraw permission แล้ว</span></label>
          <div className="button-row"><button className="primary-button">Save Exchange</button><button className="secondary-button" type="button" onClick={() => api("/api/v1/exchange/test", { method: "POST" }).then(() => onNotice({ tone: "ok", text: "Exchange test ผ่าน" })).catch((caught: Error) => onNotice({ tone: "warn", text: caught.message }))}>Test connection</button></div>
        </form>
      </article>
      <article className="panel settings-card">
        <div className="step-head"><span>02</span><div><h2>Pair, Strategy & Risk</h2><p>เลือกได้สูงสุด 3 preset และ Active พร้อมกัน 1 รายการ</p></div></div>
        <form className="form-stack" onSubmit={saveProfile}>
          <div className="preset-list">
            {catalog?.presets.map((preset) => <label key={preset.id}><input type="checkbox" checked={selected.includes(preset.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, preset.id].slice(0, 3) : selected.filter((id) => id !== preset.id))} /><span><strong>{preset.label}</strong><small>{preset.symbol} · {preset.live_certified ? "Live certified" : "SIM only"}</small></span></label>)}
          </div>
          <label className="field"><span>Active preset</span><select name="active" required>{selected.map((id) => <option key={id} value={id}>{catalog?.presets.find((preset) => preset.id === id)?.label}</option>)}</select></label>
          <div className="form-grid">
            <label className="field"><span>Risk / trade</span><input name="risk" type="number" min="0.001" max="0.01" step="0.001" defaultValue="0.01" required /></label>
            <label className="field"><span>Allocation %</span><input name="allocation" type="number" min="1" max="10" defaultValue="10" required /></label>
            <label className="field"><span>Daily loss %</span><input name="daily" type="number" min="1" max="3" defaultValue="3" required /></label>
            <label className="field"><span>Leverage</span><input name="leverage" type="number" min="1" max="2" defaultValue="1" required /></label>
          </div>
          <button className="primary-button" disabled={selected.length === 0}>Save & start SIM</button>
        </form>
      </article>
      <article className="panel settings-card">
        <div className="step-head"><span>03</span><div><h2>Security</h2><p>TOTP และ Trade PIN เป็นคนละรหัส</p></div></div>
        <div className="security-grid">
          <section>
            <div className="panel-head"><h2>Two-factor authentication</h2><span>{me.totp_enabled ? "ENABLED" : "REQUIRED"}</span></div>
            {!me.totp_enabled && !totpSetup && <button className="secondary-button" type="button" onClick={() => void setupTotp()}>ตั้งค่า TOTP</button>}
            {totpSetup && <form className="form-stack" onSubmit={enableTotp}>{qr && /* eslint-disable-next-line @next/next/no-img-element */ <img className="qr-code" src={qr} alt="TOTP QR code" />}<code className="secret-code">{totpSetup.secret}</code><label className="field"><span>รหัส 6 หลัก</span><input name="code" inputMode="numeric" minLength={6} maxLength={6} required /></label><button className="primary-button">Enable TOTP</button></form>}
            {recoveryCodes.length > 0 && <div className="recovery-box"><strong>Recovery Codes · แสดงครั้งเดียว</strong>{recoveryCodes.map((code) => <code key={code}>{code}</code>)}</div>}
          </section>
          <section>
            <div className="panel-head"><h2>Trade PIN</h2><span>{me.trade_pin_configured ? "SET" : "REQUIRED"}</span></div>
            <form className="form-stack" onSubmit={savePin}>
              {me.trade_pin_configured && <label className="field"><span>PIN ปัจจุบัน</span><input name="current" type="password" inputMode="numeric" minLength={8} maxLength={12} required /></label>}
              <label className="field"><span>PIN ใหม่ 8–12 หลัก</span><input name="pin" type="password" inputMode="numeric" minLength={8} maxLength={12} pattern="[0-9]{8,12}" required /></label>
              <button className="primary-button">Save Trade PIN</button>
            </form>
          </section>
        </div>
      </article>
    </div>
  );
}

function AdminView({ onNotice }: { onNotice: (notice: Notice) => void }) {
  const [users, setUsers] = useState<Array<Record<string, unknown>>>([]);
  const [tenants, setTenants] = useState<Array<Record<string, unknown>>>([]);
  const [capacity, setCapacity] = useState({ active: 0, capacity: 0 });
  const load = useCallback(async () => {
    try {
      const [userPayload, tenantPayload] = await Promise.all([
        api<{ items: Array<Record<string, unknown>> }>("/api/v1/admin/users"),
        api<{ items: Array<Record<string, unknown>>; active: number; capacity: number }>("/api/v1/admin/tenants"),
      ]);
      setUsers(userPayload.items);
      setTenants(tenantPayload.items);
      setCapacity({ active: tenantPayload.active, capacity: tenantPayload.capacity });
    } catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "โหลด Owner Admin ไม่สำเร็จ" }); }
  }, [onNotice]);
  useEffect(() => { void load(); }, [load]);

  async function approveUser(id: string) {
    try { await api(`/api/v1/admin/users/${id}/status`, { method: "POST", body: JSON.stringify({ status: "active" }) }); onNotice({ tone: "ok", text: "อนุมัติผู้ใช้และสร้าง workspace แล้ว" }); await load(); }
    catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "อนุมัติไม่สำเร็จ" }); }
  }

  async function approveLive(id: string) {
    try { await api(`/api/v1/admin/tenants/${id}/approve-live`, { method: "POST" }); onNotice({ tone: "ok", text: "อนุมัติ Live แล้ว" }); await load(); }
    catch (caught) { onNotice({ tone: "warn", text: caught instanceof Error ? caught.message : "อนุมัติ Live ไม่สำเร็จ" }); }
  }

  return (
    <div className="view-stack admin-view">
      <article className="panel admin-summary"><p className="eyebrow">PILOT CAPACITY</p><strong>{capacity.active}/{capacity.capacity}</strong><span>active engines</span></article>
      <article className="panel admin-card">
        <div className="panel-head"><div><h2>User approval</h2><p>สูงสุด 3 บัญชีรวม Owner</p></div><span>{users.length}</span></div>
        <div className="admin-list">{users.map((user) => <div key={textValue(user.id)}><span><strong>{textValue(user.email)}</strong><small>{textValue(user.role)} · {textValue(user.account_status)}</small></span>{user.account_status !== "active" && <button className="secondary-button compact" onClick={() => void approveUser(textValue(user.id))}>Approve</button>}</div>)}</div>
      </article>
      <article className="panel admin-card">
        <div className="panel-head"><div><h2>Tenant Live</h2><p>Exchange test และ certified profile ต้องผ่านก่อน</p></div><span>{tenants.length}</span></div>
        <div className="admin-list">{tenants.map((tenant) => <div key={textValue(tenant.id)}><span><strong>{textValue(tenant.slug)}</strong><small>{textValue(tenant.status)} · {textValue(tenant.live_status)}</small></span>{tenant.live_status === "requested" && <button className="secondary-button compact" onClick={() => void approveLive(textValue(tenant.id))}>Approve Live</button>}</div>)}</div>
      </article>
    </div>
  );
}

function ConsoleNavigation({ view, isAdmin }: { view: View; isAdmin: boolean }) {
  const items: Array<[View, string, string]> = [
    ["home", "/app", "HOME"],
    ["trade", "/app/trade", "TRADE"],
    ["activity", "/app/activity", "ACTIVITY"],
    ["settings", "/app/settings", "SETTINGS"],
  ];
  if (isAdmin) items.push(["admin", "/app/admin", "ADMIN"]);
  return <nav className="console-nav" aria-label="Console navigation">{items.map(([id, href, label]) => <Link key={id} className={view === id ? "active" : ""} href={href}>{label}</Link>)}</nav>;
}
