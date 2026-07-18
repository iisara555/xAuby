import { useEffect, useState, type FormEvent, type ReactNode } from "react"
import { Activity, Bot, Copy, KeyRound, LayoutDashboard, LogOut, Settings, ShieldCheck, Users } from "lucide-react"
import QRCode from "qrcode"
import { api, type BotSummary, type Me, setCsrf } from "../api"
import { Alert, Badge, Button, Card, Field, Input } from "./ui"

type View = "overview" | "setup" | "trade" | "security" | "admin"
type Target = { id: string; exchange_id: string; label: string; market_type: string; credential_fields: string[]; live_certified: boolean }
type Catalog = { targets: Target[]; presets: Array<{ id: string; label: string; symbol: string; live_certified: boolean }>; risk: Record<string, { default: number }> }

export function Dashboard({ me, onLogout }: { me: Me; onLogout: () => void }): ReactNode {
  const [view, setView] = useState<View>("overview")
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [bot, setBot] = useState<BotSummary | null>(null)
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")
  useEffect(() => { setCsrf(me.csrf_token) }, [me.csrf_token])

  async function refresh(): Promise<void> {
    if (!me.tenant) return
    try { setBot(await api<BotSummary>("/api/v1/bot")); setCatalog(await api<Catalog>("/api/v1/catalog")) } catch (caught) { setError(caught instanceof Error ? caught.message : "โหลดข้อมูลไม่สำเร็จ") }
  }
  useEffect(() => {
    if (!me.tenant) return
    let active = true
    Promise.all([
      api<BotSummary>("/api/v1/bot"), api<Catalog>("/api/v1/catalog"),
    ]).then(([nextBot, nextCatalog]) => {
      if (active) { setBot(nextBot); setCatalog(nextCatalog) }
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "โหลดข้อมูลไม่สำเร็จ")
    })
    return () => { active = false }
  }, [me.tenant])

  async function action(path: string): Promise<void> {
    setError(""); setNotice("")
    try { await api(path, { method: "POST" }); setNotice("ดำเนินการสำเร็จ"); await refresh() } catch (caught) { setError(caught instanceof Error ? caught.message : "ดำเนินการไม่สำเร็จ") }
  }

  if (me.account_status !== "active" || !me.tenant) return <main className="pending-shell"><Card><p className="eyebrow">ACCOUNT REVIEW</p><h1>สมัครสำเร็จแล้ว</h1><p>บัญชี <strong>{me.email}</strong> อยู่ในสถานะ <Badge tone="warn">{me.account_status}</Badge></p><p className="muted">Owner จะตรวจและอนุมัติก่อนสร้าง trading workspace คุณกลับมา login ภายหลังได้</p><Button onClick={onLogout}>ออกจากระบบ</Button></Card></main>

  const nav = [
    ["overview", "ภาพรวม", LayoutDashboard], ["setup", "ตั้งค่าการเทรด", Settings],
    ["trade", "Manual Trade", Activity], ["security", "Security", ShieldCheck],
    ...(me.role === "platform_admin" ? [["admin", "Owner Admin", Users] as const] : []),
  ] as const
  const state = bot?.state ?? {}
  return <div className="app-shell">
    <aside><div className="brand-lockup"><span className="brand-mark">xA</span><div><strong>xAuby</strong><small>Pilot Console</small></div></div><nav>{nav.map(([id, label, Icon]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}><Icon size={18} />{label}</button>)}</nav><div className="account"><small>{me.role === "platform_admin" ? "OWNER" : "USER"}</small><strong>{me.email}</strong><button onClick={onLogout}><LogOut size={16} />ออกจากระบบ</button></div></aside>
    <main className="workspace"><header className="topbar"><div><p className="eyebrow">{me.tenant.slug}</p><h1>{nav.find(([id]) => id === view)?.[1]}</h1></div><div className="status-row"><Badge tone={me.tenant.live_status === "active" ? "good" : "neutral"}>{me.tenant.live_status}</Badge><Badge tone={me.tenant.status === "running" ? "good" : "warn"}>{me.tenant.status}</Badge></div></header>
      {error && <Alert tone="bad">{error}</Alert>}{notice && <Alert tone="good">{notice}</Alert>}
      {view === "overview" && <Overview state={state} bot={bot} onAction={action} />}
      {view === "setup" && catalog && <Setup catalog={catalog} onDone={(text) => { setNotice(text); void refresh() }} onError={setError} />}
      {view === "trade" && <Trade onDone={setNotice} onError={setError} />}
      {view === "security" && <Security me={me} onDone={setNotice} onError={setError} />}
      {view === "admin" && me.role === "platform_admin" && <Admin onDone={setNotice} onError={setError} />}
    </main>
  </div>
}

function Overview({ state, bot, onAction }: { state: BotSummary["state"]; bot: BotSummary | null; onAction: (path: string) => Promise<void> }): ReactNode {
  const connection = bot?.exchange_connection ?? null
  return <><section className="metric-grid"><Card><span>Engine</span><strong>{String(bot?.service_status ?? "unknown")}</strong><Badge tone={bot?.service_status === "active" ? "good" : "warn"}>service</Badge></Card><Card><span>Exchange</span><strong>{String(connection?.exchange_id ?? connection?.target_id ?? "ยังไม่เชื่อม")}</strong><small>{String(connection?.status ?? "not configured")}</small></Card><Card><span>Position</span><strong>{String(state.position_side ?? "FLAT")}</strong><small>Qty {String(state.quantity ?? "0")}</small></Card><Card><span>Mode</span><strong>{String(state.mode ?? "SIM")}</strong><small>guarded execution</small></Card></section><Card><div className="card-head"><div><h2>Engine control</h2><p>ทุกคำสั่งถูกบันทึกใน audit trail</p></div><div className="button-row"><Button onClick={() => void onAction("/api/v1/bot/start")}>Start</Button><Button className="secondary" onClick={() => void onAction("/api/v1/bot/stop")}>Stop</Button></div></div></Card></>
}

function Setup({ catalog, onDone, onError }: { catalog: Catalog; onDone: (text: string) => void; onError: (text: string) => void }): ReactNode {
  const [selected, setSelected] = useState([catalog.presets[0]?.id ?? ""])
  const [targetId, setTargetId] = useState(catalog.targets[0]?.id ?? "")
  const target = catalog.targets.find((item) => item.id === targetId) ?? null
  async function exchange(event: FormEvent<HTMLFormElement>): Promise<void> { event.preventDefault(); const data = new FormData(event.currentTarget); try { await api("/api/v1/exchange/connect", { method: "POST", body: JSON.stringify({ target_id: targetId, api_key: data.get("key"), api_secret: data.get("secret"), passphrase: data.get("passphrase") ?? "", withdraw_disabled_attested: data.get("attest") === "on" }) }); onDone("บันทึก Exchange แล้ว กรุณากด Test") } catch (e) { onError(e instanceof Error ? e.message : "บันทึกไม่สำเร็จ") } }
  async function profile(event: FormEvent<HTMLFormElement>): Promise<void> { event.preventDefault(); const data = new FormData(event.currentTarget); try { await api("/api/v1/profile", { method: "PUT", body: JSON.stringify({ preset_ids: selected, active_preset_id: String(data.get("active")), risk: { risk_pct: Number(data.get("risk")), max_position_per_trade_pct: Number(data.get("allocation")), max_daily_loss_pct: Number(data.get("daily")), max_leverage: Number(data.get("leverage")), max_open_positions: 1 } }) }); onDone("บันทึก preset และกลับเป็น Sim แล้ว") } catch (e) { onError(e instanceof Error ? e.message : "บันทึกไม่สำเร็จ") } }
  return <div className="two-column"><Card><div className="step"><span>1</span><div><h2>Exchange connection</h2><p>ใช้ API key ที่ปิดสิทธิ์ถอนเงินเท่านั้น</p></div></div><form onSubmit={exchange} className="stack"><Field label="Exchange"><select className="input" value={targetId} onChange={(event) => setTargetId(event.target.value)}>{catalog.targets.map((item) => <option key={item.id} value={item.id}>{item.label}{item.live_certified ? "" : " (Sim only)"}</option>)}</select></Field><Field label="API Key"><Input name="key" required /></Field><Field label="API Secret"><Input name="secret" type="password" required /></Field>{target?.credential_fields.includes("passphrase") && <Field label="Passphrase"><Input name="passphrase" type="password" required /></Field>}<label className="check"><input name="attest" type="checkbox" required /> ยืนยันว่าปิด Withdraw permission แล้ว</label><Button>บันทึก Exchange</Button><Button type="button" className="secondary" onClick={() => api("/api/v1/exchange/test", { method: "POST" }).then(() => onDone("Exchange test ผ่าน")).catch((e: Error) => onError(e.message))}>Test connection</Button></form></Card><Card><div className="step"><span>2</span><div><h2>Pair, Strategy & Risk</h2><p>เลือกได้ 3 presets และ active พร้อมกัน 1 รายการ</p></div></div><form onSubmit={profile} className="stack"><div className="preset-list">{catalog.presets.map((preset) => <label key={preset.id} className="preset"><input type="checkbox" checked={selected.includes(preset.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, preset.id].slice(0, 3) : selected.filter((id) => id !== preset.id))} /><span><strong>{preset.label}</strong><small>{preset.symbol} · {preset.live_certified ? "Live certified" : "Sim only"}</small></span></label>)}</div><Field label="Active preset"><select className="input" name="active">{selected.map((id) => <option key={id} value={id}>{catalog.presets.find((item) => item.id === id)?.label}</option>)}</select></Field><div className="form-grid"><Field label="Risk / trade"><Input name="risk" type="number" step="0.001" min="0.001" max="0.01" defaultValue="0.01" /></Field><Field label="Allocation %"><Input name="allocation" type="number" min="1" max="10" defaultValue="10" /></Field><Field label="Daily loss %"><Input name="daily" type="number" min="1" max="3" defaultValue="3" /></Field><Field label="Leverage"><Input name="leverage" type="number" min="1" max="2" defaultValue="1" /></Field></div><Button>บันทึกและเริ่ม Sim</Button><Button type="button" className="secondary" onClick={() => api("/api/v1/live/request", { method: "POST" }).then(() => onDone("ส่งคำขอ Live แล้ว")).catch((e: Error) => onError(e.message))}>ขออนุมัติ Live</Button></form></Card></div>
}

function Trade({ onDone, onError }: { onDone: (text: string) => void; onError: (text: string) => void }): ReactNode {
  const [challenge, setChallenge] = useState("")
  async function preview(event: FormEvent<HTMLFormElement>): Promise<void> { event.preventDefault(); const data = new FormData(event.currentTarget); try { const result = await api<{ challenge_id: string }>("/api/v1/orders/preview", { method: "POST", body: JSON.stringify({ symbol: data.get("symbol"), intent: data.get("intent"), management_mode: "strategy" }) }); setChallenge(result.challenge_id); onDone("Preview พร้อม ยืนยันภายใน 60 วินาที") } catch (e) { onError(e instanceof Error ? e.message : "Preview ไม่สำเร็จ") } }
  async function confirm(event: FormEvent<HTMLFormElement>): Promise<void> { event.preventDefault(); const data = new FormData(event.currentTarget); try { await api(`/api/v1/orders/${challenge}/confirm`, { method: "POST", body: JSON.stringify({ trade_pin: data.get("pin"), idempotency_key: crypto.randomUUID() }) }); setChallenge(""); onDone("ส่งคำสั่งเข้า execution queue แล้ว") } catch (e) { onError(e instanceof Error ? e.message : "ส่งคำสั่งไม่สำเร็จ") } }
  return <Card><div className="card-head"><div><h2>Manual order ticket</h2><p>Manual bypass signal แต่ไม่ bypass risk, SL และ execution lock</p></div><KeyRound /></div><form onSubmit={preview} className="stack"><div className="form-grid"><Field label="Pair"><Input name="symbol" defaultValue="XAUUSDT" /></Field><Field label="Intent"><select name="intent" className="input"><option>OPEN_LONG</option><option>CLOSE_LONG</option><option>OPEN_SHORT</option><option>CLOSE_SHORT</option></select></Field></div><Button>Preview order</Button></form>{challenge && <form onSubmit={confirm} className="confirm-box"><Alert>ตรวจ pair, side และ exposure ให้ถูกต้องก่อนยืนยัน</Alert><Field label="Trade PIN"><Input name="pin" type="password" inputMode="numeric" required /></Field><Button>ยืนยันคำสั่ง</Button></form>}</Card>
}

function Security({ me, onDone, onError }: { me: Me; onDone: (text: string) => void; onError: (text: string) => void }): ReactNode {
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const [qrCode, setQrCode] = useState("")
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([])
  const [enabled, setEnabled] = useState(me.totp_enabled)

  useEffect(() => {
    if (!setup) return
    let active = true
    QRCode.toDataURL(setup.otpauth_uri, {
      width: 220, margin: 1,
      color: { dark: "#18130a", light: "#ffffff" },
    }).then((value) => { if (active) setQrCode(value) })
      .catch(() => { if (active) onError("สร้าง QR code ไม่สำเร็จ กรุณาใช้ Secret แทน") })
    return () => { active = false }
  }, [onError, setup])

  async function createTotp(): Promise<void> {
    try {
      setRecoveryCodes([])
      setSetup(await api<{ secret: string; otpauth_uri: string }>("/auth/totp/setup", { method: "POST" }))
    } catch (error) { onError(error instanceof Error ? error.message : "สร้าง TOTP ไม่สำเร็จ") }
  }

  async function enableTotp(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    try {
      const result = await api<{ recovery_codes: string[] }>("/auth/totp/enable", {
        method: "POST", body: JSON.stringify({ code: data.get("code") }),
      })
      setRecoveryCodes(result.recovery_codes)
      setEnabled(true)
      setSetup(null)
      onDone("เปิด TOTP แล้ว กรุณาเก็บ Recovery Codes ไว้ในที่ปลอดภัย")
    } catch (error) { onError(error instanceof Error ? error.message : "เปิด TOTP ไม่สำเร็จ") }
  }

  async function copyText(value: string, message: string): Promise<void> {
    try { await navigator.clipboard.writeText(value); onDone(message) }
    catch { onError("คัดลอกไม่สำเร็จ กรุณาเลือกข้อความและคัดลอกเอง") }
  }

  return <div className="two-column"><Card><div className="card-head"><div><h2>Two-factor authentication</h2><p>TOTP จำเป็นก่อนใช้ Live และ Owner Admin</p></div><Badge tone={enabled ? "good" : "warn"}>{enabled ? "Enabled" : "Required"}</Badge></div>
    {!enabled && !setup && <div className="security-start"><p>ใช้ Google Authenticator, Microsoft Authenticator หรือแอป TOTP อื่นได้</p><Button onClick={() => void createTotp()}>เริ่มตั้งค่า TOTP</Button></div>}
    {setup && <><div className="qr-panel">{qrCode && <img className="qr-image" src={qrCode} alt="QR code สำหรับเพิ่ม xAuby ในแอป Authenticator" />}<div><strong>1. สแกน QR code</strong><p>หรือเพิ่มบัญชีด้วย Secret ด้านล่าง</p><div className="secret-row"><code>{setup.secret}</code><Button type="button" className="secondary compact" aria-label="คัดลอก TOTP Secret" onClick={() => void copyText(setup.secret, "คัดลอก TOTP Secret แล้ว")}><Copy size={15} /> คัดลอก</Button></div></div></div><form className="stack" onSubmit={enableTotp}><Field label="2. กรอกรหัส 6 หลักจากแอป"><Input name="code" inputMode="numeric" autoComplete="one-time-code" minLength={6} maxLength={6} pattern="[0-9]{6}" required /></Field><Button>ยืนยันและเปิด TOTP</Button></form></>}
    {recoveryCodes.length > 0 && <Alert tone="warn"><div className="recovery-head"><strong>Recovery Codes — แสดงครั้งเดียว</strong><Button type="button" className="secondary compact" onClick={() => void copyText(recoveryCodes.join("\n"), "คัดลอก Recovery Codes แล้ว")}><Copy size={15} /> คัดลอกทั้งหมด</Button></div><p>เก็บไว้นอก VPS และอย่าส่งให้ผู้อื่น แต่ละรหัสใช้ได้ครั้งเดียว</p><div className="recovery-codes">{recoveryCodes.map((code) => <code key={code}>{code}</code>)}</div></Alert>}
  </Card><Card><h2>Trade PIN</h2><p>ใช้ยืนยัน Manual Order ทุกครั้ง และไม่ควรซ้ำกับรหัสผ่าน</p><form className="stack" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); api("/api/v1/trade-pin", { method: "POST", body: JSON.stringify({ pin: data.get("pin"), current_pin: data.get("current") || null }) }).then(() => onDone("บันทึก Trade PIN แล้ว")).catch((e: Error) => onError(e.message)) }}><Field label="PIN ใหม่ 8–12 หลัก"><Input name="pin" type="password" inputMode="numeric" autoComplete="new-password" minLength={8} maxLength={12} pattern="[0-9]{8,12}" required /></Field>{me.trade_pin_configured && <Field label="PIN ปัจจุบัน"><Input name="current" type="password" inputMode="numeric" autoComplete="current-password" minLength={8} maxLength={12} pattern="[0-9]{8,12}" required /></Field>}<Button>บันทึก Trade PIN</Button></form></Card></div>
}

function Admin({ onDone, onError }: { onDone: (text: string) => void; onError: (text: string) => void }): ReactNode {
  const [users, setUsers] = useState<Array<{ id: string; email: string; account_status: string; role: string }>>([])
  async function load(): Promise<void> { try { const result = await api<{ items: typeof users }>("/api/v1/admin/users"); setUsers(result.items) } catch (e) { onError(e instanceof Error ? e.message : "โหลดผู้ใช้ไม่สำเร็จ") } }
  useEffect(() => {
    let active = true
    api<{ items: typeof users }>("/api/v1/admin/users")
      .then((result) => { if (active) setUsers(result.items) })
      .catch((e: unknown) => { if (active) onError(e instanceof Error ? e.message : "โหลดผู้ใช้ไม่สำเร็จ") })
    return () => { active = false }
  }, [onError])
  async function approve(id: string): Promise<void> { try { await api(`/api/v1/admin/users/${id}/status`, { method: "POST", body: JSON.stringify({ status: "active" }) }); onDone("อนุมัติและสร้าง workspace แล้ว"); await load() } catch (e) { onError(e instanceof Error ? e.message : "อนุมัติไม่สำเร็จ") } }
  return <Card><div className="card-head"><div><h2>User approval</h2><p>Pilot capacity รวม Owner สูงสุด 3 บัญชี</p></div><Bot /></div><div className="table-wrap"><table><thead><tr><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>{users.map((user) => <tr key={user.id}><td>{user.email}</td><td>{user.role}</td><td><Badge tone={user.account_status === "active" ? "good" : "warn"}>{user.account_status}</Badge></td><td>{user.account_status !== "active" && <Button onClick={() => void approve(user.id)}>Approve</Button>}</td></tr>)}</tbody></table></div></Card>
}
