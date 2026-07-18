"use client";

import { FormEvent, useEffect, useState } from "react";
import { Check, Copy, KeyRound, MailPlus, UsersRound } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR, { useSWRConfig } from "swr";
import { PageHeading } from "@/components/page-heading";
import { StatusPill } from "@/components/status-pill";
import { useCurrentUser } from "@/components/app-shell";
import { api, csrfHeaders } from "@/lib/api";

type Pilot = { id: string; email: string; role: string; account_status: string; created_at: number };
type Users = { items: Pilot[]; capacity: number };
type InviteResult = {
  ok: boolean;
  email: string;
  expires_at: number;
  invite_url: string;
  delivery: "sent" | "manual";
  delivery_detail: string;
};

export default function AdminPage() {
  const user = useCurrentUser();
  const router = useRouter();
  const { mutate: mutateGlobal } = useSWRConfig();
  const adminReady = user.role === "platform_admin" && user.totp_enabled && user.mfa_verified;
  const { data, error: usersError, mutate } = useSWR<Users>(
    adminReady ? "/api/v1/admin/users" : null,
    api,
  );
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [inviteResult, setInviteResult] = useState<InviteResult | null>(null);
  const [copied, setCopied] = useState(false);

  async function verifyMfa(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    const form = new FormData(event.currentTarget);
    try {
      await api("/auth/totp/challenge", {
        method: "POST",
        headers: csrfHeaders(user),
        body: JSON.stringify({ code: form.get("code") }),
      });
      await mutateGlobal("/api/v1/me");
      setStatus("Owner verification complete.");
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    setInviteResult(null);
    setCopied(false);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await api<InviteResult>("/api/v1/admin/invites", {
        method: "POST",
        headers: csrfHeaders(user),
        body: JSON.stringify({ email: data.get("email") }),
      });
      setInviteResult(result);
      setStatus(
        result.delivery === "sent"
          ? "Invitation email sent. You can also copy the link below."
          : "Email delivery is unavailable. Copy and send this secure link manually.",
      );
      form.reset();
      await mutate();
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : "Invitation failed"); }
    finally { setBusy(false); }
  }

  async function copyInvite() {
    if (!inviteResult) return;
    try {
      await navigator.clipboard.writeText(inviteResult.invite_url);
      setCopied(true);
      setStatus("Invitation link copied.");
    } catch {
      setStatus("Could not access the clipboard. Select and copy the link manually.");
    }
  }

  useEffect(() => {
    if (user.role !== "platform_admin") router.replace("/app");
  }, [router, user.role]);

  if (user.role !== "platform_admin") return null;

  if (!user.totp_enabled) {
    return (
      <div className="page-wrap narrow-page">
        <PageHeading eyebrow="Owner controls" title="Pilot access" />
        <article className="card admin-gate-card">
          <KeyRound size={24} />
          <div><span>Security required</span><h2>Enable your authenticator</h2><p>Owner actions require TOTP protection.</p></div>
          <Link className="button-primary" href="/app/settings">Open Security settings</Link>
        </article>
      </div>
    );
  }

  if (!user.mfa_verified) {
    return (
      <div className="page-wrap narrow-page">
        <PageHeading eyebrow="Owner controls" title="Pilot access" />
        <article className="card admin-gate-card">
          <KeyRound size={24} />
          <div><span>Owner verification</span><h2>Enter your authenticator code</h2><p>Google sign-in still requires a fresh 6-digit code before sensitive admin actions.</p></div>
          <form className="admin-mfa-form" onSubmit={verifyMfa}>
            <input name="code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" placeholder="000000" required />
            <button className="button-primary" disabled={busy}>{busy ? "Verifying…" : "Verify owner"}</button>
          </form>
          {status && <p className="field-help" role="status">{status}</p>}
        </article>
      </div>
    );
  }

  const atCapacity = Boolean(data && data.items.length >= data.capacity);
  return (
    <div className="page-wrap narrow-page">
      <PageHeading eyebrow="Owner controls" title="Pilot access" aside={<StatusPill label={data ? `${data.items.length} / ${data.capacity} users` : "Loading capacity"} />} />
      <section className="admin-grid">
        <article className="card invite-card">
          <MailPlus size={24} />
          <div><span>Private pilot</span><h2>Invite a user</h2><p>Each accepted invitation creates one isolated tenant workspace.</p></div>
          <form className="invite-form" onSubmit={invite}>
            <input name="email" type="email" placeholder="pilot@example.com" required />
            <button className="button-primary" disabled={busy || !data || atCapacity}>{busy ? "Creating…" : "Create invite"}</button>
          </form>
          {usersError && <p className="form-error" role="alert">{usersError instanceof Error ? usersError.message : "Could not load pilot capacity"}</p>}
          {status && <p className="field-help" role="status">{status}</p>}
          {inviteResult && (
            <div className="invite-result">
              <div>
                <span>{inviteResult.delivery === "sent" ? "Email sent" : "Manual delivery"}</span>
                <strong>{inviteResult.email}</strong>
                <small>Expires {new Date(inviteResult.expires_at * 1000).toLocaleString()}</small>
              </div>
              <div className="invite-link-row">
                <input aria-label="Invitation link" readOnly value={inviteResult.invite_url} onFocus={(event) => event.currentTarget.select()} />
                <button className="button-secondary" type="button" onClick={copyInvite}>{copied ? <Check size={16} /> : <Copy size={16} />}{copied ? "Copied" : "Copy link"}</button>
              </div>
            </div>
          )}
        </article>
        <article className="card users-card"><div className="section-heading"><div><span>Capacity</span><h2>Users</h2></div><UsersRound size={22} /></div><div className="user-list">{data?.items.map((pilot) => <div key={pilot.id}><span className="avatar">{(pilot.email?.[0] ?? "?").toUpperCase()}</span><span><strong>{pilot.email}</strong><small>{pilot.role === "platform_admin" ? "Owner" : "Pilot"}</small></span><StatusPill label={pilot.account_status} tone={pilot.account_status === "active" ? "good" : "warn"} /></div>)}</div></article>
      </section>
    </div>
  );
}
