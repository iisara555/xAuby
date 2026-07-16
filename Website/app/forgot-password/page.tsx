"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { AuthCard } from "@/components/auth-card";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    await api("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email: data.get("email") }) });
    setSent(true);
    setBusy(false);
  }
  return (
    <AuthCard eyebrow="Account recovery" title="Reset your password" description="We will email a single-use link if the address belongs to a pilot account." footer={<Link href="/login">Back to sign in</Link>}>
      {sent ? <div className="success-panel"><strong>Check your inbox</strong><p>The reset link is valid for 30 minutes.</p></div> : (
        <form className="form-stack" onSubmit={submit}>
          <label>Email<input name="email" type="email" autoComplete="email" required /></label>
          <button className="button-primary wide" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
        </form>
      )}
    </AuthCard>
  );
}
