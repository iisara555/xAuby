"use client";

import { Bell, Check, Info, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { useWorkspacePair } from "@/components/workspace-pair";
import { api } from "@/lib/api";
import type { User } from "@/lib/api";

type ActivityData = { events: Array<Record<string, unknown>> };
const IMPORTANT = ["position", "order", "trade", "risk", "halt", "stop", "start", "restart", "engine", "connect", "error", "warning"];
const NOISE = ["tick", "signal", "regime", "indicator", "candle"];

function eventText(event: Record<string, unknown>): string {
  return String(event.event ?? event.event_type ?? event.type ?? event.title ?? "Engine update");
}

function eventTime(event: Record<string, unknown>): number {
  const value = event.timestamp ?? event.created_at ?? event.time;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric < 1e12 ? numeric * 1000 : numeric;
  const parsed = Date.parse(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function important(event: Record<string, unknown>): boolean {
  const text = `${eventText(event)} ${String(event.message ?? event.reason ?? "")} ${String(event.level ?? event.severity ?? "")}`.toLowerCase();
  if (NOISE.some((word) => text.includes(word)) && !text.includes("error") && !text.includes("risk")) return false;
  return IMPORTANT.some((word) => text.includes(word));
}

export function NotificationCenter({ user }: { user: User }) {
  const { selectedSymbol: symbol } = useWorkspacePair();
  const [open, setOpen] = useState(false);
  const [seenAt, setSeenAt] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const storageKey = `xauby.notifications.v1.seen.${user.id}.${user.tenant?.id ?? "none"}.${symbol || "all"}`;
  const { data } = useSWR<ActivityData>(symbol ? `/api/v1/runtime/activity?symbol=${encodeURIComponent(symbol)}&limit=50` : null, api, {
    refreshInterval: () => typeof document === "undefined" || document.visibilityState === "visible" ? 15000 : 0,
    revalidateOnFocus: true,
  });
  const events = useMemo(() => (data?.events ?? []).filter(important).sort((a, b) => eventTime(b) - eventTime(a)), [data]);
  const unread = events.filter((event) => eventTime(event) > seenAt).length;

  useEffect(() => setSeenAt(Number(window.localStorage.getItem(storageKey) ?? 0)), [storageKey]);
  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const closeWithKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeWithKeyboard);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeWithKeyboard);
    };
  }, []);

  function markRead() {
    const latest = Math.max(Date.now(), ...events.map(eventTime));
    window.localStorage.setItem(storageKey, String(latest));
    setSeenAt(latest);
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) markRead();
  }

  return <div className="notification-center" ref={root}>
    <button className="notification-trigger" type="button" onClick={toggle} aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} aria-expanded={open}>
      <Bell size={18} />{unread > 0 && <span>{unread > 9 ? "9+" : unread}</span>}
    </button>
    {open && <section className="notification-popover" aria-label="Notifications">
      <header><div><small>Workspace alerts</small><strong>{symbol || "All pairs"}</strong></div><button type="button" onClick={markRead}><Check size={14} /> Mark read</button></header>
      {events.length ? <ol>{events.slice(0, 12).map((event, index) => {
        const level = String(event.level ?? event.severity ?? "info").toLowerCase();
        const AlertIcon = level.includes("error") || level.includes("warn") ? TriangleAlert : Info;
        return <li key={String(event.id ?? event.timestamp ?? index)}><AlertIcon size={16} /><div><strong>{eventText(event).replaceAll("_", " ")}</strong><p>{String(event.message ?? event.reason ?? "Engine status changed")}</p><time>{formatTime(eventTime(event))}</time></div></li>;
      })}</ol> : <p className="notification-empty">No position, order, risk, or system alerts.</p>}
    </section>}
  </div>;
}

function formatTime(timestamp: number): string {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(timestamp));
}
