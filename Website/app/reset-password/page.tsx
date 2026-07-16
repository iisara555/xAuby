"use client";

import { FormEvent, Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthCard } from "@/components/auth-card";
import { api } from "@/lib/api";

function ResetForm() {
  const search = useSearchParams();
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (data.get("password") !== data.get("confirm")) return setError("Passwords do not match");
    setBusy(true);
    try {
      await api("/auth/reset-password", { method: "POST", body: JSON.stringify({ token: search.get("token"), password: data.get("password") }) });
      router.replace("/login");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Reset failed");
      setBusy(false);
    }
  }
  return (
    <AuthCard eyebrow="Account recovery" title="Choose a new password" description="This will sign out every existing session for your account.">
      <form className="form-stack" onSubmit={submit}>
        <label>New password<input name="password" type="password" autoComplete="new-password" minLength={12} required /></label>
        <label>Confirm password<input name="confirm" type="password" autoComplete="new-password" minLength={12} required /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button-primary wide" disabled={busy}>{busy ? "Saving…" : "Save new password"}</button>
      </form>
    </AuthCard>
  );
}

export default function ResetPasswordPage() {
  return <Suspense><ResetForm /></Suspense>;
}
