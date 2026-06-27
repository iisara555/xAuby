"""Interactive Quick Config menu system."""
import os
import sys
import re
import time
import json
import subprocess
from copy import deepcopy
from datetime import datetime

import yaml

from xauby.utils import (
    C_RESET, C_PRIMARY,
    C_MUTED, RESET, BOLD, GREEN, RED, YELLOW,
    WHITE, BB_AMBER, BB_BG_AMBER, BB_CYAN,
    get_terminal_width, center_text,
)
from xauby.ui.menu import (
    print_line, print_menu_row, check_engine_status,
)
from xauby.database.db import resolve_db_path

from xauby.launcher.config_io import *  # noqa: F401,F403
from xauby.launcher.process import restart_bot_service
from xauby.launcher.maintenance import send_test_telegram

__all__ = [
    "_QUICK_CONFIG_TARGET_SYMBOL",
    "pick_active_strategy_interactive",
    "HOT_RELOAD_HINT",
    "quick_config_pair_timeframe",
    "quick_config_manage_pairlist",
    "quick_config_dashboard_focus",
    "_quick_config_load_values",
    "_quick_config_draw_frame",
    "_quick_config_load_opt_params",
    "quick_config_submenu_trading",
    "quick_config_submenu_multipair",
    "quick_config_submenu_alerts",
    "quick_config_submenu_notifications",
    "quick_config_submenu_macro",
    "quick_config_submenu_ai_news",
    "quick_config_submenu_backtest",
    "_qc_offer_restart",
    "_qc_prompt_number",
    "quick_config_submenu_global_trading",
    "quick_config_submenu_risk_guards",
    "quick_config_submenu_portfolio",
    "_exchange_credential_targets",
    "exchange_connection_check",
    "_qc_set_credential",
    "quick_config_switch_exchange_wizard",
    "_qc_cred_status",
    "quick_config_submenu_exchange",
    "quick_config_submenu_system_features",
    "quick_config_submenu_strategy_params",
    "quick_config_submenu_regime_router",
    "quick_config_regime_mapping",
    "quick_config_editor",
]


_QUICK_CONFIG_TARGET_SYMBOL = ""


def pick_active_strategy_interactive(current: str, symbol: str = "") -> None:
    """Interactive submenu: choose a strategy plugin for one symbol or fallback."""
    installed = list_installed_strategies()
    if not installed:
        print(f"\n{RED}[ERR] No strategy plugins found under xauby/strategies/.{RESET}")
        time.sleep(1.5)
        return

    print(f"\n{BOLD}{BB_AMBER}• Active Strategy Plugin{RESET}")
    print(f"  Current: {GREEN}{current}{RESET}\n")
    for idx, name in enumerate(installed, start=1):
        marker = f"{GREEN} (current){RESET}" if name == current else ""
        print(f"  [{idx}] {name}{marker}")

    try:
        choice = input(
            f"\n {BOLD}Select strategy (1-{len(installed)}) or Enter to cancel:{RESET} "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not choice:
        print(f"{YELLOW}No changes made.{RESET}")
        time.sleep(1.0)
        return

    try:
        pick = int(choice)
    except ValueError:
        print(f"{RED}[ERR] Invalid input.{RESET}")
        time.sleep(1.2)
        return

    if pick < 1 or pick > len(installed):
        print(f"{RED}[ERR] Choice out of range.{RESET}")
        time.sleep(1.2)
        return

    selected = installed[pick - 1]
    if selected == current:
        print(f"{YELLOW}Strategy already set to {selected!r}.{RESET}")
        time.sleep(1.0)
        return

    cfg = _load_bot_yaml()
    if not _set_pair_strategy_values(cfg, symbol, strategy=selected):
        time.sleep(1.2)
        return
    target = _normalize_symbol(symbol) or "global fallback"
    print(f"\n{GREEN}[*] {target} strategy set to {selected!r}.{RESET}")
    print(f"{YELLOW}Restart the bot engine (option [8]) for the change to take effect.{RESET}")

    eng_state, _ = check_engine_status()
    if eng_state == "RUNNING":
        restart = input(
            f"\n{BOLD}Restart the bot engine service now? (y/N):{RESET} "
        ).strip().lower()
        if restart in ("y", "yes"):
            restart_bot_service()
    time.sleep(1.0)


HOT_RELOAD_HINT = (
    f"{GREEN}[*] Engine will pick up pair changes within ~30s (hot reload).{RESET}"
    f" Restart only if you change global strategy."
)


def quick_config_pair_timeframe():
    from xauby.runtime.pair_config import load_whitelist, set_pair_timeframes, ALLOWED_TIMEFRAMES

    wl = load_whitelist(".")
    assets = [a for a in (wl.get("assets") or []) if isinstance(a, dict)]
    if not assets:
        print(f"{YELLOW}No assets in coin_whitelist.json.{RESET}")
        time.sleep(1.5)
        return
    print(f"\n{BOLD}Pair timeframe (per whitelist asset){RESET}")
    for i, a in enumerate(assets, 1):
        base = str(a.get("symbol", "")).upper()
        en = "ON" if a.get("enabled") else "OFF"
        ptf = a.get("primary_timeframe", "4h")
        ctf = a.get("confirm_timeframe", "1d")
        print(f"  [{i}] {base} ({en})  primary={ptf}  confirm={ctf}")
    try:
        pick = input(f" Select asset [1-{len(assets)}] or Enter to cancel: ").strip()
        if not pick:
            return
        idx = int(pick) - 1
        if idx < 0 or idx >= len(assets):
            raise ValueError("out of range")
        base = str(assets[idx].get("symbol", "")).upper()
        print(f" Allowed primary: {', '.join(ALLOWED_TIMEFRAMES)}")
        ptf = input(f" Primary timeframe [{assets[idx].get('primary_timeframe', '4h')}]: ").strip()
        if not ptf:
            ptf = str(assets[idx].get("primary_timeframe", "4h"))
        ctf = input(
            f" Confirm timeframe [{assets[idx].get('confirm_timeframe', '1d')}]: "
        ).strip()
        if ptf not in ALLOWED_TIMEFRAMES:
            raise ValueError(f"primary timeframe must be one of: {', '.join(ALLOWED_TIMEFRAMES)}")
        if ctf and ctf not in ALLOWED_TIMEFRAMES:
            raise ValueError(f"confirm timeframe must be one of: {', '.join(ALLOWED_TIMEFRAMES)}")
        if ctf:
            set_pair_timeframes(base, ptf, ctf, project_root=".")
        else:
            set_pair_timeframes(base, ptf, None, project_root=".")
        print(f"{GREEN}[*] Updated {base} timeframes.{RESET}")
        print(HOT_RELOAD_HINT)
    except (ValueError, KeyError) as e:
        print(f"{RED}[ERR] {e}{RESET}")
    time.sleep(1.5)


def quick_config_manage_pairlist():
    from xauby.runtime.pair_config import (
        load_whitelist,
        add_whitelist_asset,
        update_whitelist_asset,
    )

    while True:
        wl = load_whitelist(".")
        assets = list(wl.get("assets") or [])
        print(f"\n{BOLD}Manage pairlist (coin_whitelist.json){RESET}")
        if not assets:
            print(f"  {YELLOW}(empty — add a symbol below){RESET}")
        for i, a in enumerate(assets, 1):
            if not isinstance(a, dict):
                continue
            base = str(a.get("symbol", "")).upper()
            en = "enabled" if a.get("enabled") else "disabled"
            print(f"  [{i}] {base} — {en}")
        print(f"  [a] Add symbol (e.g. BTC)")
        print(f"  [0] Back")
        sub = input(f" {BOLD}Choice:{RESET} ").strip().lower()
        if sub in ("0", "q", ""):
            return
        if sub == "a":
            raw = input(" Base symbol (e.g. BTC, XAUT): ").strip().upper().replace("USDT", "")
            if not raw:
                continue
            try:
                sym = add_whitelist_asset(raw, enabled=True, project_root=".")
                print(f"{GREEN}[*] Added/enabled {sym}.{RESET}")
                print(HOT_RELOAD_HINT)
            except Exception as e:
                print(f"{RED}[ERR] {e}{RESET}")
            time.sleep(1.2)
            continue
        try:
            idx = int(sub) - 1
            entry = assets[idx]
            base = str(entry.get("symbol", "")).upper()
            new_en = not bool(entry.get("enabled", True))
            update_whitelist_asset(base, enabled=new_en, project_root=".")
            state = "enabled" if new_en else "disabled"
            print(f"{GREEN}[*] {base} is now {state}.{RESET}")
            print(HOT_RELOAD_HINT)
        except (ValueError, IndexError, KeyError) as e:
            print(f"{RED}[ERR] {e}{RESET}")
        time.sleep(1.2)


def quick_config_dashboard_focus():
    from xauby.runtime.pair_config import load_whitelist
    from xauby.runtime.pair_registry import _normalize_symbol
    from xauby.ui.state_view import write_focus_request

    wl = load_whitelist(".")
    quote = str(wl.get("quote_asset", "USDT") or "USDT").upper()

    engine_pairs: list = []
    current_focus = ""
    state_path = os.path.join(os.getcwd(), "core/logs/xauby_bot_state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                st = json.load(f) or {}
            engine_pairs = list(st.get("pairs") or (st.get("by_symbol") or {}).keys())
            current_focus = str(st.get("focus_symbol") or "").upper().replace("_", "")
        except Exception:
            pass
    focus_path = os.path.join(os.getcwd(), "core/dashboard_focus.json")
    if os.path.exists(focus_path):
        try:
            with open(focus_path, "r", encoding="utf-8") as f:
                current_focus = str(
                    (json.load(f) or {}).get("focus_symbol") or current_focus
                ).upper().replace("_", "")
        except Exception:
            pass

    options: list = []
    seen = set()

    def _add(sym: str, label: str) -> None:
        sym = sym.upper().replace("_", "")
        if not sym or sym in seen:
            return
        seen.add(sym)
        options.append((sym, label))

    for a in wl.get("assets") or []:
        if not isinstance(a, dict):
            continue
        sym = _normalize_symbol(a.get("symbol", ""), quote)
        if not sym:
            continue
        en = bool(a.get("enabled", True))
        tags = []
        if en:
            tags.append("whitelist ON")
        else:
            tags.append("whitelist OFF")
        if sym in engine_pairs:
            tags.append("engine active")
        _add(sym, " · ".join(tags))

    for sym in engine_pairs:
        s = str(sym).upper().replace("_", "")
        if s and s not in seen:
            _add(s, "engine active (not in whitelist)")

    if not options:
        print(f"{YELLOW}No pairs found. Add assets in Manage Pairlist first.{RESET}")
        time.sleep(1.5)
        return

    print(f"\n{BOLD}Dashboard focus symbol{RESET}")
    if current_focus:
        print(f"  {C_MUTED}Current focus:{C_RESET} {current_focus}")
    print(f"  {C_MUTED}Tip: OFF pairs can be focused for chart view; enable in Pairlist to trade.{RESET}")
    for i, (sym, label) in enumerate(options, 1):
        mark = f" {GREEN}*{RESET}" if sym == current_focus else ""
        print(f"  [{i}] {sym}{mark}  ({label})")
    raw = input(
        f" Select [1-{len(options)}], symbol name (e.g. BTC), or Enter to cancel: "
    ).strip().upper().replace("_", "")
    if not raw:
        return
    sym = raw
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            sym = options[idx][0]
        else:
            print(f"{RED}[ERR] Invalid index.{RESET}")
            time.sleep(1.2)
            return
    elif not raw.endswith(quote):
        sym = _normalize_symbol(raw, quote)
    write_focus_request(sym, ".")
    print(f"{GREEN}[*] Dashboard focus set to {sym} (engine reads core/dashboard_focus.json).{RESET}")
    time.sleep(1.5)


def _quick_config_load_values() -> dict:
    """Load current Quick Config field values from YAML and .env."""
    try:
        from dotenv import load_dotenv
        from xauby.runtime.paths import env_file

        env_path = env_file()
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
    except Exception:
        pass
    cfg = _load_bot_yaml()
    global _QUICK_CONFIG_TARGET_SYMBOL
    symbols = _quick_config_symbols(cfg)
    focus_symbol = symbols[0]
    try:
        state_path = os.path.join("core", "logs", "xauby_bot_state.json")
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f) or {}
            focus_symbol = _normalize_symbol(state.get("focus_symbol") or focus_symbol)
    except Exception:
        pass
    if _QUICK_CONFIG_TARGET_SYMBOL in symbols:
        focus_symbol = _QUICK_CONFIG_TARGET_SYMBOL
    elif focus_symbol not in symbols:
        focus_symbol = symbols[0]
    strat_name = _strategy_name_for_symbol_from_cfg(cfg, focus_symbol)
    strat_cfg = _strategy_cfg_for_symbol(cfg, focus_symbol)
    portfolio_cfg = _portfolio_cfg_for_symbol(cfg, focus_symbol)
    guard = cfg.get("macro_sentiment_guard", {}) or {}
    news_provider = str(guard.get("news_provider", "minimax") or "minimax").lower()

    env_keys = {
        "minimax": ["MINIMAX_API_KEY", "AI_API_KEY"],
        "openai": ["OPENAI_API_KEY", "AI_API_KEY"],
        "gemini": ["GEMINI_API_KEY", "AI_API_KEY"],
        "claude": ["CLAUDE_API_KEY", "ANTHROPIC_API_KEY", "AI_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY", "AI_API_KEY"],
        "qwen": ["QWEN_API_KEY", "DASHSCOPE_API_KEY", "AI_API_KEY"],
    }
    ai_api_key = ""
    active_key_var = env_keys.get(news_provider, [f"{news_provider.upper()}_API_KEY", "AI_API_KEY"])[0]
    for k in env_keys.get(news_provider, [f"{news_provider.upper()}_API_KEY", "AI_API_KEY"]):
        val = os.environ.get(k, "")
        if val:
            ai_api_key = val
            active_key_var = k
            break

    try:
        from xauby.runtime.exchange_config import credential_env_names
        key_env, secret_env, _ = credential_env_names(cfg)
    except Exception:
        key_env, secret_env = "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"
    api_key = os.environ.get(key_env, "")
    api_secret = os.environ.get(secret_env, "")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    fred_api_key = os.environ.get("FRED_API_KEY", "")

    return {
        "sim_only": bool(cfg.get("simulate_only", True)),
        "symbols": symbols,
        "focus_symbol": focus_symbol,
        "active_strategy": strat_name,
        "use_d1_regime_filter": bool(strat_cfg.get("use_d1_regime_filter", False)),
        "max_risk": float(portfolio_cfg.get("risk_pct", cfg.get("trading", {}).get("risk_pct", 0.01))),
        "stop_loss_disabled": bool(strat_cfg.get("disable_stop_loss", False)),
        "sl_atr": float(strat_cfg.get("sl_atr_mult", 2.0)),
        "be_enabled": bool(strat_cfg.get("breakeven_sl_enabled", False)),
        "be_activation": float(strat_cfg.get("breakeven_activation_atr_mult", 1.5)),
        "be_buffer": float(strat_cfg.get("breakeven_buffer_atr_mult", 0.1)),
        "guard_enabled": bool(guard.get("enabled", False)),
        "use_fred_enabled": bool(guard.get("use_fred", False)),
        "use_news_enabled": bool(guard.get("use_news", False)),
        "news_provider": news_provider,
        "news_model": str(guard.get("news_model", "") or ""),
        "news_base_url": str(guard.get("news_base_url", "") or ""),
        "tg_enabled": os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true",
        "api_key_disp": f"SET (...{api_key[-6:]})" if api_key else "EMPTY",
        "api_sec_disp": f"SET (...{api_secret[-6:]})" if api_secret else "EMPTY",
        "tg_tok_disp": f"SET (...{tg_token[-6:]})" if tg_token else "EMPTY",
        "tg_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "fred_key_disp": f"SET (...{fred_api_key[-6:]})" if fred_api_key else "EMPTY",
        "ai_key_disp": f"SET (...{ai_api_key[-6:]})" if ai_api_key else "EMPTY",
        "active_key_var": active_key_var,
        "env_keys": env_keys,
    }


def _quick_config_draw_frame(subtitle: str, options: list, back_hint: str = "0") -> int:
    """Draw submenu box; returns terminal width."""
    W = get_terminal_width()
    if W < 35:
        W = 35
    print("\033[2J\033[H", end="")
    print(f"{BB_CYAN}┌{'─' * (W - 2)}┐{RESET}")
    title = f"{BB_BG_AMBER} QUICK CONFIG — {subtitle} {RESET}"
    print(f"{BB_CYAN}│{RESET} {center_text(title, W - 4)} {BB_CYAN}│{RESET}")
    print(f"{BB_CYAN}├{'─' * (W - 2)}┤{RESET}")
    for num, desc in options:
        print_menu_row(f" [{num}] {desc}", W, BB_CYAN)
    print_menu_row(f" [{back_hint}] Back", W, BB_CYAN)
    print(f"{BB_CYAN}└{'─' * (W - 2)}┘{RESET}")
    return W


def _quick_config_load_opt_params(symbol: str = ""):
    try:
        from xauby.backtest.best_params import load_best_params

        target = _normalize_symbol(symbol)
        if not target:
            target = _quick_config_load_values()["focus_symbol"]
        params = load_best_params(target)
        required = {"rsi_min", "rsi_max", "sl_atr_mult", "vol_min_ratio", "net_profit_pct"}
        if not isinstance(params, dict) or not required.issubset(params):
            return None
        return {**params, "_symbol": target}
    except Exception:
        return None


def quick_config_submenu_trading() -> None:
    while True:
        v = _quick_config_load_values()
        mode = "SIMULATION" if v["sim_only"] else "LIVE"
        options = [
            ("1", f"Trading mode: {mode}"),
            ("2", f"Pair risk ({v['focus_symbol']}): {v['max_risk'] * 100:.2f}%"),
            ("3", f"Stop loss ATR multiplier ({v['focus_symbol']}): {v['sl_atr']:.1f}x"
             + (" (INACTIVE: strategy disables SL)" if v["stop_loss_disabled"] else "")),
            ("4", f"Breakeven SL: {'ON' if v['be_enabled'] else 'OFF'}"
             + (" (INACTIVE: strategy disables SL)" if v["stop_loss_disabled"] else "")),
            ("5", f"Breakeven trigger ATR: {v['be_activation']:.1f}x"
             + (" (INACTIVE: strategy disables SL)" if v["stop_loss_disabled"] else "")),
            ("6", f"Breakeven buffer ATR: {v['be_buffer']:.1f}x"
             + (" (INACTIVE: strategy disables SL)" if v["stop_loss_disabled"] else "")),
            ("7", f"Daily (D1) regime filter: {'ON' if v['use_d1_regime_filter'] else 'OFF'}"),
            ("8", f"Pair strategy ({v['focus_symbol']}): {v['active_strategy']}"),
            ("9", f"Select target pair: {v['focus_symbol']}"),
        ]
        _quick_config_draw_frame("TRADING & RISK", options)
        try:
            choice = input(f" {BOLD}Select [0-9]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            new_sim = not v["sim_only"]
            update_yaml_config(sim_only=new_sim)
            update_env_variable("LIVE_TRADING", str(not new_sim).lower())
            print(f"\n{GREEN}[*] Mode → {'SIMULATION' if new_sim else 'LIVE TRADING'}{RESET}")
            _qc_offer_restart()
        elif choice == "2":
            try:
                s = input(f" Risk % for {v['focus_symbol']} (current {v['max_risk'] * 100:.2f}%): ").strip()
                if s:
                    risk_pct = float(s)
                    if 0.1 <= risk_pct <= 10.0:
                        update_yaml_config(max_risk=risk_pct / 100.0, symbol=v["focus_symbol"])
                        print(f"{GREEN}[*] Pair risk → {risk_pct}%{RESET}")
                        _qc_offer_restart()
                    else:
                        print(f"{RED}[ERR] Use 0.1–10.0%{RESET}")
                time.sleep(1.2)
            except ValueError:
                print(f"{RED}[ERR] Invalid number{RESET}")
                time.sleep(1.2)
        elif choice == "3":
            try:
                s = input(f" SL ATR mult (current {v['sl_atr']:.1f}): ").strip()
                if s:
                    ns = float(s)
                    if 1.0 <= ns <= 5.0:
                        ok = _set_pair_strategy_values(
                            _load_bot_yaml(), v["focus_symbol"], params={"sl_atr_mult": ns}
                        )
                        if ok:
                            print(f"{GREEN}[*] SL ATR → {ns}x{RESET}")
                            _qc_offer_restart()
                    else:
                        print(f"{RED}[ERR] Use 1.0–5.0{RESET}")
                time.sleep(1.2)
            except ValueError:
                print(f"{RED}[ERR] Invalid number{RESET}")
                time.sleep(1.2)
        elif choice == "4":
            if _set_pair_strategy_values(
                _load_bot_yaml(), v["focus_symbol"],
                params={"breakeven_sl_enabled": not v["be_enabled"]},
            ):
                print(f"{GREEN}[*] Breakeven toggled{RESET}")
                _qc_offer_restart()
        elif choice == "5":
            try:
                s = input(f" BE trigger ATR (current {v['be_activation']:.1f}): ").strip()
                if s:
                    na = float(s)
                    if 0.5 <= na <= 5.0:
                        ok = _set_pair_strategy_values(
                            _load_bot_yaml(), v["focus_symbol"],
                            params={"breakeven_activation_atr_mult": na},
                        )
                        if ok:
                            print(f"{GREEN}[*] BE trigger → {na}x{RESET}")
                            _qc_offer_restart()
                    else:
                        print(f"{RED}[ERR] Use 0.5–5.0{RESET}")
                time.sleep(1.2)
            except ValueError:
                print(f"{RED}[ERR] Invalid number{RESET}")
                time.sleep(1.2)
        elif choice == "6":
            try:
                s = input(f" BE buffer ATR (current {v['be_buffer']:.1f}): ").strip()
                if s:
                    nb = float(s)
                    if 0.0 <= nb <= 2.0:
                        ok = _set_pair_strategy_values(
                            _load_bot_yaml(), v["focus_symbol"],
                            params={"breakeven_buffer_atr_mult": nb},
                        )
                        if ok:
                            print(f"{GREEN}[*] BE buffer → {nb}x{RESET}")
                            _qc_offer_restart()
                    else:
                        print(f"{RED}[ERR] Use 0.0–2.0{RESET}")
                time.sleep(1.2)
            except ValueError:
                print(f"{RED}[ERR] Invalid number{RESET}")
                time.sleep(1.2)
        elif choice == "7":
            if _set_pair_strategy_values(
                _load_bot_yaml(), v["focus_symbol"],
                params={"use_d1_regime_filter": not v["use_d1_regime_filter"]},
            ):
                print(f"{GREEN}[*] D1 regime filter toggled{RESET}")
                _qc_offer_restart()
        elif choice == "8":
            pick_active_strategy_interactive(v["active_strategy"], symbol=v["focus_symbol"])
        elif choice == "9":
            global _QUICK_CONFIG_TARGET_SYMBOL
            cfg = _load_bot_yaml()
            sym = _select_symbol_interactive(cfg, "Target pair for Trading & Risk")
            _QUICK_CONFIG_TARGET_SYMBOL = sym
            print(f"{GREEN}[*] Target pair set to {sym}.{RESET}")
            time.sleep(1.0)
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_multipair() -> None:
    while True:
        options = [
            ("1", "Pair timeframe (per whitelist asset)"),
            ("2", "Manage pairlist (add / enable / disable)"),
            ("3", "Dashboard focus symbol"),
        ]
        _quick_config_draw_frame("MULTI-PAIR & DASHBOARD", options)
        try:
            choice = input(f" {BOLD}Select [0-3]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            quick_config_pair_timeframe()
        elif choice == "2":
            quick_config_manage_pairlist()
        elif choice == "3":
            quick_config_dashboard_focus()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_alerts() -> None:
    while True:
        v = _quick_config_load_values()
        options = [
            ("1", f"Telegram alerts: {'ON' if v['tg_enabled'] else 'OFF'}"),
            ("2", f"Telegram bot token: {v['tg_tok_disp']}"),
            ("3", f"Telegram chat ID: {v['tg_chat_id'] or 'EMPTY'}"),
            ("4", "Send test Telegram message"),
            ("5", "Notification schedule & thresholds"),
        ]
        _quick_config_draw_frame("TELEGRAM ALERTS & SCHEDULE", options)
        try:
            choice = input(f" {BOLD}Select [0-5]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            new_tg = not v["tg_enabled"]
            update_env_variable("TELEGRAM_ENABLED", str(new_tg).lower())
            os.environ["TELEGRAM_ENABLED"] = str(new_tg).lower()
            print(f"{GREEN}[*] Telegram → {'ON' if new_tg else 'OFF'}{RESET}")
            time.sleep(1.0)
        elif choice == "2":
            if input(f" {YELLOW}Edit Telegram token? (y/n):{RESET} ").strip().lower() in ("y", "yes"):
                nt = input(" New token: ").strip()
                if nt:
                    update_env_variable("TELEGRAM_BOT_TOKEN", nt)
                    os.environ["TELEGRAM_BOT_TOKEN"] = nt
                    print(f"{GREEN}[*] Token updated{RESET}")
            time.sleep(1.2)
        elif choice == "3":
            if input(f" {YELLOW}Edit Telegram chat ID? (y/n):{RESET} ").strip().lower() in ("y", "yes"):
                nid = input(" New chat ID: ").strip()
                if nid:
                    update_env_variable("TELEGRAM_CHAT_ID", nid)
                    os.environ["TELEGRAM_CHAT_ID"] = nid
                    print(f"{GREEN}[*] Chat ID updated{RESET}")
            time.sleep(1.2)
        elif choice == "4":
            send_test_telegram()
            time.sleep(1.5)
        elif choice == "5":
            quick_config_submenu_notifications()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_notifications() -> None:
    _DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    while True:
        cfg = _load_bot_yaml()
        wr = cfg.get("weekly_review", {}) or {}
        dd = cfg.get("daily_digest", {}) or {}
        notif = cfg.get("notifications", {}) or {}
        dow = int(wr.get("day_of_week", 6))
        dow_label = _DOW[dow] if 0 <= dow <= 6 else str(dow)
        options = [
            ("1", f"Weekly review: {'ON' if wr.get('enabled') else 'OFF'}"),
            ("2", f"Weekly review day: {dow} ({dow_label})"),
            ("3", f"Weekly review hour (UTC): {wr.get('hour_utc', 17)}"),
            ("4", f"Daily digest: {'ON' if dd.get('enabled') else 'OFF'}"),
            ("5", f"Daily digest hour (UTC): {dd.get('hour_utc', 10)}"),
            ("6", f"Regime score alert threshold: {notif.get('regime_score_threshold', 10)}"),
            ("7", f"Semi-auto confirm timeout (s): {notif.get('semi_auto_confirm_timeout_seconds', 60)}"),
        ]
        _quick_config_draw_frame("NOTIFICATION SCHEDULE", options)
        try:
            choice = input(f" {BOLD}Select [0-7]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            new_val = not bool(wr.get("enabled"))
            if _set_yaml_path("weekly_review.enabled", new_val):
                print(f"{GREEN}[*] Weekly review → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        elif choice == "2":
            n = _qc_prompt_number("Day of week (0=Mon..6=Sun)", dow, is_int=True, lo=0, hi=6)
            if n is not None and _set_yaml_path("weekly_review.day_of_week", n):
                print(f"{GREEN}[*] Weekly review day → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "3":
            n = _qc_prompt_number("Weekly review hour (UTC)", wr.get("hour_utc", 17), is_int=True, lo=0, hi=23)
            if n is not None and _set_yaml_path("weekly_review.hour_utc", n):
                print(f"{GREEN}[*] Weekly review hour → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "4":
            new_val = not bool(dd.get("enabled"))
            if _set_yaml_path("daily_digest.enabled", new_val):
                print(f"{GREEN}[*] Daily digest → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        elif choice == "5":
            n = _qc_prompt_number("Daily digest hour (UTC)", dd.get("hour_utc", 10), is_int=True, lo=0, hi=23)
            if n is not None and _set_yaml_path("daily_digest.hour_utc", n):
                print(f"{GREEN}[*] Daily digest hour → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "6":
            n = _qc_prompt_number("Regime score alert threshold", notif.get("regime_score_threshold", 10), is_int=True, lo=0, hi=100)
            if n is not None and _set_yaml_path("notifications.regime_score_threshold", n):
                print(f"{GREEN}[*] Regime score threshold → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "7":
            n = _qc_prompt_number("Semi-auto confirm timeout (s)", notif.get("semi_auto_confirm_timeout_seconds", 60), is_int=True, lo=5, hi=3600)
            if n is not None and _set_yaml_path("notifications.semi_auto_confirm_timeout_seconds", n):
                print(f"{GREEN}[*] Confirm timeout → {n}s{RESET}")
                _qc_offer_restart()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_macro() -> None:
    while True:
        v = _quick_config_load_values()
        options = [
            ("1", f"Macro sentiment guard: {'ON' if v['guard_enabled'] else 'OFF'}"),
            ("2", f"FRED rates: {'ON' if v['use_fred_enabled'] else 'OFF'} · key {v['fred_key_disp']}"),
            ("3", f"AI news: {'ON' if v['use_news_enabled'] else 'OFF'} · {v['news_provider']} · {v['ai_key_disp']}"),
        ]
        _quick_config_draw_frame("MACRO GUARD", options)
        try:
            choice = input(f" {BOLD}Select [0-3]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            update_yaml_config(guard_enabled=not v["guard_enabled"])
            print(f"{GREEN}[*] Macro guard toggled{RESET}")
            time.sleep(1.0)
        elif choice == "2":
            print(f"\n{BB_CYAN}── FRED Interest Rate ──{RESET}")
            print(f" [{v['use_fred_enabled'] and 'ON' or 'OFF'}]  Key: {v['fred_key_disp']}")
            sub = input(f" {BOLD}(1) Toggle  (2) Set API key  (0) Cancel:{RESET} ").strip()
            if sub == "1":
                update_yaml_config(use_fred=not v["use_fred_enabled"])
                print(f"{GREEN}[*] FRED toggled{RESET}")
            elif sub == "2":
                nk = input(" FRED API key: ").strip()
                if nk:
                    update_env_variable("FRED_API_KEY", nk)
                    os.environ["FRED_API_KEY"] = nk
                    print(f"{GREEN}[*] FRED key updated{RESET}")
            time.sleep(1.2)
        elif choice == "3":
            quick_config_submenu_ai_news(v)
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_ai_news(v: dict) -> None:
    """AI news provider settings (nested under Macro)."""
    use_news_enabled = v["use_news_enabled"]
    news_provider = v["news_provider"]
    news_model = v["news_model"]
    news_base_url = v["news_base_url"]
    ai_key_disp = v["ai_key_disp"]
    active_key_var = v["active_key_var"]
    env_keys = v["env_keys"]

    while True:
        print(f"\n{BB_CYAN}── AI News Sentiment ──{RESET}")
        print(f" Status:    [{'ON' if use_news_enabled else 'OFF'}]")
        print(f" Provider:  [{news_provider}]")
        print(f" Model:     [{news_model or 'DEFAULT'}]")
        print(f" Base URL:  [{news_base_url or 'DEFAULT'}]")
        print(f" API key:   [{ai_key_disp}] ({active_key_var})")
        print(f" {BOLD}(1) Toggle  (2) Provider  (3) Model  (4) Base URL  (5) API key  (0) Back{RESET}")
        sub = input(f" {BOLD}Select:{RESET} ").strip()
        if sub in ("0", ""):
            return
        if sub == "1":
            use_news_enabled = not use_news_enabled
            update_yaml_config(use_news=use_news_enabled)
            print(f"{GREEN}[*] AI news toggled{RESET}")
        elif sub == "2":
            prov = input(" Provider (gemini/openai/claude/deepseek/qwen/minimax): ").strip().lower()
            if prov in env_keys:
                news_provider = prov
                update_yaml_config(news_provider=prov)
                candidate_keys = env_keys.get(prov, [f"{prov.upper()}_API_KEY", "AI_API_KEY"])
                active_key_var = candidate_keys[0]
                ai_api_key = ""
                for k in candidate_keys:
                    val = os.environ.get(k, "")
                    if val:
                        ai_api_key = val
                        active_key_var = k
                        break
                ai_key_disp = f"SET (...{ai_api_key[-6:]})" if ai_api_key else "EMPTY"
                print(f"{GREEN}[*] Provider → {prov}{RESET}")
            else:
                print(f"{RED}[ERR] Invalid provider{RESET}")
        elif sub == "3":
            news_model = input(" Model (Enter to clear): ").strip()
            update_yaml_config(news_model=news_model)
            print(f"{GREEN}[*] Model updated{RESET}")
        elif sub == "4":
            news_base_url = input(" Base URL (Enter to clear): ").strip()
            update_yaml_config(news_base_url=news_base_url)
            print(f"{GREEN}[*] Base URL updated{RESET}")
        elif sub == "5":
            nk = input(f" API key for {news_provider} ({active_key_var}): ").strip()
            if nk:
                update_env_variable(active_key_var, nk)
                os.environ[active_key_var] = nk
                ai_key_disp = f"SET (...{nk[-6:]})"
                print(f"{GREEN}[*] Key updated{RESET}")
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
        time.sleep(1.2)


def quick_config_submenu_backtest(opt_params: dict) -> None:
    symbol = _normalize_symbol(opt_params.get("_symbol"))
    rsi_str = f"{opt_params['rsi_min']:.0f}-{opt_params['rsi_max']:.0f}"
    print(f"\n{BOLD}Apply best backtest parameters to {symbol}?{RESET}")
    print(
        f" SL {opt_params['sl_atr_mult']:.1f}x · RSI {rsi_str} · "
        f"Vol {opt_params['vol_min_ratio']:.1f}x · "
        f"NP {opt_params['net_profit_pct']:+.1f}%"
    )
    confirm = input(f" {YELLOW}Apply? (y/n):{RESET} ").strip().lower()
    if confirm not in ("y", "yes"):
        print(f"{YELLOW}Cancelled.{RESET}")
        time.sleep(1.0)
        return
    ok = _set_pair_strategy_values(
        _load_bot_yaml(),
        symbol,
        params={
            "sl_atr_mult": opt_params["sl_atr_mult"],
            "rsi_min": opt_params["rsi_min"],
            "rsi_max": opt_params["rsi_max"],
            "vol_min_ratio": opt_params["vol_min_ratio"],
        },
    )
    if not ok:
        time.sleep(1.2)
        return
    print(f"{GREEN}[*] Applied backtest parameters to {symbol}{RESET}")
    restart = input(f" {BOLD}Restart engine now? (y/N):{RESET} ").strip().lower()
    if restart in ("y", "yes"):
        restart_bot_service()
    time.sleep(1.5)


def _qc_offer_restart() -> None:
    """Print the restart hint and, if the engine is running, offer to restart."""
    print(f"{YELLOW}Restart the bot engine (option [8]) for the change to take effect.{RESET}")
    try:
        eng_state, _ = check_engine_status()
    except Exception:
        eng_state = ""
    if eng_state == "RUNNING":
        ans = input(f"\n{BOLD}Restart the bot engine service now? (y/N):{RESET} ").strip().lower()
        if ans in ("y", "yes"):
            restart_bot_service()
    time.sleep(1.0)


def _qc_prompt_number(label: str, current, *, is_int: bool, lo=None, hi=None):
    """Prompt for a number; return parsed value or None (no change / invalid)."""
    s = input(f" {label} (current {current}): ").strip()
    if not s:
        return None
    try:
        val = int(s) if is_int else float(s)
    except ValueError:
        print(f"{RED}[ERR] Invalid number{RESET}")
        time.sleep(1.2)
        return None
    if (lo is not None and val < lo) or (hi is not None and val > hi):
        rng = f"{lo}–{hi}" if lo is not None and hi is not None else (f">= {lo}" if lo is not None else f"<= {hi}")
        print(f"{RED}[ERR] Use {rng}{RESET}")
        time.sleep(1.2)
        return None
    return val


def quick_config_submenu_global_trading() -> None:
    while True:
        cfg = _load_bot_yaml()
        t = cfg.get("trading", {}) or {}
        options = [
            ("1", f"Max open positions: {t.get('max_open_positions', 2)}"),
            ("2", f"Tick interval (seconds): {t.get('interval_seconds', 60)}"),
            ("3", f"Primary timeframe: {t.get('timeframe', '4h')}"),
            ("4", f"Min order amount: {t.get('min_order_amount', 10.0)}"),
            ("5", f"Max position per trade %: {t.get('max_position_per_trade_pct', 48.0)}"),
        ]
        _quick_config_draw_frame("GLOBAL TRADING", options)
        try:
            choice = input(f" {BOLD}Select [0-5]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            n = _qc_prompt_number("Max open positions", t.get("max_open_positions", 2), is_int=True, lo=1, hi=50)
            if n is not None:
                _set_yaml_path("risk.max_open_positions", n)
            if n is not None and _set_yaml_path("trading.max_open_positions", n):
                print(f"{GREEN}[*] Max open positions → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "2":
            n = _qc_prompt_number("Tick interval (seconds)", t.get("interval_seconds", 60), is_int=True, lo=5, hi=3600)
            if n is not None and _set_yaml_path("trading.interval_seconds", n):
                print(f"{GREEN}[*] Tick interval → {n}s{RESET}")
                _qc_offer_restart()
        elif choice == "3":
            from xauby.runtime.pair_config import ALLOWED_TIMEFRAMES
            print(f" Allowed: {', '.join(ALLOWED_TIMEFRAMES)}")
            tf = input(f" Timeframe (current {t.get('timeframe', '4h')}): ").strip()
            if tf:
                if tf in ALLOWED_TIMEFRAMES:
                    if _set_yaml_path("trading.timeframe", tf):
                        print(f"{GREEN}[*] Timeframe → {tf}{RESET}")
                        _qc_offer_restart()
                else:
                    print(f"{RED}[ERR] Not an allowed timeframe{RESET}")
                    time.sleep(1.2)
        elif choice == "4":
            n = _qc_prompt_number("Min order amount", t.get("min_order_amount", 10.0), is_int=False, lo=0.0)
            if n is not None and _set_yaml_path("trading.min_order_amount", n):
                print(f"{GREEN}[*] Min order amount → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "5":
            n = _qc_prompt_number("Max position per trade %", t.get("max_position_per_trade_pct", 48.0), is_int=False, lo=0.0, hi=100.0)
            if n is not None:
                _set_yaml_path("trading.max_position_per_trade_pct", n)
                if _set_yaml_path("portfolio.position_sizing.max_position_per_trade_pct", n):
                    print(f"{GREEN}[*] Max position per trade → {n}%{RESET}")
                    _qc_offer_restart()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_risk_guards() -> None:
    while True:
        cfg = _load_bot_yaml()
        risk = cfg.get("risk", {}) or {}
        trading = cfg.get("trading", {}) or {}
        guard = risk.get("drawdown_guard", {}) or {}
        options = [
            ("1", f"Drawdown circuit-breaker: {'ON' if guard.get('enabled') else 'OFF'}"),
            ("2", f"Max drawdown %: {guard.get('max_drawdown_pct', 20.0)}"),
            ("3", f"Force-close on breach: {'ON' if guard.get('close_positions') else 'OFF'}"),
            ("4", f"Max daily loss %: {risk.get('max_daily_loss_pct', trading.get('max_daily_loss_pct', 10.0))}"),
            ("5", f"Max consecutive losses: {trading.get('max_consecutive_losses', 2)}"),
        ]
        _quick_config_draw_frame("RISK GUARDS / CIRCUIT-BREAKER", options)
        try:
            choice = input(f" {BOLD}Select [0-5]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            new_val = not bool(guard.get("enabled"))
            if _set_yaml_path("risk.drawdown_guard.enabled", new_val):
                print(f"{GREEN}[*] Drawdown guard → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        elif choice == "2":
            n = _qc_prompt_number("Max drawdown %", guard.get("max_drawdown_pct", 20.0), is_int=False, lo=1.0, hi=100.0)
            if n is not None and _set_yaml_path("risk.drawdown_guard.max_drawdown_pct", n):
                print(f"{GREEN}[*] Max drawdown → {n}%{RESET}")
                _qc_offer_restart()
        elif choice == "3":
            new_val = not bool(guard.get("close_positions"))
            if _set_yaml_path("risk.drawdown_guard.close_positions", new_val):
                print(f"{GREEN}[*] Force-close on breach → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        elif choice == "4":
            cur = risk.get("max_daily_loss_pct", trading.get("max_daily_loss_pct", 10.0))
            n = _qc_prompt_number("Max daily loss %", cur, is_int=False, lo=0.0, hi=100.0)
            if n is not None:
                _set_yaml_path("trading.max_daily_loss_pct", n)
                if _set_yaml_path("risk.max_daily_loss_pct", n):
                    print(f"{GREEN}[*] Max daily loss → {n}%{RESET}")
                    _qc_offer_restart()
        elif choice == "5":
            n = _qc_prompt_number("Max consecutive losses", trading.get("max_consecutive_losses", 2), is_int=True, lo=0, hi=50)
            if n is not None and _set_yaml_path("trading.max_consecutive_losses", n):
                print(f"{GREEN}[*] Max consecutive losses → {n}{RESET}")
                _qc_offer_restart()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_portfolio() -> None:
    while True:
        cfg = _load_bot_yaml()
        portfolio = cfg.get("portfolio", {}) or {}
        sym_blocks = portfolio.get("symbols", {}) or {}
        options = [
            ("1", f"Initial balance: {portfolio.get('initial_balance', 1000.0)}"),
            ("2", "Per-symbol allocation %"),
        ]
        _quick_config_draw_frame("PORTFOLIO SIZING & ALLOCATION", options)
        try:
            choice = input(f" {BOLD}Select [0-2]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            n = _qc_prompt_number("Initial balance", portfolio.get("initial_balance", 1000.0), is_int=False, lo=0.0)
            if n is not None and _set_yaml_path("portfolio.initial_balance", n):
                print(f"{GREEN}[*] Initial balance → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "2":
            sym = _select_symbol_interactive(cfg, "Symbol for allocation %")
            cur = ((sym_blocks.get(sym) or {}).get("allocation_pct", 50.0))
            n = _qc_prompt_number(f"Allocation % for {sym}", cur, is_int=False, lo=0.0, hi=100.0)
            if n is not None and _set_yaml_path(f"portfolio.symbols.{sym}.allocation_pct", n):
                print(f"{GREEN}[*] {sym} allocation → {n}%{RESET}")
                _qc_offer_restart()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def _exchange_credential_targets(cfg: dict):
    """Return (api_key_env, api_secret_env, base_url_env) for the configured exchange."""
    try:
        from xauby.runtime.exchange_config import credential_env_names

        return credential_env_names(cfg)
    except Exception:
        return ("BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_BASE_URL")


def exchange_connection_check(cfg: dict, *, client_factory=None):
    """Lightweight, exchange-agnostic auth + connectivity probe.

    Resolves credentials for the configured exchange, builds a client, and calls
    ``get_balances()``. Returns ``(ok: bool, message: str)``. ``client_factory``
    (signature ``(cfg, key, secret, base_url) -> client``) is injectable for tests.
    """
    try:
        from xauby.runtime.exchange_config import resolve_exchange_credentials

        api_key, api_secret, base_url = resolve_exchange_credentials(cfg)
    except Exception as e:
        return False, f"could not resolve credentials: {e}"
    if not api_key or not api_secret:
        key_env, secret_env, _ = _exchange_credential_targets(cfg)
        return False, f"missing {key_env}/{secret_env} in .env"
    factory = client_factory
    if factory is None:
        from xauby.api import create_exchange_client

        factory = create_exchange_client
    client = None
    try:
        client = factory(cfg, api_key, api_secret, base_url)
        balances = client.get_balances() or {}
        return True, f"connected — {len(balances)} asset(s) visible"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"
    finally:
        try:
            if client is not None and hasattr(client, "close"):
                client.close()
        except Exception:
            pass


def _qc_set_credential(env_var: str, label: str) -> None:
    val = input(f" New {label} for {env_var} (Enter to keep): ").strip()
    if val:
        update_env_variable(env_var, val)
        os.environ[env_var] = val
        print(f"{GREEN}[*] {env_var} updated{RESET}")
    time.sleep(1.0)


def quick_config_switch_exchange_wizard() -> None:
    """Stage, preflight, atomically activate, then offer controlled restart."""
    cfg = _load_bot_yaml()
    candidate = deepcopy(cfg)
    exch = candidate.setdefault("exchange", {})
    try:
        from xauby.api.exchange_registry import available_exchanges

        providers = available_exchanges()
    except Exception:
        providers = ["binance", "ccxt"]
    print(f"\n{BB_AMBER}{BOLD}Switch / set up exchange (guided){RESET}")
    print(f" Registered providers: {', '.join(providers)}")
    prov = input(f" Provider (current {exch.get('provider', 'binance')}, Enter to cancel): ").strip().lower()
    if not prov:
        print(f"{YELLOW}Cancelled.{RESET}")
        time.sleep(1.0)
        return
    if prov not in providers:
        print(f"{RED}[ERR] Unknown provider (not in registry){RESET}")
        time.sleep(1.2)
        return
    exch["provider"] = prov
    if prov != "binance":
        cid = input(f" CCXT exchange id (e.g. kraken, bybit) [{exch.get('ccxt_id', '')}]: ").strip().lower()
        if cid:
            exch["ccxt_id"] = cid
    qa = input(f" Quote asset [{exch.get('quote_asset', 'USDT')}]: ").strip().upper()
    if qa:
        exch["quote_asset"] = qa
    if prov == "ccxt":
        exch.setdefault("market_type", "swap")
        exch.setdefault("settle_asset", exch.get("quote_asset", "USDT"))
        exch.setdefault("margin_mode", "isolated")
        exch.setdefault("position_mode", "one_way")

    key_env, secret_env, base_url_env = _exchange_credential_targets(candidate)
    print(f"\n Credentials are stored in .env as {BOLD}{key_env}{RESET} / {BOLD}{secret_env}{RESET}.")
    k = input(f" {key_env} (Enter to skip): ").strip()
    if k:
        update_env_variable(key_env, k)
        os.environ[key_env] = k
    s = input(f" {secret_env} (Enter to skip): ").strip()
    if s:
        update_env_variable(secret_env, s)
        os.environ[secret_env] = s
    bu = input(f" Custom base URL via {base_url_env} (optional, Enter to skip): ").strip()
    if bu:
        update_env_variable(base_url_env, bu)
        os.environ[base_url_env] = bu

    from xauby.runtime.exchange_switch import (
        clear_staged_exchange, mark_exchange_activation, run_exchange_preflight,
        stage_exchange_config,
    )
    staged_path = stage_exchange_config(candidate)
    print(f"\n{BB_CYAN}Running full REST/WebSocket/position preflight...{RESET}")
    current_client = None
    try:
        from xauby.api import create_exchange_client
        from xauby.runtime.exchange_config import resolve_exchange_credentials
        old_key, old_secret, old_url = resolve_exchange_credentials(cfg)
        current_client = create_exchange_client(cfg, old_key, old_secret, old_url)
        symbols = _active_symbols_from_config(candidate)
        result = run_exchange_preflight(candidate, symbols, current_client=current_client)
    finally:
        try:
            if current_client is not None:
                current_client.close()
        except Exception:
            pass
    if not result.ok:
        print(f"{RED}[ERR] Exchange switch blocked; active config was not changed.{RESET}")
        for error in result.errors:
            print(f"  - {error}")
        print(f" Staged candidate retained at {staged_path}")
        time.sleep(2.0)
        return
    mark_exchange_activation("bot_config.yaml")
    if not _edit_bot_yaml(lambda doc: doc.__setitem__("exchange", candidate["exchange"])):
        from xauby.runtime.exchange_switch import rollback_pending_exchange_switch
        rollback_pending_exchange_switch()
        print(f"{RED}[ERR] Failed to activate staged exchange config.{RESET}")
        return
    clear_staged_exchange(staged_path)
    print(f"{GREEN}[*] Exchange config activated atomically; restart required.{RESET}")
    _qc_offer_restart()


def _qc_cred_status(env_var: str) -> str:
    val = os.environ.get(env_var, "")
    return f"SET (...{val[-6:]})" if val else "EMPTY"


def quick_config_submenu_exchange() -> None:
    _ARCH_FLAGS = [
        ("exchange_plugin_registry_enabled", "Exchange plugin registry"),
    ]
    while True:
        cfg = _load_bot_yaml()
        exch = cfg.get("exchange", {}) or {}
        arch = cfg.get("architecture", {}) or {}
        key_env, secret_env, base_url_env = _exchange_credential_targets(cfg)
        options = [
            ("1", "Switch / set up exchange (guided wizard)"),
            ("2", f"Provider: {exch.get('provider', 'binance')}"),
            ("3", f"CCXT id: {exch.get('ccxt_id', '(unset)')}"),
            ("4", f"Quote asset: {exch.get('quote_asset', 'USDT')}"),
            ("5", f"Fee % (fraction): {exch.get('fee_pct', 0.001)}"),
            ("6", f"API key ({key_env}): {_qc_cred_status(key_env)}"),
            ("7", f"API secret ({secret_env}): {_qc_cred_status(secret_env)}"),
            ("8", f"Base URL ({base_url_env}): {_qc_cred_status(base_url_env)}"),
            ("9", "Test connection"),
        ]
        flag_start = len(options) + 1
        for i, (flag, label) in enumerate(_ARCH_FLAGS, start=flag_start):
            options.append((str(i), f"{label}: {'ON' if arch.get(flag) else 'OFF'}"))
        last = flag_start + len(_ARCH_FLAGS) - 1
        _quick_config_draw_frame("EXCHANGE", options)
        try:
            choice = input(f" {BOLD}Select [0-{last}]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            quick_config_switch_exchange_wizard()
        elif choice == "2":
            try:
                from xauby.api.exchange_registry import available_exchanges
                providers = available_exchanges()
            except Exception:
                providers = ["binance", "ccxt"]
            print(f" Registered providers: {', '.join(providers)}")
            prov = input(f" Provider (current {exch.get('provider', 'binance')}): ").strip().lower()
            if prov:
                if prov in providers:
                    if _set_yaml_path("exchange.provider", prov):
                        print(f"{GREEN}[*] Provider → {prov}{RESET}")
                        _qc_offer_restart()
                else:
                    print(f"{RED}[ERR] Unknown provider (not in registry){RESET}")
                    time.sleep(1.2)
        elif choice == "3":
            cid = input(f" CCXT exchange id (current {exch.get('ccxt_id', '(unset)')}): ").strip().lower()
            if cid and _set_yaml_path("exchange.ccxt_id", cid):
                print(f"{GREEN}[*] CCXT id → {cid}{RESET}")
                _qc_offer_restart()
        elif choice == "4":
            qa = input(f" Quote asset (current {exch.get('quote_asset', 'USDT')}): ").strip().upper()
            if qa and _set_yaml_path("exchange.quote_asset", qa):
                print(f"{GREEN}[*] Quote asset → {qa}{RESET}")
                _qc_offer_restart()
        elif choice == "5":
            n = _qc_prompt_number("Fee % as fraction (e.g. 0.001)", exch.get("fee_pct", 0.001), is_int=False, lo=0.0, hi=0.1)
            if n is not None and _set_yaml_path("exchange.fee_pct", n):
                print(f"{GREEN}[*] Fee → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "6":
            _qc_set_credential(key_env, "API key")
        elif choice == "7":
            _qc_set_credential(secret_env, "API secret")
        elif choice == "8":
            _qc_set_credential(base_url_env, "base URL")
        elif choice == "9":
            print(f"\n{BB_CYAN}Testing connection...{RESET}")
            ok, msg = exchange_connection_check(cfg)
            print((f"{GREEN}[*] " if ok else f"{RED}[ERR] ") + msg + RESET)
            time.sleep(2.0)
        elif choice.isdigit() and flag_start <= int(choice) <= last:
            flag, label = _ARCH_FLAGS[int(choice) - flag_start]
            new_val = not bool(arch.get(flag))
            if _set_yaml_path(f"architecture.{flag}", new_val):
                print(f"{GREEN}[*] {label} → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_system_features() -> None:
    """Architecture switches which do not belong to exchange or regime config."""
    flags = [
        ("per_symbol_execution_mode", "Per-symbol execution mode"),
        ("event_bus_enabled", "Event bus"),
    ]
    while True:
        arch = (_load_bot_yaml().get("architecture") or {})
        options = [
            (str(i), f"{label}: {'ON' if arch.get(flag) else 'OFF'}")
            for i, (flag, label) in enumerate(flags, start=1)
        ]
        _quick_config_draw_frame("SYSTEM FEATURES", options)
        try:
            choice = input(f" {BOLD}Select [0-{len(flags)}]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice.isdigit() and 1 <= int(choice) <= len(flags):
            flag, label = flags[int(choice) - 1]
            new_val = not bool(arch.get(flag))
            if _set_yaml_path(f"architecture.{flag}", new_val):
                print(f"{GREEN}[*] {label} → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_strategy_params() -> None:
    """Per-pair strategy params beyond the basics in Trading & Risk."""
    _FIELDS = [
        ("rsi_min", "RSI min", 0.0, 100.0),
        ("rsi_max", "RSI max", 0.0, 100.0),
        ("vol_min_ratio", "Volume min ratio", 0.0, 10.0),
        ("trailing_atr_mult", "Trailing ATR multiplier", 0.0, 10.0),
        ("cool_down_minutes", "Cooldown (minutes)", 0.0, 100000.0),
    ]
    while True:
        cfg = _load_bot_yaml()
        sym = _quick_config_load_values()["focus_symbol"]
        scfg = _strategy_cfg_for_symbol(cfg, sym)
        options = [("1", f"Target pair: {sym}")]
        for i, (key, label, _lo, _hi) in enumerate(_FIELDS, start=2):
            cur = scfg.get(key, "—")
            options.append((str(i), f"{label} ({sym}): {cur}"))
        last = 1 + len(_FIELDS)
        _quick_config_draw_frame("STRATEGY PARAMS (per pair)", options)
        try:
            choice = input(f" {BOLD}Select [0-{last}]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            new_sym = _select_symbol_interactive(cfg, "Target pair for strategy params")
            try:
                from xauby.ui.state_view import write_focus_request

                write_focus_request(new_sym, ".")
            except Exception:
                pass
            print(f"{GREEN}[*] Target pair → {new_sym}{RESET}")
            time.sleep(1.0)
        elif choice.isdigit() and 2 <= int(choice) <= last:
            key, label, lo, hi = _FIELDS[int(choice) - 2]
            is_int = key == "cool_down_minutes"
            n = _qc_prompt_number(f"{label} for {sym}", scfg.get(key, "—"), is_int=is_int, lo=lo, hi=hi)
            if n is not None and key == "rsi_min" and n >= float(scfg.get("rsi_max", 100.0)):
                print(f"{RED}[ERR] RSI min must be lower than RSI max{RESET}")
                time.sleep(1.2)
                continue
            if n is not None and key == "rsi_max" and n <= float(scfg.get("rsi_min", 0.0)):
                print(f"{RED}[ERR] RSI max must be higher than RSI min{RESET}")
                time.sleep(1.2)
                continue
            if n is not None and _set_pair_strategy_values(cfg, sym, params={key: n}):
                print(f"{GREEN}[*] {label} ({sym}) → {n}{RESET}")
                _qc_offer_restart()
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_submenu_regime_router() -> None:
    while True:
        cfg = _load_bot_yaml()
        arch = cfg.get("architecture", {}) or {}
        rr = cfg.get("regime_router", {}) or {}
        options = [
            ("1", f"Regime router enabled: {'ON' if arch.get('regime_router_enabled') else 'OFF'}"),
            ("2", f"Confidence threshold: {rr.get('confidence_threshold', 0.7)}"),
            ("3", f"Debounce candles: {rr.get('debounce_candles', 3)}"),
            ("4", f"Recovery candles: {rr.get('recovery_candles', 3)}"),
            ("5", f"Force-close candles: {rr.get('force_close_candles', 6)}"),
            ("6", f"Instant switch when flat: {'ON' if rr.get('instant_switch_when_flat') else 'OFF'}"),
            ("7", "Edit regime → strategy mapping"),
        ]
        _quick_config_draw_frame("REGIME ROUTER", options)
        try:
            choice = input(f" {BOLD}Select [0-7]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            new_val = not bool(arch.get("regime_router_enabled"))
            if _set_yaml_path("architecture.regime_router_enabled", new_val):
                print(f"{GREEN}[*] Regime router → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        elif choice == "2":
            n = _qc_prompt_number("Confidence threshold", rr.get("confidence_threshold", 0.7), is_int=False, lo=0.0, hi=1.0)
            if n is not None and _set_yaml_path("regime_router.confidence_threshold", n):
                print(f"{GREEN}[*] Confidence threshold → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "3":
            n = _qc_prompt_number("Debounce candles", rr.get("debounce_candles", 3), is_int=True, lo=0, hi=100)
            if n is not None and _set_yaml_path("regime_router.debounce_candles", n):
                print(f"{GREEN}[*] Debounce → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "4":
            n = _qc_prompt_number("Recovery candles", rr.get("recovery_candles", 3), is_int=True, lo=0, hi=100)
            if n is not None and _set_yaml_path("regime_router.recovery_candles", n):
                print(f"{GREEN}[*] Recovery → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "5":
            n = _qc_prompt_number("Force-close candles", rr.get("force_close_candles", 6), is_int=True, lo=0, hi=100)
            if n is not None and _set_yaml_path("regime_router.force_close_candles", n):
                print(f"{GREEN}[*] Force-close → {n}{RESET}")
                _qc_offer_restart()
        elif choice == "6":
            new_val = not bool(rr.get("instant_switch_when_flat"))
            if _set_yaml_path("regime_router.instant_switch_when_flat", new_val):
                print(f"{GREEN}[*] Instant switch when flat → {'ON' if new_val else 'OFF'}{RESET}")
                _qc_offer_restart()
        elif choice == "7":
            quick_config_regime_mapping(rr.get("mapping", {}) or {})
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)


def quick_config_regime_mapping(current_mapping: dict) -> None:
    """Map each regime to a registered strategy (or NO_TRADE) — no ids to type."""
    from xauby.runtime.architecture_config import DEFAULT_REGIME_ROUTER_MAPPING

    try:
        strategies = list_installed_strategies()
    except Exception:
        strategies = ["cdc_action_zone"]
    regimes = list(DEFAULT_REGIME_ROUTER_MAPPING.keys())
    while True:
        cfg = _load_bot_yaml()
        mapping = (cfg.get("regime_router", {}) or {}).get("mapping", {}) or {}
        options = []
        for i, reg in enumerate(regimes, start=1):
            target = mapping.get(reg, DEFAULT_REGIME_ROUTER_MAPPING.get(reg))
            options.append((str(i), f"{reg}: {target if target else 'NO_TRADE'}"))
        _quick_config_draw_frame("REGIME → STRATEGY MAPPING", options)
        try:
            choice = input(f" {BOLD}Select regime [0-{len(regimes)}]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if not (choice.isdigit() and 1 <= int(choice) <= len(regimes)):
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)
            continue
        reg = regimes[int(choice) - 1]
        print(f"\n{BOLD}Strategy for {reg}:{RESET}")
        for j, s in enumerate(strategies, start=1):
            print(f"  [{j}] {s}")
        print(f"  [{len(strategies) + 1}] NO_TRADE (block new buys)")
        pick = input(f" Select [1-{len(strategies) + 1}] or Enter to cancel: ").strip()
        if not pick.isdigit():
            continue
        idx = int(pick)
        if idx == len(strategies) + 1:
            _set_yaml_path(f"regime_router.mapping.{reg}", None)
            print(f"{GREEN}[*] {reg} → NO_TRADE{RESET}")
            _qc_offer_restart()
        elif 1 <= idx <= len(strategies):
            _set_yaml_path(f"regime_router.mapping.{reg}", strategies[idx - 1])
            print(f"{GREEN}[*] {reg} → {strategies[idx - 1]}{RESET}")
            _qc_offer_restart()


def quick_config_editor():
    opt_params = _quick_config_load_opt_params()
    while True:
        hub = [
            ("1", "Trading & Risk — mode, SL, breakeven, D1, strategy"),
            ("2", "Global Trading — positions, interval, timeframe, sizing cap"),
            ("3", "Risk Guards — drawdown circuit-breaker, daily loss"),
            ("4", "Portfolio — balance, allocation"),
            ("5", "Strategy Params — per-pair RSI/vol/trailing/cooldown"),
            ("6", "Regime Router — enable, thresholds, mapping"),
            ("7", "Multi-Pair & Dashboard — timeframes, pairlist, focus"),
            ("8", "Telegram Alerts & Schedule — credentials, tests, digests"),
            ("9", "Macro Guard — sentiment, FRED, AI news"),
            ("10", "Exchange — provider, credentials, connection test"),
            ("11", "System Features — execution mode, event bus"),
        ]
        n = 11
        if opt_params:
            hub.append((
                "12",
                f"Apply best backtest to {opt_params['_symbol']} (NP {opt_params['net_profit_pct']:+.1f}%)",
            ))
            n = 12
        _quick_config_draw_frame("MAIN MENU", hub, back_hint="0")
        try:
            prompt = f"0-{n}"
            choice = input(f" {BOLD}Select [{prompt}]:{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if choice in ("0", ""):
            return
        if choice == "1":
            quick_config_submenu_trading()
        elif choice == "2":
            quick_config_submenu_global_trading()
        elif choice == "3":
            quick_config_submenu_risk_guards()
        elif choice == "4":
            quick_config_submenu_portfolio()
        elif choice == "5":
            quick_config_submenu_strategy_params()
        elif choice == "6":
            quick_config_submenu_regime_router()
        elif choice == "7":
            quick_config_submenu_multipair()
        elif choice == "8":
            quick_config_submenu_alerts()
        elif choice == "9":
            quick_config_submenu_macro()
        elif choice == "10":
            quick_config_submenu_exchange()
        elif choice == "11":
            quick_config_submenu_system_features()
        elif choice == "12" and opt_params:
            quick_config_submenu_backtest(opt_params)
        else:
            print(f"{RED}[ERR] Invalid choice{RESET}")
            time.sleep(1.0)
