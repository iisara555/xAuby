"""Chart legend bar for the Textual TUI (CDC zones + EMA markers)."""

from __future__ import annotations

from typing import List, Optional, Tuple

ZONE_LEGEND = [
    ("GREEN", "Buy zone", (52, 211, 153)),
    ("BLUE", "PreBuy 2", (129, 140, 248)),
    ("LBLUE", "PreBuy 1", (34, 211, 238)),
    ("YELLOW", "PreSell 1", (253, 224, 71)),
    ("ORANGE", "PreSell 2", (251, 146, 60)),
    ("RED", "Sell zone", (251, 113, 133)),
]

EMA_LEGEND = [
    ("EMA12 (AP)", (168, 85, 247)),
    ("EMA26 (AP)", (56, 189, 248)),
]


def _use_indicator_registry() -> bool:
    from xauby.runtime.architecture_config import indicator_registry_enabled, tui_indicator_registry

    try:
        import yaml

        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    return indicator_registry_enabled(cfg) or tui_indicator_registry(cfg)


def _registry_legend_entries(strategy_name: Optional[str]) -> Tuple[List[tuple], List[tuple]]:
    from xauby.ui.chart_registry import chart_display_metadata

    strat = strategy_name or "xauby_actionzone"
    meta = chart_display_metadata(strat)
    zones: List[tuple] = []
    lines: List[tuple] = []
    for z in meta.get("zones") or []:
        if isinstance(z, dict):
            zones.append((str(z.get("key", "")), str(z.get("label", "")), tuple(z.get("color", (200, 200, 200)))))
    for line in meta.get("lines") or []:
        if isinstance(line, dict):
            lines.append((str(line.get("label", line.get("key", "Line"))), tuple(line.get("color", (200, 200, 200)))))
    if not zones:
        zones = list(ZONE_LEGEND)
    if not lines:
        lines = list(EMA_LEGEND)
    return zones, lines


def _compact_zone_labels(labels: List[str]) -> List[str]:
    """Shorten zone labels for narrow widths while keeping them distinct.

    Drops only the generic word "zone" (e.g. "Buy zone" -> "Buy") but keeps
    discriminators like numbers or Bull/Bear ("PreBuy 2", "ST Bull"). If the
    shortened labels would collide, the full labels are returned instead so the
    legend never shows ambiguous duplicates (e.g. "ST  ST").
    """
    shortened = []
    for label in labels:
        tokens = [t for t in label.split() if t.lower() != "zone"]
        shortened.append(" ".join(tokens) or label)
    if len(set(shortened)) < len(set(labels)):
        return list(labels)
    return shortened


def format_chart_legend(
    width: int,
    *,
    compact: bool = False,
    strategy_name: Optional[str] = None,
) -> str:
    """ANSI legend for zone colours and indicator lines."""
    from xauby.utils.colors import C_MUTED, C_RESET, fg_rgb, make_gemini_gradient

    cross_glyph = "✦"
    if _use_indicator_registry():
        zone_legend, ema_legend = _registry_legend_entries(strategy_name)
        try:
            from xauby.ui.chart_registry import chart_display_metadata

            cross_glyph = str(chart_display_metadata(strategy_name or "xauby_actionzone").get("cross_glyph") or "")
        except Exception:
            cross_glyph = ""
    else:
        zone_legend = ZONE_LEGEND
        ema_legend = EMA_LEGEND

    parts = [f"{C_MUTED}Zones:{C_RESET}"]
    zone_labels = [label for _key, label, _rgb in zone_legend]
    display_labels = _compact_zone_labels(zone_labels) if compact else zone_labels
    for (_key, _label, rgb), short in zip(zone_legend, display_labels):
        c = fg_rgb(*rgb)
        parts.append(f"{c}■{C_RESET}{short}")
    ema_parts = [f"{C_MUTED}Lines:{C_RESET}"]
    for label, rgb in ema_legend:
        c = fg_rgb(*rgb)
        short = label.replace(" (AP)", "") if compact else label
        ema_parts.append(f"{c}—{C_RESET}{short}")
    if cross_glyph:
        ema_parts.append(f"{make_gemini_gradient(cross_glyph)}{C_RESET}Cross")
    ema_parts.append(f"{C_MUTED}▲Buy ▼Sell{C_RESET}")
    line = "  ".join(parts)
    if width < 75:
        return line + "\n" + "  ".join(ema_parts)
    return line + "  │  " + "  ".join(ema_parts[1:])
