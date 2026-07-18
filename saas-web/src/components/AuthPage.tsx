import { useState, type FormEvent, type ReactNode } from "react"
import { api, setCsrf } from "../api"
import { Alert, Button, Card, Field, Input } from "./ui"

type Mode = "login" | "signup" | "forgot" | "reset" | "verify" | "confirm-email"

function modeFromLocation(): Mode {
  const path = window.location.pathname
  if (path.includes("reset-password")) return "reset"
  if (path.includes("verify-email")) return "verify"
  if (path.includes("confirm-email")) return "confirm-email"
  return "login"
}

export function AuthPage({ onAuthenticated }: { onAuthenticated: () => void }): ReactNode {
  const [mode, setMode] = useState<Mode>(modeFromLocation())
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  function switchMode(next: Mode): void {
    window.history.replaceState({}, "", "/login")
    setMode(next); setMessage(""); setError("")
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); setBusy(true); setError(""); setMessage("")
    const data = new FormData(event.currentTarget)
    try {
      if (mode === "login") {
        const result = await api<{ csrf_token: string }>("/auth/login", {
          method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password"), totp_code: data.get("totp") }),
        })
        setCsrf(result.csrf_token); onAuthenticated(); return
      }
      if (mode === "signup") {
        await api("/auth/signup", { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) })
        setMessage("ส่งลิงก์ยืนยันแล้ว กรุณาตรวจอีเมล จากนั้นรอ Owner อนุมัติ")
      } else if (mode === "forgot") {
        await api("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email: data.get("email") }) })
        setMessage("หากอีเมลนี้อยู่ในระบบ เราได้ส่งลิงก์ตั้งรหัสใหม่แล้ว")
      } else {
        const token = new URLSearchParams(window.location.search).get("token") ?? ""
        const endpoint = mode === "reset" ? "/auth/reset-password" : mode === "verify" ? "/auth/verify-email" : "/auth/confirm-email"
        const body = mode === "reset" ? { token, password: data.get("password") } : { token }
        await api(endpoint, { method: "POST", body: JSON.stringify(body) })
        setMessage(mode === "verify" ? "ยืนยันอีเมลแล้ว กรุณารอ Owner อนุมัติ" : "ดำเนินการสำเร็จ กรุณาเข้าสู่ระบบใหม่")
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "เกิดข้อผิดพลาด") }
    finally { setBusy(false) }
  }

  const title = { login: "เข้าสู่ระบบ", signup: "สมัคร xAuby Pilot", forgot: "ลืมรหัสผ่าน", reset: "ตั้งรหัสผ่านใหม่", verify: "ยืนยันอีเมล", "confirm-email": "ยืนยันอีเมลใหม่" }[mode]
  return <main className="auth-shell">
    <div className="brand-lockup"><span className="brand-mark">xA</span><div><strong>xAuby</strong><small>Guarded trading workspace</small></div></div>
    <Card className="auth-card">
      <header><p className="eyebrow">SECURE PILOT</p><h1>{title}</h1><p>ควบคุม Sim, Manual และ Live จาก browser ในพื้นที่แยกของคุณ</p></header>
      {error && <Alert tone="bad">{error}</Alert>}{message && <Alert tone="good">{message}</Alert>}
      <form onSubmit={submit} className="stack">
        {["login", "signup", "forgot"].includes(mode) && <Field label="Email"><Input name="email" type="email" autoComplete="email" required /></Field>}
        {["login", "signup", "reset"].includes(mode) && <Field label={mode === "signup" ? "Password อย่างน้อย 12 ตัว" : "Password"} hint={mode !== "login" ? "อย่างน้อย 12 ตัว พร้อมตัวพิมพ์ใหญ่ พิมพ์เล็ก และตัวเลข" : undefined}><Input name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "login" ? 1 : 12} maxLength={128} required /></Field>}
        {mode === "login" && <Field label="TOTP (ถ้าเปิดใช้)"><Input name="totp" inputMode="numeric" maxLength={6} autoComplete="one-time-code" /></Field>}
        <Button type="submit" disabled={busy}>{busy ? "กำลังดำเนินการ…" : title}</Button>
      </form>
      {mode === "login" && <div className="auth-links"><button onClick={() => switchMode("signup")}>สมัครใช้งาน</button><button onClick={() => switchMode("forgot")}>ลืมรหัสผ่าน</button></div>}
      {mode !== "login" && <button className="text-button" onClick={() => switchMode("login")}>กลับไปเข้าสู่ระบบ</button>}
      {["login", "signup"].includes(mode) && <>
        <a className="google-button" href="/auth/google/start">
          {mode === "signup" ? "สมัครด้วย Google (แนะนำ)" : "ดำเนินการต่อด้วย Google"}
        </a>
        {mode === "signup" && <p className="auth-foot">ไม่ต้องรออีเมลยืนยัน — Owner จะอนุมัติ workspace หลังสมัคร</p>}
      </>}
    </Card>
    <p className="auth-foot">Live ต้องผ่าน Sim, TOTP, Exchange Test และ Owner approval</p>
  </main>
}
