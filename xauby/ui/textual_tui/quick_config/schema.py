"""Declarative schema for the Quick Config submenus.

Each submenu is a list of ``Field`` descriptors. A field's ``get``/``set`` call
the launcher's existing pure config helpers (``xauby.launcher.config_io`` +
``_quick_config_load_values``), so the Textual layer carries no persistence logic
of its own. One generic screen renders any schema; this keeps ~13 submenus DRY
and visually consistent with the main menu.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Callable, List, Optional, Tuple

from xauby.launcher.config_io import (
    _load_bot_yaml,
    _set_pair_strategy_values,
    _set_yaml_path,
    list_installed_strategies,
    update_env_variable,
    update_yaml_config,
)


@dataclass
class Ctx:
    """A fresh snapshot of config + computed values for one render/write."""

    cfg: dict
    v: dict
    focus_symbol: str


def build_ctx() -> Ctx:
    """Load a fresh config + value snapshot (never cached across edits)."""
    from xauby.launcher.quick_config import _quick_config_load_values

    v = _quick_config_load_values()
    return Ctx(cfg=_load_bot_yaml(), v=v, focus_symbol=v["focus_symbol"])


@dataclass
class Field:
    key: str
    label: str
    kind: str  # toggle | number | choice | text | action
    get: Callable[[Ctx], Any]
    set: Optional[Callable[[Any, Ctx], bool]] = None
    fmt: Optional[Callable[[Any, Ctx], str]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    is_int: bool = False
    unit: str = ""
    secret: bool = False  # text fields: mask input + never echo the value
    choices: Optional[Callable[[Ctx], List[Tuple[str, str]]]] = None
    run: Optional[Callable[[Any, Ctx], None]] = None  # (screen, ctx)
    enabled: Callable[[Ctx], bool] = dataclass_field(default=lambda c: True)
    # Optional cross-field check run on the entered value before persisting;
    # returns an error message (rejects the write) or None (accept).
    validate: Optional[Callable[[Any, Ctx], Optional[str]]] = None
    color: str = "default"  # default | good | live | action | muted
    needs_restart: bool = True

    def display(self, value: Any, ctx: Ctx) -> str:
        if self.fmt is not None:
            return self.fmt(value, ctx)
        if self.kind == "toggle":
            return "ON" if value else "OFF"
        if self.kind == "number":
            shown = f"{int(value)}" if self.is_int else f"{float(value):g}"
            return f"{shown}{self.unit}"
        return str(value)


@dataclass
class Submenu:
    id: str
    title: str
    fields: Callable[[Ctx], List[Field]]


# ── Shared helpers ───────────────────────────────────────────────────────────

def _set_target(symbol: str, c: Ctx) -> bool:
    import xauby.launcher.quick_config as qc

    qc._QUICK_CONFIG_TARGET_SYMBOL = symbol
    return True


def _target_field() -> Field:
    return Field(
        "target", "Target pair", "choice",
        get=lambda c: c.v["focus_symbol"], fmt=lambda val, c: val,
        choices=lambda c: [(s, s) for s in c.v["symbols"]],
        color="action", needs_restart=False, set=_set_target,
    )


def _yaml_number(key: str, label: str, path: str, *, lo=None, hi=None,
                 is_int: bool = False, unit: str = "", default=0,
                 also_path: Optional[str] = None) -> Field:
    def setter(value, c, p=path, ap=also_path):
        ok = True
        if ap is not None:
            ok = _set_yaml_path(ap, value) and ok
        return _set_yaml_path(p, value) and ok

    return Field(
        key, label, "number",
        get=lambda c, p=path, d=default: _dig(c.cfg, p, d),
        minimum=lo, maximum=hi, is_int=is_int, unit=unit, set=setter,
    )


def _yaml_toggle(key: str, label: str, path: str) -> Field:
    return Field(
        key, label, "toggle",
        get=lambda c, p=path: bool(_dig(c.cfg, p, False)),
        set=lambda new, c, p=path: _set_yaml_path(p, new),
    )


def _dig(cfg: dict, dotted: str, default):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
        if node is None:
            return default
    return node


# ── Schemas ──────────────────────────────────────────────────────────────────

def build_trading(ctx: Ctx) -> List[Field]:
    sym = ctx.v["focus_symbol"]
    sl_live = lambda c: not c.v["stop_loss_disabled"]

    def set_mode(live: bool, c: Ctx) -> bool:
        update_yaml_config(sim_only=not live)
        update_env_variable("LIVE_TRADING", str(live).lower())
        return True

    def set_risk(pct: float, c: Ctx) -> bool:
        update_yaml_config(max_risk=pct / 100.0, symbol=c.focus_symbol)
        return True

    def pair_param(param: str):
        return lambda val, c: _set_pair_strategy_values(
            _load_bot_yaml(), c.focus_symbol, params={param: val}
        )

    def strategy_choices(c: Ctx):
        return [(s, s) for s in list_installed_strategies()]

    return [
        Field("sim", "Trading mode", "toggle",
              get=lambda c: not c.v["sim_only"],
              fmt=lambda val, c: "LIVE" if val else "SIMULATION",
              set=set_mode, color="live"),
        Field("risk", f"Pair risk ({sym})", "number",
              get=lambda c: c.v["max_risk"] * 100, minimum=0.1, maximum=10.0,
              unit="%", fmt=lambda val, c: f"{val:.2f}%", set=set_risk),
        Field("sl_atr", f"Stop loss ATR mult ({sym})", "number",
              get=lambda c: c.v["sl_atr"], minimum=1.0, maximum=5.0, unit="x",
              fmt=lambda val, c: f"{val:.1f}x", enabled=sl_live,
              set=pair_param("sl_atr_mult")),
        Field("be", "Breakeven SL", "toggle", get=lambda c: c.v["be_enabled"],
              enabled=sl_live, set=pair_param("breakeven_sl_enabled")),
        Field("be_trig", "Breakeven trigger ATR", "number",
              get=lambda c: c.v["be_activation"], minimum=0.5, maximum=5.0,
              unit="x", fmt=lambda val, c: f"{val:.1f}x", enabled=sl_live,
              set=pair_param("breakeven_activation_atr_mult")),
        Field("be_buf", "Breakeven buffer ATR", "number",
              get=lambda c: c.v["be_buffer"], minimum=0.0, maximum=2.0,
              unit="x", fmt=lambda val, c: f"{val:.1f}x", enabled=sl_live,
              set=pair_param("breakeven_buffer_atr_mult")),
        Field("d1", "Daily (D1) regime filter", "toggle",
              get=lambda c: c.v["use_d1_regime_filter"],
              set=pair_param("use_d1_regime_filter")),
        Field("strat", f"Pair strategy ({sym})", "choice",
              get=lambda c: c.v["active_strategy"], fmt=lambda val, c: val,
              choices=strategy_choices, color="action",
              set=lambda val, c: _set_pair_strategy_values(
                  _load_bot_yaml(), c.focus_symbol, strategy=val)),
        _target_field(),
    ]


def build_global_trading(ctx: Ctx) -> List[Field]:
    from xauby.runtime.symbol_utils import ALLOWED_TIMEFRAMES

    return [
        _yaml_number("max_open_positions", "Max open positions",
                     "trading.max_open_positions", lo=1, hi=50, is_int=True, default=2,
                     also_path="risk.max_open_positions"),
        _yaml_number("interval_seconds", "Tick interval", "trading.interval_seconds",
                     lo=5, hi=3600, is_int=True, unit="s", default=60),
        Field("timeframe", "Primary timeframe", "choice",
              get=lambda c: _dig(c.cfg, "trading.timeframe", "4h"),
              choices=lambda c: [(tf, tf) for tf in ALLOWED_TIMEFRAMES],
              color="action", set=lambda v, c: _set_yaml_path("trading.timeframe", v)),
        _yaml_number("min_order_amount", "Min order amount", "trading.min_order_amount",
                     lo=0.0, default=10.0),
        _yaml_number("max_position_per_trade_pct", "Max position per trade",
                     "trading.max_position_per_trade_pct", lo=0.0, hi=100.0, unit="%",
                     default=48.0, also_path="portfolio.position_sizing.max_position_per_trade_pct"),
    ]


def build_risk_guards(ctx: Ctx) -> List[Field]:
    return [
        _yaml_toggle("dd_enabled", "Drawdown circuit-breaker",
                     "risk.drawdown_guard.enabled"),
        _yaml_number("max_drawdown_pct", "Max drawdown",
                     "risk.drawdown_guard.max_drawdown_pct", lo=1.0, hi=100.0,
                     unit="%", default=20.0),
        _yaml_toggle("close_positions", "Force-close on breach",
                     "risk.drawdown_guard.close_positions"),
        _yaml_number("max_daily_loss_pct", "Max daily loss", "risk.max_daily_loss_pct",
                     lo=0.0, hi=100.0, unit="%", default=10.0,
                     also_path="trading.max_daily_loss_pct"),
        _yaml_number("max_consecutive_losses", "Max consecutive losses",
                     "trading.max_consecutive_losses", lo=0, hi=50, is_int=True, default=2),
    ]


def build_portfolio(ctx: Ctx) -> List[Field]:
    sym = ctx.v["focus_symbol"]
    return [
        _yaml_number("initial_balance", "Initial balance", "portfolio.initial_balance",
                     lo=0.0, default=1000.0),
        _target_field(),
        Field("allocation", f"Allocation % ({sym})", "number",
              get=lambda c: _dig(c.cfg, f"portfolio.symbols.{c.focus_symbol}.allocation_pct", 50.0),
              minimum=0.0, maximum=100.0, unit="%",
              set=lambda v, c: _set_yaml_path(
                  f"portfolio.symbols.{c.focus_symbol}.allocation_pct", v)),
    ]


_STRAT_PARAM_FIELDS = [
    ("rsi_min", "RSI min", 0.0, 100.0, False),
    ("rsi_max", "RSI max", 0.0, 100.0, False),
    ("vol_min_ratio", "Volume min ratio", 0.0, 10.0, False),
    ("trailing_atr_mult", "Trailing ATR multiplier", 0.0, 10.0, False),
    ("cool_down_minutes", "Cooldown (minutes)", 0.0, 100000.0, True),
]


def build_strategy_params(ctx: Ctx) -> List[Field]:
    from xauby.launcher.quick_config import _strategy_cfg_for_symbol

    fields: List[Field] = [_target_field()]
    for key, label, lo, hi, is_int in _STRAT_PARAM_FIELDS:
        validate = None
        if key == "rsi_min":
            def validate(v, c):  # noqa: F811
                scfg = _strategy_cfg_for_symbol(c.cfg, c.focus_symbol)
                return "RSI min must be lower than RSI max" if v >= float(scfg.get("rsi_max", 100.0)) else None
        elif key == "rsi_max":
            def validate(v, c):  # noqa: F811
                scfg = _strategy_cfg_for_symbol(c.cfg, c.focus_symbol)
                return "RSI max must be higher than RSI min" if v <= float(scfg.get("rsi_min", 0.0)) else None
        fields.append(Field(
            key, label, "number",
            get=lambda c, k=key: _strategy_cfg_for_symbol(c.cfg, c.focus_symbol).get(k, 0),
            minimum=lo, maximum=hi, is_int=is_int, validate=validate,
            set=lambda v, c, k=key: _set_pair_strategy_values(
                _load_bot_yaml(), c.focus_symbol, params={k: v}),
        ))
    return fields


def build_regime_router(ctx: Ctx) -> List[Field]:
    return [
        _yaml_toggle("rr_enabled", "Regime router enabled",
                     "architecture.regime_router_enabled"),
        _yaml_number("confidence_threshold", "Confidence threshold",
                     "regime_router.confidence_threshold", lo=0.0, hi=1.0, default=0.7),
        _yaml_number("debounce_candles", "Debounce candles",
                     "regime_router.debounce_candles", lo=0, hi=100, is_int=True, default=3),
        _yaml_number("recovery_candles", "Recovery candles",
                     "regime_router.recovery_candles", lo=0, hi=100, is_int=True, default=3),
        _yaml_number("force_close_candles", "Force-close candles",
                     "regime_router.force_close_candles", lo=0, hi=100, is_int=True, default=6),
        _yaml_toggle("instant_switch", "Instant switch when flat",
                     "regime_router.instant_switch_when_flat"),
        Field("mapping", "Edit regime → strategy mapping", "action",
              get=lambda c: "", fmt=lambda v, c: "›", color="action",
              run=_push_submenu("regime_mapping")),
    ]


SYS_FLAGS = [
    ("per_symbol_execution_mode", "Per-symbol execution mode"),
    ("event_bus_enabled", "Event bus"),
]


def build_system_features(ctx: Ctx) -> List[Field]:
    def mk(flag: str, label: str) -> Field:
        return Field(
            flag, label, "toggle",
            get=lambda c, f=flag: bool((c.cfg.get("architecture") or {}).get(f)),
            set=lambda new, c, f=flag: _set_yaml_path(f"architecture.{f}", new),
        )

    return [mk(f, l) for f, l in SYS_FLAGS]


def _push_submenu(sid: str):
    def run(scr, ctx):
        from xauby.ui.textual_tui.quick_config.screens import QuickConfigScreen
        scr.app.push_screen(QuickConfigScreen(scr.db, sid))
    return run


def _env_text(key: str, label: str, env_var: str, display_key: str, *,
              secret: bool = True) -> Field:
    def setter(value, c, var=env_var):
        update_env_variable(var, value)
        os.environ[var] = value
        return True

    return Field(key, label, "text",
                 get=lambda c, dk=display_key: c.v[dk], set=setter, secret=secret,
                 color="action", needs_restart=False)


def build_alerts(ctx: Ctx) -> List[Field]:
    def set_tg_enabled(new, c):
        update_env_variable("TELEGRAM_ENABLED", str(new).lower())
        os.environ["TELEGRAM_ENABLED"] = str(new).lower()
        return True

    return [
        Field("tg_enabled", "Telegram alerts", "toggle",
              get=lambda c: c.v["tg_enabled"], set=set_tg_enabled, needs_restart=False),
        _env_text("tg_token", "Telegram bot token", "TELEGRAM_BOT_TOKEN", "tg_tok_disp"),
        Field("tg_chat", "Telegram chat ID", "text",
              get=lambda c: c.v["tg_chat_id"] or "EMPTY", secret=False, color="action",
              needs_restart=False,
              set=lambda v, c: (update_env_variable("TELEGRAM_CHAT_ID", v)
                                or os.environ.__setitem__("TELEGRAM_CHAT_ID", v) or True)),
        Field("tg_test", "Send test Telegram message", "action",
              get=lambda c: "", fmt=lambda v, c: "›", color="action",
              run=lambda scr, c: _run_test_telegram(scr)),
        Field("schedule", "Notification schedule & thresholds", "action",
              get=lambda c: "", fmt=lambda v, c: "›", color="action",
              run=_push_submenu("notifications")),
    ]


def _run_test_telegram(scr) -> None:
    from xauby.launcher.maintenance import send_test_telegram

    ok = send_test_telegram()
    scr.notify("Test message sent" if ok else "Telegram not configured or send failed",
               severity="information" if ok else "error")


_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_notifications(ctx: Ctx) -> List[Field]:
    return [
        _yaml_toggle("wr_enabled", "Weekly review", "weekly_review.enabled"),
        Field("wr_day", "Weekly review day (0=Mon..6=Sun)", "number",
              get=lambda c: _dig(c.cfg, "weekly_review.day_of_week", 6),
              minimum=0, maximum=6, is_int=True,
              fmt=lambda v, c: f"{int(v)} ({_DOW[int(v)] if 0 <= int(v) <= 6 else v})",
              set=lambda v, c: _set_yaml_path("weekly_review.day_of_week", v)),
        _yaml_number("wr_hour", "Weekly review hour (UTC)", "weekly_review.hour_utc",
                     lo=0, hi=23, is_int=True, default=17),
        _yaml_toggle("dd_enabled", "Daily digest", "daily_digest.enabled"),
        _yaml_number("dd_hour", "Daily digest hour (UTC)", "daily_digest.hour_utc",
                     lo=0, hi=23, is_int=True, default=10),
        _yaml_number("regime_score", "Regime score alert threshold",
                     "notifications.regime_score_threshold", lo=0, hi=100, is_int=True, default=10),
        _yaml_number("confirm_timeout", "Semi-auto confirm timeout",
                     "notifications.semi_auto_confirm_timeout_seconds", lo=5, hi=3600,
                     is_int=True, unit="s", default=60),
    ]


def build_macro(ctx: Ctx) -> List[Field]:
    return [
        _yaml_toggle("guard", "Macro sentiment guard", "macro_sentiment_guard.enabled"),
        _yaml_toggle("use_fred", "FRED rates", "macro_sentiment_guard.use_fred"),
        _env_text("fred_key", "FRED API key", "FRED_API_KEY", "fred_key_disp"),
        Field("ai_news", "AI news provider settings", "action",
              get=lambda c: c.v["news_provider"], fmt=lambda v, c: v, color="action",
              run=_push_submenu("ai_news")),
    ]


_AI_PROVIDERS = ["gemini", "openai", "claude", "deepseek", "qwen", "minimax"]


def build_ai_news(ctx: Ctx) -> List[Field]:
    def set_ai_key(value, c):
        var = c.v["active_key_var"]
        update_env_variable(var, value)
        os.environ[var] = value
        return True

    return [
        _yaml_toggle("use_news", "AI news sentiment", "macro_sentiment_guard.use_news"),
        Field("provider", "Provider", "choice",
              get=lambda c: c.v["news_provider"], color="action",
              choices=lambda c: [(p, p) for p in _AI_PROVIDERS],
              set=lambda v, c: _set_yaml_path("macro_sentiment_guard.news_provider", v)),
        Field("model", "Model", "text",
              get=lambda c: c.v["news_model"] or "DEFAULT", secret=False, color="action",
              set=lambda v, c: _set_yaml_path("macro_sentiment_guard.news_model", v)),
        Field("base_url", "Base URL", "text",
              get=lambda c: c.v["news_base_url"] or "DEFAULT", secret=False, color="action",
              set=lambda v, c: _set_yaml_path("macro_sentiment_guard.news_base_url", v)),
        Field("ai_key", "API key", "text",
              get=lambda c: c.v["ai_key_disp"], secret=True, color="action",
              needs_restart=False, set=set_ai_key),
    ]


def _cred_field(key: str, label: str, env_var: str, *, secret: bool = True) -> Field:
    def getter(c, var=env_var):
        val = os.environ.get(var, "")
        return f"SET (...{val[-6:]})" if val else "EMPTY"

    def setter(value, c, var=env_var):
        update_env_variable(var, value)
        os.environ[var] = value
        return True

    return Field(key, f"{label} ({env_var})", "text", get=getter, set=setter,
                 secret=secret, color="action", needs_restart=False)


def _run_conn_test(scr) -> None:
    from xauby.launcher.quick_config import exchange_connection_check

    def work():
        ok, msg = exchange_connection_check(_load_bot_yaml())
        scr.app.call_from_thread(
            scr.notify, msg, severity="information" if ok else "error")

    scr.run_worker(work, thread=True)


def build_exchange(ctx: Ctx) -> List[Field]:
    try:
        from xauby.runtime.exchange_config import credential_env_names
        key_env, secret_env, base_env = credential_env_names(ctx.cfg)
    except Exception:
        key_env, secret_env, base_env = (
            "BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_BASE_URL")
    try:
        from xauby.api.exchange_registry import available_exchanges
        providers = available_exchanges()
    except Exception:
        providers = ["binance", "ccxt"]

    return [
        Field("provider", "Provider", "choice",
              get=lambda c: _dig(c.cfg, "exchange.provider", "binance"),
              choices=lambda c: [(p, p) for p in providers], color="action",
              set=lambda v, c: _set_yaml_path("exchange.provider", v)),
        Field("ccxt_id", "CCXT id", "text",
              get=lambda c: _dig(c.cfg, "exchange.ccxt_id", "(unset)"),
              secret=False, color="action",
              set=lambda v, c: _set_yaml_path("exchange.ccxt_id", v.lower())),
        Field("market_type", "Market type", "choice",
              get=lambda c: _dig(c.cfg, "exchange.market_type", "spot"), color="action",
              choices=lambda c: [("spot", "spot"), ("swap", "swap")],
              set=lambda v, c: _set_yaml_path("exchange.market_type", v)),
        Field("margin_mode", "Margin mode", "choice",
              get=lambda c: _dig(c.cfg, "exchange.margin_mode", "isolated"), color="action",
              choices=lambda c: [("isolated", "isolated")],
              set=lambda v, c: _set_yaml_path("exchange.margin_mode", v)),
        _yaml_number("default_leverage", "Default leverage", "derivatives.default_leverage",
                     lo=1.0, hi=3.0, default=1.0),
        _yaml_number("max_leverage", "Maximum leverage", "derivatives.max_leverage",
                     lo=1.0, hi=3.0, default=3.0),
        Field("quote_asset", "Quote asset", "text",
              get=lambda c: _dig(c.cfg, "exchange.quote_asset", "USDT"),
              secret=False, color="action",
              set=lambda v, c: _set_yaml_path("exchange.quote_asset", v.upper())),
        _yaml_number("fee_pct", "Fee % (fraction)", "exchange.fee_pct",
                     lo=0.0, hi=0.1, default=0.001),
        _cred_field("ex_key", "API key", key_env),
        _cred_field("ex_secret", "API secret", secret_env),
        _cred_field("ex_base", "Base URL", base_env, secret=False),
        Field("test", "Test connection", "action",
              get=lambda c: "", fmt=lambda v, c: "›", color="action",
              run=lambda scr, c: _run_conn_test(scr)),
        _yaml_toggle("plugin_registry", "Exchange plugin registry",
                     "architecture.exchange_plugin_registry_enabled"),
    ]


def build_regime_mapping(ctx: Ctx) -> List[Field]:
    from xauby.runtime.architecture_config import DEFAULT_REGIME_ROUTER_MAPPING

    try:
        strategies = list_installed_strategies()
    except Exception:
        strategies = ["cdc_action_zone"]
    choices = [(s, s) for s in strategies] + [("NO_TRADE", "NO_TRADE (block buys)")]
    fields: List[Field] = []
    for reg in DEFAULT_REGIME_ROUTER_MAPPING:
        def getter(c, r=reg):
            mapping = (c.cfg.get("regime_router", {}) or {}).get("mapping", {}) or {}
            if r in mapping:
                value = mapping.get(r)
            else:
                value = DEFAULT_REGIME_ROUTER_MAPPING.get(r)
            return "NO_TRADE" if value in (None, "None", "null", "") else value

        def setter(v, c, r=reg):
            return _set_yaml_path(f"regime_router.mapping.{r}",
                                  None if v == "NO_TRADE" else v)

        fields.append(Field(reg, reg, "choice", get=getter, fmt=lambda val, c: val,
                            choices=lambda c, ch=choices: ch, color="action", set=setter))
    return fields


def build_multipair(ctx: Ctx) -> List[Field]:
    def action(key, label, sid):
        return Field(key, label, "action", get=lambda c: "", fmt=lambda v, c: "›",
                     color="action", run=_push_submenu(sid))

    return [
        action("timeframes", "Pair timeframes", "pair_timeframes"),
        action("pairlist", "Manage pairlist (enable / disable / add)", "pairlist"),
        action("focus", "Dashboard focus symbol", "dashboard_focus"),
    ]


def _wl_asset(base: str):
    from xauby.runtime.pair_config import load_whitelist

    for x in (load_whitelist(".").get("assets") or []):
        if isinstance(x, dict) and str(x.get("symbol", "")).upper() == base:
            return x
    return {}


def build_pair_timeframes(ctx: Ctx) -> List[Field]:
    from xauby.runtime.pair_config import load_whitelist, set_pair_timeframes
    from xauby.runtime.symbol_utils import ALLOWED_TIMEFRAMES

    assets = [a for a in (load_whitelist(".").get("assets") or []) if isinstance(a, dict)]
    tfs = [(t, t) for t in ALLOWED_TIMEFRAMES]
    fields: List[Field] = []
    for a in assets:
        base = str(a.get("symbol", "")).upper()

        def set_p(v, c, b=base):
            set_pair_timeframes(b, v, _wl_asset(b).get("confirm_timeframe"), project_root=".")
            return True

        def set_c(v, c, b=base):
            set_pair_timeframes(b, _wl_asset(b).get("primary_timeframe", "4h"), v, project_root=".")
            return True

        fields.append(Field(f"{base}_primary", f"{base} primary TF", "choice",
                            get=lambda c, b=base: _wl_asset(b).get("primary_timeframe", "4h"),
                            fmt=lambda val, c: val, choices=lambda c: tfs, color="action",
                            needs_restart=False, set=set_p))
        fields.append(Field(f"{base}_confirm", f"{base} confirm TF", "choice",
                            get=lambda c, b=base: _wl_asset(b).get("confirm_timeframe", "1d"),
                            fmt=lambda val, c: val, choices=lambda c: tfs, color="action",
                            needs_restart=False, set=set_c))
    return fields


def build_pairlist(ctx: Ctx) -> List[Field]:
    from xauby.runtime.pair_config import load_whitelist, update_whitelist_asset, add_whitelist_asset

    assets = [a for a in (load_whitelist(".").get("assets") or []) if isinstance(a, dict)]
    fields: List[Field] = []
    for a in assets:
        base = str(a.get("symbol", "")).upper()
        fields.append(Field(f"pair_{base}", base, "toggle",
                            get=lambda c, b=base: bool(_wl_asset(b).get("enabled")),
                            needs_restart=False,
                            set=lambda new, c, b=base: (
                                update_whitelist_asset(b, enabled=new, project_root=".") or True)))

    def add_sym(v, c):
        add_whitelist_asset(v.upper().replace("USDT", ""), enabled=True, project_root=".")
        return True

    fields.append(Field("add", "Add symbol", "text", get=lambda c: "(e.g. BTC)",
                        secret=False, color="action", needs_restart=False, set=add_sym))
    return fields


def build_dashboard_focus(ctx: Ctx) -> List[Field]:
    def setter(v, c):
        from xauby.ui.state_view import write_focus_request
        write_focus_request(v, ".")
        return True

    return [Field("focus", "Focus symbol", "choice",
                  get=lambda c: c.v["focus_symbol"], fmt=lambda val, c: val,
                  choices=lambda c: [(s, s) for s in c.v["symbols"]],
                  color="action", needs_restart=False, set=setter)]


# ── Registry ─────────────────────────────────────────────────────────────────

SUBMENUS = {
    "trading": Submenu("trading", "Trading & Risk", build_trading),
    "global_trading": Submenu("global_trading", "Global Trading", build_global_trading),
    "risk_guards": Submenu("risk_guards", "Risk Guards", build_risk_guards),
    "portfolio": Submenu("portfolio", "Portfolio", build_portfolio),
    "strategy_params": Submenu("strategy_params", "Strategy Params", build_strategy_params),
    "regime_router": Submenu("regime_router", "Regime Router", build_regime_router),
    "alerts": Submenu("alerts", "Telegram Alerts & Schedule", build_alerts),
    "macro": Submenu("macro", "Macro Guard", build_macro),
    "exchange": Submenu("exchange", "Exchange", build_exchange),
    "multipair": Submenu("multipair", "Multi-Pair & Dashboard", build_multipair),
    "system_features": Submenu("system_features", "System Features", build_system_features),
    # Reached via action fields (not shown in the hub):
    "notifications": Submenu("notifications", "Notification Schedule", build_notifications),
    "ai_news": Submenu("ai_news", "AI News Sentiment", build_ai_news),
    "regime_mapping": Submenu("regime_mapping", "Regime → Strategy Mapping", build_regime_mapping),
    "pair_timeframes": Submenu("pair_timeframes", "Pair Timeframes", build_pair_timeframes),
    "pairlist": Submenu("pairlist", "Manage Pairlist", build_pairlist),
    "dashboard_focus": Submenu("dashboard_focus", "Dashboard Focus", build_dashboard_focus),
}

# Hub layout: (group title, [(submenu id, label)]). Entries whose id is not yet
# in SUBMENUS render but report "coming soon" until their milestone lands.
HUB_GROUPS = [
    ("TRADING", [
        ("trading", "Trading & Risk"),
        ("global_trading", "Global Trading"),
        ("risk_guards", "Risk Guards"),
        ("portfolio", "Portfolio"),
    ]),
    ("STRATEGY", [
        ("strategy_params", "Strategy Params"),
        ("regime_router", "Regime Router"),
    ]),
    ("INTEGRATIONS", [
        ("multipair", "Multi-Pair & Dashboard"),
        ("alerts", "Telegram Alerts & Schedule"),
        ("macro", "Macro Guard"),
        ("exchange", "Exchange"),
    ]),
    ("SYSTEM", [
        ("system_features", "System Features"),
    ]),
]
