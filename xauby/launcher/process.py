"""Process control: tmux dashboard, engine/TUI launch, restart, backtester."""
import os
import sys
import re
import time
import json
import shlex
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
    "_XAUBY_TMUX_SESSION",
    "_is_inside_tmux",
    "_tmux_available",
    "_has_tmux_session",
    "_kill_tmux_session",
    "run_in_tmux_dashboard",
    "run_engine_with_tui",
    "run_textual_tui",
    "get_running_engine_pid",
    "kill_local_engine_processes",
    "run_backtester_tui",
    "restart_bot_service",
]


_XAUBY_TMUX_SESSION = os.environ.get("XAUBY_TMUX_SESSION", "dashboard")

# Env var names that must never be passed via `tmux -e` (would leak into the
# world-readable process table). Matched case-insensitively as substrings.
_SENSITIVE_ENV_PAT = re.compile(r"KEY|SECRET|TOKEN|PASSPHRASE|PASSWORD", re.IGNORECASE)


def _is_inside_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def _tmux_available() -> bool:
    try:
        subprocess.run(
            ["tmux", "-V"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return True
    except Exception:
        return False


def _has_tmux_session(name: str) -> bool:
    try:
        r = subprocess.run(
            ["tmux", "has-session", "-t", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return r.returncode == 0
    except Exception:
        return False


def _kill_tmux_session(name: str) -> None:
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        pass


def run_in_tmux_dashboard(cmd: list[str], env: dict) -> bool:
    """Run TUI inside tmux session 'dashboard'.
    Returns True if tmux was used (this process is replaced via exec).
    """
    if _is_inside_tmux() or not _tmux_available():
        return False
    name = _XAUBY_TMUX_SESSION
    try:
        _kill_tmux_session(name)
        tmux_cmd = ["tmux", "new-session", "-d", "-s", name, "-c", os.getcwd()]
        # Only NON-sensitive vars go through `-e`: tmux records them in the
        # session's argv, which is world-readable via `ps aux`. Credentials
        # (API keys/secrets, tokens, passphrases) are instead loaded inside the
        # session by sourcing .env, so they never reach the process table.
        for k, v in env.items():
            if _SENSITIVE_ENV_PAT.search(k):
                continue
            tmux_cmd.extend(["-e", f"{k}={v}"])
        inner = "set -a; [ -f .env ] && . ./.env; set +a; exec " + " ".join(cmd)
        tmux_cmd.append(f"bash -c {shlex.quote(inner)}")
        subprocess.run(tmux_cmd, check=True)
        os.execvp("tmux", ["tmux", "attach", "-t", name])
        return True
    except Exception:
        return False


def run_engine_with_tui(live_mode: bool):
    # Setup environment overrides
    env = os.environ.copy()
    if live_mode:
        env["SIMULATE_ONLY"] = "false"
        env["BOT_READ_ONLY"] = "false"
        mode_str = "LIVE TRADING"
        mode_color = RED
    else:
        env["SIMULATE_ONLY"] = "true"
        mode_str = "SIMULATION (Paper Trading)"
        mode_color = GREEN

    os.makedirs("core/logs", exist_ok=True)
    log_path = "core/logs/xauby_engine_bg.log"
    log_file = open(log_path, "w", encoding="utf-8")

    W = get_terminal_width()
    if W < 35:
        W = 35

    print_line(W)
    print(center_text(f"{mode_color}{BOLD}LAUNCHING BOT ENGINE ({mode_str})...{RESET}", W))
    print(center_text(f"{WHITE}Logs redirected to: {log_path}{RESET}", W))
    print_line(W)

    cmd = [sys.executable, "run_xauby.py"]
    if live_mode:
        cmd.append("--live")
    else:
        cmd.append("--simulate")
        
    proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
    
    print(f" {GREEN}• Engine background process started (PID: {proc.pid}){RESET}")
    print(f" {GREEN}• Launching Textual dashboard...{RESET}")
    time.sleep(2.5)  # Give the bot engine time to setup and generate the state file

    env["FROM_LAUNCHER"] = "true"
    env["XAUBY_START_SCREEN"] = "dashboard"
    env.pop("NO_COLOR", None)
    env.setdefault("COLORTERM", "truecolor")
    keep_engine = True
    old_handler = None
    if sys.platform != "win32":
        import signal
        old_handler = signal.getsignal(signal.SIGWINCH)
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "xauby.ui.textual_tui.app"],
            env=env,
        )
        if result.returncode != 0 and proc.poll() is not None:
            print(f"\n{RED}[ERR] Bot engine stopped (exit {proc.returncode}).{RESET}")
            print(f"{YELLOW}Log: {log_path}{RESET}")
            keep_engine = False
    except KeyboardInterrupt:
        keep_engine = False
        print(f"\n\n{YELLOW}[*] KeyboardInterrupt — shutting down bot engine...{RESET}")
    finally:
        if sys.platform != "win32" and old_handler is not None:
            import signal
            signal.signal(signal.SIGWINCH, old_handler)
        if proc.poll() is not None:
            log_file.close()
            if keep_engine:
                print(f"{YELLOW}[*] Engine already stopped.{RESET}")
            input("\nPress Enter to return to main menu...")
        elif keep_engine:
            print(f"{GREEN}[*] Dashboard closed. Engine still running (PID: {proc.pid}).{RESET}")
            print(f"{C_MUTED}Use option [3] to reopen the dashboard, or [8] to restart the engine.{RESET}")
            log_file.close()
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_file.close()
            print(f"{GREEN}[*] Background engine process terminated.{RESET}")
            input("\nPress Enter to return to main menu...")

def run_textual_tui(*, start_screen: str = "dashboard"):
    print(f"\n{GREEN}[*] Launching Textual Dashboard...{RESET}")
    time.sleep(1.0)
    cmd = [sys.executable, "-m", "xauby.ui.textual_tui.app"]
    env = os.environ.copy()
    env["FROM_LAUNCHER"] = "true"
    env["XAUBY_START_SCREEN"] = start_screen
    env.pop("NO_COLOR", None)
    env.pop("FORCE_COLOR", None)
    env.setdefault("COLORTERM", "truecolor")

    # Prefer tmux session 'dashboard' when available
    if run_in_tmux_dashboard(cmd, env):
        return

    # Fallback: run directly
    if sys.platform != 'win32':
        import signal
        old_handler = signal.getsignal(signal.SIGWINCH)
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
    try:
        subprocess.run(cmd, env=env)
    except KeyboardInterrupt:
        pass
    finally:
        if sys.platform != 'win32':
            signal.signal(signal.SIGWINCH, old_handler)



def kill_local_engine_processes(sig: int | None = None) -> int:
    """Signal run_xauby.py processes whose cwd is THIS checkout; return count.

    A bare ``pkill -f run_xauby.py`` also matches engines launched from other
    install roots (e.g. systemd tenant engines under /opt/xauby) — those belong
    to a different deployment and must never be killed from here.
    """
    import signal
    if sig is None:
        sig = signal.SIGTERM
    try:
        out = subprocess.run(
            ["pgrep", "-f", r"run_xauby\.py"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        pids = [int(p) for p in out.stdout.split()]
    except Exception:
        return 0
    root = os.path.realpath(os.getcwd())
    count = 0
    for pid in pids:
        try:
            if os.path.realpath(f"/proc/{pid}/cwd") == root:
                os.kill(pid, sig)
                count += 1
        except (OSError, ValueError):
            continue
    return count


def get_running_engine_pid():
    state_file = "core/logs/xauby_bot_state.json"
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("pid")
    except Exception:
        return None

def run_backtester_tui():
    """Live-aligned classic backtester entrypoint.

    This intentionally delegates to xauby.backtest.service so the launcher uses
    the same config resolver as Textual Backtest, Live, Replay and Optimizer.
    """
    W = max(35, get_terminal_width())
    cfg = _load_bot_yaml()
    print("\033[2J\033[H", end="")
    print_line(W)
    print(center_text(f"{BB_BG_AMBER} STRATEGY BACKTESTER {RESET}", W))
    print_line(W)
    symbol = _select_symbol_interactive(cfg, "Backtest symbol")
    default_strategy = _strategy_name_for_symbol_from_cfg(cfg, symbol)
    strategies = list_installed_strategies()
    print(f"\nCurrent config: {symbol} -> {GREEN}{default_strategy}{RESET}")
    for i, name in enumerate(strategies, 1):
        marker = " [config]" if name == default_strategy else ""
        print(f"  [{i}] {name}{marker}")
    try:
        choice = input(f" Strategy [Enter={default_strategy}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        return
    if choice.isdigit() and 1 <= int(choice) <= len(strategies):
        strategy_name = strategies[int(choice) - 1]
    elif choice in strategies:
        strategy_name = choice
    else:
        strategy_name = default_strategy

    try:
        from xauby.backtest.service import run_focused_backtest

        print(f"\n{YELLOW}[*] Running live-aligned backtest for {symbol} / {strategy_name}...{RESET}")
        result = run_focused_backtest(symbol, strategy_name=strategy_name, engine_config=cfg)
        if not result.meta.run_ok:
            print(f"{RED}[ERR] Backtest failed: {result.meta.error}{RESET}")
        else:
            stats = result.stats or {}
            print(f"\n{GREEN}[OK] Backtest complete{RESET}")
            print(f" Strategy: {result.meta.strategy_name}")
            print(f" Data:     {result.meta.data_symbol} ({'proxy' if result.meta.used_data_proxy else 'direct'})")
            print(f" Bars:     {result.meta.bars}")
            print(f" Net:      {float(stats.get('net_profit_pct', 0.0)):+.2f}%")
            print(f" PF:       {float(stats.get('profit_factor', 0.0)):.2f}")
            print(f" Win rate: {float(stats.get('win_rate', 0.0)):.2f}%")
            print(f" DD:       {float(stats.get('max_drawdown_pct', 0.0)):.2f}%")
            print(f" Trades:   {int(stats.get('total_trades', 0) or 0)}")
    except Exception as e:
        print(f"\n{RED}[ERR] Replay backtest failed: {e}{RESET}")

    input("\nPress Enter to return to main menu...")


def restart_bot_service(*, pause: bool = True, clear_screen: bool = True):
    W = get_terminal_width()
    if W < 35:
        W = 35

    if clear_screen:
        print("\033[2J\033[H", end="")
    print(f"{BB_CYAN}┌{'─' * (W - 2)}┐{RESET}")
    print(f"{BB_CYAN}│{RESET} {center_text(f'{BB_BG_AMBER} RESTART BOT ENGINE SERVICE {RESET}', W - 4)} {BB_CYAN}│{RESET}")
    print(f"{BB_CYAN}├{'─' * (W - 2)}┤{RESET}")
    
    is_linux = sys.platform.startswith("linux")
    has_systemd = False
    if is_linux:
        try:
            r = subprocess.run(["systemctl", "list-unit-files", "xauby.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if b"xauby.service" in r.stdout:
                has_systemd = True
        except Exception:
            pass
            
    if has_systemd:
        print_menu_row(center_text("Systemd Service detected: xauby.service", W - 4), W, BB_CYAN)
        print_menu_row(center_text("Restarting service...", W - 4), W, BB_CYAN)
        print(f"{BB_CYAN}└{'─' * (W - 2)}┘{RESET}\n")
        
        print(f"{YELLOW}[*] Executing systemctl restart xauby.service...{RESET}")
        try:
            r = subprocess.run(["systemctl", "restart", "xauby.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode != 0:
                r = subprocess.run(["sudo", "systemctl", "restart", "xauby.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
            if r.returncode == 0:
                print(f"{YELLOW}[*] Waiting for bot engine to report ONLINE...{RESET}")
                for i in range(10):
                    time.sleep(1.0)
                    chk = subprocess.run(["systemctl", "is-active", "xauby.service"], stdout=subprocess.PIPE, text=True)
                    state = chk.stdout.strip()
                    eng_status, _ = check_engine_status()
                    if state == "active" or eng_status == "RUNNING":
                        print(f"\n{GREEN}[✓] Service restarted successfully and is now ONLINE!{RESET}")
                        break
                    sys.stdout.write(f"\r  Checking status ({i+1}/10)... ")
                    sys.stdout.flush()
                else:
                    print(f"\n{YELLOW}[WARNING] Service restarted but status is: {state}. Check logs for details.{RESET}")
            else:
                err_msg = r.stderr.decode("utf-8", errors="ignore").strip()
                print(f"{RED}[ERR] Failed to restart service: {err_msg}{RESET}")
        except Exception as e:
            print(f"{RED}[ERR] Exception while restarting service: {e}{RESET}")
            
    else:
        print_menu_row(center_text("Local/Windows background process mode detected", W - 4), W, BB_CYAN)
        print_menu_row(center_text("Scanning for running engine process...", W - 4), W, BB_CYAN)
        print(f"{BB_CYAN}└{'─' * (W - 2)}┘{RESET}\n")
        
        pid = get_running_engine_pid()
        eng_status, is_sim = check_engine_status()
        
        try:
            with open("bot_config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            yaml_sim = cfg.get("simulate_only", True)
        except Exception:
            yaml_sim = True
            
        if eng_status == "RUNNING" and is_sim is not None:
            yaml_sim = is_sim
            
        if pid:
            print(f"{YELLOW}[*] Found running engine process (PID: {pid}). Terminating...{RESET}")
            import signal
            terminated = False
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(5):
                    time.sleep(1.0)
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        terminated = True
                        break
                if not terminated:
                    print(f"{YELLOW}[*] Process still active. Force killing (SIGKILL)...{RESET}")
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(1.0)
            except Exception as e:
                print(f"{YELLOW}[*] Process check/termination: {e}{RESET}")
        else:
            print(f"{YELLOW}[*] No active local PID file found. Cleaning stale engine runs...{RESET}")
            if sys.platform == 'win32':
                try:
                    subprocess.run('wmic process where "CommandLine like \'%run_xauby.py%\'" call terminate', stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
                except Exception:
                    pass
            else:
                try:
                    kill_local_engine_processes()
                except Exception:
                    pass
                    
        print(f"{YELLOW}[*] Spawning clean bot engine process...{RESET}")
        env = os.environ.copy()
        if yaml_sim:
            env["SIMULATE_ONLY"] = "true"
            cmd = [sys.executable, "run_xauby.py", "--simulate"]
            mode_str = "SIMULATED (Paper)"
        else:
            env["SIMULATE_ONLY"] = "false"
            env["BOT_READ_ONLY"] = "false"
            cmd = [sys.executable, "run_xauby.py", "--live"]
            mode_str = "LIVE TRADING"
            
        os.makedirs("core/logs", exist_ok=True)
        log_path = "core/logs/xauby_engine_bg.log"
        log_file = None
        try:
            log_file = open(log_path, "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
            print(f"{GREEN}[✓] Bot Engine restarted successfully (PID: {proc.pid}) in {mode_str} mode!{RESET}")
            print(f"Logs redirected to: {log_path}")
        except Exception as e:
            if log_file is not None:
                log_file.close()
            print(f"{RED}[ERR] Failed to spawn bot engine: {e}{RESET}")

    from xauby.runtime.exchange_switch import PENDING_PATH, rollback_pending_exchange_switch
    if os.path.exists(PENDING_PATH):
        for _ in range(20):
            if not os.path.exists(PENDING_PATH):
                break
            time.sleep(1.0)
        if os.path.exists(PENDING_PATH) and rollback_pending_exchange_switch():
            print(f"{RED}[ERR] New exchange failed health verification; config rolled back.{RESET}")
            restart_bot_service(pause=False, clear_screen=False)

    if pause:
        input("\nPress Enter to return to main menu...")
