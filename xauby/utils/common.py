import os
from datetime import datetime, timezone, timedelta
from xauby.utils.colors import ANSI_ESCAPE

TH_TZ = timezone(timedelta(hours=7))

def visible_len(text: str) -> int:
    """Returns the visual length of text, excluding ANSI escape codes."""
    return len(ANSI_ESCAPE.sub('', text))

def get_terminal_width(default: int = 70) -> int:
    """Returns current terminal width in columns, or default if fails."""
    try:
        return os.get_terminal_size().columns
    except Exception:
        return default

def get_terminal_height(default: int = 24) -> int:
    """Returns current terminal height in lines, or default if fails."""
    try:
        return os.get_terminal_size().lines
    except Exception:
        return default

def center_text(text: str, width: int, padding_char: str = " ") -> str:
    """Centers text within a given width, taking ANSI codes into account."""
    raw_len = visible_len(text)
    if raw_len >= width:
        return text
    left_padding = (width - raw_len) // 2
    right_padding = width - raw_len - left_padding
    return f"{padding_char * left_padding}{text}{padding_char * right_padding}"

def parse_utc_iso(iso_str: str) -> datetime:
    """Parse an ISO timestamp as UTC (naive values treated as UTC)."""
    s = iso_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ts_ict(iso_str: str, fmt: str = "%H:%M:%S") -> str:
    """Convert UTC ISO timestamp to Thailand Time (ICT, UTC+7)."""
    if not iso_str:
        return ""
    s = str(iso_str)
    try:
        return parse_utc_iso(s).astimezone(TH_TZ).strftime(fmt)
    except Exception:
        return s.replace("T", " ")[11:19]


def format_to_ict(iso_str: str) -> str:
    """Converts UTC ISO string to Thailand Time (ICT, UTC+7) string representation."""
    if not iso_str:
        return ""
    try:
        return parse_utc_iso(iso_str).astimezone(TH_TZ).strftime("%m-%d %H:%M")
    except Exception:
        return iso_str.replace("T", " ")[5:16]
