"use client";

import {
  Activity,
  ChevronUp,
  Gauge,
  LogOut,
  Settings,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api, csrfHeaders, User } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { ProfileAvatar } from "@/components/profile-avatar";
import { ServerHealth } from "@/components/server-health";
import { PairSwitcher } from "@/components/pair-switcher";
import { WorkspacePairProvider } from "@/components/workspace-pair";
import { NotificationCenter } from "@/components/notification-center";

const UserContext = createContext<User | null>(null);

export function useCurrentUser(): User {
  const user = useContext(UserContext);
  if (!user) throw new Error("useCurrentUser must be used inside AppShell");
  return user;
}

const nav = [
  { href: "/app", label: "Home", icon: Gauge },
  { href: "/app/activity", label: "Activity", icon: Activity },
  { href: "/app/settings", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: user, error, isLoading } = useMe();
  const [healthOpen, setHealthOpen] = useState(false);

  useEffect(() => {
    if (!user && error && "status" in error && error.status === 401) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [error, pathname, router, user]);

  if (isLoading || !user) {
    return (
      <main className="auth-shell" aria-busy="true">
        <div className="loading-mark"><span />xAuby</div>
      </main>
    );
  }

  async function logout() {
    try {
      await api("/auth/logout", { method: "POST", headers: csrfHeaders(user!) });
    } finally {
      router.replace("/login");
    }
  }

  const items = user.role === "platform_admin"
    ? [...nav, { href: "/app/admin", label: "Admin", icon: ShieldCheck }]
    : nav;

  return (
    <UserContext.Provider value={user}>
      <WorkspacePairProvider>
        <div className="console-shell">
          <aside className="desktop-rail">
            <Link className="wordmark" href="/app" aria-label="xAuby dashboard">
              <span />xAuby
            </Link>
            <nav aria-label="Primary navigation">
              {items.map(({ href, label, icon: Icon }) => {
                const active = href === "/app" ? pathname === href : pathname.startsWith(href);
                return (
                  <Link className={active ? "rail-link active" : "rail-link"} href={href} key={href}>
                    <Icon size={19} aria-hidden="true" /><span>{label}</span>
                  </Link>
                );
              })}
            </nav>
            <ServerHealth />
            <div className="rail-account">
              <ProfileAvatar name={user.display_name} email={user.email} src={user.avatar_url} size={30} />
              <div><strong>{user.display_name || user.email.split("@")[0]}</strong><span>{user.role === "platform_admin" ? "Owner" : "Pilot"}</span></div>
              <button onClick={logout} aria-label="Sign out"><LogOut size={17} /></button>
            </div>
          </aside>
          <main className="console-main"><div className="workspace-toolbar"><PairSwitcher /><NotificationCenter user={user} /></div>{children}</main>
          <div className={healthOpen ? "mobile-health-strip open" : "mobile-health-strip"}>
            <button type="button" onClick={() => setHealthOpen((value) => !value)} aria-expanded={healthOpen}><span>System health</span><ChevronUp size={15} /></button>
            {healthOpen && <ServerHealth compact />}
          </div>
          <nav className="mobile-nav" aria-label="Mobile navigation">
            {items.map(({ href, label, icon: Icon }) => {
              const active = href === "/app" ? pathname === href : pathname.startsWith(href);
              return (
                <Link className={active ? "active" : ""} href={href} key={href}>
                  <Icon size={20} aria-hidden="true" /><span>{label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
      </WorkspacePairProvider>
    </UserContext.Provider>
  );
}
