"""Compact, responsive brand header for the launcher."""

from __future__ import annotations

from xauby.meta import PRODUCT_NAME
from xauby.utils.colors import C_RESET, fg_rgb, make_gemini_gradient


def render_launcher_banner_lines(width: int) -> list[str]:
    """Keep actions visible even in a short SSH terminal."""
    width = max(20, width)
    accent = fg_rgb(94, 234, 212)
    muted = fg_rgb(148, 163, 184)
    if width < 36:
        return [f" {make_gemini_gradient(PRODUCT_NAME.upper())}{C_RESET}"]

    title = f" {make_gemini_gradient('◆  ' + PRODUCT_NAME.upper())}{C_RESET}"
    subtitle = "TRADING CONTROL CENTER" if width >= 64 else "TRADING CONSOLE"
    rule = f" {accent}{'━' * min(width - 4, 58)}{C_RESET}"
    return [title, f" {muted}{subtitle}{C_RESET}", rule]
