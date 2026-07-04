"""Load bot state from disk for Textual screens (shared across screen instances)."""

from __future__ import annotations

import json
import os
import copy
import threading
import time
from typing import Any, Dict, List, Tuple

from xauby.ui.dashboard import resolve_dashboard_state
from xauby.ui.widgets import DEFAULT_REGIME

_LOAD_CACHE: Dict[str, Any] = {
    "mtime": -1.0,
    "focus_mtime": -1.0,
    "state": {},
    "envelope": {},
    "focus": "",
    "pairs": [],
}
_LOAD_CACHE_LOCK = threading.Lock()


def _focus_file_mtime() -> float:
    from xauby.runtime.paths import dashboard_focus_path
    path = os.path.join(os.getcwd(), dashboard_focus_path())
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def tui_refresh_interval() -> float:
    """Dashboard poll interval from bot_config.yaml cli_ui.refresh_interval_seconds."""
    try:
        import yaml

        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return max(0.5, float((cfg.get("cli_ui") or {}).get("refresh_interval_seconds", 1.0)))
    except Exception:
        return 1.0


def tui_state_stale_seconds() -> float:
    """Maximum age of engine state before the TUI switches to offline/waiting."""
    try:
        import yaml

        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        interval = float((cfg.get("trading") or {}).get("interval_seconds", 60.0))
        configured = (cfg.get("cli_ui") or {}).get("state_stale_seconds")
        return max(30.0, float(configured if configured is not None else interval * 3.0))
    except Exception:
        return 180.0


def bot_state_file_path() -> str:
    from xauby.runtime.paths import bot_state_path
    return os.path.join(os.getcwd(), bot_state_path())


def state_file_mtime() -> float:
    path = bot_state_file_path()
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _load_json_with_retry(path: str, retries: int = 3, backoff_ms: float = 10.0) -> Any:
    """Load JSON from *path* with retry on decode races."""
    last_exc: Exception = Exception("unknown")
    for attempt in range(retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff_ms / 1000.0 * (attempt + 1))
    raise last_exc


def load_bot_state_from_disk() -> Tuple[Dict[str, Any], Dict[str, Any], str, List[str]]:
    """Return (state, envelope, focus_symbol, pairs). Cached until file mtime changes."""
    global _LOAD_CACHE
    path = bot_state_file_path()
    if not os.path.exists(path):
        with _LOAD_CACHE_LOCK:
            _LOAD_CACHE = {
                "mtime": 0.0,
                "focus_mtime": 0.0,
                "state": {},
                "envelope": {},
                "focus": "",
                "pairs": [],
            }
        return {}, {}, "", []

    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime > tui_state_stale_seconds():
            with _LOAD_CACHE_LOCK:
                _LOAD_CACHE = {
                    "mtime": mtime,
                    "focus_mtime": _focus_file_mtime(),
                    "state": {}, "envelope": {}, "focus": "", "pairs": [],
                }
            return {}, {}, "", []
        focus_mtime = _focus_file_mtime()
        with _LOAD_CACHE_LOCK:
            if (
                mtime == _LOAD_CACHE["mtime"]
                and focus_mtime == _LOAD_CACHE["focus_mtime"]
                and _LOAD_CACHE["state"]
            ):
                return (
                    copy.deepcopy(_LOAD_CACHE["state"]),
                    copy.deepcopy(_LOAD_CACHE["envelope"]),
                    _LOAD_CACHE["focus"],
                    list(_LOAD_CACHE["pairs"]),
                )
        raw = _load_json_with_retry(path)
        state, envelope, focus, pairs = resolve_dashboard_state(raw)
        with _LOAD_CACHE_LOCK:
            _LOAD_CACHE = {
                "mtime": mtime,
                "focus_mtime": focus_mtime,
                "state": state,
                "envelope": envelope,
                "focus": focus,
                "pairs": pairs,
            }
        return copy.deepcopy(state), copy.deepcopy(envelope), focus, list(pairs)
    except Exception:
        return {}, {}, "", []


def fallback_bot_state(*, focus_symbol: str = "") -> Dict[str, Any]:
    sym = (focus_symbol or "").upper().replace("_", "")
    if not sym:
        try:
            from xauby.runtime.symbol_resolver import focus_symbol_from_config

            sym = focus_symbol_from_config()
        except Exception:
            sym = ""
    return {
        "symbol": sym,
        "regime": dict(DEFAULT_REGIME),
        "multi_pair": False,
    }


def _regime_key(state: Dict[str, Any]) -> Tuple[Any, ...]:
    regime = state.get("regime") or {}
    bias = regime.get("strategy_bias") or {}
    return (
        str(regime.get("regime") or ""),
        round(float(regime.get("confidence") or 0.0), 2),
        int(regime.get("gold_score") or 0),
        str(regime.get("phase") or ""),
        str(regime.get("risk_state") or ""),
        str(regime.get("trend_strength") or ""),
        str(regime.get("volatility_state") or ""),
        str(regime.get("liquidity_state") or ""),
        str(regime.get("transition_risk") or ""),
        str(bias.get("family") or ""),
        str(bias.get("posture") or ""),
    )


def _checklist_sig(state: Dict[str, Any]) -> Tuple[Any, ...]:
    def freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return tuple(sorted((str(k), freeze(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(freeze(v) for v in value)
        return value

    meta = state.get("signal_meta") or {}
    checklist = meta.get("checklist") or []
    return tuple(
        (
            item.get("label"),
            item.get("ok"),
            item.get("value"),
            item.get("hint"),
            freeze(item.get("columns")),
            freeze(item.get("bar")),
        )
        for item in checklist[:20]
        if isinstance(item, dict)
    )


def _price_key(price: float) -> float:
    if price >= 1000:
        return round(price, 0)
    if price >= 10:
        return round(price, 1)
    return round(price, 2)


def _envelope_prices_sig(envelope: Dict[str, Any]) -> Tuple[Any, ...]:
    by_symbol = envelope.get("by_symbol") or {}
    return tuple(
        sorted(
            (
                sym,
                _price_key(float((snap or {}).get("current_price") or 0.0)),
                round(float((snap or {}).get("price_change_24h_pct") or (snap or {}).get("percent_change_24h") or 0.0), 2),
            )
            for sym, snap in by_symbol.items()
            if isinstance(snap, dict)
        )
    )


def _positions_sig(envelope: Dict[str, Any]) -> Tuple[Any, ...]:
    rows = []
    for sym, snap in (envelope.get("by_symbol") or {}).items():
        if not isinstance(snap, dict):
            continue
        pos = snap.get("position") or {}
        current_price = float((snap or {}).get("current_price") or 0.0)
        rows.append(
            (
                sym,
                str(pos.get("state") or "idle"),
                round(current_price, 4 if current_price < 10 else 2),
                round(float((snap or {}).get("bid") or 0.0), 4),
                round(float((snap or {}).get("ask") or 0.0), 4),
                round(float(pos.get("quantity") or 0.0), 8),
                round(float(pos.get("entry_price") or 0.0), 4),
                round(float(pos.get("stop_loss") or 0.0), 4),
                round(float(pos.get("highest_price_seen") or 0.0), 4),
                round(float(pos.get("unrealized_pnl") or 0.0), 2),
                round(float(pos.get("unrealized_pnl_pct") or 0.0), 2),
                bool(pos.get("partial_tp_taken")),
                round(float(pos.get("partial_tp_pct") or 0.0), 2),
                round(float(pos.get("partial_tp_fraction") or 0.0), 3),
                round(float(pos.get("partial_tp_trigger_price") or 0.0), 4),
                str(pos.get("opened_at") or ""),
                str(pos.get("stop_loss_order_id") or ""),
                str(pos.get("position_side") or "LONG"),
                round(float(pos.get("leverage") or 1.0), 2),
                str(pos.get("management_mode") or "strategy"),
                str(pos.get("market_type") or "SPOT"),
                str(pos.get("feed_health") or "OK"),
            )
        )
    return tuple(sorted(rows))[:12]


def _portfolio_sig(envelope: Dict[str, Any]) -> Tuple[Any, ...]:
    by_symbol = envelope.get("by_symbol") or {}
    agg = envelope.get("aggregate") or {}
    agg_sig = (
        round(float(agg.get("total_equity_usdt") or 0.0), 2),
        int(agg.get("open_positions") or 0),
        bool(agg.get("simulate_only", True)),
        bool(agg.get("read_only", False)),
    )
    bal_sig = tuple(
        sorted(
            (
                sym,
                round(float((snap or {}).get("current_price") or 0.0), 2),
                round(float((snap or {}).get("total_equity_usdt") or 0.0), 2),
                round(float(((snap or {}).get("equity_breakdown") or {}).get("portfolio_total_usdt") or 0.0), 2),
                round(float(((snap or {}).get("equity_breakdown") or {}).get("usdt_balance_usdt") or 0.0), 2),
                round(float(((snap or {}).get("equity_breakdown") or {}).get("base_quantity") or 0.0), 8),
                round(float(((snap or {}).get("equity_breakdown") or {}).get("base_value_usdt") or 0.0), 2),
                round(float(((snap or {}).get("equity_breakdown") or {}).get("symbol_exposure_usdt") or 0.0), 2),
                tuple(
                    sorted(
                        (
                            str(asset),
                            round(float(qty or 0.0), 8),
                        )
                        for asset, qty in ((snap or {}).get("portfolio") or {}).items()
                    )
                ),
            )
            for sym, snap in by_symbol.items()
            if isinstance(snap, dict)
        )
    )[:12]
    return (agg_sig, bal_sig)


def bot_state_fingerprint(
    state: Dict[str, Any],
    envelope: Dict[str, Any],
    focus: str,
) -> Tuple[Any, ...]:
    """Stable tuple for dashboard panels/chart — excludes header-only volatile fields."""
    if not state:
        return ("empty",)

    sym = str(state.get("symbol") or "")
    price = _price_key(float(state.get("current_price") or 0.0))
    change = round(
        float(
            state.get("price_change_24h_pct")
            or state.get("percent_change_24h")
            or 0.0
        ),
        2,
    )

    return (
        focus,
        sym,
        price,
        change,
        _envelope_prices_sig(envelope),
        bool(state.get("simulate_only", True)),
        bool(state.get("read_only", False)),
        tuple(sorted((state.get("exchange") or {}).items()))
        if isinstance(state.get("exchange"), dict)
        else str(state.get("exchange") or ""),
        str(state.get("strategy_name") or ""),
        int(state.get("last_candle_timestamp") or 0),
        _regime_key(state),
        _checklist_sig(state),
        _positions_sig(envelope),
        _portfolio_sig(envelope),
        str(state.get("primary_timeframe") or ""),
    )


def _host_metrics_tier_sig() -> Tuple[Any, ...]:
    try:
        from xauby.ui.textual_tui.system_cache import get_cached_host_metrics
        from xauby.ui.dashboard import _metric_tier

        cpu_pct, ram_load, _, _, db_size_mb = get_cached_host_metrics()
        return (
            _metric_tier(cpu_pct, 50.0, 80.0),
            _metric_tier(ram_load, 60.0, 85.0),
            _metric_tier(db_size_mb, 200.0, 500.0),
            int(cpu_pct) // 10,
            int(ram_load) // 10,
            int(db_size_mb) // 50,
        )
    except Exception:
        return ("na", "na", "na", 0, 0, 0)


def header_fingerprint(
    state: Dict[str, Any],
    layout_width: int,
) -> Tuple[Any, ...]:
    """Header-only data (latency, uptime, etc.) — updated separately from panels."""
    if not state:
        return ("empty", layout_width // 10 * 10)

    sym = str(state.get("symbol") or "")
    price = _price_key(float(state.get("current_price") or 0.0))
    change = round(
        float(
            state.get("price_change_24h_pct")
            or state.get("percent_change_24h")
            or 0.0
        ),
        2,
    )

    return (
        layout_width // 10 * 10,
        sym,
        price,
        change,
        bool(state.get("simulate_only", True)),
        bool(state.get("read_only", False)),
        tuple(sorted((state.get("exchange") or {}).items()))
        if isinstance(state.get("exchange"), dict)
        else str(state.get("exchange") or ""),
        str(state.get("strategy_name") or ""),
        str(state.get("primary_timeframe") or ""),
        int(state.get("api_latency_ms") or 0),
        round(float(state.get("clock_offset_seconds") or 0.0), 1),
        str(state.get("engine_started_at") or ""),
        _host_metrics_tier_sig(),
    )


def decision_gates_panel_fingerprint(
    state: Dict[str, Any],
    layout_width: int,
) -> Tuple[Any, ...]:
    if not state:
        return ("empty", layout_width // 10 * 10)
    meta = state.get("signal_meta") or {}
    return (
        layout_width // 10 * 10,
        str(meta.get("action") or "HOLD"),
        str(meta.get("intent") or ""),
        str(meta.get("position_side") or ""),
        _checklist_sig(state),
        str(meta.get("reason") or "")[:120],
        str((state.get("position") or {}).get("state") or ""),
    )


def checklist_panel_fingerprint(state: Dict[str, Any], layout_width: int) -> Tuple[Any, ...]:
    """Fingerprint for merged signal+checklist panel (includes decision action/reason)."""
    if not state:
        return ("empty", layout_width // 10 * 10)
    meta = state.get("signal_meta") or {}
    return (
        layout_width // 10 * 10,
        _checklist_sig(state),
        str(state.get("symbol") or ""),
        str(meta.get("action") or "HOLD"),
        str(meta.get("intent") or ""),
        str(meta.get("position_side") or ""),
        str(meta.get("strategy_name") or ""),
        str(meta.get("reason") or "")[:120],
        str((state.get("position") or {}).get("state") or ""),
    )


def portfolio_panel_fingerprint(envelope: Dict[str, Any], layout_width: int) -> Tuple[Any, ...]:
    if not envelope:
        return ("empty", layout_width // 10 * 10)
    agg_sig, bal_sig = _portfolio_sig(envelope)
    return (
        layout_width // 10 * 10,
        str(envelope.get("focused_symbol") or ""),
        agg_sig,
        bal_sig,
        _envelope_prices_sig(envelope),
    )


def positions_panel_fingerprint(envelope: Dict[str, Any], layout_width: int) -> Tuple[Any, ...]:
    if not envelope:
        return ("empty", layout_width // 10 * 10)
    return (layout_width // 10 * 10, _positions_sig(envelope))


def regime_panel_fingerprint(state: Dict[str, Any], layout_width: int) -> Tuple[Any, ...]:
    if not state:
        return ("empty", layout_width // 10 * 10)
    g_info = state.get("macro_guard") or {}
    pos = state.get("position") or {}
    risk = state.get("risk") or {}
    return (
        layout_width // 10 * 10,
        _regime_key(state),
        bool(g_info.get("enabled")),
        str(g_info.get("status") or ""),
        str(pos.get("state") or ""),
        round(float(pos.get("unrealized_pnl_pct") or 0.0), 2),
        round(float(risk.get("risk_pct") or 0.01), 4),
        tuple(str(e) for e in (state.get("recent_events") or [])[:3]),
        int(time.time()) // 60,
    )


def chart_panel_fingerprint(state: Dict[str, Any], layout_width: int) -> Tuple[Any, ...]:
    """Fingerprint for when the candle/volume block needs recomputing."""
    from xauby.ui.dashboard import get_layout_profile, _chart_dimensions
    from xauby.ui.textual_tui.layout import is_phone_layout

    if not state:
        return ("empty", layout_width)

    w = max(38, layout_width)
    profile = get_layout_profile(w)
    content_width = max(16, w - 4)
    candle_w, chart_h, show_vol = _chart_dimensions(w, profile, content_width=content_width)
    price = float(state.get("current_price") or 0.0)
    if price >= 1000:
        price_key = round(price, 0)
    elif price >= 10:
        price_key = round(price, 1)
    else:
        price_key = round(price, 2)

    return (
        str(state.get("symbol") or ""),
        str(state.get("primary_timeframe") or "4h"),
        str(state.get("strategy_name") or ""),
        int(state.get("last_candle_timestamp") or 0),
        w // 10 * 10,
        candle_w,
        chart_h,
        show_vol,
        is_phone_layout(w),
        price_key,
    )
