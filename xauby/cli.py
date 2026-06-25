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
import sys


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

    print("\n[*] Restarting xAuby bot engine + TUI...")

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
            print("  → Stopping engine (no PID file, using pkill)...")
            try:
                subprocess.run(
                    ["pkill", "-f", "python.*run_xauby\\.py"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
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

    print("\n[*] Updating xAuby from origin/main and restarting engine...")
    result = subprocess.run([deploy_script, "--restart", "--branch=main"])
    if result.returncode != 0:
        print(f"[ERR] Update failed with exit code {result.returncode}.")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    _ensure_project_root()

    parser = argparse.ArgumentParser(
        prog="xauby",
        description="xAuby Trading Bot — one-command launcher",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["restart", "update"],
        help="Command: restart or update",
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
    args = parser.parse_args(argv)

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
