"""Product identity strings for xAuby."""

from __future__ import annotations

import os

from xauby.utils.common import visible_len

PRODUCT_NAME = "xAuby"
PRODUCT_TAGLINE = "Alternative Store of Value Trading System"
PRODUCT_TITLE = f"{PRODUCT_NAME} : {PRODUCT_TAGLINE}"
PRODUCT_TITLE_SHORT = f"{PRODUCT_NAME} : ASoV"


def product_title_for_width(max_visible: int) -> str:
    """Pick full title, short title, or name only to fit terminal width."""
    if max_visible <= 0:
        return PRODUCT_NAME
    if visible_len(PRODUCT_TITLE) <= max_visible:
        return PRODUCT_TITLE
    if visible_len(PRODUCT_TITLE_SHORT) <= max_visible:
        return PRODUCT_TITLE_SHORT
    return PRODUCT_NAME


def load_bot_display_name(project_root: str = ".") -> str:
    """Read cli_ui.bot_name from bot_config.yaml, else PRODUCT_TITLE."""
    try:
        import yaml
    except ImportError:
        return PRODUCT_TITLE

    root = project_root or "."
    for path in (
        os.path.join(root, "bot_config.yaml"),
        os.path.join(os.getcwd(), "bot_config.yaml"),
        "bot_config.yaml",
    ):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            name = (raw.get("cli_ui") or {}).get("bot_name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except Exception:
            continue
    return PRODUCT_TITLE


def resolve_header_bot_title(max_visible: int, project_root: str = ".") -> str:
    """Dashboard/launcher header: config name if it fits, else width-aware fallback."""
    configured = load_bot_display_name(project_root)
    if visible_len(configured) <= max_visible:
        return configured
    return product_title_for_width(max_visible)
