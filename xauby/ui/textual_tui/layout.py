"""Responsive layout breakpoints for Textual TUI (Termius / mobile SSH)."""

STACKED_BREAKPOINT = 110  # tablet + phone: vertical stacked panels
PHONE_BREAKPOINT = 75  # ultra-compact content rendering

# Textual reports bracket keys by Unicode name, not the literal character.
FOCUS_PREV_KEYS = frozenset({
    "0", "left_square_bracket", "comma", "minus",
})
FOCUS_NEXT_KEYS = frozenset({
    "9", "right_square_bracket", "period", "equals", "plus",
})


def is_stacked_layout(width: int) -> bool:
    """True when panels should stack vertically (tablet or phone)."""
    return width < STACKED_BREAKPOINT


def is_phone_layout(width: int) -> bool:
    """True when ultra-compact phone rendering is needed."""
    return width < PHONE_BREAKPOINT


def sync_dashboard_layout(content, width: int) -> None:
    """Apply CSS layout classes based on terminal width."""
    if is_stacked_layout(width):
        content.add_class("stacked-layout")
        content.remove_class("wide-layout")
    else:
        content.remove_class("stacked-layout")
        content.add_class("wide-layout")

    if is_phone_layout(width):
        content.add_class("phone-layout")
    else:
        content.remove_class("phone-layout")


def layout_width_tier(width: int) -> str:
    """Coarse width bucket for render-cache invalidation."""
    if is_phone_layout(width):
        return "phone"
    if is_stacked_layout(width):
        return "tablet"
    return "wide"


def sync_backtest_layout(split, width: int) -> None:
    """Apply responsive CSS classes for the backtest screen."""
    if is_stacked_layout(width):
        split.add_class("stacked")
    else:
        split.remove_class("stacked")

    if is_phone_layout(width):
        split.add_class("phone-layout")
    else:
        split.remove_class("phone-layout")
