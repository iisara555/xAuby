"""Maintenance: Telegram test, config check, DB backup/restore/maintenance."""
import os
import sys
import re
import time
import json
import subprocess
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

__all__ = [
    "send_test_telegram",
    "check_config",
    "backup_database",
    "restore_database",
    "db_maintenance",
    "db_maintenance_menu",
]


def send_test_telegram() -> bool:
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    if not tg_token or not tg_chat_id:
        print(f"\n{RED}[ERR] Telegram credentials are not fully set in .env!{RESET}")
        print(f"Please set: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return False
        
    print(f"\n{YELLOW}[*] Sending test Telegram notification to chat ID {tg_chat_id}...{RESET}")
    import requests
    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
    payload = {
        "chat_id": tg_chat_id,
        "text": "🔔 *xAuby Test Alert*\nThis is a test notification to verify your Telegram connection from the launcher.",
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"{GREEN}[✓] Test message sent successfully!{RESET}")
            return True
        else:
            print(f"{RED}[ERR] Telegram API returned code {r.status_code}: {r.text}{RESET}")
            return False
    except Exception as e:
        print(f"{RED}[ERR] Failed to connect to Telegram API: {e}{RESET}")
        return False

def check_config():
    W = get_terminal_width()
    if W < 35:
        W = 35
    
    print_line(W)
    if W < 50:
        check_title = f"{BB_BG_AMBER} CONFIG CHECK {RESET}"
    else:
        check_title = f"{BB_BG_AMBER} SYSTEM CONFIGURATION CHECK {RESET}"
    print(center_text(check_title, W))
    print_line(W)

    # 1. Required Python Libraries Check
    print(f" {BOLD}{BB_AMBER}• Python Libraries Check:{RESET}")
    deps = ["pandas", "pandas_ta", "requests", "yaml", "sqlite3", "websocket"]
    for dep in deps:
        try:
            if dep == "yaml":
                __import__("yaml")
            elif dep == "websocket":
                __import__("websocket")
            else:
                __import__(dep)
            status = f"{GREEN}LOADED (OK){RESET}"
        except ImportError:
            status = f"{RED}MISSING (run: pip install {dep}){RESET}"
        print(f"    - {dep:<12}: {status}")
    print()

    # 2. API Keys Check
    try:
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        from xauby.runtime.exchange_config import credential_env_names
        key_env, secret_env, _ = credential_env_names(cfg)
    except Exception:
        cfg = {}
        key_env, secret_env = "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"
    api_key = os.environ.get(key_env, "")
    api_secret = os.environ.get(secret_env, "")
    exchange_cfg = cfg.get("exchange") or {}
    exchange_label = str(exchange_cfg.get("ccxt_id") or exchange_cfg.get("name") or exchange_cfg.get("provider") or "exchange").upper()
    
    if api_key and api_secret:
        key_status = f"{GREEN}LOADED (ends in ...{api_key[-6:]}){RESET}"
    else:
        key_status = f"{RED}MISSING / NOT INSTALLED{RESET}"

    if W < 55:
        print(f" {BOLD}{BB_AMBER}• {exchange_label} API Keys:{RESET}")
        print(f"    - Status: {key_status}")
    else:
        print(f" {BOLD}{BB_AMBER}• {exchange_label} API Keys:{RESET}  {key_status}")

    # 3. Mode check from config
    try:
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        yaml_sim = cfg.get("simulate_only", True)
        yaml_ro = cfg.get("read_only", False)
    except Exception as e:
        yaml_sim = f"Error reading YAML: {e}"
        yaml_ro = "Error"
        cfg = {}

    # 4. Live safety check from env
    live_allowed = os.environ.get("LIVE_TRADING", "false").lower() == "true"
    if W < 55:
        print(f"    - config sim_only: {YELLOW if yaml_sim else GREEN}{yaml_sim}{RESET}")
        print(f"    - config read_only:{YELLOW if yaml_ro else GREEN}{yaml_ro}{RESET}")
        print(f"    - env LIVE_TRADING:{GREEN if live_allowed else RED}{live_allowed}{RESET}")
    else:
        print(f"    - config.yaml simulate_only: {YELLOW if yaml_sim else GREEN}{yaml_sim}{RESET}")
        print(f"    - config.yaml read_only:     {YELLOW if yaml_ro else GREEN}{yaml_ro}{RESET}")
        print(f"    - .env LIVE_TRADING:        {GREEN if live_allowed else RED}{live_allowed}{RESET}")

    active_strategy = get_active_strategy_name(cfg)
    installed = list_installed_strategies()
    strat_ok = active_strategy in installed
    strat_status = (
        f"{GREEN}{active_strategy}{RESET}"
        if strat_ok else
        f"{RED}{active_strategy} (plugin not found){RESET}"
    )
    if W < 55:
        print(f"    - strategy active: {strat_status}")
        print(f"    - plugins found: {len(installed)}")
    else:
        print(f"    - strategy.active:           {strat_status}")
        print(f"    - installed plugins:         {', '.join(installed) if installed else '(none)'}")
    print()

    # 5. Network Connectivity & Latency Check
    print(f" {BOLD}{BB_AMBER}• Network & API Latency Check:{RESET}")
    server_time_ms = 0
    client = None
    try:
        from xauby.api import create_exchange_client
        from xauby.runtime.exchange_config import resolve_exchange_credentials
        key, secret, base_url = resolve_exchange_credentials(cfg)
        t0 = time.time()
        client = create_exchange_client(cfg, key, secret, base_url)
        info = client.get_exchange_info()
        latency = (time.time() - t0) * 1000
        exchange_net = (
            f"{GREEN}CONNECTED (Latency: {latency:.1f} ms, "
            f"Markets: {len(info.get('symbols', []))}){RESET}"
        )
    except Exception as e:
        exchange_net = f"{RED}UNREACHABLE ({e}){RESET}"
    finally:
        try:
            if client is not None:
                client.close()
        except Exception:
            pass
    print(f"    - {exchange_label} API: {exchange_net}")
    
    # Telegram Connection
    import requests
    tg_enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() == "true"
    tg_net = f"{RED}DISABLED{RESET}"
    if tg_enabled:
        try:
            t0 = time.time()
            requests.get("https://api.telegram.org", timeout=5)
            latency = (time.time() - t0) * 1000
            tg_net = f"{GREEN}CONNECTED (Latency: {latency:.1f} ms){RESET}"
        except Exception as e:
            tg_net = f"{RED}UNREACHABLE ({e}){RESET}"
    print(f"    - Telegram API  : {tg_net}")
    print()

    # 6. Time Synchronization Check (Time Drift)
    print(f" {BOLD}{BB_AMBER}• Clock Synchronization Check:{RESET}")
    if server_time_ms > 0:
        server_ts = server_time_ms / 1000.0
        local_ts = time.time()
        drift = server_ts - local_ts
        if abs(drift) <= 2.0:
            drift_status = f"{GREEN}OK ({drift:+.3f}s drift){RESET}"
        elif abs(drift) <= 10.0:
            if W < 70:
                drift_status = f"{YELLOW}WARNING ({drift:+.3f}s drift)\n      [!] Sync system clock to prevent recvWindow errors{RESET}"
            else:
                drift_status = f"{YELLOW}WARNING ({drift:+.3f}s drift) - Sync system clock to prevent recvWindow errors{RESET}"
        else:
            if W < 70:
                drift_status = f"{RED}CRITICAL ({drift:+.3f}s drift)\n      [!] Sync clock immediately; exchange may reject orders.{RESET}"
            else:
                drift_status = f"{RED}CRITICAL ({drift:+.3f}s drift) - exchange may reject orders; sync immediately.{RESET}"
    else:
        drift_status = f"{RED}UNKNOWN (could not fetch server time){RESET}"
    print(f"    - Clock Offset  : {drift_status}")
    print()

    # 7. Live API Keys & IP Whitelist Validation Check
    print(f" {BOLD}{BB_AMBER}• API Credentials & Whitelist Validation:{RESET}")
    if api_key and api_secret:
        try:
            from xauby.api import create_exchange_client
            from xauby.runtime.exchange_config import resolve_exchange_credentials
            _, _, base_url = resolve_exchange_credentials(cfg)
            client = create_exchange_client(cfg, api_key, api_secret, base_url)
            balances = client.get_balances()
            
            auth_status = f"{GREEN}VALIDATED (Active & Connected){RESET}"
            
            market_type = str(exchange_cfg.get("market_type") or "spot").upper()
            trade_perm = f"{GREEN}{market_type} ORDER API SUPPORTED{RESET} (key permission checked on order)"
            
            # Extract live balances
            port_details = ", ".join([f"{asset}: {bal['available']:.4f}" for asset, bal in balances.items() if bal['available'] > 0])
            
            if W < 55:
                auth_msg = f"    - Status: {auth_status}"
                auth_msg += f"\n    - Trade : {trade_perm}"
                if port_details:
                    auth_msg += f"\n    - Bal   : {WHITE}{port_details}{RESET}"
                else:
                    auth_msg += f"\n    - Bal   : {WHITE}No assets > 0{RESET}"
            else:
                auth_msg = f"    - Keys Status   : {auth_status}"
                auth_msg += f"\n    - Trading Perm  : {trade_perm}"
                if port_details:
                    auth_msg += f"\n    - Live Balances : {WHITE}{port_details}{RESET}"
                else:
                    auth_msg += f"\n    - Live Balances : {WHITE}No assets found with balance > 0{RESET}"
        except Exception as e:
            auth_status = f"{RED}VALIDATION FAILED ({e}){RESET}"
            if W < 55:
                auth_msg = f"    - Status: {auth_status}"
            else:
                auth_msg = f"    - Keys Status   : {auth_status}"
        finally:
            try:
                if 'client' in locals() and client is not None:
                    client.close()
            except Exception:
                pass
    else:
        if W < 55:
            auth_msg = f"    - Status: {RED}SKIPPED (Keys missing){RESET}"
        else:
            auth_msg = f"    - Keys Status   : {RED}SKIPPED (API Keys missing in env){RESET}"
    print(auth_msg)
    print()

    # 8. SQLite Database Schema Check
    print(f" {BOLD}{BB_AMBER}• SQLite Database Schema Check:{RESET}")
    db_path = resolve_db_path()
    db_exists = os.path.exists(db_path)
    if db_exists:
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.cursor()

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [r[0] for r in cursor.fetchall()]
                required_tables = ["prices", "closed_trades", "trade_states"]
                missing_tables = [t for t in required_tables if t not in tables]

                if not missing_tables:
                    cursor.execute("SELECT COUNT(*) FROM prices")
                    price_count = cursor.fetchone()[0]
                    cursor.execute("SELECT COUNT(*) FROM closed_trades")
                    trade_count = cursor.fetchone()[0]
                    if W < 70:
                        db_schema_status = f"{GREEN}INTEGRITY OK ({price_count} prices, {trade_count} trades){RESET}"
                    else:
                        db_schema_status = f"{GREEN}INTEGRITY OK ({price_count} price points, {trade_count} trades persisted){RESET}"
                else:
                    db_schema_status = f"{YELLOW}WARNING (Missing tables: {', '.join(missing_tables)}){RESET}"
            finally:
                conn.close()
        except Exception as e:
            db_schema_status = f"{RED}CORRUPTED / ERROR ({e}){RESET}"
    else:
        db_schema_status = f"{YELLOW}NOT FOUND (will initialize on bot start){RESET}"

    if W < 55:
        print(f"    - DB File: {GREEN if db_exists else YELLOW}{os.path.basename(db_path)}{RESET}")
        print(f"    - Schema : {db_schema_status}")
    else:
        print(f"    - Database File : {GREEN if db_exists else YELLOW}{db_path}{RESET}")
        print(f"    - Schema Status : {db_schema_status}")
    print()

    # 9. System Readiness
    if not yaml_sim and not yaml_ro and live_allowed and api_key and server_time_ms > 0:
        trade_readiness = f"{RED}{BOLD}READY FOR LIVE TRADING!{RESET}"
    elif yaml_sim:
        trade_readiness = f"{GREEN}SIMULATION MODE (Paper Trading){RESET}"
    else:
        trade_readiness = f"{YELLOW}SAFETY LOCKED (Executor will skip placing orders){RESET}"
        
    print(f" {BOLD}{BB_AMBER}• Final System Readiness: {RESET}{trade_readiness}")
    print_line(W)
    
    if tg_enabled:
        tg_choice = input("\n Send a test Telegram notification? (y/n): ").strip().lower()
        if tg_choice in ("y", "yes"):
            send_test_telegram()
    input("\nPress Enter to return to main menu...")

def backup_database():
    import sqlite3
    db_path = resolve_db_path()
    if not os.path.exists(db_path):
        print(f"\n{RED}[ERR] Active database not found at {db_path}!{RESET}")
        input("\nPress Enter to continue...")
        return
        
    os.makedirs("backups", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"backups/crypto_bot_lite_{timestamp}.bak"
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        with src, dst:
            src.backup(dst)
        src.close()
        dst.close()
        print(f"\n{GREEN}[✓] Database backed up successfully!{RESET}")
        print(f"Backup saved to: {WHITE}{backup_path}{RESET}")
        try:
            with open("bot_config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            from xauby.utils.retention import run_backup_retention
            stats = run_backup_retention(cfg)
            if stats.get("deleted"):
                print(f"{GREEN}[✓] Backup retention: removed {stats['deleted']} old file(s).{RESET}")
        except Exception as e:
            print(f"{YELLOW}[!] Backup retention skipped: {e}{RESET}")
    except Exception as e:
        print(f"\n{RED}[ERR] Failed to backup database: {e}{RESET}")
    input("\nPress Enter to continue...")

def restore_database():
    import sqlite3
    db_path = resolve_db_path()
    backup_dir = "backups"
    if not os.path.exists(backup_dir):
        print(f"\n{YELLOW}[!] No backups folder found.{RESET}")
        input("\nPress Enter to continue...")
        return
        
    bak_files = sorted([f for f in os.listdir(backup_dir) if f.endswith(".bak")], reverse=True)
    if not bak_files:
        print(f"\n{YELLOW}[!] No backup files found in {backup_dir}.{RESET}")
        input("\nPress Enter to continue...")
        return
        
    print(f"\n{BOLD}{BB_AMBER}Select a backup to restore:{RESET}")
    for idx, f in enumerate(bak_files, 1):
        full_path = os.path.join(backup_dir, f)
        size_mb = os.path.getsize(full_path) / (1024 * 1024)
        print(f"  [{idx}] {f:<30} ({size_mb:.2f} MB)")
        
    try:
        choice = input(f"\nSelect option (1-{len(bak_files)}) or press Enter to cancel: ").strip()
        if not choice:
            return
        idx = int(choice) - 1
        if idx < 0 or idx >= len(bak_files):
            print(f"{RED}[ERR] Invalid selection.{RESET}")
            input("\nPress Enter to continue...")
            return
            
        selected_bak = os.path.join(backup_dir, bak_files[idx])
        print(f"\n{RED}{BOLD}[WARNING] YOU ARE ABOUT TO OVERWRITE THE ACTIVE DATABASE WITH:{RESET}")
        print(f"  {selected_bak}")
        confirm = input(" Type 'CONFIRM' to proceed: ").strip()
        if confirm.upper() == "CONFIRM":
            print(f"{YELLOW}[!] Note: If systemd engine service is running, stop it first to prevent database locks.{RESET}")
            src = sqlite3.connect(selected_bak)
            dst = sqlite3.connect(db_path)
            with src, dst:
                src.backup(dst)
            src.close()
            dst.close()
            print(f"\n{GREEN}[✓] Database restored successfully from backup!{RESET}")
        else:
            print(f"{YELLOW}Restore cancelled.{RESET}")
    except ValueError:
        print(f"{RED}[ERR] Invalid input number.{RESET}")
    except Exception as e:
        print(f"\n{RED}[ERR] Failed to restore database: {e}{RESET}")
    input("\nPress Enter to continue...")

def db_maintenance():
    db_path = resolve_db_path()
    if not os.path.exists(db_path):
        print(f"\n{RED}[ERR] Database file {db_path} not found!{RESET}")
        input("\nPress Enter to continue...")
        return
        
    try:
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        retention = cfg.get("candle_retention", {})
    except Exception as e:
        print(f"{RED}[ERR] Failed to read config retention settings: {e}{RESET}")
        input("\nPress Enter to continue...")
        return
        
    size_before = os.path.getsize(db_path) / (1024 * 1024)
    print(f"\nCurrent Database Size: {C_PRIMARY}{size_before:.2f} MB{C_RESET}")
    
    if not retention.get("enabled", False):
        print(f"{YELLOW}[!] Candle retention is disabled in config. Enabling it is recommended.{RESET}")
        
    confirm = input(" Start candle pruning and database vacuum? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print(f"{YELLOW}Maintenance cancelled.{RESET}")
        input("\nPress Enter to continue...")
        return
        
    print(f"\n{YELLOW}[*] Pruning old candles from database...{RESET}")
    import sqlite3
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        timeframes = retention.get("timeframes", {})
        symbol = os.environ.get("DEFAULT_SYMBOL", default_runtime_symbol()).upper().replace("_", "")
        now_ts = int(time.time())

        pruned_total = 0
        for tf, days in timeframes.items():
            cutoff_ts = now_ts - (int(days) * 24 * 3600)
            cursor.execute("DELETE FROM prices WHERE symbol = ? AND timeframe = ? AND timestamp < ?", (symbol, tf, cutoff_ts))
            pruned_total += cursor.rowcount
            print(f"  - Deleted {cursor.rowcount} candles for {symbol} {tf} older than {days} days.")

        print(f"{GREEN}[✓] Pruning completed (total {pruned_total} rows deleted).{RESET}")
        conn.commit()

        print(f"{YELLOW}[*] Running VACUUM to rebuild database file and reclaim space...{RESET}")
        conn_vac = sqlite3.connect(db_path)
        try:
            conn_vac.isolation_level = None
            conn_vac.execute("VACUUM;")
        finally:
            conn_vac.close()
        
        size_after = os.path.getsize(db_path) / (1024 * 1024)
        saved = size_before - size_after
        print(f"\n{GREEN}[✓] Database maintenance complete!{RESET}")
        print(f"Database size after VACUUM: {C_PRIMARY}{size_after:.2f} MB{C_RESET} (Saved {saved:.2f} MB)")
    except Exception as e:
        print(f"{RED}[ERR] Database maintenance failed: {e}{RESET}")
    finally:
        if conn is not None:
            conn.close()

    input("\nPress Enter to continue...")

def db_maintenance_menu():
    while True:
        W = get_terminal_width()
        if W < 35:
            W = 35
        print("\033[2J\033[H", end="")
        
        print(f"{BB_CYAN}┌{'─' * (W - 2)}┐{RESET}")
        if W < 50:
            db_title = f"{BB_BG_AMBER} DB MAINTENANCE {RESET}"
        else:
            db_title = f"{BB_BG_AMBER} DATABASE DIAGNOSTICS & BACKUP TOOLS {RESET}"
        print(f"{BB_CYAN}│{RESET} {center_text(db_title, W - 4)} {BB_CYAN}│{RESET}")
        print(f"{BB_CYAN}├{'─' * (W - 2)}┤{RESET}")
        
        db_path = resolve_db_path()
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            size_str = f"Active Database Size: {size_mb:.2f} MB"
        else:
            size_str = "Active Database Size: NOT FOUND"
            
        print_menu_row(center_text(size_str, W - 4), W, BB_CYAN)
        print(f"{BB_CYAN}├{'─' * (W - 2)}┤{RESET}")
        
        options = [
            ("1", "BACKUP ACTIVE DATABASE"),
            ("2", "RESTORE FROM BACKUP"),
            ("3", "DATABASE CLEANUP & MAINTENANCE (PRUNE & VACUUM)"),
            ("4", "RETURN TO MAIN MENU")
        ]
        
        for num, desc in options:
            print_menu_row(f" [{num}] {WHITE}{desc}{RESET}", W, BB_CYAN)
            
        print(f"{BB_CYAN}└{'─' * (W - 2)}┘{RESET}")
        
        try:
            choice = input(f" {BOLD}Select option (1-4):{RESET} ").strip()
            if choice == "1":
                backup_database()
            elif choice == "2":
                restore_database()
            elif choice == "3":
                db_maintenance()
            elif choice == "4":
                break
        except (KeyboardInterrupt, EOFError):
            break



# ── Return-value helpers for the native Textual System Check / DB Tools ───────

def collect_config_report() -> str:
    """Run the config check and capture its ANSI report (no prompts/blocking)."""
    import builtins
    import contextlib
    import io

    buf = io.StringIO()
    orig_input = builtins.input
    builtins.input = lambda *a, **k: ""  # auto-decline the trailing prompts
    try:
        with contextlib.redirect_stdout(buf):
            check_config()
    except Exception as e:  # never let a probe crash the screen
        buf.write(f"\n[ERR] config check failed: {e}\n")
    finally:
        builtins.input = orig_input
    return buf.getvalue()


def db_size_mb():
    """Active DB size in MB, or None if missing."""
    path = resolve_db_path()
    if not os.path.exists(path):
        return None
    return os.path.getsize(path) / (1024 * 1024)


def do_backup():
    """Back up the active DB. Returns (ok, message)."""
    import sqlite3

    db_path = resolve_db_path()
    if not os.path.exists(db_path):
        return False, f"Active database not found at {db_path}"
    os.makedirs("backups", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"backups/crypto_bot_lite_{ts}.bak"
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        with src, dst:
            src.backup(dst)
        src.close()
        dst.close()
        msg = f"Backed up to {backup_path}"
        try:
            with open("bot_config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            from xauby.utils.retention import run_backup_retention
            stats = run_backup_retention(cfg)
            if stats.get("deleted"):
                msg += f" · retention removed {stats['deleted']} old file(s)"
        except Exception:
            pass
        return True, msg
    except Exception as e:
        return False, f"Backup failed: {e}"


def list_backups():
    """Return [(name, size_mb)] for available .bak files, newest first."""
    d = "backups"
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted([f for f in os.listdir(d) if f.endswith(".bak")], reverse=True):
        out.append((f, os.path.getsize(os.path.join(d, f)) / (1024 * 1024)))
    return out


def do_restore(name: str):
    """Restore the active DB from backups/<name>. Returns (ok, message)."""
    import sqlite3

    path = os.path.join("backups", name)
    if not os.path.exists(path):
        return False, "Backup not found"
    try:
        src = sqlite3.connect(path)
        dst = sqlite3.connect(resolve_db_path())
        with src, dst:
            src.backup(dst)
        src.close()
        dst.close()
        return True, f"Restored from {name}"
    except Exception as e:
        return False, f"Restore failed: {e}"


def do_db_maintenance():
    """Prune old candles + VACUUM. Returns (ok, report)."""
    import sqlite3

    db_path = resolve_db_path()
    if not os.path.exists(db_path):
        return False, "Database not found"
    try:
        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        retention = cfg.get("candle_retention", {}) or {}
    except Exception as e:
        return False, f"config read failed: {e}"
    size_before = os.path.getsize(db_path) / (1024 * 1024)
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        symbol = os.environ.get("DEFAULT_SYMBOL", default_runtime_symbol()).upper().replace("_", "")
        now_ts = int(time.time())
        pruned = 0
        for tf, days in (retention.get("timeframes", {}) or {}).items():
            cutoff = now_ts - (int(days) * 24 * 3600)
            cursor.execute(
                "DELETE FROM prices WHERE symbol = ? AND timeframe = ? AND timestamp < ?",
                (symbol, tf, cutoff))
            pruned += cursor.rowcount
        conn.commit()
        vac = sqlite3.connect(db_path)
        try:
            vac.isolation_level = None
            vac.execute("VACUUM;")
        finally:
            vac.close()
        size_after = os.path.getsize(db_path) / (1024 * 1024)
        return True, (f"Pruned {pruned} row(s) · {size_before:.2f} MB → "
                      f"{size_after:.2f} MB (saved {size_before - size_after:.2f} MB)")
    except Exception as e:
        return False, f"Maintenance failed: {e}"
    finally:
        if conn is not None:
            conn.close()
