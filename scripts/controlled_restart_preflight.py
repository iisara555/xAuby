#!/usr/bin/env python3
"""Read-only preflight before restarting the live engine.

Exit codes:
  0 = safe enough to restart
  1 = unsafe (open orders / asset balances / ambiguous live state)
  2 = unable to verify exchange state
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

# Executing ``scripts/foo.py`` puts only ``scripts/`` first on sys.path.  The
# VPS also has an old editable install, so preflight could silently import a
# different release than the one being staged.  Pin imports to this checkout.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
while REPO_ROOT in sys.path:
    sys.path.remove(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

from xauby.api import create_exchange_client
from xauby.api.interface import IExchangeGateway
from xauby.database.db import LiteDB
from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.exchange_config import (
    credential_env_names,
    resolve_exchange_credentials,
    resolve_quote_asset,
)
from xauby.runtime.pair_registry import PairRegistry
from xauby.runtime.paths import bot_state_path, config_file, config_root

MATERIALIZED_CREDENTIAL_ROOT = "/run/xauby/credentials"


def _load_dotenv(path: str | None = None) -> None:
    if path is None:
        instance = str(os.environ.get("XAUBY_INSTANCE_ID") or "").strip()
        runtime_credentials = (
            os.path.join(MATERIALIZED_CREDENTIAL_ROOT, f"{instance}.env")
            if instance else ""
        )
        tenant_secrets = config_file("secrets.env")
        path = next(
            (
                candidate for candidate in (
                    runtime_credentials,
                    tenant_secrets,
                    config_file(".env"),
                )
                if candidate and os.path.exists(candidate)
            ),
            tenant_secrets,
        )
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_state() -> Dict[str, Any]:
    try:
        with open(bot_state_path(), "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _state_value(state: Dict[str, Any], key: str, default: Any = None) -> Any:
    aggregate = state.get("aggregate") or {}
    if key in state:
        return state.get(key)
    return aggregate.get(key, default)


def _base_asset(symbol: str, quote: str = "USDT") -> str:
    sym = str(symbol or "").upper().replace("_", "")
    return sym[: -len(quote)] if quote and sym.endswith(quote) else sym


def _symbol(value: Any) -> str:
    return (
        str(value or "")
        .split(":", 1)[0]
        .upper()
        .replace("/", "")
        .replace("_", "")
        .replace("-", "")
    )


def _order_id(order: Any) -> str:
    if isinstance(order, dict):
        for key in ("orderId", "order_id", "id", "clientOrderId", "client_id"):
            val = order.get(key)
            if val is not None:
                return str(val)
    return ""


def _open_position_from_snapshot(snapshot: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    pos = snapshot.get("position") or {}
    if pos.get("state") != "bought":
        return {}
    return {
        "symbol": symbol,
        "state": "bought",
        "quantity": float(pos.get("quantity", 0.0) or 0.0),
        "stop_loss_order_id": pos.get("stop_loss_order_id"),
    }


def _load_tracked_positions(pairs: List[Any], state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    tracked: Dict[str, Dict[str, Any]] = {}
    try:
        db = LiteDB(readonly=True)
        for pair in pairs:
            pos = db.get_trade_state(pair.symbol)
            if pos.get("state") == "bought":
                tracked[pair.symbol] = {
                    "symbol": pair.symbol,
                    "state": "bought",
                    "quantity": float(pos.get("quantity", 0.0) or 0.0),
                    "position_side": str(
                        pos.get("position_side") or "LONG"
                    ).upper(),
                    "entry_price": float(pos.get("entry_price", 0.0) or 0.0),
                    "exchange_position_id": pos.get("exchange_position_id"),
                    "stop_loss_order_id": pos.get("stop_loss_order_id"),
                }
    except Exception:
        by_symbol = state.get("by_symbol") or {}
        for pair in pairs:
            snap = by_symbol.get(pair.symbol)
            if isinstance(snap, dict):
                pos = _open_position_from_snapshot(snap, pair.symbol)
                if pos:
                    tracked[pair.symbol] = pos
        if not tracked and isinstance(state.get("position"), dict):
            symbol = str(state.get("symbol") or "").upper().replace("_", "")
            if symbol:
                pos = _open_position_from_snapshot(state, symbol)
                if pos:
                    tracked[symbol] = pos
    return tracked


def _qty_tolerance(client: IExchangeGateway, symbol: str) -> float:
    try:
        filters = client.get_symbol_filters(symbol)
        min_qty = float(filters.get("minQty") or 0.0)
        step_size = float(filters.get("stepSize") or 0.0001)
        return max(min_qty, step_size, 1e-12)
    except Exception:
        return 1e-8


def _price_tolerance(client: IExchangeGateway, symbol: str, price: float) -> float:
    try:
        filters = client.get_symbol_filters(symbol)
        tick_size = float(filters.get("tickSize") or 0.0)
    except Exception:
        tick_size = 0.0
    return max(tick_size, abs(float(price or 0.0)) * 1e-6, 1e-8)


def _derivative_positions(
    client: IExchangeGateway,
    pairs: List[Any],
    tracked_positions: Dict[str, Dict[str, Any]],
    *,
    allow_tracked_positions: bool,
) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Fail-closed comparison of exchange swap positions to durable state."""
    if not hasattr(client, "get_positions"):
        return {}, ["exchange adapter cannot verify derivative positions"]
    try:
        # Account-wide query is intentional: restricting to configured pairs
        # cannot reveal an orphan position on a third instrument.
        rows = client.get_positions() or []
    except Exception as exc:
        return {}, [
            f"derivative position verification failed: {exc.__class__.__name__}: {exc}"
        ]

    allowed_symbols = {_symbol(pair.symbol) for pair in pairs}
    live_by_symbol: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []
    for row in rows:
        symbol = _symbol(row.get("symbol"))
        quantity = abs(float(row.get("quantity") or row.get("contracts") or 0.0))
        if quantity <= 0:
            continue
        if not symbol or symbol not in allowed_symbols:
            reasons.append(
                f"exchange has untracked derivative position {symbol or 'UNKNOWN'} qty={quantity}"
            )
            continue
        if symbol in live_by_symbol:
            reasons.append(f"{symbol} has multiple exchange derivative positions")
            continue
        live_by_symbol[symbol] = {
            "symbol": symbol,
            "position_side": str(row.get("position_side") or "").upper(),
            "quantity": quantity,
            "entry_price": float(row.get("entry_price") or 0.0),
            "exchange_position_id": row.get("exchange_position_id"),
        }

    pair_symbols = {_symbol(pair.symbol) for pair in pairs}
    for symbol in sorted(pair_symbols):
        tracked = tracked_positions.get(symbol)
        live = live_by_symbol.get(symbol)
        if not allow_tracked_positions:
            if live:
                reasons.append(f"{symbol} has an open derivative position")
            continue
        if tracked and not live:
            reasons.append(f"{symbol} is tracked locally but missing on exchange")
            continue
        if live and not tracked:
            reasons.append(f"{symbol} exists on exchange but is not tracked locally")
            continue
        if not tracked or not live:
            continue

        tracked_side = str(tracked.get("position_side") or "").upper()
        if tracked_side not in {"LONG", "SHORT"} or live["position_side"] != tracked_side:
            reasons.append(
                f"{symbol} side mismatch: exchange={live['position_side'] or 'UNKNOWN'} "
                f"tracked={tracked_side or 'UNKNOWN'}"
            )
        expected_qty = abs(float(tracked.get("quantity") or 0.0))
        if abs(live["quantity"] - expected_qty) > _qty_tolerance(client, symbol):
            reasons.append(
                f"{symbol} quantity mismatch: exchange={live['quantity']} tracked={expected_qty}"
            )
        expected_entry = float(tracked.get("entry_price") or 0.0)
        live_entry = float(live.get("entry_price") or 0.0)
        if expected_entry <= 0 or live_entry <= 0:
            reasons.append(
                f"{symbol} entry price is unavailable: exchange={live_entry} tracked={expected_entry}"
            )
        elif abs(live_entry - expected_entry) > _price_tolerance(
            client, symbol, expected_entry
        ):
            reasons.append(
                f"{symbol} entry mismatch: exchange={live_entry} tracked={expected_entry}"
            )
        tracked_position_id = str(tracked.get("exchange_position_id") or "")
        live_position_id = str(live.get("exchange_position_id") or "")
        if (
            tracked_position_id
            and live_position_id
            and live_position_id != tracked_position_id
        ):
            reasons.append(
                f"{symbol} position id mismatch: exchange={live_position_id} "
                f"tracked={tracked_position_id}"
            )
    return live_by_symbol, reasons


def run_preflight(
    *,
    require_exchange: bool = True,
    allow_tracked_positions: bool = True,
    credentials_env: str | None = None,
) -> Dict[str, Any]:
    _load_dotenv(credentials_env)
    cfg_path = config_file("bot_config.yaml")
    cfg = load_bot_config(cfg_path)
    state = _load_state()
    registry = PairRegistry(
        cfg,
        config_path=cfg_path,
        project_root=config_root(),
        read_only=True,
    )
    pairs = registry.load(None)
    quote = str(((cfg.get("portfolio") or {}).get("quote_asset")) or resolve_quote_asset(cfg)).upper()
    market_type = str((cfg.get("exchange") or {}).get("market_type") or "spot").lower()
    is_derivative = market_type == "swap"
    tracked_positions = _load_tracked_positions(pairs, state)

    report: Dict[str, Any] = {
        "safe_to_restart": False,
        "exchange_checked": False,
        "allow_tracked_positions": allow_tracked_positions,
        "reasons": [],
        "state": {
            "pid": _state_value(state, "pid"),
            "run_id": _state_value(state, "run_id"),
            "simulate_only": _state_value(state, "simulate_only"),
            "read_only": _state_value(state, "read_only"),
            "open_positions": _state_value(state, "open_positions"),
        },
        "pairs": [p.symbol for p in pairs],
        "tracked_positions": tracked_positions,
        "exchange_positions": {},
        "open_orders": {},
        "balances": {},
    }

    api_key, api_secret, base_url = resolve_exchange_credentials(cfg)
    if not api_key or not api_secret:
        key_env, secret_env, _ = credential_env_names(cfg)
        report["reasons"].append(f"missing {key_env}/{secret_env}; cannot verify exchange")
        if require_exchange:
            return report

    if api_key and api_secret:
        try:
            client = create_exchange_client(cfg, api_key, api_secret, base_url)
            balances = client.get_balances()
            report["exchange_checked"] = True
            if is_derivative:
                try:
                    account_orders = client.get_open_orders() or []
                except TypeError:
                    account_orders = []
                    report["reasons"].append(
                        "exchange adapter cannot query account-wide open orders"
                    )
                allowed_symbols = {_symbol(pair.symbol) for pair in pairs}
                for order in account_orders:
                    order_symbol = _symbol(order.get("symbol"))
                    if order_symbol not in allowed_symbols:
                        report["reasons"].append(
                            f"exchange has untracked open order on "
                            f"{order_symbol or 'UNKNOWN'}"
                        )
                live_positions, position_reasons = _derivative_positions(
                    client,
                    pairs,
                    tracked_positions,
                    allow_tracked_positions=allow_tracked_positions,
                )
                report["exchange_positions"] = live_positions
                report["reasons"].extend(position_reasons)
            for pair in pairs:
                orders = client.get_open_orders(pair.symbol)
                if orders:
                    report["open_orders"][pair.symbol] = orders
                    tracked_sl_id = str(
                        (tracked_positions.get(pair.symbol) or {}).get("stop_loss_order_id") or ""
                    )
                    untracked_orders = [
                        order for order in orders if not tracked_sl_id or _order_id(order) != tracked_sl_id
                    ]
                    if not allow_tracked_positions:
                        report["reasons"].append(f"{pair.symbol} has {len(orders)} open orders")
                    elif untracked_orders:
                        report["reasons"].append(
                            f"{pair.symbol} has {len(untracked_orders)} untracked open orders"
                        )
                base = _base_asset(pair.symbol, quote)
                bal = balances.get(base) or {}
                qty = float(bal.get("available", 0.0) or 0.0) + float(bal.get("reserved", 0.0) or 0.0)
                if qty > 0:
                    report["balances"][base] = qty
                    if is_derivative:
                        tol = _qty_tolerance(client, pair.symbol)
                        if qty > tol:
                            report["reasons"].append(
                                f"{base} spot balance is non-zero ({qty}) on derivative account"
                            )
                        continue
                    tracked = tracked_positions.get(pair.symbol) if allow_tracked_positions else None
                    expected_qty = float((tracked or {}).get("quantity", 0.0) or 0.0)
                    if not tracked:
                        tol = _qty_tolerance(client, pair.symbol)
                        if qty > tol:
                            report["reasons"].append(f"{base} balance is non-zero ({qty}) with no tracked position")
                    elif abs(expected_qty - qty) > _qty_tolerance(client, pair.symbol):
                        report["reasons"].append(
                            f"{base} balance {qty} does not match tracked quantity {expected_qty}"
                        )
        except Exception as exc:
            report["reasons"].append(f"exchange verification failed: {exc.__class__.__name__}: {exc}")
            return report
        finally:
            try:
                client.close()
            except Exception:
                pass

    state_open = _state_value(state, "open_positions")
    if state_open not in (0, "0", None) and not allow_tracked_positions:
        report["reasons"].append(f"state reports open_positions={state_open}")
    if state_open is None and not report["exchange_checked"]:
        report["reasons"].append("state open_positions is unknown and exchange was not checked")
    if allow_tracked_positions and state_open not in (None, "0", 0):
        try:
            if int(state_open) > len(tracked_positions):
                report["reasons"].append(
                    f"state reports open_positions={state_open} but only {len(tracked_positions)} tracked positions were found"
                )
        except (TypeError, ValueError):
            report["reasons"].append(f"state reports invalid open_positions={state_open}")

    report["safe_to_restart"] = not report["reasons"]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-no-exchange", action="store_true", help="Do not fail solely because API keys are missing.")
    ap.add_argument(
        "--credentials-env",
        default=None,
        help=(
            "credential env file (defaults to the materialized tenant file "
            "when XAUBY_INSTANCE_ID is set)"
        ),
    )
    ap.add_argument(
        "--no-open-positions",
        action="store_true",
        help="Require zero open positions, matching the old conservative restart policy.",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = ap.parse_args()
    report = run_preflight(
        require_exchange=not args.allow_no_exchange,
        allow_tracked_positions=not args.no_open_positions,
        credentials_env=args.credentials_env,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        status = "SAFE" if report["safe_to_restart"] else "NOT SAFE"
        print(f"Controlled restart preflight: {status}")
        for reason in report.get("reasons") or []:
            print(f" - {reason}")
        if not report.get("reasons"):
            tracked = report.get("tracked_positions") or {}
            if tracked:
                print(f" - tracked open positions allowed: {', '.join(sorted(tracked))}")
            else:
                print(" - no open orders/balances detected")
    if report["safe_to_restart"]:
        return 0
    return 1 if report.get("exchange_checked") or args.allow_no_exchange else 2


if __name__ == "__main__":
    raise SystemExit(main())
