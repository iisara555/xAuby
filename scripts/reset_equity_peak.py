#!/usr/bin/env python3
"""Reset the drawdown-guard equity peak to the latest runtime equity.

Default mode is dry-run. Use --apply only after confirming the reported scope
and equity match the account you intend to reset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Any, Dict

from dotenv import load_dotenv

from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.exchange_config import resolve_exchange_credentials
from xauby.runtime.paths import bot_state_path, config_root, env_file, equity_peak_path
from xauby.utils.atomic_io import atomic_json_write


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _account_fingerprint(api_key: str) -> str:
    key = (api_key or "").strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key else ""


def _exchange_id(cfg: Dict[str, Any]) -> str:
    exchange_cfg = cfg.get("exchange") or {}
    if not isinstance(exchange_cfg, dict):
        return ""
    return str(
        exchange_cfg.get("ccxt_id")
        or exchange_cfg.get("name")
        or exchange_cfg.get("provider")
        or ""
    ).strip().lower()


def _state_equity(state: Dict[str, Any]) -> float:
    aggregate = state.get("aggregate") or {}
    raw = aggregate.get("total_equity_usdt", state.get("total_equity_usdt", 0.0))
    return float(raw or 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--mode", choices=("live", "sim"), default=None)
    parser.add_argument("--equity", type=float, default=None, help="Override latest state equity.")
    parser.add_argument("--apply", action="store_true", help="Write equity_peak.json. Default is dry-run.")
    args = parser.parse_args()

    dotenv_path = env_file()
    load_dotenv(dotenv_path if os.path.exists(dotenv_path) else None)

    cfg_path = args.config
    if not os.path.isabs(cfg_path) and os.environ.get("XAUBY_CONFIG_DIR"):
        cfg_path = os.path.join(config_root(), cfg_path)
    cfg = load_bot_config(cfg_path)
    state = _load_json(bot_state_path())

    mode = args.mode
    if mode is None:
        state_mode = str(state.get("execution_mode") or "").lower()
        if state_mode in {"live", "sim"}:
            mode = state_mode
        else:
            simulate_only = bool(state.get("simulate_only", cfg.get("simulate_only", True)))
            mode = "sim" if simulate_only else "live"

    equity = args.equity if args.equity is not None else _state_equity(state)
    if equity <= 0:
        raise SystemExit("Refusing to reset: equity is not positive. Pass --equity explicitly if needed.")

    api_key, _, _ = resolve_exchange_credentials(cfg)
    account_fp = _account_fingerprint(api_key)
    exchange_id = _exchange_id(cfg)
    if mode == "live" and account_fp:
        scope = ":".join(part for part in ("live", exchange_id, account_fp) if part)
    else:
        scope = mode

    path = equity_peak_path()
    data = _load_json(path)
    peaks = data.get("peaks")
    if not isinstance(peaks, dict):
        peaks = {}
    old_scope_peak = float(peaks.get(scope, 0.0) or 0.0)
    old_legacy_peak = float(data.get("peak", 0.0) or 0.0)

    print(f"Equity peak file: {path}")
    print(f"Scope: {scope}")
    print(f"Current scope peak: {old_scope_peak:.8f}")
    print(f"Current legacy peak: {old_legacy_peak:.8f}")
    print(f"New peak: {equity:.8f}")

    if not args.apply:
        print("Dry run only. Re-run with --apply to write equity_peak.json.")
        return 0

    peaks[scope] = float(equity)
    if mode in {"live", "sim"}:
        peaks[mode] = float(equity)
    data["peaks"] = peaks
    data["peak"] = float(equity)
    atomic_json_write(path, data, indent=None)
    print("Updated equity peak.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
