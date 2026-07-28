import os
import yaml
from xauby.utils.colors import (
    C_BG_GREEN, C_BG_YELLOW, C_BG_RED, C_BG_DARK, C_BG_INDIGO, C_BG_CYAN, BB_CYAN, RESET, make_gemini_gradient
)
from xauby.utils.common import center_text, visible_len
from xauby.runtime.paths import runtime_path
from xauby.meta import PRODUCT_NAME

def print_line(width: int, char: str = "─", color: str = BB_CYAN):
    print(f"{color}{char * width}{RESET}")

def truncate_ansi(text: str, max_len: int) -> str:
    """Truncates a string to a maximum visual length, keeping ANSI escape codes intact."""
    import re
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
    
    result = []
    vis_count = 0
    i = 0
    n = len(text)
    
    while i < n:
        match = ansi_pattern.match(text, i)
        if match:
            result.append(match.group())
            i = match.end()
        else:
            if vis_count >= max_len:
                break
            result.append(text[i])
            vis_count += 1
            i += 1
            
    if i < n:
        vis_count = 0
        result = []
        i = 0
        limit = max(0, max_len - 3)
        while i < n:
            match = ansi_pattern.match(text, i)
            if match:
                result.append(match.group())
                i = match.end()
            else:
                if vis_count >= limit:
                    break
                result.append(text[i])
                vis_count += 1
                i += 1
        result.append("...")
        
    result.append("\033[0m")
    return "".join(result)

def print_menu_row(text: str, W: int, border_color: str = BB_CYAN) -> None:
    vis = visible_len(text)
    content_width = W - 4
    if vis <= content_width:
        padding = " " * (content_width - vis)
        print(f"{border_color}│{RESET} {text}{padding} {border_color}│{RESET}")
    else:
        truncated = truncate_ansi(text, content_width)
        vis_t = visible_len(truncated)
        padding = " " * max(0, content_width - vis_t)
        print(f"{border_color}│{RESET} {truncated}{padding} {border_color}│{RESET}")

def _resolve_project_root() -> str:
    """Best-effort resolution of the project root regardless of cwd."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _resolve_db_path() -> str:
    """Locate the SQLite DB used by the engine.

    Prefer SQLITE_DB_PATH env, then the path relative to the current cwd, then
    the absolute path computed from this file's location. This keeps the
    status bar correct even when the launcher is invoked from a different cwd.
    """
    env_path = os.environ.get("SQLITE_DB_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    root = _resolve_project_root()
    candidates = [
        os.path.join(os.getcwd(), runtime_path("xauby.db")),
        os.path.join(root, runtime_path("xauby.db")),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[-1]


def _resolve_state_file() -> str:
    """Locate the engine state JSON regardless of cwd."""
    env_path = os.environ.get("XAUBY_STATE_FILE")
    if env_path and os.path.exists(env_path):
        return env_path
    root = _resolve_project_root()
    candidates = [
        os.path.join(os.getcwd(), runtime_path("logs", "xauby_bot_state.json")),
        os.path.join(root, runtime_path("logs", "xauby_bot_state.json")),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[-1]


def _resolve_config_path() -> str:
    root = _resolve_project_root()
    for p in (os.path.join(os.getcwd(), "bot_config.yaml"),
              os.path.join(root, "bot_config.yaml")):
        if os.path.exists(p):
            return p
    return os.path.join(root, "bot_config.yaml")


def _load_json_with_retry(path, retries=3, backoff_ms=10.0):
    import json
    import time
    last_exc = Exception("unknown")
    for attempt in range(retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff_ms / 1000.0 * (attempt + 1))
    raise last_exc


def check_engine_status():
    import time
    state_file = _resolve_state_file()
    if not os.path.exists(state_file):
        return "OFFLINE", None
    try:
        mtime = os.path.getmtime(state_file)
        age = time.time() - mtime
        data = _load_json_with_retry(state_file)
        interval = int(data.get("interval_seconds", 60))
        if age < (interval + 20):
            is_sim = data.get("simulate_only", True)
            return "RUNNING", is_sim
        return "OFFLINE", None
    except Exception:
        return "OFFLINE", None

def _whitelist_strategy_mode_summary(
    fallback_strategy: str,
    fallback_sim: bool,
) -> tuple[str, str]:
    """Summarize enabled whitelist pairs into (strategy_label, mode_kind).

    mode_kind is one of "SIM", "LIVE", "MIXED". Keeps the launcher status bar
    consistent with the per-pair coin_whitelist.json (single source of truth)
    instead of a single global strategy / simulate_only flag.
    """
    try:
        from xauby.runtime.pair_config import load_whitelist

        data = load_whitelist(_resolve_project_root())
        assets = [a for a in (data.get("assets") or []) if a.get("enabled")]
        if not assets:
            raise ValueError("no enabled assets")

        strategies = []
        for a in assets:
            s = str(a.get("strategy") or "").strip()
            if s and s not in strategies:
                strategies.append(s)
        if len(strategies) == 1:
            strat_label = strategies[0]
        elif len(strategies) > 1:
            strat_label = f"{len(strategies)} strategies"
        else:
            strat_label = fallback_strategy

        modes = {str(a.get("mode") or "sim").lower() for a in assets}
        has_live = "live" in modes
        has_sim = bool(modes - {"live"})
        if has_live and has_sim:
            mode_kind = "MIXED"
        elif has_live:
            mode_kind = "LIVE"
        else:
            mode_kind = "SIM"
        return strat_label, mode_kind
    except Exception:
        return fallback_strategy, ("SIM" if fallback_sim else "LIVE")


def _mode_badge_parts(mode_kind: str) -> tuple[str, str, str]:
    """(short_word, long_word, bg_color) for a whitelist mode summary."""
    if mode_kind == "LIVE":
        return "LIVE", "LIVE TRADING", C_BG_RED
    if mode_kind == "MIXED":
        return "MIXED", "SIM + LIVE", C_BG_RED
    return "SIM", "SIMULATED", C_BG_YELLOW


def _resolve_exchange_credentials_for_status():
    """Best-effort exchange-driven credential lookup for the status bar."""
    from xauby.runtime.exchange_config import resolve_exchange_credentials
    cfg = {}
    try:
        with open(_resolve_config_path(), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    return resolve_exchange_credentials(cfg)


def draw_hermes_status_bar(W: int):
    db_path = _resolve_db_path()
    db_ok = os.path.exists(db_path)
    db_status = f"{C_BG_GREEN} DB: ONLINE {RESET}" if db_ok else f"{C_BG_YELLOW} DB: PENDING {RESET}"

    api_key, api_secret, _ = _resolve_exchange_credentials_for_status()
    api_status = f"{C_BG_GREEN} API: ACTIVE {RESET}" if (api_key and api_secret) else f"{C_BG_RED} API: OFFLINE {RESET}"

    tg_enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true"
    tg_status = f"{C_BG_GREEN} TG: ACTIVE {RESET}" if tg_enabled else f"{C_BG_DARK} TG: DISABLED {RESET}"

    eng_state, eng_sim = check_engine_status()
    if eng_state == "RUNNING":
        if eng_sim:
            engine_status = f"{C_BG_GREEN} ENGINE: RUNNING (SIM) {RESET}"
        else:
            engine_status = f"{C_BG_RED} ENGINE: RUNNING (LIVE) {RESET}"
    else:
        engine_status = f"{C_BG_DARK} ENGINE: OFFLINE {RESET}"

    pairs_status = ""
    try:
        state_file = _resolve_state_file()
        if os.path.exists(state_file):
            st = _load_json_with_retry(state_file)
            if int(st.get("schema_version", 1) or 1) >= 2:
                active = st.get("pairs") or list((st.get("by_symbol") or {}).keys())
                n = len(active)
                focus = str(st.get("focus_symbol") or st.get("symbol") or "")
                if n > 0:
                    pairs_status = f"{C_BG_CYAN} PAIRS: {n} active {RESET}"
                    if focus:
                        pairs_status += f" {C_BG_INDIGO} FOCUS: {focus} {RESET}"
    except Exception:
        pass

    try:
        with open(_resolve_config_path(), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        yaml_sim = cfg.get("simulate_only", True)
        strategy_block = cfg.get("strategy") or {}
        from xauby.strategies.registry import normalize_strategy_name

        active_strategy = normalize_strategy_name(strategy_block.get("active") or cfg.get("active_strategy") or "xauby_actionzone")
    except Exception:
        yaml_sim = True
        active_strategy = "xauby_actionzone"

    # Per-pair strategy / mode come from coin_whitelist.json (single source of
    # truth) so the status bar matches what each pair actually runs.
    active_strategy, mode_kind = _whitelist_strategy_mode_summary(active_strategy, yaml_sim)
    mode_short, mode_long, mode_color = _mode_badge_parts(mode_kind)

    if W < 55:
        mode_status = f"{mode_color} MODE: {mode_short} {RESET}"
        strat_status = f"{C_BG_INDIGO} STRAT: {active_strategy[:14]} {RESET}"
        print(center_text(f"{db_status}", W))
        print(center_text(f"{api_status}", W))
        print(center_text(f"{tg_status}", W))
        print(center_text(f"{engine_status}", W))
        if pairs_status:
            print(center_text(pairs_status, W))
        print(center_text(f"{mode_status}", W))
        print(center_text(f"{strat_status}", W))
    elif W < 95:
        mode_status = f"{mode_color} WHITELIST: {mode_short} {RESET}"
        strat_status = f"{C_BG_INDIGO} STRAT: {active_strategy} {RESET}"
        row1 = f"{db_status}   {api_status}   {tg_status}"
        row2 = f"{engine_status}   {mode_status}   {strat_status}"
        if pairs_status:
            row2 = f"{pairs_status}   {row2}"
        print(center_text(row1, W))
        print(center_text(row2, W))
    else:
        mode_status_full = f"{mode_color} WHITELIST: {mode_long} {RESET}"
        strat_status = f"{C_BG_INDIGO} STRATEGY: {active_strategy} {RESET}"
        status_line = f" {db_status}   {api_status}   {tg_status}   {engine_status}"
        if pairs_status:
            status_line += f"   {pairs_status}"
        status_line += f"   {mode_status_full}   {strat_status}"
        print(center_text(status_line, W))
    
    # Draw System Metrics
    draw_system_metrics_bar(W)
    print()

def draw_system_metrics_bar(W: int) -> None:
    import shutil
    import os
    from xauby.ui.system import get_ram_usage, calculate_cpu_usage
    from xauby.utils.colors import C_DARK, C_GREEN, C_YELLOW, C_RED

    cpu = calculate_cpu_usage()
    ram_load, ram_used, ram_total = get_ram_usage()

    try:
        total, used, free = shutil.disk_usage(".")
        total_gb = total / (1024**3)
        used_gb = used / (1024**3)
        disk_pct = (used / total) * 100
    except Exception:
        disk_pct, used_gb, total_gb = None, 0, 0

    if hasattr(os, "getloadavg"):
        try:
            load_avg = os.getloadavg()
            load_str = f"{load_avg[0]:.2f}, {load_avg[1]:.2f}, {load_avg[2]:.2f}"
        except Exception:
            load_str = "N/A"
    else:
        load_str = "N/A"

    # CPU formatting
    if cpu is not None:
        cpu_color = C_GREEN if cpu < 50 else (C_YELLOW if cpu < 80 else C_RED)
        cpu_txt = f"{cpu_color}{cpu:.1f}%{RESET}"
    else:
        cpu_txt = "N/A"

    # RAM formatting
    if ram_load is not None:
        ram_color = C_GREEN if ram_load < 50 else (C_YELLOW if ram_load < 80 else C_RED)
        ram_txt = f"{ram_color}{ram_load}%{RESET} ({ram_used:.1f}/{ram_total:.1f} GB)"
        ram_txt_short = f"{ram_color}{ram_load}%{RESET}"
    else:
        ram_txt = "N/A"
        ram_txt_short = "N/A"

    # Disk formatting
    if disk_pct is not None:
        disk_color = C_GREEN if disk_pct < 50 else (C_YELLOW if disk_pct < 80 else C_RED)
        disk_txt = f"{disk_color}{disk_pct:.1f}%{RESET} ({used_gb:.0f}/{total_gb:.0f} GB)"
        disk_txt_short = f"{disk_color}{disk_pct:.1f}%{RESET}"
    else:
        disk_txt = "N/A"
        disk_txt_short = "N/A"

    cpu_comp = f"CPU: {cpu_txt}"
    ram_comp = f"RAM: {ram_txt}"
    disk_comp = f"DISK: {disk_txt}"
    load_comp = f"LOAD: {load_str}"

    # Separator line (scale dynamically without minimum threshold that causes wrapping)
    sep = f"{C_DARK}{'─' * (W - 8)}{RESET}"
    print(center_text(sep, W))

    if W < 35:
        line1 = f"CPU {cpu_txt}  │  RAM {ram_txt_short}"
        line2 = f"DISK {disk_txt_short}  │  LOAD {load_str.split(',')[0]}"
        print(center_text(line1, W))
        print(center_text(line2, W))
    elif W < 60:
        line1 = f"CPU: {cpu_txt}  │  RAM: {ram_txt_short}"
        line2 = f"DISK: {disk_txt_short}  │  LOAD: {load_str.split(',')[0]}"
        print(center_text(line1, W))
        print(center_text(line2, W))
    elif W < 85:
        line1 = f"CPU: {cpu_txt}   │   RAM: {ram_txt}"
        line2 = f"DISK: {disk_txt}   │   LOAD: {load_str}"
        print(center_text(line1, W))
        print(center_text(line2, W))
    else:
        full_line = f" {cpu_comp}   │   {ram_comp}   │   {disk_comp}   │   {load_comp}"
        print(center_text(full_line, W))

def print_menu_two_columns(opt1_str: str, opt2_str: str, W: int, border_color: str = BB_CYAN) -> None:
    from xauby.utils.common import visible_len
    # Total width of border/spacing is 7 chars:
    # 2 (left border + space) + 1 (space before divider) + 1 (divider) + 1 (space after divider) + 1 (space before right border) + 1 (right border)
    usable_width = W - 7
    col1_w = usable_width // 2
    col2_w = usable_width - col1_w
    
    vis1 = visible_len(opt1_str)
    vis2 = visible_len(opt2_str)
    
    if vis1 > col1_w:
        opt1_str = truncate_ansi(opt1_str, col1_w)
        vis1 = visible_len(opt1_str)
    if vis2 > col2_w:
        opt2_str = truncate_ansi(opt2_str, col2_w)
        vis2 = visible_len(opt2_str)
        
    pad1 = " " * max(0, col1_w - vis1)
    pad2 = " " * max(0, col2_w - vis2)
    
    col1_part = f"{opt1_str}{pad1}"
    col2_part = f"{opt2_str}{pad2}"
    
    print(f"{border_color}│{RESET} {col1_part} {border_color}│{RESET} {col2_part} {border_color}│{RESET}")

def make_3d_gemini_logo(text: str) -> str:
    """Applies a smooth Gemini color gradient to the solid blocks '█', and a dark shadow color to outlines."""
    from xauby.utils.colors import fg_rgb, C_RESET, ANSI_ESCAPE
    clean_text = ANSI_ESCAPE.sub('', text)
    non_space_chars = [c for c in clean_text if not c.isspace()]
    n = len(non_space_chars)
    if n == 0:
        return text
    colors = [
        (99, 102, 241),   # Indigo 400
        (168, 85, 247),  # Purple 400
        (244, 63, 94),   # Pink 500
        (34, 211, 238)   # Cyan 400
    ]
    result = []
    char_idx = 0
    for char in clean_text:
        if char.isspace():
            result.append(char)
            continue
        if char != "█":
            # Outline/shadow character -> Slate 600 shadow color
            shadow_color = fg_rgb(71, 85, 105)
            result.append(f"{shadow_color}{char}")
            char_idx += 1
            continue
            
        if n > 1:
            t = char_idx / (n - 1)
        else:
            t = 0.5
        
        num_segments = len(colors) - 1
        segment_len = 1.0 / num_segments
        segment_idx = int(t // segment_len)
        if segment_idx >= num_segments:
            segment_idx = num_segments - 1
            
        segment_t = (t - segment_idx * segment_len) / segment_len
        
        c1 = colors[segment_idx]
        c2 = colors[segment_idx + 1]
        
        r = int(c1[0] + segment_t * (c2[0] - c1[0]))
        g = int(c1[1] + segment_t * (c2[1] - c1[1]))
        b = int(c1[2] + segment_t * (c2[2] - c1[2]))
        
        result.append(f"\033[38;2;{r};{g};{b}m{char}")
        char_idx += 1
        
    return "".join(result) + C_RESET

def draw_hermes_banner(W: int):
    """Render a terminal-safe xAuby banner without Unicode block-art mojibake."""
    border_color = BB_CYAN
    logo_large = [
        " __  __    _    _   _ ______   __",
        " \\ \\/ /   / \\  | | | | __ ) \\ / /",
        "  \\  /   / _ \\ | | | |  _ \\\\ V / ",
        "  /  \\  / ___ \\| |_| | |_) || |  ",
        " /_/\\_\\/_/   \\_\\\\___/|____/ |_|  ",
    ]
    logo_small = [PRODUCT_NAME, "trading system"]

    if W >= 82:
        box_w = 52
        logo = logo_large
        subtitle = "Alternative Store of Value Trading System"
    elif W >= 36:
        box_w = 34
        logo = logo_small
        subtitle = "ASoV Trading System"
    else:
        print(center_text(make_gemini_gradient(PRODUCT_NAME), W))
        print()
        draw_hermes_status_bar(W)
        return

    padding = max(0, (W - box_w) // 2)
    pad_str = " " * padding
    print(f"{pad_str}{border_color}+{'-' * (box_w - 2)}+{RESET}")
    for line in logo:
        centered_line = center_text(make_3d_gemini_logo(line), box_w - 2)
        print(f"{pad_str}{border_color}|{RESET}{centered_line}{border_color}|{RESET}")
    brand = center_text(make_gemini_gradient(PRODUCT_NAME), box_w - 2)
    print(f"{pad_str}{border_color}|{RESET}{brand}{border_color}|{RESET}")
    centered_sub = center_text(make_gemini_gradient(subtitle), box_w - 2)
    print(f"{pad_str}{border_color}|{RESET}{centered_sub}{border_color}|{RESET}")
    print(f"{pad_str}{border_color}+{'-' * (box_w - 2)}+{RESET}")
    print()
    draw_hermes_status_bar(W)
