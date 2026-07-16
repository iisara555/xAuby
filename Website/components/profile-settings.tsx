"use client";

import { Camera, Trash2 } from "lucide-react";
import { ChangeEvent, FormEvent, useState } from "react";
import { api, csrfHeaders } from "@/lib/api";
import { useCurrentUser } from "@/components/app-shell";
import { ProfileAvatar } from "@/components/profile-avatar";
import { useMe } from "@/lib/hooks";

export function ProfileSettings() {
  const user = useCurrentUser();
  const { mutate } = useMe();
  const [displayName, setDisplayName] = useState(user.display_name || user.email.split("@")[0]);
  const [avatarDataUrl, setAvatarDataUrl] = useState<string | null>(null);
  const [removeAvatar, setRemoveAvatar] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  async function selectAvatar(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 1_000_000) {
      setStatus("Choose a PNG, JPEG or WebP image up to 1 MB.");
      event.target.value = "";
      return;
    }
    try {
      setAvatarDataUrl(await readDataUrl(file));
      setRemoveAvatar(false);
      setStatus("");
    } catch {
      setStatus("Could not read this image. Please choose another file.");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setStatus("");
    try {
      await api("/api/v1/profile/appearance", {
        method: "PATCH",
        headers: csrfHeaders(user),
        body: JSON.stringify({ display_name: displayName, avatar_data_url: avatarDataUrl, remove_avatar: removeAvatar }),
      });
      await mutate();
      setAvatarDataUrl(null);
      setRemoveAvatar(false);
      setStatus("Profile updated.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not update profile");
    } finally {
      setBusy(false);
    }
  }

  const preview = removeAvatar ? null : avatarDataUrl ?? user.avatar_url;
  return <form className="profile-settings" onSubmit={submit}>
    <div className="profile-photo-editor">
      <ProfileAvatar name={displayName} email={user.email} src={preview} size={88} />
      <div><strong>Profile photo</strong><small>PNG, JPEG or WebP · maximum 1 MB</small><div className="profile-photo-actions"><label className="button-secondary"><Camera size={16} />Choose image<input type="file" accept="image/png,image/jpeg,image/webp" onChange={selectAvatar} /></label>{(user.avatar_url || avatarDataUrl) && <button type="button" className="button-secondary" onClick={() => { setAvatarDataUrl(null); setRemoveAvatar(true); }}><Trash2 size={16} />Remove</button>}</div></div>
    </div>
    <div className="form-stack profile-fields">
      <label>Display name<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={40} required /></label>
      <label>Email<input value={user.email} disabled /></label>
    </div>
    <div className="profile-save-row"><button className="button-primary" disabled={busy}>{busy ? "Saving…" : "Save profile"}</button>{status && <p className="field-help" role="status">{status}</p>}</div>
  </form>;
}

function readDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
