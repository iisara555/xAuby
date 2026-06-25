"""Bridge IndicatorRegistry plugins to chart and TUI rendering."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

import pandas as pd

from xauby.runtime.architecture_config import strategy_chart_indicators
from xauby.runtime.trading_config import strategy_config_block
from xauby.strategies.indicators.registry import load_indicator


@dataclass
class IndicatorView:
    strategy_name: str
    df: pd.DataFrame
    legend: List[Tuple[str, str, Tuple[int, int, int]]] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    panel_items: List[Dict[str, Any]] = field(default_factory=list)
    zone_colors: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)
    zone_bg_colors: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)
    zone_column: str = "zone"
    line_columns: List[str] = field(default_factory=list)


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


def build_indicator_view(
    df: pd.DataFrame,
    strategy_name: str,
    config: Dict[str, Any] | None = None,
    symbol: str = "",
) -> IndicatorView:
    cfg = config or {}
    ind_cfg = _indicator_config(cfg, strategy_name, symbol)
    out_df = df.copy()
    legend: List[Tuple[str, str, Tuple[int, int, int]]] = []
    metrics: List[Dict[str, Any]] = []
    panel_items: List[Dict[str, Any]] = []
    zone_colors: Dict[str, Tuple[int, int, int]] = {}
    zone_bg: Dict[str, Tuple[int, int, int]] = {}
    zone_column = "zone"
    line_columns: List[str] = []

    for name in strategy_chart_indicators(cfg, strategy_name):
        plugin = load_indicator(name, ind_cfg)
        out_df = plugin.compute(out_df, ind_cfg)
        legend.extend(plugin.legend_entries())
        snap = plugin.snapshot(out_df, ind_cfg)
        for item in plugin.panel_items(snap):
            metrics.append(
                {
                    "plugin": name,
                    "key": item.get("label"),
                    "label": item.get("label"),
                    "value": item.get("value"),
                    "ok": item.get("ok"),
                }
            )
        panel_items.extend(plugin.panel_items(snap))
        zone_colors.update(plugin.zone_color_map())
        zone_bg.update(plugin.zone_bg_color_map())
        zcol = plugin.display_config.get("zone_column")
        if zcol:
            zone_column = str(zcol)
        for line in plugin.display_config.get("lines") or []:
            if isinstance(line, dict):
                key = str(line.get("key", ""))
                if key:
                    line_columns.append(key)

    return IndicatorView(
        strategy_name=strategy_name,
        df=out_df,
        legend=legend,
        metrics=metrics,
        panel_items=panel_items,
        zone_colors=zone_colors,
        zone_bg_colors=zone_bg,
        zone_column=zone_column,
        line_columns=line_columns,
    )


def build_indicator_display_payload(view: IndicatorView) -> Dict[str, Any]:
    return {
        "strategy": view.strategy_name,
        "metrics": view.metrics,
        "legend": [
            {"key": k, "label": label, "color": list(rgb)}
            for k, label, rgb in view.legend
        ],
        "panel_items": view.panel_items,
    }
