#!/usr/bin/env python3
"""Dead-man's switch: alert when the engine goes SILENT, not when it errors.

Roadmap P0.6.

Every alert path the bot has today is sent *by the engine*. A process cannot
report its own death, so the failure that matters most — the engine stopping,
hanging, being OOM-killed, or never coming back after a deploy — is precisely
the one that produces no notification at all. The existing
`xauby-healthcheck.timer` probes the public frontend and control-plane URLs, so
it stays green while the trading engine is stone dead, and it has no alert
channel: it exits non-zero into journald, where nobody is looking.

This runs as a separate one-shot process on a timer, reads the durable artifact
the engine touches every tick, and alerts on its own if that artifact goes
stale.

Deliberate design choices, each of which is load-bearing:

* **Standard library only. No `xauby` imports.** A checker that imports the
  package dies with the package. If a bad deploy breaks an import, an importing
  checker raises, systemd records a failure nobody reads, and the silence is
  indistinguishable from health — the exact failure mode this exists to catch.
  Paths and credentials come from arguments or the environment.
* **Direct synchronous POST**, not `TelegramNotifier`. That class queues onto a
  background thread; a one-shot process can exit before the queue flushes, so
  the alert is silently dropped.
* **Debounced with recovery.** A five-minute timer against a day-long outage
  would send ~288 messages. It alerts, then re-alerts only every
  `--realert-sec`, and sends one message when the engine comes back.
* **Exit code 1 while alerting**, so any external monitor that inspects the exit
  status sees the finding even if the Telegram send failed. Note the shipped
  unit sets `SuccessExitStatus=1` on purpose — a silent engine is a real finding,
  not a unit fault worth retrying, and the timer is the retry mechanism — so
  `systemctl list-units --failed` will *not* list it. Read the journal, or the
  marker file, to see the switch's own view.

Usage:
    scripts/deadman_switch.py --state-file /var/lib/xauby/runtime/owner-itsara/logs/xauby_bot_state.json
    scripts/deadman_switch.py --env-file /etc/xauby/tenants/owner-itsara/secrets.env --dry-run

Credentials: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The **environment wins
outright** when it carries both; only otherwise are the `--env-file` paths tried
in order, the first supplying both winning. Without them the check still runs and
still exits non-zero; it just cannot notify, and says so.

The unit passes them through `EnvironmentFile=`, which systemd reads as PID 1
before dropping to `User=xauby-control` — so /etc/xauby/deadman.env can stay
root:root 0600 and the service user never needs read access to a live bot token.
`--env-file` remains for manual runs, and an unreadable file there degrades to
"no credentials found" rather than killing the process (verified on the VPS,
2026-07-27).

**A watchdog must never source its credentials from an artifact the watched
process creates.** The engine's own Telegram credentials are materialized to
/run/xauby/credentials/<tenant>.env by the engine unit's ExecStartPre — which
means they exist only after the engine has started, on a tmpfs that a reboot
clears, at mode 0600 owned by another user. Pointing here at that file would
leave the switch unable to alert in exactly the case it exists for: host reboots,
engine never starts, no credentials file, silence. Give it its own persistent
file instead (deploy/systemd uses /etc/xauby/deadman.env).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_MAX_AGE_SEC = 300.0
DEFAULT_REALERT_SEC = 3600.0
TELEGRAM_TIMEOUT_SEC = 15


def parse_env_file(path: str) -> Dict[str, str]:
    """Read KEY=VALUE lines. Never sourced as shell — a token is data.

    An unreadable file yields ``{}``, not an exception. `os.path.isfile` only
    stats, so a credentials file owned by another user passes that check and
    then raises PermissionError on open — which would kill the watchdog every
    time the timer fired, and a watchdog that dies is indistinguishable from a
    healthy system. Degrading to "no credentials here" lets the caller warn.
    """
    out: Dict[str, str] = {}
    if not path or not os.path.isfile(path):
        return out
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] cannot read {path}: {exc}", file=sys.stderr)
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[key.strip()] = value
    return out


def pid_alive(pid: Optional[int]) -> Optional[bool]:
    """True/False if determinable, None if we cannot tell."""
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — that still counts as alive.
        return True
    except Exception:
        return None


def read_state(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """Return (parsed state, mtime). Either may be None."""
    if not os.path.exists(path):
        return None, None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), mtime
    except Exception:
        # A truncated or half-written state file is itself a symptom, but the
        # mtime is still the freshness signal we care about.
        return None, mtime


def send_telegram(token: str, chat_id: str, text: str) -> Tuple[bool, str]:
    """One synchronous POST. Returns (ok, detail)."""
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT_SEC) as resp:
            if 200 <= resp.status < 300:
                return True, "sent"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def load_marker(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_marker(path: str, data: Dict[str, Any]) -> None:
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except Exception as exc:  # never let bookkeeping suppress the alert
        print(f"[WARN] could not persist marker {path}: {exc}", file=sys.stderr)


def fmt_age(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def build_alert(
    *, label: str, state: Optional[Dict[str, Any]], age: Optional[float],
    max_age: float, state_path: str, alive: Optional[bool],
) -> str:
    lines = [f"🔴 xAuby DEAD-MAN'S SWITCH — {label}"]
    if age is None:
        lines.append(f"No engine state file at {state_path}")
    else:
        lines.append(f"Engine silent for {fmt_age(age)} (threshold {fmt_age(max_age)})")
    if state:
        pid = state.get("pid")
        lines.append(f"pid={pid} alive={'yes' if alive else 'no' if alive is False else 'unknown'}")
        for key, name in (("symbol", "symbol"), ("current_price", "last price"),
                          ("run_id", "run_id"), ("timestamp", "engine ts")):
            if state.get(key) is not None:
                lines.append(f"{name}: {state.get(key)}")
        if state.get("simulate_only") is not None:
            lines.append(f"mode: {'SIM' if state.get('simulate_only') else 'LIVE'}")
    lines.append("")
    lines.append("The engine is not ticking. Positions may be unmanaged.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--state-file", default=os.environ.get("XAUBY_STATE_FILE", ""),
                    help="engine state JSON (default: $XAUBY_STATE_FILE, else "
                         "$XAUBY_HOME/logs/xauby_bot_state.json, else core/logs/...)")
    ap.add_argument("--max-age-sec", type=float,
                    default=float(os.environ.get("XAUBY_DEADMAN_MAX_AGE", DEFAULT_MAX_AGE_SEC)),
                    help=f"silence threshold in seconds (default {DEFAULT_MAX_AGE_SEC:g})")
    ap.add_argument("--realert-sec", type=float, default=DEFAULT_REALERT_SEC,
                    help=f"minimum gap between repeat alerts (default {DEFAULT_REALERT_SEC:g})")
    ap.add_argument("--marker", default="", help="debounce state file")
    ap.add_argument("--env-file", action="append", default=None,
                    help="KEY=VALUE file holding TELEGRAM_BOT_TOKEN / "
                         "TELEGRAM_CHAT_ID. Repeatable: the first file that "
                         "supplies both wins, so a persistent watchdog-owned "
                         "file can be listed ahead of any fallback.")
    ap.add_argument("--label", default=os.environ.get("XAUBY_INSTANCE_ID", "engine"),
                    help="tenant/instance name shown in the alert")
    ap.add_argument("--dry-run", action="store_true", help="never send; print instead")
    args = ap.parse_args()

    state_path = args.state_file
    if not state_path:
        home = os.environ.get("XAUBY_HOME") or "core"
        instance = os.environ.get("XAUBY_INSTANCE_ID") or ""
        base = os.path.join(home, instance) if instance else home
        state_path = os.path.join(base, "logs", "xauby_bot_state.json")

    marker_path = args.marker or f"{state_path}.deadman"

    env_files = args.env_file if args.env_file is not None else [
        f for f in [os.environ.get("XAUBY_DEADMAN_ENV", "")] if f
    ]
    env = dict(os.environ)
    # The environment wins outright when it already carries both keys. The unit
    # delivers them via EnvironmentFile=, which systemd reads as PID 1 *before*
    # dropping to User=, so the credentials file never has to be readable by the
    # service user at all. Letting a fallback file override that would quietly
    # move the watchdog back onto the engine's own credentials.
    creds_from = ""
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        creds_from = "environment"
    else:
        for candidate in env_files:
            parsed = parse_env_file(candidate)
            if parsed.get("TELEGRAM_BOT_TOKEN") and parsed.get("TELEGRAM_CHAT_ID"):
                env.update(parsed)
                creds_from = candidate
                break
            env.update({k: v for k, v in parsed.items()
                        if k not in ("TELEGRAM_BOT_TOKEN",)})
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        print(
            "[WARN] no Telegram credentials found in "
            f"{env_files or 'the environment'} — this check can DETECT silence "
            "but cannot NOTIFY anyone about it",
            file=sys.stderr,
        )

    state, mtime = read_state(state_path)
    now = time.time()
    age = (now - mtime) if mtime is not None else None
    alive = pid_alive((state or {}).get("pid"))

    # Missing state file counts as silent: a checkout that never started, a wrong
    # path after a deploy, and a crashed engine are all "not ticking".
    stale = age is None or age > args.max_age_sec
    marker = load_marker(marker_path)
    was_alerting = bool(marker.get("alerting"))
    last_alert = float(marker.get("last_alert_ts") or 0.0)

    if not stale:
        print(f"[OK] {args.label}: engine ticking, state age {fmt_age(age or 0)} "
              f"(threshold {fmt_age(args.max_age_sec)})")
        if was_alerting:
            text = (f"✅ xAuby recovered — {args.label}\n"
                    f"Engine is ticking again (state age {fmt_age(age or 0)}).")
            if args.dry_run:
                print(f"[DRY-RUN] would send recovery:\n{text}")
            else:
                ok, detail = send_telegram(token, chat_id, text)
                print(f"[INFO] recovery notice: {detail}")
            save_marker(marker_path, {"alerting": False, "last_alert_ts": last_alert,
                                      "last_ok_ts": now})
        else:
            save_marker(marker_path, {"alerting": False, "last_alert_ts": last_alert,
                                      "last_ok_ts": now})
        return 0

    label_reason = "no state file" if age is None else f"silent {fmt_age(age)}"
    print(f"[ALERT] {args.label}: {label_reason} (threshold {fmt_age(args.max_age_sec)}) "
          f"state={state_path}", file=sys.stderr)

    should_send = (not was_alerting) or (now - last_alert >= args.realert_sec)
    if should_send:
        text = build_alert(label=args.label, state=state, age=age,
                           max_age=args.max_age_sec, state_path=state_path, alive=alive)
        if args.dry_run:
            print(f"[DRY-RUN] would send:\n{text}")
            sent = True
        else:
            sent, detail = send_telegram(token, chat_id, text)
            print(f"[INFO] alert: {detail}", file=sys.stderr)
        save_marker(marker_path, {"alerting": True,
                                  "last_alert_ts": now if sent else last_alert,
                                  "last_ok_ts": marker.get("last_ok_ts")})
    else:
        print(f"[INFO] alert suppressed, next in "
              f"{fmt_age(args.realert_sec - (now - last_alert))}", file=sys.stderr)
        save_marker(marker_path, {**marker, "alerting": True})

    # Non-zero so systemd marks the unit failed and any external monitor sees it,
    # independent of whether the notification got through.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
