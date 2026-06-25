#!/usr/bin/env python3
"""Reduce a live spot position by a fraction and re-protect the remainder."""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

from xauby.api.utils import make_client_id, round_step
from xauby.engine.trading import LiteTradingEngine


def _balance_total(raw_balances: dict, asset: str) -> tuple[float, float, float]:
    raw = raw_balances.get(asset.upper(), {})
    if isinstance(raw, dict):
        available = float(raw.get("available", 0.0) or 0.0)
        reserved = float(raw.get("reserved", 0.0) or 0.0)
        return available, reserved, available + reserved
    total = float(raw or 0.0)
    return total, 0.0, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--fraction", type=float, default=0.5)
    parser.add_argument("--config", default="bot_config.yaml")
    args = parser.parse_args()

    symbol = args.symbol.upper().replace("_", "")
    if not (0.0 < args.fraction < 1.0):
        raise SystemExit("--fraction must be between 0 and 1")

    os.environ["SIMULATE_ONLY"] = "false"
    os.environ["BOT_READ_ONLY"] = "false"

    engine = LiteTradingEngine(config_path=args.config)
    state = engine.db.get_trade_state(symbol)
    if state.get("state") != "bought":
        raise SystemExit(f"{symbol}: DB state is {state.get('state')!r}, not bought")

    original_qty = float(state.get("quantity", 0.0) or 0.0)
    if original_qty <= 0:
        raise SystemExit(f"{symbol}: DB quantity is {original_qty}")

    ticker = engine.client.get_ticker(symbol)
    price = float(ticker.get("last") or ticker.get("price") or ticker.get("bid") or 0.0)
    if price <= 0:
        raise SystemExit(f"{symbol}: unable to fetch current ticker price")

    filters = engine.client.get_symbol_filters(symbol)
    step = float(filters.get("stepSize") or 0.0)
    min_qty = float(filters.get("minQty") or 0.0)
    min_notional = float(filters.get("minNotional") or 0.0)
    sell_qty = round_step(original_qty * args.fraction, step) if step > 0 else original_qty * args.fraction
    if sell_qty <= 0 or sell_qty < min_qty or (min_notional > 0 and sell_qty * price < min_notional):
        raise SystemExit(
            f"{symbol}: sell qty {sell_qty:.8f} is below exchange limits "
            f"(min_qty={min_qty}, min_notional={min_notional})"
        )

    sl_order_id = state.get("stop_loss_order_id")
    if sl_order_id:
        print(f"Cancelling existing SL order {sl_order_id}...")
        try:
            engine.client.cancel_order(symbol, str(sl_order_id))
        except Exception as exc:
            print(f"WARNING: cancel SL failed: {exc}")
        time.sleep(1.5)

    base = engine._get_base_asset(symbol)
    balances = engine.client.get_balances()
    available, reserved, total = _balance_total(balances, base)
    print(f"{base} balance before sell: available={available:.8f}, reserved={reserved:.8f}, total={total:.8f}")
    if available + 1e-12 < sell_qty:
        raise SystemExit(f"{symbol}: available {available:.8f} is below sell qty {sell_qty:.8f}")

    client_id = make_client_id("reduce")
    print(f"Placing MARKET SELL {sell_qty:.8f} {base} ({args.fraction:.0%} reduce)...")
    order = engine.client.place_order(
        symbol=symbol,
        side="SELL",
        order_type="MARKET",
        amount=sell_qty,
        price=price,
        client_id=client_id,
    )
    order_id = str(order.get("orderId") or order.get("id") or "")
    status = str(order.get("status") or "").upper()
    filled_qty = float(order.get("executedQty", 0.0) or order.get("filled", 0.0) or 0.0)
    order_details = order
    if status not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"} and order_id:
        status, filled_qty, order_details = engine._poll_order_adaptive(order_id, symbol=symbol)

    quote_qty = float(order_details.get("cummulativeQuoteQty", 0.0) or 0.0)
    filled_price = quote_qty / filled_qty if filled_qty > 0 and quote_qty > 0 else price
    if status != "FILLED" or filled_qty <= 0:
        raise SystemExit(f"{symbol}: reduce order did not fill (status={status}, filled={filled_qty})")

    remaining_qty = max(0.0, original_qty - filled_qty)
    remaining_qty = round_step(remaining_qty, step) if step > 0 else remaining_qty
    stop_loss = float(state.get("stop_loss", 0.0) or 0.0)
    new_sl = engine._place_sl_with_retry(remaining_qty, stop_loss, symbol=symbol) if remaining_qty > 0 else None
    new_sl_id = new_sl[0] if new_sl else None
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    entry_price = float(state.get("entry_price", 0.0) or 0.0)
    entry_cost = filled_qty * entry_price
    gross_exit = filled_qty * filled_price
    net_pnl = gross_exit - entry_cost
    net_pnl_pct = (net_pnl / entry_cost) * 100 if entry_cost > 0 else 0.0

    engine.db.save_closed_trade(
        {
            "symbol": symbol,
            "side": "SELL",
            "amount": filled_qty,
            "entry_price": entry_price,
            "exit_price": filled_price,
            "entry_cost": entry_cost,
            "gross_exit": gross_exit,
            "entry_fee": 0.0,
            "exit_fee": 0.0,
            "total_fees": 0.0,
            "net_pnl": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "trigger": f"Manual reduce {args.fraction:.0%}",
            "opened_at": state.get("opened_at"),
            "closed_at": now,
            "strategy_name": "manual_reduce",
            "execution_mode": "live",
        }
    )
    engine.db.save_trade_state(
        symbol=symbol,
        state="bought" if remaining_qty > 0 else "idle",
        entry_price=entry_price if remaining_qty > 0 else 0.0,
        stop_loss=stop_loss if remaining_qty > 0 else 0.0,
        take_profit=float(state.get("take_profit", 0.0) or 0.0) if remaining_qty > 0 else 0.0,
        highest_price_seen=float(state.get("highest_price_seen", entry_price) or entry_price) if remaining_qty > 0 else 0.0,
        quantity=remaining_qty,
        opened_at=state.get("opened_at") if remaining_qty > 0 else None,
        last_transition_at=now,
        stop_loss_order_id=new_sl_id,
    )

    print(
        f"Reduced {symbol}: sold={filled_qty:.8f} @ {filled_price:.2f}, "
        f"remaining={remaining_qty:.8f}, new_sl_order_id={new_sl_id}, "
        f"net_pnl={net_pnl:+.4f} USDT"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
