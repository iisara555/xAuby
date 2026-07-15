export type Me = {
  id: string;
  email: string;
  role: "platform_admin" | "user";
  csrf_token: string;
  account_status: string;
  email_verified: boolean;
  totp_enabled: boolean;
  mfa_verified: boolean;
  trade_pin_configured: boolean;
  tenant: null | {
    id: string;
    slug: string;
    status: string;
    live_status: string;
  };
};

export type RuntimeSnapshot = {
  ok: boolean;
  source: "legacy_webui" | "tenant_engine";
  read_only: boolean;
  as_of: number;
  age_sec: number | null;
  stale: boolean;
  state: Record<string, unknown>;
  currency: Record<string, unknown>;
  detail: Record<string, unknown>;
  error?: string;
};

export type Candle = {
  timestamp: number | string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  ema_fast?: number;
  ema_slow?: number;
  zone?: string;
};

export type Catalog = {
  presets: Array<{
    id: string;
    label: string;
    symbol: string;
    strategy: string;
    primary_timeframe: string;
    live_certified: boolean;
  }>;
};

let csrfToken = "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function setCsrfToken(value: string): void {
  csrfToken = value;
}

function errorMessage(payload: unknown, status: number): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (typeof record.detail === "string" && record.detail.trim()) return record.detail;
    if (typeof record.message === "string" && record.message.trim()) return record.message;
    if (Array.isArray(record.detail)) {
      const first = record.detail.find(
        (item) => item && typeof item === "object" && typeof (item as Record<string, unknown>).msg === "string",
      ) as Record<string, unknown> | undefined;
      if (first) return String(first.msg);
    }
  }
  return `ระบบตอบกลับผิดพลาด (HTTP ${status})`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (init.body) headers.set("Content-Type", "application/json");
  if (csrfToken && !["GET", "HEAD"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(errorMessage(payload, response.status), response.status);
  return payload as T;
}

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function numberValue(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function textValue(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}
