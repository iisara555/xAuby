#!/usr/bin/env python3
"""Verify that the XAU D1 gate is actually live — by asking the running engine.

Three separate attempts to confirm this by hand failed on path guesses: the log
lived under a different root, config turned out to live in
/etc/xauby/tenants/<tenant>/ rather than the checkout, and the state file sits
under XAUBY_HOME/XAUBY_INSTANCE_ID. So this does not guess. It finds the running
engine process and reads `/proc/<pid>/environ` to learn where *that process*
believes its config and runtime data are, then reports two things:

  CONFIG  — what the resolver produces from the tenant files (intent)
  RUNTIME — what the engine last computed, from its state file (reality)

Both must agree, and RUNTIME is the one that counts. `D1: OFF` means the gate is
not active; `D1: UNKNOWN` means the 1d frame has not arrived yet (shorts are
blocked, which is fail-safe); a real zone (RED/GREEN/BLUE/…) means it is live.

Usage (on the VPS):
    sudo python3 scripts/verify_d1_gate.py
    sudo python3 scripts/verify_d1_gate.py --symbol XAUUSDT --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ENGINE_HINT = "run_xauby.py"
ZONES_LIVE = {"RED", "GREEN", "BLUE", "LBLUE", "YELLOW", "ORANGE"}


def find_engines() -> List[Dict[str, Any]]:
    """Locate running engine processes without shelling out."""
    found: List[Dict[str, Any]] = []
    if not os.path.isdir("/proc"):
        return found
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        if ENGINE_HINT not in cmdline:
            continue
        info: Dict[str, Any] = {"pid": pid, "cmdline": cmdline.strip(), "env": {}, "cwd": ""}
        try:
            info["cwd"] = os.path.realpath(f"/proc/{pid}/cwd")
        except Exception:
            pass
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                raw = fh.read().decode("utf-8", "replace")
            for item in raw.split("\0"):
                if "=" in item:
                    k, _, v = item.partition("=")
                    if k.startswith("XAUBY_"):
                        info["env"][k] = v
        except PermissionError:
            info["env_error"] = "permission denied reading /proc/<pid>/environ — run with sudo"
        except Exception as exc:
            info["env_error"] = str(exc)
        found.append(info)
    return found


def state_file_for(env: Dict[str, str], cwd: str) -> str:
    """Mirror xauby.runtime.paths: XAUBY_HOME[/XAUBY_INSTANCE_ID]/logs/..."""
    home = env.get("XAUBY_HOME") or "core"
    instance = env.get("XAUBY_INSTANCE_ID") or ""
    base = os.path.join(home, instance) if instance else home
    path = os.path.join(base, "logs", "xauby_bot_state.json")
    return path if os.path.isabs(path) else os.path.join(cwd or ".", path)


def snapshot_for_symbol(raw: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    by_symbol = raw.get("by_symbol") or {}
    if by_symbol:
        return dict(by_symbol.get(symbol) or {})
    if str(raw.get("symbol") or "").upper().replace("_", "") == symbol:
        return dict(raw)
    return {}


def d1_from_snapshot(snap: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(zone, where it was found). Zone is None when absent."""
    indicators = snap.get("indicators") or {}
    zone = indicators.get("cdc_zone_d1")
    if zone:
        return str(zone).upper(), "indicators.cdc_zone_d1"
    status = str((snap.get("signal_meta") or {}).get("status_summary") or "")
    if "D1:" in status:
        token = status.split("D1:", 1)[1].strip().split()[0].strip("|").strip()
        if token:
            return token.upper(), "signal_meta.status_summary"
    return None, ""


def resolve_config(config_dir: str, symbol: str) -> Dict[str, Any]:
    """Ask the real resolver what the tenant config means. Best-effort."""
    out: Dict[str, Any] = {"config_dir": config_dir}
    try:
        os.environ["XAUBY_CONFIG_DIR"] = config_dir
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from xauby.backtest.service import _prepare_backtest_config, resolve_strategy_name
        from xauby.observability.replay_validation import load_bot_config

        cfg = load_bot_config(os.path.join(config_dir, "bot_config.yaml"))
        name = resolve_strategy_name(symbol, cfg, None)
        _m, strat, _p, regime_tf, use_d1, _u = _prepare_backtest_config(symbol, name, cfg, None)
        shared = bool(strat.get("use_d1_regime_filter", False))

        def side(key: str) -> bool:
            value = strat.get(key)
            if value is None:
                return shared
            return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes", "on")

        out.update({
            "strategy": name,
            "use_d1_regime_filter": shared,
            "long_gated": side("use_d1_regime_filter_long"),
            "short_gated": side("use_d1_regime_filter_short"),
            "enable_short": bool(strat.get("enable_short")),
            "partial_tp_pct": strat.get("partial_tp_pct"),
            "regime_tf": regime_tf,
            "loads_1d": bool(use_d1),
        })
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="XAUUSDT")
    ap.add_argument("--state-file", default="", help="override state file discovery")
    ap.add_argument("--config-dir", default="", help="override tenant config dir")
    ap.add_argument("--max-age-sec", type=float, default=300.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    symbol = args.symbol.upper().replace("_", "")
    report: Dict[str, Any] = {"symbol": symbol}

    engines = find_engines()
    report["engines"] = [
        {k: e[k] for k in ("pid", "cwd", "env", "env_error") if k in e} for e in engines
    ]

    env: Dict[str, str] = {}
    cwd = ""
    if engines:
        env = engines[0].get("env") or {}
        cwd = engines[0].get("cwd") or ""

    config_dir = args.config_dir or env.get("XAUBY_CONFIG_DIR") or cwd or "."
    state_path = args.state_file or state_file_for(env, cwd)
    report["state_file"] = state_path

    cfg = resolve_config(config_dir, symbol)
    report["config"] = cfg

    runtime: Dict[str, Any] = {}
    if os.path.exists(state_path):
        try:
            runtime["age_sec"] = round(time.time() - os.path.getmtime(state_path), 1)
        except OSError:
            pass
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            snap = snapshot_for_symbol(raw, symbol)
            zone, where = d1_from_snapshot(snap)
            runtime.update({"d1_zone": zone, "source": where,
                            "pairs": raw.get("pairs") or list((raw.get("by_symbol") or {}).keys())})
        except Exception as exc:
            runtime["error"] = f"{type(exc).__name__}: {exc}"
    else:
        runtime["error"] = "state file not found"
    report["runtime"] = runtime

    if args.json:
        print(json.dumps(report, indent=2, default=str))

    # ---- verdict -----------------------------------------------------------
    problems: List[str] = []
    if not engines:
        problems.append("no running engine found (is it on this host?)")
    if len(engines) > 1:
        problems.append(f"{len(engines)} engines running — expected exactly one")
    if cfg.get("error"):
        problems.append(f"config did not resolve: {cfg['error']}")
    else:
        if not cfg.get("short_gated"):
            problems.append("CONFIG: shorts are NOT D1-gated")
        if cfg.get("long_gated"):
            problems.append("CONFIG: longs ARE D1-gated (expected ungated)")
        if not cfg.get("enable_short"):
            problems.append("CONFIG: shorts disabled — the gate would be moot")
        if not cfg.get("loads_1d"):
            problems.append("CONFIG: engine will not load 1d candles")
        if cfg.get("partial_tp_pct"):
            problems.append(f"CONFIG: partial_tp_pct={cfg['partial_tp_pct']} still set")

    zone = runtime.get("d1_zone")
    age = runtime.get("age_sec")
    if runtime.get("error"):
        problems.append(f"RUNTIME: {runtime['error']}")
    elif zone is None:
        problems.append("RUNTIME: no D1 zone in the state file yet")
    elif zone == "OFF":
        problems.append("RUNTIME: D1 reports OFF — the gate is not active")
    elif zone == "UNKNOWN":
        problems.append("RUNTIME: D1 is UNKNOWN — 1d frame not arrived yet "
                        "(shorts blocked, fail-safe; recheck in a few minutes)")
    if age is not None and age > args.max_age_sec:
        problems.append(f"RUNTIME: state file is {age:.0f}s old — engine may not be ticking")

    if not args.json:
        print(f"symbol        : {symbol}")
        for e in engines:
            print(f"engine        : pid {e['pid']} cwd {e.get('cwd', '?')}")
            if e.get("env_error"):
                print(f"                ! {e['env_error']}")
        print(f"config dir    : {cfg.get('config_dir')}")
        if cfg.get("error"):
            print(f"config        : ERROR {cfg['error']}")
        else:
            print(f"config        : shorts_gated={cfg.get('short_gated')} "
                  f"longs_gated={cfg.get('long_gated')} "
                  f"enable_short={cfg.get('enable_short')} loads_1d={cfg.get('loads_1d')}")
            print(f"                partial_tp_pct={cfg.get('partial_tp_pct')!r} "
                  f"regime_tf={cfg.get('regime_tf')!r}")
        print(f"state file    : {state_path}")
        print(f"runtime D1    : {zone!r}"
              + (f"  (from {runtime.get('source')})" if runtime.get("source") else "")
              + (f"  age {age}s" if age is not None else ""))
        print()
        if problems:
            print("RESULT: NOT CONFIRMED")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"RESULT: CONFIRMED — D1 gate is live, daily zone {zone}, "
                  f"shorts gated, longs free")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
