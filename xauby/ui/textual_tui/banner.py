"""xAuby banner lines for the Textual launcher menu."""

from __future__ import annotations

from xauby.utils.colors import C_RESET, make_gemini_gradient, fg_rgb
from xauby.utils.common import center_text
from xauby.meta import PRODUCT_NAME

_LOGO_LARGE = [
    "██╗  ██╗ █████╗ ██╗   ██╗██████╗ ██╗   ██╗",
    "╚██╗██╔╝██╔══██╗██║   ██║██╔══██╗╚██╗ ██╔╝",
    " ╚███╔╝ ███████║██║   ██║██████╔╝ ╚████╔╝ ",
    " ██╔██╗ ██╔══██║██║   ██║██╔══██╗  ╚██╔╝  ",
    "██╔╝ ██╗██║  ██║╚██████╔╝██████╔╝   ██║   ",
    "╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝    ╚═╝   ",
]
# Compact solid logo for narrow / phone-width boxes (≤ 34 cols).
_LOGO_COMPACT = [
    "█ █ ▄▀▄ █ █ █▀▄ █ █",
    " █  █▀█ █ █ █▀▄  █ ",
    "█ █ █ █ ▀▀▀ ▀▀▀  █ ",
]
_SUBTITLE_WIDE = "Alternative Store of Value  ·  Trading System"
_SUBTITLE_COMPACT = "ASoV  ·  Trading System"

_IND = fg_rgb(99, 102, 241)   # Indigo 400 — box lines


def _top_border(box_w: int) -> str:
    """╔══ ✦ xAuby ✦ ══╗  with gradient title embedded in the top edge."""
    title_vis = f" ✦ {PRODUCT_NAME} ✦ "   # 12 visible chars including surrounding spaces
    fill = box_w - 2 - len(title_vis)
    left = max(1, fill // 2)
    right = max(1, fill - left)
    return (
        f"{_IND}╔{'═' * left}{C_RESET}"
        f" {make_gemini_gradient(f'✦ {PRODUCT_NAME} ✦')} "
        f"{_IND}{'═' * right}╗{C_RESET}"
    )


def _bottom_border(box_w: int) -> str:
    return f"{_IND}╚{'═' * (box_w - 2)}╝{C_RESET}"


def _row(content: str, box_w: int) -> str:
    """║  centered ANSI content  ║"""
    return f"{_IND}║{C_RESET}{center_text(content, box_w - 2)}{_IND}║{C_RESET}"


def _box_wide(width: int, box_w: int) -> list[str]:
    p = " " * max(0, (width - box_w) // 2)
    out = [p + _top_border(box_w)]
    out.append(p + _row("", box_w))
    for line in _LOGO_LARGE:
        out.append(p + _row(make_gemini_gradient(line), box_w))
    out.append(p + _row("", box_w))
    # gradient ┄ divider row
    divider = make_gemini_gradient("┄" * (box_w - 4))
    out.append(p + _row(divider, box_w))
    # decorated subtitle
    out.append(p + _row(make_gemini_gradient(f"◈  {_SUBTITLE_WIDE}  ◈"), box_w))
    out.append(p + _bottom_border(box_w))
    return out


def _box_compact(width: int, box_w: int) -> list[str]:
    p = " " * max(0, (width - box_w) // 2)
    out = [p + _top_border(box_w), p + _row("", box_w)]
    for line in _LOGO_COMPACT:
        out.append(p + _row(make_gemini_gradient(line), box_w))
    out.append(p + _row("", box_w))
    out.append(p + _row(make_gemini_gradient(f"◈  {_SUBTITLE_COMPACT}  ◈"), box_w))
    out.append(p + _bottom_border(box_w))
    return out


def render_launcher_banner_lines(width: int) -> list[str]:
    """Return ANSI banner lines centered for *width*."""
    w = max(20, width)
    if w >= 60:
        return _box_wide(w, min(60, w - 4))
    if w >= 36:
        return _box_compact(w, min(36, w - 2))
    return [center_text(make_gemini_gradient(f"✦ {PRODUCT_NAME} ✦"), w)]
