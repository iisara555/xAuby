"""Merge live bot runtime state into backtest configuration."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from xauby.runtime.trading_config import resolve_trading_config

_RISK_STRAT_KEYS = (
    "sl_atr_mult",
    "rsi_min",
    "rsi_max",
    "vol_min_ratio",
    "fresh_zone_window",
    "require_fresh_zone",
    "ap_smoothing",
    "swing_lookback",
    "reclaim_window",
    "mss_lookback",
    "require_mss",
    "min_body_atr",
    "ema_fast",
    "ema_slow",
    "atr_period",
    "rsi_period",
    "volume_ma_period",
    "use_d1_regime_filter",
    "trailing_atr_mult",
    "breakeven_sl_enabled",
    "breakeven_activation_atr_mult",
    "breakeven_buffer_atr_mult",
)

CHECKLIST_STRAT_KEYS = (
    "rsi_min",
    "rsi_max",
    "vol_min_ratio",
    "require_fresh_zone",
    "fresh_zone_window",
    "ap_smoothing",
    "swing_lookback",
    "reclaim_window",
    "mss_lookback",
    "require_mss",
    "min_body_atr",
    "use_d1_regime_filter",
)


def extract_checklist_config(strat_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Thresholds the strategy uses for checklist gates and entry filters."""
    cfg = strat_cfg or {}
    return {
        "rsi_min": float(cfg.get("rsi_min", 45.0)),
        "rsi_max": float(cfg.get("rsi_max", 70.0)),
        "vol_min_ratio": float(cfg.get("vol_min_ratio", 1.0)),
        "require_fresh_zone": bool(cfg.get("require_fresh_zone", True)),
        "fresh_zone_window": int(cfg.get("fresh_zone_window", 3)),
        "ap_smoothing": int(cfg.get("ap_smoothing", 1) or 1),
        "swing_lookback": int(cfg.get("swing_lookback", 20)),
        "reclaim_window": int(cfg.get("reclaim_window", 3)),
        "mss_lookback": int(cfg.get("mss_lookback", 5)),
        "require_mss": bool(cfg.get("require_mss", True)),
        "min_body_atr": float(cfg.get("min_body_atr", 0.15)),
        "use_d1_regime_filter": bool(cfg.get("use_d1_regime_filter", False)),
    }


def runtime_config_fingerprint(bot_state: Optional[Dict[str, Any]]) -> str:
    """Stable fingerprint for TUI backtest result cache invalidation."""
    if not bot_state:
        return ""
    risk = bot_state.get("risk") or {}
    parts = [
        str(bot_state.get("primary_timeframe") or ""),
        str(bot_state.get("confirm_timeframe") or ""),
    ]
    for key in (
        "risk_pct",
        "sl_atr_mult",
        "rsi_min",
        "rsi_max",
        "vol_min_ratio",
        "use_d1_regime_filter",
        "require_fresh_zone",
        "fresh_zone_window",
        "ap_smoothing",
        "swing_lookback",
        "reclaim_window",
        "mss_lookback",
        "require_mss",
        "min_body_atr",
    ):
        val = risk.get(key)
        if val is not None:
            parts.append(f"{key}={val}")
    return "|".join(parts)


def merge_runtime_into_backtest_config(
    bot_state: Optional[Dict[str, Any]],
    strat_cfg: Dict[str, Any],
    trading_cfg: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[str], Optional[str]]:
    """Overlay runtime snapshot onto yaml config (runtime > yaml)."""
    merged_strat = dict(strat_cfg or {})
    merged_trading = dict(trading_cfg or {})
    primary_override: Optional[str] = None
    regime_override: Optional[str] = None

    if not bot_state:
        return merged_strat, merged_trading, primary_override, regime_override

    primary = str(bot_state.get("primary_timeframe") or "").strip()
    if primary:
        primary_override = primary

    confirm = str(bot_state.get("confirm_timeframe") or "").strip()
    if confirm:
        regime_override = confirm

    risk = bot_state.get("risk") or {}
    if isinstance(risk, dict):
        for key in _RISK_STRAT_KEYS:
            if key in risk and risk[key] is not None:
                merged_strat[key] = risk[key]
        if risk.get("risk_pct") is not None:
            merged_trading["risk_pct"] = risk["risk_pct"]
        if risk.get("sl_atr_mult") is not None:
            merged_trading["sl_atr_mult"] = risk["sl_atr_mult"]

    return merged_strat, merged_trading, primary_override, regime_override


def build_config_used_snapshot(
    strat_cfg: Dict[str, Any],
    trading_cfg: Dict[str, Any],
    *,
    primary_tf: str,
    regime_tf: Optional[str],
) -> Dict[str, Any]:
    """Params actually applied in a replay run (for TUI display)."""
    sl_mult = float(strat_cfg.get("sl_atr_mult") or 2.5)
    risk_pct = float(trading_cfg.get("risk_pct") or strat_cfg.get("risk_pct") or 0.01)
    return {
        "primary_timeframe": primary_tf,
        "confirm_timeframe": regime_tf or "",
        "sl_atr_mult": sl_mult,
        "trailing_atr_mult": float(strat_cfg.get("trailing_atr_mult", 1.5)),
        "ema_fast": int(strat_cfg.get("ema_fast", 20)),
        "ema_slow": int(strat_cfg.get("ema_slow", 50)),
        "atr_period": int(strat_cfg.get("atr_period", 14)),
        "rsi_period": int(strat_cfg.get("rsi_period", 14)),
        "volume_ma_period": int(strat_cfg.get("volume_ma_period", 20)),
        "rsi_min": float(strat_cfg.get("rsi_min", 45.0)),
        "rsi_max": float(strat_cfg.get("rsi_max", 70.0)),
        "vol_min_ratio": float(strat_cfg.get("vol_min_ratio", 1.0)),
        "risk_pct": risk_pct,
        "use_d1_regime_filter": bool(strat_cfg.get("use_d1_regime_filter", False)),
        "require_fresh_zone": bool(strat_cfg.get("require_fresh_zone", True)),
        "fresh_zone_window": int(strat_cfg.get("fresh_zone_window", 3)),
        "swing_lookback": int(strat_cfg.get("swing_lookback", 20)),
        "reclaim_window": int(strat_cfg.get("reclaim_window", 3)),
        "mss_lookback": int(strat_cfg.get("mss_lookback", 5)),
        "require_mss": bool(strat_cfg.get("require_mss", True)),
        "min_body_atr": float(strat_cfg.get("min_body_atr", 0.15)),
        "breakeven_sl_enabled": bool(strat_cfg.get("breakeven_sl_enabled", False)),
        "breakeven_activation_atr_mult": float(
            strat_cfg.get("breakeven_activation_atr_mult", 1.5)
        ),
        "breakeven_buffer_atr_mult": float(
            strat_cfg.get("breakeven_buffer_atr_mult", 0.1)
        ),
        "ap_smoothing": int(strat_cfg.get("ap_smoothing", 1) or 1),
        "checklist_gates": extract_checklist_config(strat_cfg),
    }


def build_effective_config_used_snapshot(
    cfg: Dict[str, Any],
    strategy_name: str,
    symbol: str,
    *,
    primary_tf: Optional[str] = None,
    regime_tf: Optional[str] = None,
    project_root: str = ".",
    for_live: bool = False,
) -> Dict[str, Any]:
    """Canonical config-used snapshot from the 3-level resolver."""
    eff = resolve_trading_config(
        cfg,
        strategy_name,
        symbol=symbol,
        project_root=project_root,
        for_live=for_live,
    )
    snap = build_config_used_snapshot(
        eff.strategy,
        {
            "risk_pct": eff.portfolio.get("risk_pct"),
            "max_position_per_trade_pct": eff.portfolio.get("max_position_per_trade_pct"),
        },
        primary_tf=primary_tf or eff.primary_timeframe,
        regime_tf=regime_tf if regime_tf is not None else eff.confirm_timeframe,
    )
    snap["config_schema"] = "effective.v1"
    snap["strategy_name"] = eff.strategy_name
    snap["symbol"] = eff.symbol or symbol
    return snap
