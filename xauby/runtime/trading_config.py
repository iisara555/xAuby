"""Canonical trading configuration resolver.

This module separates config ownership without breaking the legacy YAML shape:

* Engine config: exchange, logging, monitoring, notifications, global guards.
* Strategy config: every parameter that can change signal or exit outcomes.
* Portfolio config: sizing, allocation, and capital management.

Legacy blocks remain supported as fallbacks, but live, replay, and backtest
should call these helpers so they agree on the same effective values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple

from xauby.runtime.architecture_config import whitelist_strict
from xauby.runtime.config_error import ConfigError
from xauby.runtime.exchange_config import resolve_quote_asset
from xauby.runtime.pair_config import load_whitelist
from xauby.runtime.strategy_pair_config import merge_strategy_config
from xauby.runtime.whitelist_validator import strategy_name_from_whitelist_entry
from xauby.strategies.registry import STRATEGY_ID_ALIASES, normalize_strategy_name


STRATEGY_RESULT_KEYS = frozenset(
    {
        "ap_smoothing",
        "disable_stop_loss",
        "position_pct",
        "use_d1_regime_filter",
        "require_fresh_zone",
        "fresh_zone_window",
        "rsi_min",
        "rsi_max",
        "vol_min_ratio",
        "sl_atr_mult",
        "trailing_atr_mult",
        "fixed_tp_pct",
        "breakeven_sl_enabled",
        "breakeven_activation_atr_mult",
        "breakeven_buffer_atr_mult",
        "timeframe",
        "primary_timeframe",
        "confirm_timeframe",
        "cool_down_minutes",
        "backtest_data_proxy",
    }
)


PORTFOLIO_RESULT_KEYS = frozenset(
    {
        "risk_pct",
        "max_position_per_trade_pct",
        "max_open_positions",
        "max_daily_trades",
        "min_order_amount",
        "initial_balance",
        "min_balance_threshold",
        "target_allocation",
    }
)


@dataclass(frozen=True)
class EffectiveTradingConfig:
    """Resolved single source of truth for one strategy/symbol context."""

    strategy_name: str
    symbol: str = ""
    strategy: Dict[str, Any] = field(default_factory=dict)
    portfolio: Dict[str, Any] = field(default_factory=dict)
    engine: Dict[str, Any] = field(default_factory=dict)
    primary_timeframe: str = "4h"
    confirm_timeframe: str = ""

    def config_used(self) -> Dict[str, Any]:
        """Flat snapshot for replay/backtest display and reproducibility."""
        out = dict(self.strategy)
        out.update(
            {
                "primary_timeframe": self.primary_timeframe,
                "confirm_timeframe": self.confirm_timeframe,
                "risk_pct": self.portfolio.get("risk_pct"),
                "max_position_per_trade_pct": self.portfolio.get(
                    "max_position_per_trade_pct"
                ),
            }
        )
        return out


@dataclass(frozen=True)
class CanonicalRuntimeConfig:
    """Read-only platform config snapshot for UI, marketplace, and tooling."""

    schema_version: int
    exchange_id: str
    quote_asset: str
    simulate_only: bool
    read_only: bool
    symbols: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    portfolio: Dict[str, Any] = field(default_factory=dict)
    engine: Dict[str, Any] = field(default_factory=dict)
    config_sources: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _legacy_strategy_names(name: str) -> Tuple[str, ...]:
    canonical = normalize_strategy_name(name)
    aliases = tuple(old for old, new in STRATEGY_ID_ALIASES.items() if new == canonical)
    return (canonical, *aliases)


def active_strategy_name(cfg: Dict[str, Any], fallback: str = "xauby_actionzone") -> str:
    strategy_block = cfg.get("strategy") or {}
    return normalize_strategy_name(strategy_block.get("active") or cfg.get("active_strategy") or fallback)


def _base_from_symbol(symbol: str, quote: str = "USDT") -> str:
    sym = str(symbol or "").upper().replace("_", "")
    if sym.endswith(quote):
        return sym[: -len(quote)]
    return sym


def strategy_name_from_whitelist(
    symbol: str,
    *,
    project_root: str = ".",
    whitelist_path: Optional[str] = None,
) -> Optional[str]:
    wl = load_whitelist(project_root, whitelist_path)
    quote = str(wl.get("quote_asset", "USDT") or "USDT").upper()
    base = _base_from_symbol(symbol, quote)
    for asset in wl.get("assets") or []:
        if str(asset.get("symbol", "")).upper() == base:
            name = strategy_name_from_whitelist_entry(asset)
            return name or None
    return None


def strategy_name_for_symbol(
    cfg: Dict[str, Any],
    symbol: str,
    *,
    fallback: str = "xauby_actionzone",
    project_root: str = ".",
    whitelist_path: Optional[str] = None,
    strict: Optional[bool] = None,
) -> str:
    """Resolve plugin id for a symbol, falling back to strategy.active."""
    is_strict = whitelist_strict(cfg) if strict is None else bool(strict)
    sym = str(symbol or "").upper().replace("_", "")

    if is_strict:
        wl_name = strategy_name_from_whitelist(
            sym,
            project_root=project_root,
            whitelist_path=whitelist_path,
        )
        if not wl_name:
            raise ConfigError(f"{sym}: missing 'strategy' in whitelist (strict mode)")
        return normalize_strategy_name(wl_name)

    default = active_strategy_name(cfg, fallback)
    quote = str(((cfg.get("portfolio") or {}).get("quote_asset")) or resolve_quote_asset(cfg)).upper()
    base = _base_from_symbol(sym, quote)
    symbols = (cfg.get("strategy") or {}).get("symbols") or {}
    override = symbols.get(sym) or symbols.get(base) or {}
    if isinstance(override, dict):
        name = str(override.get("strategy") or override.get("strategy_name") or "").strip()
        if name:
            return normalize_strategy_name(name)
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def bounded_position_fraction(
    position_fraction: Any,
    allocation_pct: Any = None,
) -> float:
    """Cap fixed-fraction sizing by the symbol's portfolio allocation.

    CDC Pure expresses its desired size as a 0..1 equity fraction, while the
    portfolio owns a per-symbol allocation in percent.  Keeping this conversion
    here gives previews and every execution side the same sizing rule.
    """
    fraction = max(0.0, min(_as_float(position_fraction, 0.0), 1.0))
    allocation = _as_float(allocation_pct, 0.0)
    if allocation > 0.0:
        fraction = min(fraction, max(0.0, min(allocation / 100.0, 1.0)))
    return fraction


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def strategy_config_block(
    cfg: Dict[str, Any],
    strategy_name: Optional[str] = None,
    *,
    symbol: str = "",
    project_root: str = ".",
    for_live: bool = True,
) -> Dict[str, Any]:
    """Return effective strategy-owned config.

    New canonical shape:
      strategy.config.<strategy_name>

    Legacy fallback:
      strategies.<strategy_name> + coin_whitelist[].strategy_params overlay
    """
    name = normalize_strategy_name(strategy_name or strategy_name_for_symbol(cfg, symbol))
    strategy_root = cfg.get("strategy") or {}
    canonical = None
    strategy_config = strategy_root.get("config") or {}
    legacy_config = cfg.get("strategies") or {}
    for candidate in _legacy_strategy_names(name):
        canonical = strategy_config.get(candidate)
        if canonical is not None:
            break
    if canonical is None:
        for candidate in _legacy_strategy_names(name):
            canonical = legacy_config.get(candidate)
            if canonical is not None:
                break
    if canonical is None:
        canonical = {}
    plugin_defaults: Dict[str, Any] = {}
    try:
        from xauby.strategies.registry import strategy_manifest

        plugin_defaults = dict(strategy_manifest(name).get("default_config") or {})
    except (KeyError, TypeError, ValueError):
        # Keep legacy/custom configurations resolvable before their plugin is
        # installed; engine loading will still reject an unknown strategy.
        plugin_defaults = {}
    merged = {**plugin_defaults, **dict(canonical or {})}
    try:
        configured_name = strategy_name_for_symbol(
            cfg,
            symbol,
            project_root=project_root,
        ) if symbol else name
    except ConfigError:
        # Explicit runtime/test strategy names may refer to a synthetic symbol
        # that is intentionally absent from a strict whitelist.  Such symbols
        # receive no pair-owned overrides.
        configured_name = ""
    apply_pair_overrides = configured_name == name
    symbol_key = str(symbol or "").upper().replace("_", "")
    if symbol_key:
        quote = str(((cfg.get("portfolio") or {}).get("quote_asset")) or resolve_quote_asset(cfg)).upper()
        base_key = symbol_key[: -len(quote)] if symbol_key.endswith(quote) else symbol_key
        symbol_overrides = strategy_root.get("symbols") or {}
        override = (
            symbol_overrides.get(symbol_key)
            or symbol_overrides.get(base_key)
            or {}
        )
        if isinstance(override, dict):
            allowed = (
                {k: v for k, v in override.items() if k not in {"strategy", "strategy_name"}}
                if apply_pair_overrides
                else {
                    k: v
                    for k, v in override.items()
                    if k in {"timeframe", "primary_timeframe", "confirm_timeframe"}
                }
            )
            merged.update(allowed)
    if symbol and apply_pair_overrides:
        # Legacy fallback only. New configs should put symbol-specific strategy
        # values under strategy.symbols.<SYMBOL>.
        merged = merge_strategy_config(
            merged,
            symbol,
            project_root=project_root,
            for_live=for_live,
        )
    normalized = normalize_strategy_config(merged, cfg=cfg)
    # normalize_strategy_config carries legacy CDC defaults for callers that
    # still use it directly.  A strategy block must only override plugin
    # defaults with values actually owned by that block; otherwise CDC defaults
    # (RSI 45..70, volume 1.0, etc.) leak into BBKC/BBRSI after router switches.
    structural = {"enabled", "timeframe", "primary_timeframe", "confirm_timeframe"}
    return {
        key: value
        for key, value in normalized.items()
        if key in merged or key in structural
    }


def portfolio_config_block(cfg: Dict[str, Any], symbol: str = "") -> Dict[str, Any]:
    """Return portfolio-owned sizing/capital config with legacy fallbacks."""
    sym = str(symbol or "").upper().replace("_", "")
    portfolio = dict(cfg.get("portfolio") or {})
    sizing = dict(portfolio.get("position_sizing") or {})
    symbol_cfg = dict((portfolio.get("symbols") or {}).get(sym) or {}) if sym else {}
    symbol_sizing = dict(symbol_cfg.get("position_sizing") or {})
    trading = cfg.get("trading") or {}
    risk = cfg.get("risk") or {}
    auto_sizing = ((cfg.get("auto_trader") or {}).get("position_sizing") or {})

    risk_pct = (
        sizing.get("risk_pct")
        if sizing.get("risk_pct") is not None
        else portfolio.get("risk_pct")
    )
    if risk_pct is None:
        risk_pct = trading.get("risk_pct")
    if risk_pct is None:
        max_risk_pct = risk.get("max_risk_per_trade_pct")
        risk_pct = _as_float(max_risk_pct, 1.0) / 100.0 if max_risk_pct is not None else 0.01

    max_position_pct = (
        sizing.get("max_position_per_trade_pct")
        or portfolio.get("max_position_per_trade_pct")
        or trading.get("max_position_per_trade_pct")
        or risk.get("max_position_per_trade_pct")
        or auto_sizing.get("max_position_pct")
        or 28.0
    )

    min_order_amount = (
        sizing.get("min_order_amount")
        or portfolio.get("min_order_amount")
        or trading.get("min_order_amount")
        or 10.0
    )

    out = dict(portfolio)
    out.update(
        {
            "risk_pct": _as_float(risk_pct, 0.01),
            "max_position_per_trade_pct": _as_float(max_position_pct, 28.0),
            "max_open_positions": _as_int(risk.get("max_open_positions"), 1),
            "max_daily_trades": _as_int(risk.get("max_daily_trades"), 999),
            "min_order_amount": _as_float(min_order_amount, 10.0),
        }
    )
    if symbol_cfg:
        out["symbol"] = sym
        out["allocation_pct"] = _as_float(
            symbol_cfg.get("allocation_pct"),
            _as_float(out.get("allocation_pct"), 0.0),
        )
        if symbol_sizing.get("risk_pct") is not None:
            out["risk_pct"] = _as_float(symbol_sizing.get("risk_pct"), out["risk_pct"])
        if symbol_sizing.get("max_position_per_trade_pct") is not None:
            out["max_position_per_trade_pct"] = _as_float(
                symbol_sizing.get("max_position_per_trade_pct"),
                out["max_position_per_trade_pct"],
            )
        if symbol_sizing.get("min_order_amount") is not None:
            out["min_order_amount"] = _as_float(
                symbol_sizing.get("min_order_amount"),
                out["min_order_amount"],
            )
    if "target_allocation" not in out:
        out["target_allocation"] = (cfg.get("rebalance") or {}).get("target_allocation") or {}
    return out


MAX_SANE_RISK_PCT = 0.10  # risk_pct is a fraction (0.02 = 2%); above 10% is almost surely a unit mistake


def validate_risk_config(cfg: Dict[str, Any]) -> None:
    """Refuse startup when any risk_pct looks like a percent instead of a fraction.

    Convention: ``risk_pct`` is a fraction of equity risked per trade
    (0.02 = 2%). Legacy ``risk.max_risk_per_trade_pct`` is in percent and is
    divided by 100 elsewhere, so it is checked against 10.0 instead.

    Raises ValueError listing every offending key so the operator can fix the
    YAML in one pass.
    """
    offenders = []

    def _check(path: str, value: Any) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if v > MAX_SANE_RISK_PCT:
            offenders.append(f"{path}={v}")

    _check("trading.risk_pct", (cfg.get("trading") or {}).get("risk_pct"))
    portfolio = cfg.get("portfolio") or {}
    _check("portfolio.risk_pct", portfolio.get("risk_pct"))
    _check(
        "portfolio.position_sizing.risk_pct",
        (portfolio.get("position_sizing") or {}).get("risk_pct"),
    )
    for sym, sym_cfg in (portfolio.get("symbols") or {}).items():
        if isinstance(sym_cfg, dict):
            _check(
                f"portfolio.symbols.{sym}.position_sizing.risk_pct",
                (sym_cfg.get("position_sizing") or {}).get("risk_pct"),
            )
    for name, strat_cfg in (cfg.get("strategies") or {}).items():
        if isinstance(strat_cfg, dict):
            _check(f"strategies.{name}.risk_pct", strat_cfg.get("risk_pct"))

    legacy_pct = (cfg.get("risk") or {}).get("max_risk_per_trade_pct")
    try:
        if legacy_pct is not None and float(legacy_pct) > MAX_SANE_RISK_PCT * 100:
            offenders.append(f"risk.max_risk_per_trade_pct={float(legacy_pct)} (percent unit)")
    except (TypeError, ValueError):
        pass

    if offenders:
        raise ValueError(
            "risk_pct misconfigured — values are fractions (0.02 = 2% per trade), "
            f"refusing to start with per-trade risk above {MAX_SANE_RISK_PCT:.0%}: "
            + ", ".join(offenders)
        )


def validate_open_positions_config(cfg: Dict[str, Any], live_pair_count: int = 0) -> None:
    """Refuse startup when the max_open_positions keys disagree or under-cap.

    Three same-named keys live in different blocks with different consumers:
    the engine BUY gate reads ``trading.max_open_positions``, the config
    resolver reads ``risk.max_open_positions``, and ``portfolio`` carries a
    display-only copy. A mismatch means one path enforces a limit another
    path (and the operator) doesn't expect — the July 2026 incident had the
    portfolio key raised to 3 while the enforced keys stayed at 1, silently
    blocking the second live pair's entries.

    Raises ValueError when the set keys disagree, or when the effective cap is
    below ``live_pair_count`` (certified live pairs would silently never trade).
    """

    def _get(block: str) -> Any:
        return (cfg.get(block) or {}).get("max_open_positions")

    values: Dict[str, int] = {}
    for block in ("trading", "risk", "portfolio"):
        raw = _get(block)
        if raw is None:
            continue
        try:
            values[f"{block}.max_open_positions"] = int(raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"{block}.max_open_positions is not an integer: {raw!r}"
            ) from None

    distinct = set(values.values())
    if len(distinct) > 1:
        listing = ", ".join(f"{k}={v}" for k, v in sorted(values.items()))
        raise ValueError(
            "max_open_positions keys disagree — the engine enforces "
            "trading.max_open_positions and the resolver reads "
            f"risk.max_open_positions; keep all set keys equal: {listing}"
        )

    if values and live_pair_count > 0:
        effective = next(iter(distinct))
        if effective < live_pair_count:
            raise ValueError(
                f"max_open_positions={effective} is below the {live_pair_count} "
                "live whitelist pair(s) — the extra pair(s) would silently never "
                "enter. Raise the cap or set the pair(s) to sim."
            )


def engine_config_block(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return engine-owned operational config."""
    keys = (
        "exchange",
        "logging",
        "monitoring",
        "notifications",
        "macro_sentiment_guard",
        "pre_trade_gate",
        "websocket",
        "execution",
        "trading",
        "risk",
    )
    return {key: dict(cfg.get(key) or {}) for key in keys if isinstance(cfg.get(key), dict)}


def normalize_strategy_config(
    strat_cfg: Dict[str, Any],
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize strategy result keys and legacy SL/TP aliases."""
    src = dict(strat_cfg or {})
    cfg = cfg or {}

    # Legacy fallback for timeframe only. Strategy config remains authoritative.
    mode = (cfg.get("strategy_mode") or {}).get((cfg.get("strategy_mode") or {}).get("active", ""), {})
    trading = cfg.get("trading") or {}
    if "primary_timeframe" not in src:
        src["primary_timeframe"] = src.get("timeframe") or mode.get("primary_timeframe") or trading.get("timeframe") or "4h"
    if "confirm_timeframe" not in src:
        src["confirm_timeframe"] = mode.get("confirm_timeframe") or ""

    out = {
        "enabled": _as_bool(src.get("enabled"), True),
        "ap_smoothing": _as_int(src.get("ap_smoothing"), 2),
        "disable_stop_loss": _as_bool(src.get("disable_stop_loss"), False),
        "position_pct": _as_float(src.get("position_pct"), 1.0),
        "use_d1_regime_filter": _as_bool(src.get("use_d1_regime_filter"), False),
        "require_fresh_zone": _as_bool(src.get("require_fresh_zone"), True),
        "fresh_zone_window": _as_int(src.get("fresh_zone_window"), 3),
        "rsi_min": _as_float(src.get("rsi_min"), 45.0),
        "rsi_max": _as_float(src.get("rsi_max"), 70.0),
        "vol_min_ratio": _as_float(src.get("vol_min_ratio"), 1.0),
        "sl_atr_mult": _as_float(src.get("sl_atr_mult"), 2.0),
        "trailing_atr_mult": _as_float(src.get("trailing_atr_mult"), 1.5),
        "breakeven_sl_enabled": _as_bool(src.get("breakeven_sl_enabled"), False),
        "breakeven_activation_atr_mult": _as_float(
            src.get("breakeven_activation_atr_mult"), 1.5
        ),
        "breakeven_buffer_atr_mult": _as_float(
            src.get("breakeven_buffer_atr_mult"), 0.1
        ),
        "risk_pct": _as_float(src.get("risk_pct"), 0.0),
        "cool_down_minutes": _as_int(src.get("cool_down_minutes"), 0),
        "timeframe": str(src.get("timeframe") or src.get("primary_timeframe") or "4h"),
        "primary_timeframe": str(src.get("primary_timeframe") or src.get("timeframe") or "4h"),
        "confirm_timeframe": str(src.get("confirm_timeframe") or ""),
        **({"backtest_data_proxy": src["backtest_data_proxy"]} if src.get("backtest_data_proxy") else {}),
    }
    if "fixed_tp_pct" in src:
        out["fixed_tp_pct"] = _as_float(src.get("fixed_tp_pct"), 0.0)
    for key, value in src.items():
        if key not in out and key not in {"strategy", "strategy_name"}:
            out[key] = value
    return out


def resolve_trading_config(
    cfg: Dict[str, Any],
    strategy_name: Optional[str] = None,
    *,
    symbol: str = "",
    project_root: str = ".",
    for_live: bool = True,
) -> EffectiveTradingConfig:
    name = normalize_strategy_name(strategy_name or strategy_name_for_symbol(cfg, symbol))
    strategy = strategy_config_block(
        cfg,
        name,
        symbol=symbol,
        project_root=project_root,
        for_live=for_live,
    )
    portfolio = portfolio_config_block(cfg, symbol)
    # Backward compatibility: old strategy risk_pct can override if explicitly set.
    if _as_float(strategy.get("risk_pct"), 0.0) > 0:
        portfolio["risk_pct"] = _as_float(strategy.get("risk_pct"), portfolio["risk_pct"])

    return EffectiveTradingConfig(
        strategy_name=name,
        symbol=str(symbol or "").upper().replace("_", ""),
        strategy=strategy,
        portfolio=portfolio,
        engine=engine_config_block(cfg),
        primary_timeframe=str(strategy.get("primary_timeframe") or strategy.get("timeframe") or "4h"),
        confirm_timeframe=str(strategy.get("confirm_timeframe") or ""),
    )


def split_legacy_runtime_config(
    cfg: Dict[str, Any],
    strategy_name: Optional[str] = None,
    *,
    symbol: str = "",
    project_root: str = ".",
    for_live: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (strategy_cfg, trading_cfg) for older call sites."""
    eff = resolve_trading_config(
        cfg,
        strategy_name,
        symbol=symbol,
        project_root=project_root,
        for_live=for_live,
    )
    trading = dict((cfg.get("trading") or {}))
    trading["risk_pct"] = eff.portfolio["risk_pct"]
    trading["max_position_per_trade_pct"] = eff.portfolio["max_position_per_trade_pct"]
    trading["min_order_amount"] = eff.portfolio["min_order_amount"]
    # Keep old simulators working while making strategy the owner of exit params.
    trading.pop("sl_atr_mult", None)
    return dict(eff.strategy), trading


def _exchange_id(cfg: Dict[str, Any]) -> str:
    exchange = cfg.get("exchange") or {}
    if isinstance(exchange, dict):
        value = exchange.get("id") or exchange.get("provider") or exchange.get("name")
        if value:
            return str(value)
    return str(cfg.get("exchange_id") or "binance_th")


def _bool_cfg(cfg: Dict[str, Any], key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    return _as_bool(value, default)


def canonical_runtime_config(
    cfg: Dict[str, Any],
    *,
    project_root: str = ".",
    config_path: str = "bot_config.yaml",
    whitelist_path: Optional[str] = None,
    for_live: bool = True,
) -> CanonicalRuntimeConfig:
    """Build a stable read-only platform config view from legacy YAML shapes."""
    from xauby.runtime.pair_registry import PairRegistry

    runtime_cfg = dict(cfg or {})
    runtime_cfg["architecture"] = {
        **dict(runtime_cfg.get("architecture") or {}),
        "sync_yaml_pairs_from_whitelist": False,
    }
    registry = PairRegistry(
        runtime_cfg,
        config_path=config_path,
        project_root=project_root,
        whitelist_path=whitelist_path,
        read_only=True,
    )
    specs = registry.load(None)
    symbols: Dict[str, Dict[str, Any]] = {}
    for spec in specs:
        eff = resolve_trading_config(
            runtime_cfg,
            spec.strategy_name,
            symbol=spec.symbol,
            project_root=project_root,
            for_live=for_live,
        )
        symbols[spec.symbol] = {
            "symbol": spec.symbol,
            "enabled": bool(spec.enabled),
            "strategy_name": eff.strategy_name,
            "primary_timeframe": eff.primary_timeframe,
            "confirm_timeframe": eff.confirm_timeframe,
            "execution_mode": spec.execution_mode
            or ("sim" if _bool_cfg(runtime_cfg, "simulate_only", True) else "live"),
            "regime_router_enabled": bool(spec.regime_router_enabled),
            "regime_router_live_confirmed": bool(spec.regime_router_live_confirmed),
            "strategy": dict(eff.strategy),
            "portfolio": dict(eff.portfolio),
        }

    portfolio = portfolio_config_block(runtime_cfg)
    read_only = _bool_cfg(
        runtime_cfg,
        "read_only",
        _as_bool(portfolio.get("read_only"), False),
    )
    quote_asset = str(portfolio.get("quote_asset") or registry.quote_asset or resolve_quote_asset(runtime_cfg)).upper()
    return CanonicalRuntimeConfig(
        schema_version=1,
        exchange_id=_exchange_id(runtime_cfg),
        quote_asset=quote_asset,
        simulate_only=_bool_cfg(runtime_cfg, "simulate_only", True),
        read_only=read_only,
        symbols=symbols,
        portfolio=portfolio,
        engine=engine_config_block(runtime_cfg),
        config_sources={
            "config": config_path,
            "whitelist": whitelist_path or getattr(registry, "whitelist_path", ""),
        },
    )
