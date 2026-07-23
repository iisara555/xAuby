"use client";

import { ArrowRight, LockKeyhole } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { AuthCard } from "@/components/auth-card";
import { api, type User } from "@/lib/api";

function LoginForm() {
  const search = useSearchParams();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const requestedDestination = search.get("next");
      const destination = requestedDestination === "/app" || requestedDestination?.startsWith("/app/")
        ? requestedDestination
        : "/app";
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"), password: form.get("password"), totp_code: form.get("totp") ?? "",
        }),
      });
      // Verify that the browser accepted the session cookie before leaving the
      // form. A full navigation clears any cached pre-login SWR 401 response;
      // client-only routing could otherwise send a successful first login back
      // here and make the operator submit the form twice.
      await api<User>("/api/v1/me");
      window.location.replace(destination);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard eyebrow="Private pilot" title="Welcome back" description="Sign in to monitor your strategy, risk and exchange connection.">
      <a className="button-google" href="/auth/google/start"><span>G</span>Continue with Google</a>
      <div className="form-divider"><span>or use email</span></div>
      <form className="form-stack" onSubmit={submit}>
        <label>Email<input name="email" type="email" autoComplete="email" required placeholder="you@example.com" /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
        <label>Authenticator or recovery code <small>Only if enabled</small><input name="totp" autoComplete="one-time-code" pattern="[0-9]{6}|[A-Fa-f0-9]{10}" maxLength={10} /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button-primary wide" disabled={busy}>{busy ? "Signing in…" : <>Sign in <ArrowRight size={18} /></>}</button>
      </form>
      <div className="form-links"><Link href="/forgot-password">Forgot password?</Link><span><LockKeyhole size={14} /> Invite only</span></div>
    </AuthCard>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
