"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, setCsrfToken, type Me } from "./api";

type Mode = "login" | "signup" | "forgot" | "reset" | "verify" | "confirm-email";

export function LoginClient({ forcedMode = "login" }: { forcedMode?: Mode }) {
  const [mode, setMode] = useState<Mode>(forcedMode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    api<Me>("/api/v1/me")
      .then((me) => {
        if (!active) return;
        setCsrfToken(me.csrf_token);
        window.location.replace("/app");
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  function switchMode(next: Mode) {
    setMode(next);
    setError("");
    setNotice("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    const form = new FormData(event.currentTarget);
    try {
      if (mode === "login") {
        const result = await api<{ csrf_token: string }>("/auth/login", {
          method: "POST",
          body: JSON.stringify({
            email: form.get("email"),
            password: form.get("password"),
            totp_code: form.get("totp") || "",
          }),
        });
        setCsrfToken(result.csrf_token);
        window.location.replace("/app");
        return;
      }
      if (mode === "signup") {
        await api("/auth/signup", {
          method: "POST",
          body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
        });
        setNotice("สมัครสำเร็จ กรุณาตรวจอีเมลและรอ Owner อนุมัติบัญชี");
      } else if (mode === "forgot") {
        await api("/auth/forgot-password", {
          method: "POST",
          body: JSON.stringify({ email: form.get("email") }),
        });
        setNotice("หากอีเมลอยู่ในระบบ เราได้ส่งลิงก์ตั้งรหัสผ่านใหม่แล้ว");
      } else {
        const token = new URLSearchParams(window.location.search).get("token") ?? "";
        const endpoint =
          mode === "reset"
            ? "/auth/reset-password"
            : mode === "verify"
              ? "/auth/verify-email"
              : "/auth/confirm-email";
        await api(endpoint, {
          method: "POST",
          body: JSON.stringify(mode === "reset" ? { token, password: form.get("password") } : { token }),
        });
        setNotice("ดำเนินการสำเร็จ คุณสามารถเข้าสู่ระบบได้แล้ว");
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "ไม่สามารถดำเนินการได้");
    } finally {
      setBusy(false);
    }
  }

  const titles: Record<Mode, string> = {
    login: "เข้าสู่ระบบ",
    signup: "สมัคร xAuby Pilot",
    forgot: "ลืมรหัสผ่าน",
    reset: "ตั้งรหัสผ่านใหม่",
    verify: "ยืนยันอีเมล",
    "confirm-email": "ยืนยันอีเมลใหม่",
  };
  const needsEmail = ["login", "signup", "forgot"].includes(mode);
  const needsPassword = ["login", "signup", "reset"].includes(mode);

  return (
    <main className="auth-page">
      <Link className="auth-brand" href="/">
        <strong>xAuby</strong>
        <span>Alternative Store of Value<br />Trading System</span>
      </Link>
      <section className="auth-card">
        <header>
          <p className="eyebrow">SECURE PILOT</p>
          <h1>{titles[mode]}</h1>
          <p>ดูสถานะ Bot, Simulation และการเทรดของคุณจาก browser</p>
        </header>
        {error && <div className="notice notice-warn">{error}</div>}
        {notice && <div className="notice">{notice}</div>}
        {mode === "login" || mode === "signup" ? (
          <a className="google-signin" href="/auth/google/start">
            <span>G</span>{mode === "signup" ? "สมัครด้วย Google" : "ดำเนินการต่อด้วย Google"}
          </a>
        ) : null}
        <div className="auth-divider"><span>หรือใช้รหัสผ่าน</span></div>
        <form className="form-stack" onSubmit={submit}>
          {needsEmail && (
            <label className="field">
              <span>Email</span>
              <input name="email" type="email" autoComplete="email" required />
            </label>
          )}
          {needsPassword && (
            <label className="field">
              <span>{mode === "signup" ? "Password อย่างน้อย 12 ตัว" : "Password"}</span>
              <input
                name="password"
                type="password"
                minLength={mode === "login" ? 1 : 12}
                maxLength={128}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
              />
            </label>
          )}
          {mode === "login" && (
            <label className="field">
              <span>TOTP (ถ้าเปิดใช้งาน)</span>
              <input name="totp" inputMode="numeric" minLength={6} maxLength={6} autoComplete="one-time-code" />
            </label>
          )}
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? "กำลังดำเนินการ…" : titles[mode]}
          </button>
        </form>
        <div className="auth-links">
          {mode === "login" ? (
            <>
              <button type="button" onClick={() => switchMode("signup")}>สมัครใช้งาน</button>
              <button type="button" onClick={() => switchMode("forgot")}>ลืมรหัสผ่าน</button>
            </>
          ) : (
            <button type="button" onClick={() => switchMode("login")}>กลับไปเข้าสู่ระบบ</button>
          )}
        </div>
      </section>
      <p className="auth-foot">Live ต้องผ่าน Exchange Test, TOTP, Risk profile และ Owner approval</p>
    </main>
  );
}
