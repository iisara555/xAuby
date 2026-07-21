"use client";

import { FormEvent, useEffect, useState } from "react";
import { Check, Copy, KeyRound, MailPlus, Rocket, UsersRound } from "lucide-react";
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
type AdminTenant = {
  id: string;
  slug: string;
  owner_email: string;
  status: string;
  live_status: string;
  exchange_id: string;
  market_type: string;
  created_at: number;
};
type Tenants = { items: AdminTenant[]; capacity: number; active: number };

function tenantStatusTone(status: string): "good" | "warn" | "bad" | "neutral" {
  if (status === "running") return "good";
  if (status === "starting" || status === "degraded" || status === "queued") return "warn";
  if (status === "stopped" || status === "error") return "bad";
  return "neutral";
}

function liveStatusTone(status: string): "good" | "warn" | "bad" | "neutral" {
  if (status === "active") return "good";
  if (status === "requested" || status === "approved") return "warn";
  if (status === "rejected") return "bad";
  return "neutral";
}

export default function AdminPage() {
  const user = useCurrentUser();
  const router = useRouter();
  const { mutate: mutateGlobal } = useSWRConfig();
  const adminReady = user.role === "platform_admin" && user.totp_enabled && user.mfa_verified;
  const { data, error: usersError, mutate } = useSWR<Users>(
    adminReady ? "/api/v1/admin/users" : null,
    api,
  );
  const { data: tenants, error: tenantsError, mutate: mutateTenants } = useSWR<Tenants>(
    adminReady ? "/api/v1/admin/tenants" : null,
    api,
    { refreshInterval: 15000 },
  );
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [inviteResult, setInviteResult] = useState<InviteResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [opsStatus, setOpsStatus] = useState("");
  const [opsBusyId, setOpsBusyId] = useState<string | null>(null);

  async function approveLive(tenantId: string) {
    setOpsBusyId(tenantId);
    setOpsStatus("");
    try {
      await api(`/api/v1/admin/tenants/${tenantId}/approve-live`, {
        method: "POST",
        headers: csrfHeaders(user),
      });
      setOpsStatus("Live approved. The tenant engine is restarting into live mode.");
      await mutateTenants();
    } catch (reason) {
      setOpsStatus(reason instanceof Error ? reason.message : "Approval failed");
    } finally {
      setOpsBusyId(null);
    }
  }

  async function setPilotStatus(userId: string, nextStatus: "active" | "suspended") {
    setOpsBusyId(userId);
    setOpsStatus("");
    try {
      await api(`/api/v1/admin/users/${userId}/status`, {
        method: "POST",
        headers: csrfHeaders(user),
        body: JSON.stringify({ status: nextStatus }),
      });
      setOpsStatus(
        nextStatus === "active" ? "Pilot reactivated." : "Pilot suspended and their engine was stopped.",
      );
      await Promise.all([mutate(), mutateTenants()]);
    } catch (reason) {
      setOpsStatus(reason instanceof Error ? reason.message : "Status change failed");
    } finally {
      setOpsBusyId(null);
    }
  }

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
        <article className="card users-card"><div className="section-heading"><div><span>Capacity</span><h2>Users</h2></div><UsersRound size={22} /></div><div className="user-list">{data?.items.map((pilot) => (
          <div key={pilot.id}>
            <span className="avatar">{(pilot.email?.[0] ?? "?").toUpperCase()}</span>
            <span><strong>{pilot.email}</strong><small>{pilot.role === "platform_admin" ? "Owner" : "Pilot"}</small></span>
            <span className="user-row-actions">
              <StatusPill label={pilot.account_status} tone={pilot.account_status === "active" ? "good" : "warn"} />
              {pilot.role !== "platform_admin" && (
                pilot.account_status === "active" ? (
                  <button type="button" className="button-danger row-action" disabled={opsBusyId === pilot.id} onClick={() => setPilotStatus(pilot.id, "suspended")}>
                    {opsBusyId === pilot.id ? "…" : "Suspend"}
                  </button>
                ) : (
                  <button type="button" className="button-secondary row-action" disabled={opsBusyId === pilot.id} onClick={() => setPilotStatus(pilot.id, "active")}>
                    {opsBusyId === pilot.id ? "…" : "Reactivate"}
                  </button>
                )
              )}
            </span>
          </div>
        ))}</div></article>

        <article className="card tenants-card">
          <div className="section-heading">
            <div><span>Engine capacity</span><h2>Tenants</h2></div>
            <StatusPill label={tenants ? `${tenants.active} / ${tenants.capacity} engines` : "Loading"} tone={tenants && tenants.active >= tenants.capacity ? "warn" : "neutral"} />
          </div>
          {tenantsError && <p className="form-error" role="alert">{tenantsError instanceof Error ? tenantsError.message : "Could not load tenants"}</p>}
          {opsStatus && <p className="field-help" role="status">{opsStatus}</p>}
          <div className="tenant-list">
            {tenants?.items.map((tenant) => (
              <div key={tenant.id}>
                <span><strong>{tenant.owner_email}</strong><small>{tenant.slug} &middot; {tenant.exchange_id}/{tenant.market_type}</small></span>
                <StatusPill label={tenant.status} tone={tenantStatusTone(tenant.status)} />
                <StatusPill label={tenant.live_status.replace(/_/g, " ")} tone={liveStatusTone(tenant.live_status)} />
                {tenant.live_status === "requested" ? (
                  <button type="button" className="button-primary row-action" disabled={opsBusyId === tenant.id} onClick={() => approveLive(tenant.id)}>
                    <Rocket size={14} />{opsBusyId === tenant.id ? "Approving…" : "Approve live"}
                  </button>
                ) : <span className="row-action-spacer" />}
              </div>
            ))}
            {tenants && tenants.items.length === 0 && <p className="field-help">No tenants provisioned yet.</p>}
          </div>
        </article>
      </section>
    </div>
  );
}
