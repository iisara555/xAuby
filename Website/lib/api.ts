export type Tenant = {
  id: string;
  slug: string;
  status: string;
  live_status: string;
  exchange_id: string;
  market_type: "spot" | "swap";
};

export type User = {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  role: "user" | "platform_admin";
  csrf_token: string;
  tenant: Tenant | null;
  account_status: string;
  password_configured: boolean;
  totp_enabled: boolean;
  mfa_verified: boolean;
  trade_pin_configured: boolean;
};

export type RuntimeSnapshot = {
  ok: boolean;
  stale: boolean;
  source: string;
  read_only: boolean;
  age_sec?: number | null;
  state: Record<string, unknown>;
  detail: Record<string, unknown>;
  currency: Record<string, unknown>;
};

export type ExchangeConnection = {
  exchange_id: string;
  target_id: string;
  key_last4: string;
  status: string;
  tested_at?: number | null;
};

export type Bot = {
  tenant: Tenant;
  service_status: string;
  state: Record<string, unknown>;
  exchange_connection: ExchangeConnection | null;
};

export type Preset = {
  id: string;
  target_id: string;
  label: string;
  symbol: string;
  strategy: string;
  primary_timeframe: string;
  confirm_timeframe: string;
  market_type: string;
  cdc_pure_certified?: boolean;
  stop_loss_required?: boolean;
  execution_profile?: Record<string, unknown>;
  backtest?: {
    status: "validated" | "insufficient" | "pending";
    score_label: string;
    period: string;
    duration: string;
    win_rate_pct: number | null;
    max_drawdown_pct: number | null;
    trades: number | null;
    source: string;
  };
};

export type Target = {
  id: string;
  exchange_id: string;
  label: string;
  market_type: string;
  credential_fields: string[];
  manual_allowed_sides: Array<"long" | "short">;
  manual_long_live_certified: boolean;
  manual_short_live_certified: boolean;
};

export type TradingProfile = {
  profile: null | {
    active_preset_id: string;
    target_id: string;
    presets: Preset[];
    risk: Record<string, number | boolean>;
  };
  compiled: Record<string, unknown>;
};

export type TradeLog = {
  items: Array<Record<string, unknown>>;
  summary: { total?: number; wins?: number; losses?: number; net_pnl?: number; fees?: number; win_rate?: number };
  next_cursor?: number | null;
};

export type OrderPreview = {
  challenge_id: string;
  expires_at: number;
  digest: string;
  preview: {
    symbol: string;
    intent: string;
    side: string;
    mode: string;
    mark_price: number;
    estimated_quantity: number;
    estimated_notional: number;
    sizing_mode?: "cdc_pure" | "risk_based" | "close_position";
    allocation_pct?: number | null;
  };
};

export type Catalog = {
  targets: Target[];
  presets: Preset[];
  features: { manual_trading: boolean; public_signup: boolean; invite_only: boolean };
  limits: { configured_pairs: number; active_pairs: number };
};

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(payload.detail ?? "Something went wrong", response.status);
  }
  return response.json() as Promise<T>;
}

export function csrfHeaders(user: User): HeadersInit {
  return { "X-CSRF-Token": user.csrf_token };
}

export function valueAt(source: Record<string, unknown>, ...keys: string[]): unknown {
  let current: unknown = source;
  for (const key of keys) {
    if (!current || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

export function formatNumber(value: unknown, digits = 2): string {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(parsed)
    : "—";
}
