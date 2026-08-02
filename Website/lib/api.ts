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

export type RuntimePrice = {
  ok: boolean;
  stale: boolean;
  source: string;
  read_only: boolean;
  symbol: string;
  price: number | null;
  bid?: number | null;
  ask?: number | null;
  timestamp?: string | null;
  age_sec?: number | null;
};

export type ExchangeConnection = {
  exchange_id: string;
  target_id: string;
  key_last4: string;
  status: string;
  tested_at?: number | null;
  capabilities?: {
    /**
     * What the EXCHANGE said about this key's withdrawal permission, not what
     * the user ticked during onboarding. Tri-state on purpose:
     *   true  - the venue confirms withdrawals are disabled
     *   false - the venue reports withdrawals are ENABLED (a finding)
     *   null / undefined - could not be determined; show as unverified, never
     *                      as a pass
     */
    withdraw_disabled_verified?: boolean | null;
    withdraw_permission_checked?: boolean;
    withdraw_permission_detail?: string;
    [key: string]: unknown;
  } | null;
};

export type TelegramConnection = {
  chat_id: string;
  token_last4: string;
  bot_username: string;
  status: string;
  enabled: boolean;
  tested_at?: number | null;
  updated_at?: number | null;
};

export type TelegramPreferences = {
  alerts_enabled: boolean;
  alert_channel: string;
  trade_lifecycle: boolean;
  risk_safety: boolean;
  system_health: boolean;
  periodic_reports: boolean;
  commands_enabled: boolean;
};

export type Bot = {
  tenant: Tenant;
  service_status: string;
  state: Record<string, unknown>;
  exchange_connection: ExchangeConnection | null;
  telegram_connection: TelegramConnection | null;
  api_whitelist_ips?: string[];
};

export type Preset = {
  id: string;
  target_id: string;
  label: string;
  symbol: string;
  asset?: string;
  strategy: string;
  primary_timeframe: string;
  confirm_timeframe: string;
  market_type: string;
  allocation_pct?: number;
  max_position_per_trade_pct?: number;
  allowed_sides?: Array<"long" | "short">;
  /**
   * APPROVAL, not a research result: whether an operator may activate this
   * preset live. Gates canGoLive. A preset can be approved for live and still
   * have failed certification — see certification_status.
   */
  live_certified?: boolean;
  /**
   * VERDICT: did the measurement clear the pre-registered acceptance gate?
   * Independent of both live_certified (approval) and backtest.status
   * (evidence quality).
   */
  certification_status?: "certified" | "failed" | "not_assessed";
  certification_note?: string;
  /**
   * Present when a preset is approved for live WITHOUT a passing verdict. The
   * backend refuses to build the catalog otherwise, so its absence on a live
   * preset means the verdict is "certified".
   */
  operator_override?: {
    decided_by: string;
    decided_at: string;
    reason: string;
  };
  cdc_pure_certified?: boolean;
  stop_loss_required?: boolean;
  execution_profile?: Record<string, unknown>;
  strategy_traits?: string[];
  backtest?: {
    /** EVIDENCE quality only — not whether it passed. */
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
  live_certified?: boolean;
  manual_allowed_sides: Array<"long" | "short">;
  manual_long_live_certified: boolean;
  manual_short_live_certified: boolean;
};

export type TradingProfile = {
  profile: null | {
    preset_ids?: string[];
    active_preset_id: string;
    target_id: string;
    presets: Preset[];
    risk: Record<string, number | boolean>;
  };
  compiled: Record<string, unknown>;
};

export type StrategyCandidate = {
  preset_id: string;
  label: string;
  symbol: string;
  target_id: string;
  strategy: string;
  role: "champion" | "challenger" | "retired";
  mode: "live" | "shadow";
  status: "active" | "warming" | "eligible" | "paused" | string;
  shadow_runtime_status?: "not_connected" | "healthy" | "stale" | string;
  certification_status: "certified" | "failed" | "not_assessed" | string;
  live_certified: boolean;
  certificate_config_fingerprint?: string;
  eligible_for_promotion: boolean;
  eligibility_reasons: string[];
  winning_evaluations: number;
  evaluation_history_count?: number;
  evaluation?: {
    source?: "certificate" | "forward_sim" | string;
    score?: number;
    profit_factor?: number;
    net_return_pct?: number;
    max_drawdown_pct?: number;
    trades?: number;
    forward_days?: number;
    evaluated_at?: number;
    run_id?: string;
    artifact_sha256?: string;
    config_fingerprint?: string;
    venue?: string;
    timeframe?: string;
    data_window_start?: string;
    data_window_end?: string;
    fill_model?: string;
    fees_pct?: number;
    slippage_pct?: number;
    provenance_valid?: boolean;
    note?: string;
  };
  backtest?: Preset["backtest"];
  strategy_traits?: string[];
  certification_note?: string;
};

export type StrategyPool = {
  version: number;
  revision?: number;
  symbol: string;
  target_id: string;
  policy: {
    min_forward_days: number;
    min_forward_trades: number;
    min_profit_factor: number;
    max_drawdown_pct: number;
    score_margin: number;
    winning_evaluations: number;
  };
  champion_id: string | null;
  candidates: StrategyCandidate[];
  promotion?: {
    from?: string | null;
    to?: string;
    status?: string;
    at?: number;
  } | null;
  history: Array<Record<string, unknown>>;
  evaluation_history?: Array<Record<string, unknown>>;
};

export type StrategyPoolsResponse = {
  pools: StrategyPool[];
  promotion_mode: "manual" | string;
  max_candidates: number;
  shadow_runtime?: "not_connected" | "healthy" | "degraded" | string;
  tenant_live_status: string;
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
    management_mode?: "strategy" | "strategy_handoff" | "manual";
    mode: string;
    mark_price: number;
    estimated_quantity: number;
    estimated_notional: number;
    sizing_mode?: "cdc_pure" | "risk_based" | "close_position";
    sizing_basis?: "atr" | "fixed_pct" | null;
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
    this.name = "ApiError";
  }
}

function errorMessage(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.message === "string" && record.message.trim()) {
      const reasons = Array.isArray(record.reasons)
        ? record.reasons.filter((item): item is string => typeof item === "string")
        : [];
      return reasons.length ? `${record.message}: ${reasons.join(", ")}` : record.message;
    }
    try {
      return JSON.stringify(detail);
    } catch {
      return "Something went wrong";
    }
  }
  return "Something went wrong";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    let payload: Record<string, unknown> = {};
    try {
      const parsed = body ? JSON.parse(body) : {};
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        payload = parsed as Record<string, unknown>;
      }
    } catch {
      // Keep the status-specific fallback below for non-JSON proxy errors.
    }
    throw new ApiError(
      errorMessage(payload.detail ?? (body.trim() || undefined)),
      response.status,
    );
  }
  // DELETE and action endpoints may legitimately return 204. Reading text
  // first also avoids a second body-consumption failure for an empty 200.
  const body = await response.text();
  if (!body.trim()) return undefined as T;
  try {
    return JSON.parse(body) as T;
  } catch {
    throw new ApiError("Invalid API response", response.status);
  }
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
  if (value == null || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(parsed)
    : "—";
}
