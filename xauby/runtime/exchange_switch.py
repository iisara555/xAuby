"""Staged, fail-closed exchange switching preflight."""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from xauby.utils.atomic_io import atomic_json_write

PENDING_PATH = "core/exchange-switch-pending.json"
BACKUP_PATH = "core/bot_config.exchange-switch.bak"


@dataclass
class ExchangePreflightResult:
    ok: bool
    checks: Dict[str, bool] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


def stage_exchange_config(config: Dict[str, Any], path: str = "core/exchange-switch-staged.json") -> str:
    atomic_json_write(path, config, indent=2)
    return path


def clear_staged_exchange(path: str = "core/exchange-switch-staged.json") -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def mark_exchange_activation(config_path: str = "bot_config.yaml") -> None:
    os.makedirs(os.path.dirname(BACKUP_PATH), exist_ok=True)
    shutil.copy2(config_path, BACKUP_PATH)
    atomic_json_write(PENDING_PATH, {"config_path": config_path, "started_at": time.time()})


def finalize_exchange_activation() -> None:
    for path in (PENDING_PATH, BACKUP_PATH):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def rollback_pending_exchange_switch() -> bool:
    if not os.path.exists(PENDING_PATH) or not os.path.exists(BACKUP_PATH):
        return False
    with open(PENDING_PATH, "r", encoding="utf-8") as handle:
        config_path = str((json.load(handle) or {}).get("config_path") or "bot_config.yaml")
    tmp = config_path + ".rollback.tmp"
    shutil.copy2(BACKUP_PATH, tmp)
    os.replace(tmp, config_path)
    finalize_exchange_activation()
    return True


def verify_pending_exchange_activation(engine: Any, timeout: float = 12.0) -> bool:
    if not os.path.exists(PENDING_PATH):
        return True
    expected = str((engine.config.get("exchange") or {}).get("ccxt_id")
                   or (engine.config.get("exchange") or {}).get("name") or "binance").lower()
    for symbol in engine._pair_registry.active_symbols():
        identity = engine.client.market_identity(symbol) if hasattr(engine.client, "market_identity") else None
        if identity is not None and identity.exchange.lower() != expected:
            return False
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        ready = True
        for symbol in engine._pair_registry.active_symbols():
            sc = engine._sc(symbol)
            if float(sc.get_tick_snapshot().get("last") or 0.0) <= 0:
                ready = False
                break
            required = {"candle", "trades", "order_book"}
            if engine.ws is not None and not required.issubset(set(sc.market_data)):
                ready = False
                break
        if ready:
            finalize_exchange_activation()
            return True
        time.sleep(0.1)
    return False


def run_exchange_preflight(
    candidate: Dict[str, Any],
    symbols: List[str],
    *,
    current_client: Any = None,
    client_factory: Optional[Callable[..., Any]] = None,
    websocket_factory: Optional[Callable[..., Any]] = None,
    timeout: float = 12.0,
) -> ExchangePreflightResult:
    checks: Dict[str, bool] = {}
    errors: List[str] = []
    client = None
    ws = None
    try:
        if current_client is not None:
            positions = current_client.get_positions(symbols) if hasattr(current_client, "get_positions") else []
            orders = current_client.get_open_orders() if hasattr(current_client, "get_open_orders") else []
            checks["old_exchange_flat"] = not positions and not orders
            if not checks["old_exchange_flat"]:
                errors.append("old exchange still has an open position or order")

        from xauby.runtime.derivatives_config import derivatives_settings
        settings = derivatives_settings(candidate)
        checks["derivatives_config"] = True
        if client_factory is None:
            from xauby.api import create_exchange_client
            client_factory = create_exchange_client
        from xauby.runtime.exchange_config import resolve_exchange_credentials
        key, secret, base_url = resolve_exchange_credentials(candidate)
        client = client_factory(candidate, key, secret, base_url)
        caps = getattr(client, "capabilities", {}) or {}
        required_caps = ("swap", "positions", "reduce_only") if settings["market_type"] == "swap" else ()
        checks["capabilities"] = all(caps.get(v) for v in required_caps)
        if not checks["capabilities"]:
            errors.append("candidate adapter lacks required derivatives capabilities")
        checks["account"] = isinstance(client.get_balances(), dict)
        info = client.get_exchange_info()
        available = {str(v.get("symbol") or "").upper() for v in info.get("symbols", [])}
        checks["markets"] = all(s.upper() in available for s in symbols)
        if not checks["markets"]:
            errors.append("one or more configured symbols are unavailable on candidate market")
        for symbol in symbols:
            checks[f"ticker:{symbol}"] = float(client.get_ticker(symbol).get("last") or 0.0) > 0
            checks[f"candles:{symbol}"] = bool(client.get_candles(symbol, "1m", limit=2))
            checks[f"trades:{symbol}"] = isinstance(client.get_recent_trades(symbol, 2), list)
            book = client.get_order_book(symbol, 5)
            checks[f"book:{symbol}"] = bool(book.get("bids")) and bool(book.get("asks"))
        if settings["market_type"] == "swap":
            checks["positions"] = isinstance(client.get_positions(symbols), list)
            for symbol in symbols:
                checks[f"funding:{symbol}"] = isinstance(client.get_funding_rate(symbol), dict)

        if websocket_factory is None:
            from xauby.api import create_exchange_websocket
            websocket_factory = create_exchange_websocket
        got_tick = set()
        got_market: Dict[str, set[str]] = {s: set() for s in symbols}
        ws = websocket_factory(
            candidate, symbols,
            lambda event: got_tick.add(str(event.get("symbol") or "").upper()),
            on_market=lambda event: got_market.setdefault(str(event.get("symbol") or "").upper(), set()).add(str(event.get("kind") or "")),
        )
        checks["websocket_available"] = ws is not None
        if ws is not None:
            ws.start()
            deadline = time.time() + max(1.0, timeout)
            required = {"candle", "trades", "order_book"}
            while time.time() < deadline:
                if all(s in got_tick and required.issubset(got_market.get(s, set())) for s in symbols):
                    break
                time.sleep(0.1)
            checks["websocket_channels"] = all(
                s in got_tick and required.issubset(got_market.get(s, set())) for s in symbols
            )
            if not checks["websocket_channels"]:
                errors.append("candidate websocket did not produce ticker/candle/trades/order-book samples")
    except Exception as exc:
        errors.append(f"{exc.__class__.__name__}: {exc}")
    finally:
        for obj in (ws, client):
            try:
                if obj is not None and hasattr(obj, "stop"):
                    obj.stop()
                elif obj is not None and hasattr(obj, "close"):
                    obj.close()
            except Exception:
                pass
    failed = [name for name, ok in checks.items() if not ok]
    errors.extend(f"failed check: {name}" for name in failed if f"failed check: {name}" not in errors)
    return ExchangePreflightResult(ok=bool(checks) and not errors, checks=checks, errors=errors)
