"""Indicator plugin base contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

import pandas as pd


class Indicator(ABC):
    """Standalone indicator plugin for chart rendering."""

    name: str = "base"
    display_config: Dict[str, Any] = {}

    @abstractmethod
    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        """Return input df with indicator columns appended."""

    def snapshot(self, df: pd.DataFrame, config: Dict[str, Any]) -> Dict[str, Any]:
        """Last-bar scalar values for TUI (no ANSI)."""
        computed = self.compute(df, config)
        if computed is None or computed.empty:
            return {}
        row = computed.iloc[-1]
        out: Dict[str, Any] = {}
        for metric in self.display_config.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            key = str(metric.get("key", ""))
            if key and key in row.index:
                val = row[key]
                out[key] = None if pd.isna(val) else float(val) if isinstance(val, (int, float)) else str(val)
        zone_key = str(self.display_config.get("zone_column") or "zone")
        if zone_key in row.index:
            out[zone_key] = str(row[zone_key])
        return out

    def panel_items(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Checklist-compatible items for dashboard/TUI."""
        items: List[Dict[str, Any]] = []
        for metric in self.display_config.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            key = str(metric.get("key", ""))
            zone_key = str(self.display_config.get("zone_column") or "zone")
            if key == zone_key:
                # The canonical zone row is inserted below with buy-zone
                # semantics; do not render the same metric twice.
                continue
            label = str(metric.get("label", key))
            value = snapshot.get(key, "—")
            ok = metric.get("ok")
            if ok is None and "min" in metric and "max" in metric:
                try:
                    ok = float(value) >= float(metric["min"]) and float(value) <= float(metric["max"])
                except (TypeError, ValueError):
                    ok = None
            items.append(
                {
                    "label": label,
                    "value": value,
                    "ok": ok,
                    "hint": metric.get("hint", ""),
                }
            )
        zone_key = str(self.display_config.get("zone_column") or "zone")
        if zone_key in snapshot:
            zone_val = str(snapshot.get(zone_key, ""))
            buy_zones = set(self.display_config.get("buy_zones") or ["GREEN"])
            items.insert(
                0,
                {
                    "label": str(self.display_config.get("zone_label") or "Zone"),
                    "value": zone_val,
                    "ok": zone_val in buy_zones,
                },
            )
        return items

    def legend_entries(self) -> List[Tuple[str, str, Tuple[int, int, int]]]:
        """Optional legend rows: (key, label, rgb)."""
        out: List[Tuple[str, str, Tuple[int, int, int]]] = []
        for z in self.display_config.get("zones") or []:
            if isinstance(z, dict):
                out.append(
                    (
                        str(z.get("key", "")),
                        str(z.get("label", "")),
                        tuple(z.get("color", (200, 200, 200))),
                    )
                )
        for line in self.display_config.get("lines") or []:
            if isinstance(line, dict):
                out.append(
                    (
                        str(line.get("key", "")),
                        str(line.get("label", "")),
                        tuple(line.get("color", (200, 200, 200))),
                    )
                )
        return out

    def zone_color_map(self) -> Dict[str, Tuple[int, int, int]]:
        out: Dict[str, Tuple[int, int, int]] = {}
        for z in self.display_config.get("zones") or []:
            if isinstance(z, dict):
                key = str(z.get("key", ""))
                rgb = tuple(z.get("color", (200, 200, 200)))
                if key and len(rgb) == 3:
                    out[key] = rgb  # type: ignore[assignment]
        return out

    def zone_bg_color_map(self) -> Dict[str, Tuple[int, int, int]]:
        out: Dict[str, Tuple[int, int, int]] = {}
        for z in self.display_config.get("zones") or []:
            if isinstance(z, dict):
                key = str(z.get("key", ""))
                bg = z.get("bg_color") or z.get("background")
                if key and isinstance(bg, (list, tuple)) and len(bg) == 3:
                    out[key] = tuple(bg)  # type: ignore[assignment]
        return out
