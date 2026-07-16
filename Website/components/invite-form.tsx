"use client";

import { ArrowRight } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AuthCard } from "./auth-card";
import { api } from "@/lib/api";

export function InviteForm({ token }: { token: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ email: string }>(`/auth/invite?token=${encodeURIComponent(token)}`)
      .then((result) => setEmail(result.email))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Invitation unavailable"));
  }, [token]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (data.get("password") !== data.get("confirm")) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/auth/invite/accept", {
        method: "POST",
        body: JSON.stringify({ token, password: data.get("password") }),
      });
      router.replace("/app/settings");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not accept invitation");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard eyebrow="Pilot invitation" title="Create your workspace" description={email ? `Your invitation is reserved for ${email}.` : "Checking your invitation…"}>
      <a className="button-google" href={`/auth/google/start?invite=${encodeURIComponent(token)}`}><span>G</span>Continue with Google</a>
      <div className="form-divider"><span>or set a password</span></div>
      <form className="form-stack" onSubmit={submit}>
        <label>Password<input name="password" type="password" autoComplete="new-password" minLength={12} required /></label>
        <label>Confirm password<input name="confirm" type="password" autoComplete="new-password" minLength={12} required /></label>
        <small className="field-help">Use 12+ characters with upper/lowercase letters and a number.</small>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button-primary wide" disabled={busy || !email}>{busy ? "Creating workspace…" : <>Accept invitation <ArrowRight size={18} /></>}</button>
      </form>
    </AuthCard>
  );
}
