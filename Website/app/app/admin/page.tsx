"use client";

import { FormEvent, useState } from "react";
import { MailPlus, UsersRound } from "lucide-react";
import useSWR from "swr";
import { PageHeading } from "@/components/page-heading";
import { StatusPill } from "@/components/status-pill";
import { useCurrentUser } from "@/components/app-shell";
import { api, csrfHeaders } from "@/lib/api";

type Pilot = { id: string; email: string; role: string; account_status: string; created_at: number };
type Users = { items: Pilot[]; capacity: number };

export default function AdminPage() {
  const user = useCurrentUser();
  const { data, mutate } = useSWR<Users>(user.role === "platform_admin" ? "/api/v1/admin/users" : null, api);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api("/api/v1/admin/invites", { method: "POST", headers: csrfHeaders(user), body: JSON.stringify({ email: data.get("email") }) });
      setStatus("Invitation sent.");
      form.reset();
      await mutate();
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : "Invitation failed"); }
    finally { setBusy(false); }
  }
  if (user.role !== "platform_admin") return null;
  return (
    <div className="page-wrap narrow-page">
      <PageHeading eyebrow="Owner controls" title="Pilot access" aside={<StatusPill label={`${data?.items.length ?? 1} / ${data?.capacity ?? 3} users`} />} />
      <section className="admin-grid">
        <article className="card invite-card"><MailPlus size={24} /><div><span>Private pilot</span><h2>Invite a user</h2><p>Each accepted invitation creates one isolated tenant workspace.</p></div><form className="invite-form" onSubmit={invite}><input name="email" type="email" placeholder="pilot@example.com" required /><button className="button-primary" disabled={busy || (data?.items.length ?? 3) >= (data?.capacity ?? 3)}>{busy ? "Sending…" : "Send invite"}</button></form>{status && <p className="field-help" role="status">{status}</p>}</article>
        <article className="card users-card"><div className="section-heading"><div><span>Capacity</span><h2>Users</h2></div><UsersRound size={22} /></div><div className="user-list">{data?.items.map((pilot) => <div key={pilot.id}><span className="avatar">{pilot.email[0].toUpperCase()}</span><span><strong>{pilot.email}</strong><small>{pilot.role === "platform_admin" ? "Owner" : "Pilot"}</small></span><StatusPill label={pilot.account_status} tone={pilot.account_status === "active" ? "good" : "warn"} /></div>)}</div></article>
      </section>
    </div>
  );
}
