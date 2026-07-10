from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import pandas as pd
import pandas_ta as ta

from xauby.ui.chart_axis import (
    format_price_axis as _format_price_axis,
    nice_price_step as _nice_price_step,
    price_axis_label_rows as _price_axis_label_rows,
)
from xauby.storage.interface import IDatabaseRepository
from xauby.strategies.cdc_action_zone import classify_zone
from xauby.utils.colors import (
    C_RESET, C_PRIMARY, C_MUTED, C_DARK, C_GREEN, C_RED,
    RESET, fg_rgb, bg_rgb, make_gemini_gradient,
    blend_rgb, shade_rgb,
)
from xauby.utils.common import format_to_ict, TH_TZ, get_terminal_width

_CHART_CACHE = {
    "symbol": "",
    "width": 0,
    "height": 0,
    "show_volume": False,
    "last_ts": 0,
    "last_price": 0.0,
    "last_trade_time": "",
    "lines": []
}

_AP_SMOOTHING_CACHE: Optional[int] = None
_CHART_UI_CACHE: Optional[Dict[str, Any]] = None
_ARCHITECTURE_CACHE: Optional[Dict[str, Any]] = None

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

RGB_SLATE_MUTED = (148, 163, 184)
RGB_SLATE_DARK = (71, 85, 105)
RGB_PANEL_BG = (24, 24, 27)
RGB_INDIGO = (129, 140, 248)

DEFAULT_ZONE_RGB = {
    "GREEN": (52, 211, 153),
    "BLUE": (129, 140, 248),
    "LBLUE": (34, 211, 238),
    "RED": (251, 113, 133),
    "ORANGE": (251, 146, 60),
    "YELLOW": (253, 224, 71),
}

DEFAULT_ZONE_BG_RGB = {
    "GREEN": (6, 78, 59),
    "BLUE": (49, 46, 129),
    "LBLUE": (22, 78, 99),
    "RED": (153, 27, 27),
    "ORANGE": (124, 45, 18),
    "YELLOW": (120, 53, 4),
}


def _rgb_to_fg(rgb: tuple[int, int, int]) -> str:
    return fg_rgb(*rgb)


def _rgb_to_bg(rgb: tuple[int, int, int]) -> str:
    return bg_rgb(*rgb)


def _chart_theme_enabled() -> bool:
    """Visual-only chart shading toggle; flat solid candles are the default."""
    value = _load_chart_ui_config().get("chart_candle_gradient", "flat")
    return str(value).lower() not in {"0", "false", "off", "none", "flat"}


def _candle_body_cell(
    zone_rgb: tuple[int, int, int],
    bg_rgb_value: Optional[tuple[int, int, int]],
    *,
    row: int,
    body_min: int,
    body_max: int,
) -> str:
    """Render one shaded candle body cell using the existing palette."""
    if not _chart_theme_enabled():
        return _rgb_to_fg(zone_rgb) + "█" + C_RESET
    span = max(1, body_max - body_min)
    pos = (row - body_min) / span if span else 0.5
    edge_distance = min(pos, 1.0 - pos) * 2.0
    factor = 0.78 + (edge_distance * 0.25)
    fg = shade_rgb(zone_rgb, factor)
    bg = blend_rgb(bg_rgb_value or RGB_PANEL_BG, RGB_PANEL_BG, 0.38)
    glyph = "█" if edge_distance > 0.55 else ("▓" if edge_distance > 0.25 else "▒")
    return _rgb_to_bg(bg) + _rgb_to_fg(fg) + glyph + C_RESET


def _wick_cell(zone_rgb: tuple[int, int, int]) -> str:
    if not _chart_theme_enabled():
        return _rgb_to_fg(zone_rgb) + "│" + C_RESET
    return _rgb_to_fg(blend_rgb(zone_rgb, RGB_SLATE_DARK, 0.36)) + "│" + C_RESET


def _volume_cell(base_rgb: tuple[int, int, int], norm: float, glyph: str) -> str:
    intensity = 0.62 + (max(0.0, min(1.0, norm)) * 0.38)
    return _rgb_to_fg(shade_rgb(base_rgb, intensity)) + glyph + C_RESET


def _gradient_bar(base_rgb: tuple[int, int, int], filled: int, width: int) -> str:
    chars: List[str] = []
    for i in range(max(0, filled)):
        t = i / max(1, filled - 1)
        rgb = shade_rgb(base_rgb, 0.68 + (0.32 * t))
        chars.append(f"{_rgb_to_fg(rgb)}█{C_RESET}")
    return "".join(chars) + (" " * max(0, width - filled))


def _load_chart_ui_config() -> Dict[str, Any]:
    """Read cli_ui chart settings from bot_config.yaml (cached)."""
    global _CHART_UI_CACHE
    if _CHART_UI_CACHE is not None:
        return _CHART_UI_CACHE
    defaults = {
        "chart_candle_cols": 1,
        "chart_max_candles": 0,
        "chart_candle_gradient": "flat",
    }
    try:
        import yaml
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        ui = cfg.get("cli_ui", {}) or {}
        cols = max(1, min(2, int(ui.get("chart_candle_cols", 1))))
        max_c = int(ui.get("chart_max_candles", 0) or 0)
        _CHART_UI_CACHE = {
            "chart_candle_cols": cols,
            "chart_max_candles": max(0, max_c),
            "chart_candle_gradient": ui.get("chart_candle_gradient", "flat"),
        }
    except Exception:
        _CHART_UI_CACHE = dict(defaults)
    return _CHART_UI_CACHE


def _load_architecture_config() -> Dict[str, Any]:
    global _ARCHITECTURE_CACHE
    if _ARCHITECTURE_CACHE is not None:
        return _ARCHITECTURE_CACHE
    try:
        import yaml
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        _ARCHITECTURE_CACHE = cfg.get("architecture") or {}
    except Exception:
        _ARCHITECTURE_CACHE = {}
    return _ARCHITECTURE_CACHE


def _use_indicator_registry() -> bool:
    from xauby.runtime.architecture_config import indicator_registry_enabled

    try:
        import yaml
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    return indicator_registry_enabled(cfg)


def get_chart_candle_cols() -> int:
    """Terminal columns per candle (1 = compact, 2 = legacy wide gap)."""
    return int(_load_chart_ui_config().get("chart_candle_cols", 1))


def resolve_chart_timeframe(pair_primary: str) -> str:
    """Dashboard chart TF follows each pair's primary_timeframe (whitelist / registry)."""
    from xauby.runtime.pair_registry import _normalize_tf
    return _normalize_tf(pair_primary or "4h")


def get_chart_max_candles_cap() -> int:
    """Optional hard cap from config; 0 = use layout profile limits only."""
    return int(_load_chart_ui_config().get("chart_max_candles", 0) or 0)


def _get_ap_smoothing() -> int:
    """Read xAuby ActionZone ap_smoothing from bot_config.yaml.

    Mirrors the strategy so the dashboard chart zones match the bot's zones.
    1 = raw close, 2 = Piriya CDC V3 (AP = ema(close, 2)). Cached after first read.
    """
    global _AP_SMOOTHING_CACHE
    if _AP_SMOOTHING_CACHE is not None:
        return _AP_SMOOTHING_CACHE
    ap = 1
    try:
        import yaml
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        strategy_config = (cfg.get("strategy") or {}).get("config") or {}
        legacy_config = cfg.get("strategies") or {}
        raw = (
            (strategy_config.get("xauby_actionzone") or {}).get("ap_smoothing")
            or (strategy_config.get("cdc_action_zone") or {}).get("ap_smoothing")
            or (legacy_config.get("xauby_actionzone") or {}).get("ap_smoothing")
            or (legacy_config.get("cdc_action_zone") or {}).get("ap_smoothing")
            or 1
        )
        ap = max(1, int(raw))
    except Exception:
        ap = 1
    _AP_SMOOTHING_CACHE = ap
    return ap

def get_chart_lines(
    db: IDatabaseRepository,
    symbol: str,
    width: int = 27,
    height: int = 15,
    current_price: Optional[float] = None,
    show_volume: bool = False,
    max_line_width: Optional[int] = None,
    timeframe: str = "4h",
    *,
    include_footer_legend: bool = True,
    strategy_name: Optional[str] = None,
) -> List[str]:
    """Cached wrapper for get_chart_lines_raw to avoid redundant computations."""
    global _CHART_CACHE
    tf = (timeframe or "4h").lower()
    try:
        candles = db.get_candles(symbol, tf, limit=1)
        latest_ts = candles[-1]["timestamp"] if candles else 0
    except Exception:
        latest_ts = 0

    try:
        trades = db.get_closed_trades(symbol, limit=1)
        last_trade_time = trades[0].get("closed_at", "") if trades else ""
    except Exception:
        last_trade_time = ""

    curr_p = current_price or 0.0
    last_p = _CHART_CACHE["last_price"]
    price_changed = False
    if curr_p > 0 and last_p > 0:
        price_changed = abs(curr_p - last_p) / last_p > 0.0001
    elif curr_p != last_p:
        price_changed = True

    candle_cols = get_chart_candle_cols()
    if (
        _CHART_CACHE.get("symbol") == symbol
        and _CHART_CACHE.get("timeframe") == tf
        and _CHART_CACHE.get("width") == width
        and _CHART_CACHE.get("height") == height
        and _CHART_CACHE.get("show_volume") == show_volume
        and _CHART_CACHE.get("candle_cols") == candle_cols
        and _CHART_CACHE.get("last_ts") == latest_ts
        and _CHART_CACHE.get("last_trade_time") == last_trade_time
        and _CHART_CACHE.get("include_footer_legend") == include_footer_legend
        and _CHART_CACHE.get("strategy_name") == (strategy_name or "")
        and not price_changed
    ):
        # Return a copy — callers (e.g. phone TUI) may extend with volume lines.
        return list(_CHART_CACHE["lines"])

    lines = get_chart_lines_raw(
        db,
        symbol,
        width,
        height,
        current_price,
        show_volume=show_volume,
        max_line_width=max_line_width,
        timeframe=tf,
        include_footer_legend=include_footer_legend,
        strategy_name=strategy_name,
    )
    _CHART_CACHE = {
        "symbol": symbol,
        "timeframe": tf,
        "width": width,
        "height": height,
        "show_volume": show_volume,
        "candle_cols": candle_cols,
        "include_footer_legend": include_footer_legend,
        "strategy_name": strategy_name or "",
        "last_ts": latest_ts,
        "last_price": curr_p,
        "last_trade_time": last_trade_time,
        "lines": lines,
    }
    return lines


def fetch_chart_dataframe(
    db: IDatabaseRepository,
    symbol: str,
    width: int,
    timeframe: str = "4h",
    current_price: Optional[float] = None,
    strategy_name: Optional[str] = None,
):
    """Load OHLC + indicator series for charting. Returns (df_chart, error_message)."""
    try:
        tf = (timeframe or "4h").lower()
        bar_seconds = TIMEFRAME_SECONDS.get(tf, 14400)
        raw_candles = db.get_candles(symbol, tf, limit=width + 50)
        if not raw_candles or len(raw_candles) < 26:
            n = len(raw_candles) if raw_candles else 0
            return None, f"Waiting for candles (have {n}/26)"

        df = pd.DataFrame(raw_candles)
        if current_price is not None and current_price > 0 and not df.empty:
            last_idx = df.index[-1]
            df.loc[last_idx, "close"] = float(current_price)
            df.loc[last_idx, "high"] = max(float(df.loc[last_idx, "high"]), float(current_price))
            df.loc[last_idx, "low"] = min(float(df.loc[last_idx, "low"]), float(current_price))

        if _use_indicator_registry():
            from xauby.ui.chart_registry import compute_chart_dataframe

            df = compute_chart_dataframe(df, symbol, strategy_name=strategy_name)
            df_chart = df.tail(width).copy()
            df_chart.attrs["timeframe"] = tf
            df_chart.attrs["bar_seconds"] = bar_seconds
            return df_chart, None

        close = df["close"].astype(float)
        ap_smoothing = _get_ap_smoothing()
        if ap_smoothing >= 2:
            ap_series = ta.ema(close, length=ap_smoothing).fillna(close)
        else:
            ap_series = close
        df["ap"] = ap_series
        df["ema12"] = ta.ema(ap_series, length=12).fillna(df["close"])
        df["ema26"] = ta.ema(ap_series, length=26).fillna(df["close"])

        df_chart = df.tail(width).copy()
        zones = []
        for _, row in df_chart.iterrows():
            ap_val = float(row["ap"]) if not pd.isna(row["ap"]) else float(row["close"])
            zones.append(
                classify_zone(ap_val, float(row["ema12"]), float(row["ema26"]))
            )
        df_chart["zone"] = zones
        df_chart.attrs["timeframe"] = tf
        df_chart.attrs["bar_seconds"] = bar_seconds
        return df_chart, None
    except Exception as e:
        return None, f"Failed to load chart data: {e}"


def get_chart_lines_raw(
    db: IDatabaseRepository,
    symbol: str,
    width: int = 27,
    height: int = 15,
    current_price: Optional[float] = None,
    show_volume: bool = False,
    max_line_width: Optional[int] = None,
    timeframe: str = "4h",
    *,
    include_footer_legend: bool = True,
    strategy_name: Optional[str] = None,
) -> List[str]:
    """
    Renders a TradingView-style candlestick chart in the terminal with CDC ActionZone v3 colors
    and trade markers. Spacing per candle is controlled by cli_ui.chart_candle_cols (1 or 2).
    """
    try:
        # Fetch width + 50 to allow EMA warm-up
        tf = (timeframe or "4h").lower()
        candle_cols = get_chart_candle_cols()
        bar_seconds = TIMEFRAME_SECONDS.get(tf, 14400)

        if _use_indicator_registry():
            df_chart, err = fetch_chart_dataframe(
                db,
                symbol,
                width,
                tf,
                current_price,
                strategy_name=strategy_name,
            )
            if df_chart is None:
                return [f"  {err or 'Waiting for chart data...'}"]
            bar_seconds = int(df_chart.attrs.get("bar_seconds", bar_seconds))
        else:
            raw_candles = db.get_candles(symbol, tf, limit=width + 50)
            if not raw_candles or len(raw_candles) < 26:
                return [f"  Waiting for candles (have {len(raw_candles) if raw_candles else 0}/26)"]

            df = pd.DataFrame(raw_candles)

            # Override the last candle (current forming candle) with live ticker price
            if current_price is not None and current_price > 0 and not df.empty:
                last_idx = df.index[-1]
                df.loc[last_idx, "close"] = float(current_price)
                df.loc[last_idx, "high"] = max(float(df.loc[last_idx, "high"]), float(current_price))
                df.loc[last_idx, "low"] = min(float(df.loc[last_idx, "low"]), float(current_price))

            close = df["close"].astype(float)

            # CDC V3 source smoothing (AP = ema(close, n)); mirrors the strategy so
            # the chart's zone colours/EMA lines match the bot's decisions.
            ap_smoothing = _get_ap_smoothing()
            if ap_smoothing >= 2:
                ap_series = ta.ema(close, length=ap_smoothing).fillna(close)
            else:
                ap_series = close
            df["ap"] = ap_series

            df["ema12"] = ta.ema(ap_series, length=12)
            df["ema26"] = ta.ema(ap_series, length=26)

            # Fill NaN values
            df["ema12"] = df["ema12"].fillna(df["close"])
            df["ema26"] = df["ema26"].fillna(df["close"])

            # Slice to last `width` candles
            df_chart = df.tail(width).copy()
            zones = []
            for _, row in df_chart.iterrows():
                ap_val = float(row["ap"]) if not pd.isna(row["ap"]) else float(row["close"])
                zones.append(
                    classify_zone(ap_val, float(row["ema12"]), float(row["ema26"]))
                )
            df_chart["zone"] = zones

        # Precalculate boundaries
        max_h = df_chart["high"].max()
        min_l = df_chart["low"].min()
        price_range = max_h - min_l
        if price_range == 0:
            price_range = 1.0

        # Calculate volume height dynamically (approx 15-20% of original height)
        vol_rows = max(2, int(round(height * 0.18)))
        candle_height = max(5, height - vol_rows)

        # Helper to map price to row index [0, candle_height-1]
        def price_to_row(price):
            val = int(round((price - min_l) / price_range * (candle_height - 1)))
            return max(0, min(candle_height - 1, val))

        # Fetch closed trades to draw markers
        trades = db.get_closed_trades(symbol, limit=20)
        markers = {}
        for c_idx, c_row in df_chart.iterrows():
            c_start = int(c_row["timestamp"])
            c_end = c_start + bar_seconds
            
            for t in trades:
                try:
                    op_str = t.get("opened_at", "").replace("T", " ")[:19]
                    cl_str = t.get("closed_at", "").replace("T", " ")[:19]
                    op_t = datetime.strptime(op_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                    cl_t = datetime.strptime(cl_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                    
                    if c_start <= op_t < c_end:
                        markers[c_start] = "\033[92m▲\033[0m" # Light Green BUY arrow
                    if c_start <= cl_t < c_end:
                        markers[c_start] = "\033[91m▼\033[0m" # Light Red SELL arrow
                except Exception:
                    continue

        registry_active = _use_indicator_registry()
        strat_for_chart = strategy_name or "xauby_actionzone"
        zone_column = "zone"
        cross_glyph = "✦"
        line_defs = [
            {"key": "ema12", "label": "EMA12", "color": (168, 85, 247), "glyph": "●"},
            {"key": "ema26", "label": "EMA26", "color": (56, 189, 248), "glyph": "◆"},
        ]
        zone_rgb_map = dict(DEFAULT_ZONE_RGB)
        zone_bg_rgb_map = dict(DEFAULT_ZONE_BG_RGB)
        color_map = {key: _rgb_to_fg(rgb) for key, rgb in zone_rgb_map.items()}
        bg_color_map = {key: _rgb_to_bg(rgb) for key, rgb in zone_bg_rgb_map.items()}
        if registry_active:
            try:
                from xauby.ui.chart_registry import chart_display_metadata, resolve_chart_strategy_name

                strat_for_chart = strategy_name or resolve_chart_strategy_name(symbol)
                meta = chart_display_metadata(strat_for_chart)
                zone_column = str(meta.get("zone_column") or zone_column)
                cross_glyph = str(meta.get("cross_glyph") or "")
                meta_lines = [x for x in (meta.get("lines") or []) if isinstance(x, dict)]
                if meta_lines:
                    line_defs = meta_lines
                zone_rgb_map = {}
                zone_bg_rgb_map = {}
                color_map = {}
                bg_color_map = {}
                for z in meta.get("zones") or []:
                    if not isinstance(z, dict):
                        continue
                    key = str(z.get("key") or "")
                    rgb = z.get("color") or (241, 245, 249)
                    bg = z.get("bg_color") or z.get("background")
                    if key and isinstance(rgb, (list, tuple)) and len(rgb) == 3:
                        zone_rgb_map[key] = tuple(int(x) for x in rgb)
                        color_map[key] = fg_rgb(*rgb)
                    if key and isinstance(bg, (list, tuple)) and len(bg) == 3:
                        zone_bg_rgb_map[key] = tuple(int(x) for x in bg)
                        bg_color_map[key] = bg_rgb(*bg)
            except Exception:
                pass
        default_candle_rgb = RGB_SLATE_MUTED
        default_candle_color = _rgb_to_fg(default_candle_rgb)

        cross_candle_idx: set[int] = set()
        cross_line_keys = [str(x.get("key", "")) for x in line_defs[:2]]
        if cross_glyph and len(cross_line_keys) == 2 and all(k in df_chart.columns for k in cross_line_keys):
            first_vals = df_chart[cross_line_keys[0]].astype(float).tolist()
            second_vals = df_chart[cross_line_keys[1]].astype(float).tolist()
            for i in range(1, len(first_vals)):
                spread_prev = first_vals[i - 1] - second_vals[i - 1]
                spread_curr = first_vals[i] - second_vals[i]
                if spread_prev * spread_curr < 0:
                    cross_candle_idx.add(i)

        candles_to_draw = []
        for i, (idx, row) in enumerate(df_chart.iterrows()):
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
            ts = int(row["timestamp"])
            if zone_column in row and row.get(zone_column):
                zone = str(row.get(zone_column))
            elif not registry_active:
                f = float(row["ema12"]) if "ema12" in row and not pd.isna(row["ema12"]) else c
                s = float(row["ema26"]) if "ema26" in row and not pd.isna(row["ema26"]) else c
                ap_val = float(row["ap"]) if "ap" in row and not pd.isna(row["ap"]) else c
                zone = classify_zone(ap_val, f, s)
            else:
                zone = "NEUTRAL"

            line_rows = []
            for line in line_defs:
                key = str(line.get("key", ""))
                if not key or key not in row or pd.isna(row[key]):
                    continue
                rgb = line.get("color") or (241, 245, 249)
                glyph = str(line.get("glyph") or "●")[:1]
                line_rows.append(
                    {
                        "key": key,
                        "row": price_to_row(float(row[key])),
                        "glyph": glyph,
                        "color": fg_rgb(*rgb) if isinstance(rgb, (list, tuple)) and len(rgb) == 3 else default_candle_color,
                    }
                )
            cross_row = None
            if i in cross_candle_idx and len(line_rows) >= 2:
                cross_row = price_to_row(
                    (float(row[cross_line_keys[0]]) + float(row[cross_line_keys[1]])) / 2.0
                )

            candles_to_draw.append({
                "o": price_to_row(o),
                "h": price_to_row(h),
                "l": price_to_row(l),
                "c": price_to_row(c),
                "line_rows": line_rows,
                "zone": zone,
                "ts": ts,
                "cross": i in cross_candle_idx,
                "cross_row": cross_row,
            })

        chart_lines = []

        price_step = _nice_price_step(price_range)
        axis_labels = _price_axis_label_rows(min_l, max_h, candle_height, price_step)
        try:
            marker_price = (
                float(current_price)
                if current_price is not None and float(current_price) > 0
                else float(df_chart.iloc[-1]["close"])
            )
            current_price_row = price_to_row(marker_price)
        except Exception:
            marker_price = 0.0
            current_price_row = None

        # Draw from top row (candle_height-1) down to 0
        for r in range(candle_height - 1, -1, -1):
            row_str = ""
            for item in candles_to_draw:
                body_min = min(item["o"], item["c"])
                body_max = max(item["o"], item["c"])
                
                is_body = body_min <= r <= body_max
                is_wick = item["l"] <= r <= item["h"]
                line_hit = next((line for line in item.get("line_rows", []) if line.get("row") == r), None)
                
                char = " "
                color = ""
                zone = item.get("zone", "")
                candle_rgb = zone_rgb_map.get(zone, default_candle_rgb)
                body_bg_rgb = zone_bg_rgb_map.get(zone)
                body_bg = bg_color_map.get(zone, "")

                show_cross = bool(cross_glyph) and item.get("cross") and item.get("cross_row") == r

                if show_cross:
                    gap = " " if candle_cols >= 2 else ""
                    row_str += make_gemini_gradient(cross_glyph) + gap
                    continue
                elif line_hit:
                    char = str(line_hit.get("glyph") or "●")[:1]
                    line_color = str(line_hit.get("color") or default_candle_color)
                    color = (body_bg + line_color) if is_body and body_bg else line_color
                elif is_body:
                    gap = " " if candle_cols >= 2 else ""
                    row_str += _candle_body_cell(
                        candle_rgb,
                        body_bg_rgb,
                        row=r,
                        body_min=body_min,
                        body_max=body_max,
                    ) + gap
                    continue
                elif is_wick:
                    gap = " " if candle_cols >= 2 else ""
                    row_str += _wick_cell(candle_rgb) + gap
                    continue

                gap = " " if candle_cols >= 2 else ""
                if color:
                    row_str += color + char + "\033[0m" + gap
                else:
                    row_str += (" " if candle_cols >= 2 else " ") + gap
                    
            tick_price = axis_labels.get(r)
            is_current_price_row = current_price_row is not None and r == current_price_row
            axis_divider = f"{C_DARK}│{C_RESET}"
            if is_current_price_row and marker_price > 0:
                price_label = (
                    f"{_rgb_to_bg((49, 46, 129))}"
                    f"{_rgb_to_fg((199, 210, 254))}"
                    f" {_format_price_axis(marker_price, price_step)} {C_RESET}"
                )
                axis_divider = f"{_rgb_to_fg(RGB_INDIGO)}┤{C_RESET}"
            elif tick_price is not None:
                price_label = f"{C_MUTED}{_format_price_axis(tick_price, price_step)}{C_RESET}"
            else:
                price_label = f"{C_DARK}│{C_RESET}"
            chart_lines.append(f"  {row_str}{axis_divider} {price_label}")

        # Footer labels sized to the chart row budget (not full terminal when embedded)
        line_budget = max_line_width if max_line_width is not None else get_terminal_width()
        if line_budget < 38:
            line_budget = 38

        # Add markers row
        marker_cell = " " if candle_cols >= 2 else ""
        if (width * candle_cols + 27) <= line_budget:
            marker_label = "Markers (▲=Buy, ▼=Sell)"
        elif (width * candle_cols + 17) <= line_budget:
            marker_label = "Markers (▲/▼)"
        elif (width * candle_cols + 11) <= line_budget:
            marker_label = "Markers"
        elif (width * candle_cols + 7) <= line_budget:
            marker_label = "▲/▼"
        else:
            marker_label = ""
        marker_str = ""
        for item in candles_to_draw:
            m = markers.get(item["ts"], " ")
            marker_str += m + marker_cell
        chart_lines.append(f"  {marker_str}{C_DARK}│{C_RESET} {C_MUTED}{marker_label}{C_RESET}")
        
        # Add volume bars row(s) below markers and above timeline
        if "volume" in df_chart.columns:
            chart_lines.extend(
                format_volume_bars_lines(
                    df_chart,
                    width,
                    vol_rows=vol_rows,
                    candle_cols=candle_cols,
                    color_by_direction=True,
                    show_label=False,
                )
            )
            
        # Add timeline row
        total_cols = width * candle_cols
        if total_cols >= 26:
            start_time = datetime.fromtimestamp(candles_to_draw[0]["ts"], tz=TH_TZ).strftime("%m/%d %H:%M")
            end_time = datetime.fromtimestamp(candles_to_draw[-1]["ts"], tz=TH_TZ).strftime("%m/%d %H:%M")
        else:
            start_time = datetime.fromtimestamp(candles_to_draw[0]["ts"], tz=TH_TZ).strftime("%H:%M")
            end_time = datetime.fromtimestamp(candles_to_draw[-1]["ts"], tz=TH_TZ).strftime("%H:%M")
            
        spaces_needed = total_cols - len(start_time) - len(end_time) - 2
        if spaces_needed < 2:
            spaces_needed = 2
            
        tf_label = tf.upper()
        timeframe_label = (
            f"Timeframe: {tf_label} (ICT)"
            if (width * candle_cols + 25) <= line_budget
            else f"{tf_label} (ICT)"
        )
        timeline_str = f"  {C_MUTED}{start_time}{' ' * spaces_needed}{end_time}{C_RESET} {C_DARK}│{C_RESET} {C_MUTED}{timeframe_label}{C_RESET}"
        chart_lines.append(timeline_str)

        if include_footer_legend:
            try:
                from xauby.ui.textual_tui.chart_legend import format_chart_legend

                chart_lines.extend(
                    f"  {line}"
                    for line in format_chart_legend(
                        line_budget,
                        compact=line_budget < 118,
                        strategy_name=strat_for_chart,
                    ).splitlines()
                )
            except Exception:
                line_parts = [f"{C_MUTED}Leg:{C_RESET}"]
                for key, color in color_map.items():
                    line_parts.append(f"{color}■{key}{RESET}")
                for line in line_defs:
                    rgb = line.get("color") or (241, 245, 249)
                    glyph = str(line.get("glyph") or "●")[:1]
                    label = str(line.get("label") or line.get("key") or "Line")
                    line_parts.append(f"{fg_rgb(*rgb)}{glyph}{label}{RESET}")
                chart_lines.append("  " + " ".join(line_parts))

        return chart_lines
    except Exception as e:
        return [f"  Failed to render chart: {e}"]

def format_volume_bars_lines(
    df_chart,
    width: int,
    *,
    vol_rows: int = 2,
    candle_cols: Optional[int] = None,
    color_by_direction: bool = True,
    show_label: bool = False,
) -> List[str]:
    """ASCII volume histogram aligned to candle columns (used inline or on mobile panel)."""
    if df_chart is None or "volume" not in getattr(df_chart, "columns", []):
        return []
    cols = candle_cols if candle_cols is not None else get_chart_candle_cols()
    vols = df_chart["volume"].astype(float).tolist()
    if not vols:
        return [f"  {C_MUTED}No volume data{C_RESET}"]
    
    # Fetch open and close to determine direction
    opens = df_chart["open"].astype(float).tolist() if "open" in df_chart.columns else [0.0] * len(vols)
    closes = df_chart["close"].astype(float).tolist() if "close" in df_chart.columns else [0.0] * len(vols)
    
    vmax = max(vols) if vols else 1.0
    vol_rows = max(1, min(4, vol_rows))
    lines: List[str] = []

    vol_green = DEFAULT_ZONE_RGB["GREEN"]
    vol_red = DEFAULT_ZONE_RGB["RED"]
    
    for vol_row in range(vol_rows):
        v_str = ""
        threshold = 1.0 - (vol_row / vol_rows)
        for idx, v in enumerate(vols):
            norm = (v / vmax) if vmax else 0.0
            vol_gap = " " if cols >= 2 else ""
            
            if color_by_direction:
                is_up = closes[idx] >= opens[idx]
                base_rgb = vol_green if is_up else vol_red
            else:
                base_rgb = vol_green
                
            if norm >= threshold:
                v_str += f"{_volume_cell(base_rgb, norm, '█')}{vol_gap}"
            elif norm >= threshold - (0.5 / vol_rows):
                v_str += f"{_volume_cell(base_rgb, norm, '▒')}{vol_gap}"
            else:
                v_str += (" " if cols >= 2 else " ") + vol_gap
        
        if show_label:
            label = f" {C_MUTED}Vol{C_RESET}" if vol_row == 0 else ""
        else:
            label = ""
        lines.append(f"  {v_str}{C_DARK}│{C_RESET}{label}")
    return lines


_VOLUME_CACHE: Dict[str, Any] = {
    "symbol": "",
    "timeframe": "",
    "width": 0,
    "vol_rows": 0,
    "last_ts": 0,
    "last_price": 0.0,
    "lines": [],
}


def get_volume_chart_lines(
    db: IDatabaseRepository,
    symbol: str,
    width: int = 27,
    current_price: Optional[float] = None,
    timeframe: str = "4h",
    *,
    vol_rows: int = 3,
) -> List[str]:
    """Standalone volume chart (mobile dashboard panel below candles)."""
    global _VOLUME_CACHE
    tf = (timeframe or "4h").lower()
    try:
        candles = db.get_candles(symbol, tf, limit=1)
        latest_ts = candles[-1]["timestamp"] if candles else 0
    except Exception:
        latest_ts = 0

    curr_p = current_price or 0.0
    last_p = _VOLUME_CACHE["last_price"]
    price_changed = False
    if curr_p > 0 and last_p > 0:
        price_changed = abs(curr_p - last_p) / last_p > 0.0001
    elif curr_p != last_p:
        price_changed = True

    if (
        _VOLUME_CACHE.get("symbol") == symbol
        and _VOLUME_CACHE.get("timeframe") == tf
        and _VOLUME_CACHE.get("width") == width
        and _VOLUME_CACHE.get("vol_rows") == vol_rows
        and _VOLUME_CACHE.get("last_ts") == latest_ts
        and not price_changed
    ):
        return list(_VOLUME_CACHE["lines"])

    df_chart, err = fetch_chart_dataframe(db, symbol, width, timeframe, current_price)
    if df_chart is None:
        lines = [f"  {C_MUTED}{err or 'Waiting for volume data...'}{C_RESET}"]
    else:
        lines = format_volume_bars_lines(df_chart, width, vol_rows=vol_rows)
        if not lines:
            lines = [f"  {C_MUTED}Volume data unavailable{C_RESET}"]

    _VOLUME_CACHE = {
        "symbol": symbol,
        "timeframe": tf,
        "width": width,
        "vol_rows": vol_rows,
        "last_ts": latest_ts,
        "last_price": curr_p,
        "lines": lines,
    }
    return list(lines)


def get_zone_distribution(db: IDatabaseRepository, symbol: str, limit: int = 50) -> Dict[str, int]:
    counts = {"GREEN": 0, "BLUE": 0, "LBLUE": 0, "YELLOW": 0, "ORANGE": 0, "RED": 0}
    zone_column = "zone"
    try:
        tf = resolve_chart_timeframe("4h")
        raw_candles = db.get_candles(symbol, tf, limit=limit + 50)
        if not raw_candles or len(raw_candles) < 26:
            return counts
        df = pd.DataFrame(raw_candles)
        if _use_indicator_registry():
            from xauby.ui.chart_registry import chart_display_metadata, compute_chart_dataframe, resolve_chart_strategy_name

            strat = resolve_chart_strategy_name(symbol)
            meta = chart_display_metadata(strat)
            zone_column = str(meta.get("zone_column") or zone_column)
            zone_keys = [str(z.get("key")) for z in meta.get("zones") or [] if isinstance(z, dict) and z.get("key")]
            if zone_keys:
                counts = {key: 0 for key in zone_keys}
            df = compute_chart_dataframe(df, symbol, strategy_name=strat)
        else:
            close = df["close"].astype(float)
            ap_smoothing = _get_ap_smoothing()
            if ap_smoothing >= 2:
                ap_series = ta.ema(close, length=ap_smoothing).fillna(close)
            else:
                ap_series = close
            df["ap"] = ap_series
            df["ema12"] = ta.ema(ap_series, length=12).fillna(df["close"])
            df["ema26"] = ta.ema(ap_series, length=26).fillna(df["close"])
            zones = []
            for _, row in df.iterrows():
                ap_val = float(row["ap"]) if not pd.isna(row["ap"]) else float(row["close"])
                zones.append(classify_zone(ap_val, float(row["ema12"]), float(row["ema26"])))
            df["zone"] = zones

        df_chart = df.tail(limit)
        for _, row in df_chart.iterrows():
            z = str(row.get(zone_column, ""))
            if z not in counts:
                counts[z] = 0
            counts[z] += 1
    except Exception:
        pass
    return counts

def get_bar_chart_lines(db: IDatabaseRepository, symbol: str, width: int = 50) -> List[str]:
    counts = get_zone_distribution(db, symbol, limit=50)
    total = sum(counts.values()) or 1
    bar_rgb_map = dict(DEFAULT_ZONE_RGB)
    label_map = {
        "GREEN": "Green (Buy)",
        "BLUE": "PreBuy 2",
        "LBLUE": "PreBuy 1",
        "YELLOW": "PreSell 1",
        "ORANGE": "PreSell 2",
        "RED": "Red (Sell)",
    }
    try:
        from xauby.ui.chart_registry import chart_display_metadata, resolve_chart_strategy_name

        if _use_indicator_registry():
            meta = chart_display_metadata(resolve_chart_strategy_name(symbol))
            for z in meta.get("zones") or []:
                if not isinstance(z, dict):
                    continue
                key = str(z.get("key") or "")
                rgb = z.get("color") or None
                if key:
                    label_map[key] = str(z.get("label") or key)
                if key and isinstance(rgb, (list, tuple)) and len(rgb) == 3:
                    bar_rgb_map[key] = tuple(int(x) for x in rgb)
    except Exception:
        pass

    preferred = ["GREEN", "BLUE", "LBLUE", "YELLOW", "ORANGE", "RED", "BULL", "BEAR"]
    zones = [z for z in preferred if z in counts]
    zones.extend(z for z in counts.keys() if z not in zones)
    max_label = max([len(label_map.get(z, z)) for z in zones] or [8])
    max_bar_len = max(5, width - max_label - 24) if width < 70 else max(10, width - max_label - 38)

    lines = []
    for zone in zones:
        count = counts.get(zone, 0)
        pct = (count / total) * 100.0
        bar_len = int((count / total) * max_bar_len)
        bar_rgb = bar_rgb_map.get(zone, RGB_SLATE_MUTED)
        bar_str = _gradient_bar(bar_rgb, bar_len, max_bar_len)
        label = label_map.get(zone, zone).ljust(max_label)
        suffix = f"{C_PRIMARY}{count:>2}{C_RESET} {C_MUTED}({pct:.1f}%){C_RESET}" if width < 70 else f"{C_PRIMARY}{count:>2}{C_RESET} {C_MUTED}candles ({pct:.1f}%){C_RESET}"
        lines.append(f"  {C_MUTED}{label}{C_RESET} {C_DARK}│{C_RESET} {bar_str} {suffix}")
    return lines

def get_capital_curve_lines(
    db: IDatabaseRepository,
    symbol: str,
    width: int = 50,
    height: int = 6,
    *,
    trades: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """
    Renders an ASCII line chart showing cumulative P&L growth over time.
    """
    try:
        if trades is None:
            trades = db.get_closed_trades(symbol, limit=1000)
        if not trades:
            from xauby.ui.copy import HISTORY_EMPTY
            return [f"  {HISTORY_EMPTY}"]
        
        # Reverse trades to chronological order (oldest first)
        trades_chrono = list(reversed(trades))

        
        # Build cumulative P&L series starting at 0.0
        y_vals = [0.0]
        curr = 0.0
        for t in trades_chrono:
            pnl_val = t.get("net_pnl")
            pnl_float = float(pnl_val) if pnl_val is not None else 0.0
            curr += pnl_float
            y_vals.append(curr)
            
        L = len(y_vals)
        if L < 2:
            return ["  [!] Need at least 2 points to render Capital Curve."]
            
        # Interpolate / map to width columns
        chart_y = []
        for col in range(width):
            pos = col * (L - 1) / (width - 1)
            idx_low = int(pos)
            idx_high = min(L - 1, idx_low + 1)
            weight = pos - idx_low
            val = y_vals[idx_low] * (1.0 - weight) + y_vals[idx_high] * weight
            chart_y.append(val)
            
        min_y = min(chart_y)
        max_y = max(chart_y)
        range_y = max_y - min_y
        if range_y == 0:
            range_y = 1.0
            
        grid = [[" " for _ in range(width)] for _ in range(height)]
        
        # Plot points onto the grid
        for col in range(width):
            val = chart_y[col]
            row_idx = int((val - min_y) / range_y * (height - 1))
            row_idx = max(0, min(height - 1, row_idx))
            grid_row = (height - 1) - row_idx
            
            if val > 0.0:
                grid[grid_row][col] = f"{C_GREEN}•{C_RESET}"
            elif val < 0.0:
                grid[grid_row][col] = f"{C_RED}•{C_RESET}"
            else:
                grid[grid_row][col] = "•"
                
        lines = []
        # Construct row strings with right-hand Y-axis markers
        for r in range(height):
            row_str = "".join(grid[r])
            row_val = min_y + ((height - 1 - r) / (height - 1)) * range_y if height > 1 else min_y
            
            pnl_color = C_GREEN if row_val > 0.0 else (C_RED if row_val < 0.0 else C_PRIMARY)
            lines.append(f"  {row_str}{C_DARK}│{C_RESET} {pnl_color}{row_val:+.2f} USDT{C_RESET}")
            
        # Add timeline row
        start_time_str = format_to_ict(trades_chrono[0].get("closed_at", ""))
        end_time_str = format_to_ict(trades_chrono[-1].get("closed_at", ""))
        
        start_time = start_time_str if start_time_str else "N/A"
        end_time = end_time_str if end_time_str else "N/A"
        
        spaces_needed = width - len(start_time) - len(end_time)
        if spaces_needed < 1:
            spaces_needed = 1
            
        timeline_text = f"{start_time}{' ' * spaces_needed}{end_time}"
        timeline_text = timeline_text[:width]
        if len(timeline_text) < width:
            timeline_text = timeline_text.ljust(width)
            
        lines.append(f"  {C_MUTED}{timeline_text}{C_RESET}{C_DARK}│{C_RESET} {C_MUTED}Timeline (ICT){C_RESET}")
        return lines
    except Exception as e:
        return [f"  Failed to render Capital Curve: {e}"]
