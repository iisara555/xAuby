"""Registry-backed chart dataframe builder."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from xauby.runtime.architecture_config import strategy_chart_indicators
from xauby.runtime.trading_config import strategy_config_block, strategy_name_for_symbol
from xauby.strategies.indicators.registry import load_indicator


def _load_bot_config() -> Dict[str, Any]:
    try:
        import yaml

        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _indicator_config(cfg: Dict[str, Any], strategy_name: str, symbol: str = "") -> Dict[str, Any]:
    return dict(
        strategy_config_block(
            cfg,
            strategy_name,
            symbol=symbol,
            project_root=".",
            for_live=True,
        )
    )


def resolve_chart_strategy_name(
    symbol: str,
    strategy_name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> str:
    if strategy_name:
        return strategy_name
    cfg = config or _load_bot_config()
    return strategy_name_for_symbol(cfg, symbol)


def compute_chart_dataframe(
    df: pd.DataFrame,
    symbol: str,
    *,
    strategy_name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    cfg = config or _load_bot_config()
    strat = resolve_chart_strategy_name(symbol, strategy_name, cfg)
    indicator_names = strategy_chart_indicators(cfg, strat)
    ind_cfg = _indicator_config(cfg, strat, symbol)
    out = df.copy()
    for name in indicator_names:
        indicator = load_indicator(name, ind_cfg)
        out = indicator.compute(out, ind_cfg)
    return out


def zone_color_map_from_registry(
    strategy_name: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Tuple[int, int, int]]:
    cfg = config or _load_bot_config()
    colors: Dict[str, Tuple[int, int, int]] = {}
    for name in strategy_chart_indicators(cfg, strategy_name):
        indicator = load_indicator(name, _indicator_config(cfg, strategy_name))
        for z in indicator.display_config.get("zones") or []:
            if isinstance(z, dict):
                key = str(z.get("key", ""))
                rgb = tuple(z.get("color", (200, 200, 200)))
                if key and len(rgb) == 3:
                    colors[key] = rgb  # type: ignore[assignment]
    return colors


def legend_entries_for_strategy(
    strategy_name: str,
    config: Optional[Dict[str, Any]] = None,
) -> List[tuple]:
    cfg = config or _load_bot_config()
    entries: List[tuple] = []
    for name in strategy_chart_indicators(cfg, strategy_name):
        indicator = load_indicator(name, _indicator_config(cfg, strategy_name))
        entries.extend(indicator.legend_entries())
    return entries



def chart_display_metadata(
    strategy_name: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return renderer metadata from indicator plugins without hardcoded CDC assumptions."""
    cfg = config or _load_bot_config()
    zone_column = "zone"
    zones: List[Dict[str, Any]] = []
    lines: List[Dict[str, Any]] = []
    cross_glyph = ""
    for name in strategy_chart_indicators(cfg, strategy_name):
        indicator = load_indicator(name, _indicator_config(cfg, strategy_name))
        display = indicator.display_config or {}
        if display.get("zone_column"):
            zone_column = str(display.get("zone_column"))
        for z in display.get("zones") or []:
            if isinstance(z, dict):
                zones.append(dict(z))
        for line in display.get("lines") or []:
            if isinstance(line, dict):
                lines.append(dict(line))
        if display.get("cross_glyph"):
            cross_glyph = str(display.get("cross_glyph"))
    return {
        "zone_column": zone_column,
        "zones": zones,
        "lines": lines,
        "cross_glyph": cross_glyph,
    }
