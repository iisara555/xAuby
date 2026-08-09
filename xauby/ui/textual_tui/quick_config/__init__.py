"""Native Textual Quick Config screens, loaded only when explicitly requested.

Importing a submodule such as ``quick_config.modals`` also imports this package.
Keep the launcher-backed configuration helpers lazy so the hosted read-only TUI
does not load a control surface (or its dotenv bootstrap) merely to use a modal.
"""

from __future__ import annotations

__all__ = [
    "QuickConfigHubScreen",
    "QuickConfigScreen",
    "SystemCheckScreen",
    "DBToolsScreen",
]


def __getattr__(name: str):
    if name in {"QuickConfigHubScreen", "QuickConfigScreen"}:
        from xauby.ui.textual_tui.quick_config import screens

        return getattr(screens, name)
    if name in {"SystemCheckScreen", "DBToolsScreen"}:
        from xauby.ui.textual_tui.quick_config import maintenance_screens

        return getattr(maintenance_screens, name)
    raise AttributeError(name)
