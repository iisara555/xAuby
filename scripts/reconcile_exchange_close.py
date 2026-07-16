#!/usr/bin/env python3
"""Inspect or apply one exchange-confirmed derivative close.

Dry-run is the default. Applying requires an exact ``--exchange-close-id`` and
refuses to run while the exchange still reports an open position.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xauby.api import create_exchange_client
from xauby.database.db import LiteDB
from xauby.engine.exchange_close import (
    build_confirmed_trade,
    match_position_history,
)
from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.exchange_config import resolve_exchange_credentials
from xauby.runtime.pair_registry import PairRegistry


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _public_history(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "exchange_close_id",
        "exchange_position_id",
        "symbol",
        "position_side",
        "quantity",
        "entry_price",
        "exit_price",
        "realized_pnl",
        "gross_pnl",
        "fee_cost",
        "funding_fee",
        "close_type",
        "opened_at",
        "closed_at",
    )
    return {key: row.get(key) for key in keys}


def _strategy_name(config: Dict[str, Any], symbol: str) -> str:
    registry = PairRegistry(config)
    specs = registry.load(None)
    for spec in specs:
        if spec.symbol == symbol:
            return spec.strategy_name
    return str((config.get("strategy") or {}).get("active") or "unknown")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument(
        "--db-path",
        help="Tenant SQLite path (defaults to SQLITE_DB_PATH/XAUBY_HOME resolution)",
    )
    parser.add_argument("--symbol", default="XAUUSDT")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--exchange-close-id")
    parser.add_argument("--trigger", default="Exchange-confirmed position close")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    _load_dotenv()
    config = load_bot_config(args.config)
    symbol = str(args.symbol).upper().replace("_", "")
    api_key, api_secret, base_url = resolve_exchange_credentials(config)
    client = create_exchange_client(config, api_key, api_secret, base_url)
    db = LiteDB(args.db_path)
    try:
        history = client.get_position_history(symbol, limit=args.limit)
        print(json.dumps([_public_history(row) for row in history], indent=2, sort_keys=True))
        if not args.apply:
            return 0
        if not args.exchange_close_id:
            raise SystemExit("--apply requires --exchange-close-id")
        live = client.get_positions([symbol])
        if live:
            raise SystemExit("refusing recovery while the exchange reports an open position")

        selected, error = match_position_history(
            {},
            history,
            exchange_close_id=args.exchange_close_id,
        )
        if selected is None:
            raise SystemExit(error)
        current = db.get_trade_state(symbol).to_dict()
        state = current if current.get("state") == "bought" else {
            "symbol": symbol,
            "state": "idle",
            "entry_price": selected.get("entry_price"),
            "quantity": selected.get("quantity"),
            "opened_at": selected.get("opened_at"),
            "position_side": selected.get("position_side"),
            "exchange_position_id": selected.get("exchange_position_id"),
            "management_mode": "strategy",
        }
        key = f"recovery:{args.exchange_close_id}"
        pending = db.queue_exchange_close_reconciliation(
            reconciliation_key=key,
            symbol=symbol,
            exchange_id=str(selected.get("exchange") or "exchange"),
            state_snapshot=state,
            exchange_position_id=selected.get("exchange_position_id"),
            reset_position=current.get("state") == "bought",
        )
        trade = build_confirmed_trade(
            state,
            selected,
            symbol=symbol,
            strategy_name=_strategy_name(config, symbol),
            execution_mode="live",
            prior_trades=db.get_closed_trades(symbol, limit=200),
            trigger=args.trigger,
        )
        trade_id = db.complete_exchange_close_reconciliation(
            str(pending.get("reconciliation_key") or key),
            trade,
        )
        print(json.dumps({"applied": True, "closed_trade_id": trade_id, "trade": trade}, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
