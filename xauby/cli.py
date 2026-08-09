#!/usr/bin/env python3
"""xauby CLI — one-command launcher.

Usage:
    xauby              # Start engine (sim mode) + TUI
    xauby --live       # Start engine (live mode) + TUI
    xauby --sim        # Start engine (sim mode) + TUI
    xauby --config     # Open quick config editor
    xauby restart      # Restart engine + TUI and clear cache
    xauby update       # Pull origin/main from GitHub + controlled restart
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from xauby.meta import PRODUCT_NAME


def _ensure_project_root() -> None:
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if script_dir and os.path.isfile(os.path.join(script_dir, "launcher.py")):
        os.chdir(script_dir)


def _clear_cache() -> None:
    """Remove __pycache__ dirs and runtime state cache files."""
    import shutil

    # Clear __pycache__ directories (skip venv)
    for root, dirs, _ in os.walk("."):
        if root.startswith("./venv") or root.startswith("venv"):
            continue
        for d in list(dirs):
            if d == "__pycache__":
                path = os.path.join(root, d)
                try:
                    shutil.rmtree(path)
                except Exception:
                    pass

    # Clear runtime state caches (under the active runtime root)
    from xauby.runtime.paths import (
        bot_state_path,
        dashboard_focus_path,
        runtime_path,
        sentiment_guard_state_path,
        sim_balance_path,
    )
    cache_files = [
        dashboard_focus_path(),
        sentiment_guard_state_path(),
        sim_balance_path(),
        sim_balance_path("bot_config"),
        bot_state_path(),
        runtime_path(".bot_config.lock"),
    ]
    for f in cache_files:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass


def _do_restart(live_mode: bool) -> int:
    """Kill engine + tmux, clear cache, then start fresh."""
    import signal
    import subprocess
    import time

    import launcher
    from xauby.ui.menu import check_engine_status

    print(f"\n[*] Restarting {PRODUCT_NAME} bot engine + TUI...")

    if live_mode:
        controlled_script = os.path.join("scripts", "controlled_restart_engine.sh")
        if os.path.isfile(controlled_script):
            print("  → Running controlled live engine restart...")
            result = subprocess.run([controlled_script])
            if result.returncode != 0:
                print(f"[ERR] Controlled restart failed with exit code {result.returncode}.")
                return result.returncode
            print("  → Launching TUI...")
            launcher.run_textual_tui(start_screen="dashboard")
            return 0

    # 1. Kill tmux session
    print("  → Killing tmux session 'dashboard'...")
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", "dashboard"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        pass

    # 2. Kill engine process
    pid = launcher.get_running_engine_pid()
    if pid:
        print(f"  → Stopping engine process (PID: {pid})...")
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2.0)
            try:
                os.kill(pid, 0)
                print("  → Force killing...")
                os.kill(pid, signal.SIGKILL)
                time.sleep(1.0)
            except OSError:
                pass
        except Exception:
            pass
    else:
        eng, _ = check_engine_status()
        if eng == "RUNNING":
            print("  → Stopping engine (no PID file, using cwd-scoped kill)...")
            try:
                launcher.kill_local_engine_processes()
                time.sleep(2.0)
            except Exception:
                pass

    # 3. Clear cache
    print("  → Clearing cache...")
    _clear_cache()

    # 4. Brief pause
    time.sleep(1.0)

    # 5. Start fresh
    print("  → Starting engine + TUI...")
    launcher.run_engine_with_tui(live_mode=live_mode)
    return 0


def _do_update() -> int:
    """Pull latest origin/main from GitHub, then run the controlled restart."""
    import subprocess

    deploy_script = os.path.join("scripts", "deploy_from_github.sh")
    if not os.path.isfile(deploy_script):
        print("[ERR] scripts/deploy_from_github.sh not found.")
        return 1

    print(f"\n[*] Updating {PRODUCT_NAME} from origin/main and restarting engine...")
    result = subprocess.run([deploy_script, "--restart", "--branch=main"])
    if result.returncode != 0:
        print(f"[ERR] Update failed with exit code {result.returncode}.")
    return result.returncode


def _do_tui_attach(tenant: str, *, read_only: bool) -> int:
    """Attach a fresh Textual process to one hosted tenant without control rights."""
    from xauby.saas.security import validate_tenant_slug

    if not read_only:
        print("[ERR] Hosted tenant TUI attach requires --read-only.")
        return 2
    try:
        slug = validate_tenant_slug(tenant)
    except ValueError as exc:
        print(f"[ERR] {exc}.")
        return 2

    config_root = Path(
        os.environ.get("XAUBY_TENANT_CONFIG_ROOT", "/etc/xauby/tenants")
    ).resolve()
    runtime_root = Path(
        os.environ.get("XAUBY_TENANT_RUNTIME_ROOT", "/var/lib/xauby/runtime")
    ).resolve()
    config_dir = (config_root / slug).resolve()
    runtime_dir = (runtime_root / slug).resolve()
    if config_dir.parent != config_root or runtime_dir.parent != runtime_root:
        print("[ERR] Tenant path escaped its configured root.")
        return 2
    if not config_dir.is_dir():
        print(f"[ERR] Tenant config directory not found: {config_dir}")
        return 2
    if not runtime_dir.is_dir():
        print(f"[ERR] Tenant runtime directory not found: {runtime_dir}")
        return 2

    env = os.environ.copy()
    # The observer process never needs exchange, Telegram, OAuth, or database
    # control-plane secrets.  Strip inherited credentials before spawning it.
    sensitive = ("KEY", "SECRET", "TOKEN", "PASSPHRASE", "PASSWORD")
    for name in list(env):
        if any(part in name.upper() for part in sensitive):
            env.pop(name, None)
    env.update(
        {
            "XAUBY_CONFIG_DIR": str(config_dir),
            "XAUBY_HOME": str(runtime_root),
            "XAUBY_INSTANCE_ID": slug,
            "SQLITE_DB_PATH": str(runtime_dir / "xauby.db"),
            "XAUBY_TUI_READ_ONLY": "1",
            "XAUBY_TUI_TENANT": slug,
            "BOT_READ_ONLY": "true",
            "FROM_LAUNCHER": "true",
            "XAUBY_START_SCREEN": "dashboard",
        }
    )
    env.pop("XAUBY_MENU_ACTION", None)

    print(f"\n[*] Attaching read-only TUI to tenant: {slug}")
    print("    Monitoring only — engine, orders, config, backtest, and DB actions are disabled.")
    result = subprocess.run(
        [sys.executable, "-m", "xauby.ui.textual_tui.app"],
        env=env,
    )
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    _ensure_project_root()

    parser = argparse.ArgumentParser(
        prog="xauby",
        description=f"{PRODUCT_NAME} Trading Bot — one-command launcher",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["restart", "update", "tui"],
        help="Command: restart, update, or tenant TUI attach",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Start engine in LIVE trading mode",
    )
    parser.add_argument(
        "--sim",
        "--simulate",
        action="store_true",
        help="Start engine in SIMULATION mode (default)",
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Open quick configuration editor instead of TUI",
    )
    parser.add_argument(
        "--tenant",
        default="",
        help="Hosted tenant slug for the read-only TUI attach command",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Disable all TUI actions that can change engine, order, config, or DB state",
    )
    args = parser.parse_args(argv)

    if args.command == "tui":
        if args.live or args.sim or args.config:
            print("[ERR] tui attach cannot be combined with --live, --sim, or --config.")
            return 2
        if not args.tenant:
            print("[ERR] tui attach requires --tenant <slug>.")
            return 2
        return _do_tui_attach(args.tenant, read_only=bool(args.read_only))
    if args.tenant or args.read_only:
        print("[ERR] --tenant and --read-only are only valid with the tui command.")
        return 2

    # Config editor shortcut — open the native Textual config hub. Set
    # XAUBY_CONFIG_TERMINAL=1 for the legacy terminal editor (e.g. no TTY).
    if args.config:
        import launcher

        if os.environ.get("XAUBY_CONFIG_TERMINAL") == "1":
            launcher.quick_config_editor()
            return 0
        launcher.run_textual_tui(start_screen="quick_config")
        return 0

    # Update shortcut
    if args.command == "update":
        return _do_update()

    # Restart shortcut
    if args.command == "restart":
        live_mode = bool(args.live)
        if not live_mode and not args.sim:
            try:
                import yaml

                with open("bot_config.yaml", "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                live_mode = not bool(cfg.get("simulate_only", True))
            except Exception:
                live_mode = False
        return _do_restart(live_mode)

    import launcher
    from xauby.ui.menu import check_engine_status

    eng, _ = check_engine_status()

    if eng == "RUNNING":
        print("\n[*] Engine already running. Launching TUI...")
        launcher.run_textual_tui(start_screen="dashboard")
        return 0

    # Determine mode
    live_mode = bool(args.live)
    if not live_mode and not args.sim:
        # Default: read from bot_config.yaml
        try:
            import yaml

            with open("bot_config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            live_mode = not bool(cfg.get("simulate_only", True))
        except Exception:
            live_mode = False

    print(f"\n[*] Engine offline. Starting bot engine + TUI...")
    launcher.run_engine_with_tui(live_mode=live_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
