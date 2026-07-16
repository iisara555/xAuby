import logging
from datetime import datetime, timezone

from xauby.engine.exchange_close import (
    build_confirmed_trade,
    match_position_history,
    reconciliation_key,
)
from xauby.observability import EventType

logger = logging.getLogger("lite_bot")


class ReconcileMixin:
    @staticmethod
    def _state_dict(state):
        if hasattr(state, "to_dict"):
            return state.to_dict()
        return dict(state or {})

    def _exchange_id(self) -> str:
        exchange = (getattr(self, "config", {}) or {}).get("exchange") or {}
        return str(
            exchange.get("ccxt_id")
            or exchange.get("name")
            or exchange.get("provider")
            or "exchange"
        ).lower()

    def _set_exchange_wait_meta(self, sym: str, reason: str) -> None:
        sc = self._sc(sym)
        sc.last_signal_meta = {
            **dict(sc.last_signal_meta or {}),
            "action": "HOLD",
            "intent": "HOLD",
            "position_side": None,
            "reason": reason,
            "status_summary": reason,
            "timestamp": datetime.now(timezone.utc).timestamp(),
        }
        if sym == getattr(self, "focus_symbol", None):
            self.last_signal_meta = sc.last_signal_meta

    @staticmethod
    def _exchange_close_trigger(state, history) -> str:
        take_profit = float(state.get("take_profit") or 0.0)
        exit_price = float(history.get("exit_price") or 0.0)
        side = str(state.get("position_side") or history.get("position_side") or "LONG").upper()
        if take_profit > 0 and exit_price > 0:
            if side == "SHORT" and exit_price <= take_profit * 1.001:
                return "Exchange Take Profit"
            if side != "SHORT" and exit_price >= take_profit * 0.999:
                return "Exchange Take Profit"
        return "Exchange-confirmed position close"

    def _retry_pending_exchange_closures(self, sym: str) -> bool:
        if not hasattr(self.db, "get_pending_exchange_closures"):
            return False
        pending = self.db.get_pending_exchange_closures(sym)
        if not pending:
            return False
        sc = self._sc(sym)
        reason = "Exchange close reconciliation pending; new entries are blocked"
        sc.set_exchange_reconcile_pending(True, reason)
        self._set_exchange_wait_meta(sym, reason)
        for item in pending:
            if not self._complete_pending_exchange_close(sym, item):
                return True
        return bool(self.db.get_pending_exchange_closures(sym))

    def _complete_pending_exchange_close(self, sym: str, pending) -> bool:
        key = str(pending.get("reconciliation_key") or "")
        state = dict(pending.get("state_snapshot") or {})
        if not state:
            try:
                import json

                state = json.loads(pending.get("state_json") or "{}")
            except Exception:
                state = {}
        try:
            opened_at = state.get("opened_at")
            since = None
            if opened_at:
                parsed = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                since = int(parsed.timestamp() * 1000) - (60 * 60 * 1000)
            history_rows = self.client.get_position_history(sym, since=since, limit=100)
            history, error = match_position_history(state, history_rows)
            if history is None:
                raise RuntimeError(error)
            prior = self.db.get_closed_trades(sym, limit=200)
            strategy_name = (
                self._strategy_name_for_symbol(sym)
                if hasattr(self, "_strategy_name_for_symbol")
                else str(state.get("strategy_name") or "unknown")
            )
            trade = build_confirmed_trade(
                state,
                history,
                symbol=sym,
                strategy_name=strategy_name,
                execution_mode=(
                    self._execution_mode(sym) if hasattr(self, "_execution_mode") else "live"
                ),
                prior_trades=prior,
                trigger=self._exchange_close_trigger(state, history),
            )
            self.db.complete_exchange_close_reconciliation(key, trade)
        except Exception as exc:
            self.db.mark_exchange_close_reconciliation_error(key, str(exc))
            reason = f"Exchange close reconciliation pending: {exc}"
            self._sc(sym).set_exchange_reconcile_pending(True, reason)
            self._set_exchange_wait_meta(sym, reason)
            logger.error("[%s] %s", sym, reason, exc_info=True)
            return False

        sc = self._sc(sym)
        sc.set_exchange_reconcile_pending(False)
        sc.schedule_exchange_reconcile_wait(1)
        reason = (
            f"OKX position closed; waiting one engine cycle. "
            f"Realized PnL {float(trade['net_pnl']):+.4f} USDT"
        )
        self._set_exchange_wait_meta(sym, reason)
        if hasattr(self, "_emit_event"):
            self._emit_event(
                EventType.POSITION_CLOSED,
                symbol=sym,
                position_side=history.get("position_side"),
                exit=round(float(trade.get("exit_price") or 0.0), 8),
                pnl=round(float(trade.get("net_pnl") or 0.0), 8),
                pnl_pct=round(float(trade.get("net_pnl_pct") or 0.0), 4),
                trigger=trade.get("trigger"),
                pnl_source=trade.get("pnl_source"),
                exchange_close_id=trade.get("exchange_close_id"),
            )
        self.send_telegram_alert(
            f"OKX POSITION CLOSED {sym} | Exit {float(trade['exit_price']):.4f} | "
            f"Realized PnL {float(trade['net_pnl']):+.4f} USDT "
            f"({float(trade['net_pnl_pct']):+.2f}%)"
        )
        logger.info("[%s] Exchange close reconciled: %s", sym, trade)
        return True

    def _reconcile_single_symbol(self, symbol: str):
        sym = symbol.upper().replace("_", "")
        try:
            state = self.db.get_trade_state(sym)
            market_type = str((self.config.get("exchange") or {}).get("market_type", "spot")).lower()
            if market_type == "swap" and hasattr(self.client, "get_positions"):
                self._reconcile_derivative_position(sym, state)
                return
            if state.get("state") != "bought":
                # Symmetric with the swap orphan guard: if the engine died after a
                # spot BUY filled but before the state was saved, the exchange
                # holds the base asset while local state is idle. Adopting it
                # blindly is unsafe (we don't know entry/SL), so halt new entries
                # and alert for manual reconcile instead of trading around it.
                self._detect_spot_orphan(sym, state)
                logger.info(f"[{sym}] Local state is not 'bought': cancelling orphan orders if any.")
                self._cancel_orphan_orders(symbol=sym)
                return

            exchange_name = str((self.config.get("exchange") or {}).get("name")
                                or (self.config.get("exchange") or {}).get("ccxt_id")
                                or (self.config.get("exchange") or {}).get("provider") or "configured")
            logger.info("[%s] Local state is 'bought'. Reconciling with %s exchange...", sym, exchange_name)
            balances = self.client.get_balances()
            if not isinstance(balances, dict):
                logger.warning(
                    "[%s] get_balances() returned %s; skipping reconcile, keeping DB state.",
                    sym, type(balances).__name__,
                )
                return
            base_asset = self._get_base_asset(sym)
            live_bal_info = balances.get(base_asset, {})
            avail_bal = float(live_bal_info.get("available", 0.0))
            locked_bal = float(live_bal_info.get("reserved", 0.0))
            total_bal = avail_bal + locked_bal

            db_qty = float(state.get("quantity", 0.0))

            logger.info(
                f"[{sym}] Reconciliation: Base Asset: {base_asset} | DB Qty: {db_qty:.6f} | "
                f"Exchange Balance: {total_bal:.6f} (Avail: {avail_bal:.6f}, Locked: {locked_bal:.6f})"
            )

            f = self.client.get_symbol_filters(sym)
            min_qty = float(f.get("minQty") or 0.0)
            step_size = float(f.get("stepSize") or 0.0001)
            dust_limit = max(min_qty, step_size)

            if total_bal < dust_limit:
                msg = (
                    f"⚠️ *Startup Reconciliation*: Exchange balance for {base_asset} ({total_bal:.6f}) "
                    f"is below dust/trade limit. Resetting local state to IDLE."
                )
                logger.warning(msg)
                self.send_telegram_alert(msg)
                self.db.save_trade_state(sym, "idle")
                self._cancel_orphan_orders(symbol=sym)
            elif abs(db_qty - total_bal) > dust_limit:
                msg = (
                    f"🔄 *Startup Reconciliation*: Mismatch detected for {base_asset}. "
                    f"Updating local state quantity from {db_qty:.6f} to {total_bal:.6f}."
                )
                logger.warning(msg)
                self.send_telegram_alert(msg)
                self.db.save_trade_state(
                    symbol=sym,
                    state="bought",
                    entry_price=state.get("entry_price", 0.0),
                    stop_loss=state.get("stop_loss", 0.0),
                    take_profit=state.get("take_profit", 0.0),
                    highest_price_seen=state.get("highest_price_seen", state.get("entry_price", 0.0)),
                    quantity=total_bal,
                    opened_at=state.get("opened_at"),
                    last_transition_at=state.get("last_transition_at"),
                    stop_loss_order_id=state.get("stop_loss_order_id"),
                )
                state = self.db.get_trade_state(sym)
            else:
                logger.info(f"[{sym}] Startup state is fully reconciled with exchange balance.")

            state = self.db.get_trade_state(sym)
            if state.get("state") == "bought":
                state = self._ensure_sl_protected(state, symbol=sym)
                self._cancel_orphan_orders(tracked_sl_id=state.get("stop_loss_order_id"), symbol=sym)

        except Exception as e:
            logger.error(f"Error during startup state reconciliation for {sym}: {e}", exc_info=True)
            self.send_telegram_alert(
                f"⚠️ *Startup Reconciliation Failed* (`{sym}`): `{e}`. Continuing with database state."
            )

    def _detect_spot_orphan(self, sym: str, state) -> None:
        """Halt + alert when the exchange holds a tradeable base balance the
        engine is not tracking (state is idle). Mirrors the swap orphan guard."""
        try:
            balances = self.client.get_balances()
            if not isinstance(balances, dict):
                return  # cannot assess orphan without balances → stay safe (no false halt)
            base_asset = self._get_base_asset(sym)
            info = balances.get(base_asset, {}) or {}
            total_bal = float(info.get("available", 0.0) or 0.0) + float(info.get("reserved", 0.0) or 0.0)
            if total_bal <= 0:
                return
            f = self.client.get_symbol_filters(sym)
            dust_limit = max(float(f.get("minQty") or 0.0), float(f.get("stepSize") or 0.0001))
            if total_bal < dust_limit:
                return  # leftover dust, not a real position
            # Only escalate when the balance is worth a real order.
            from xauby.runtime.trading_config import resolve_trading_config
            eff = resolve_trading_config(
                self.config, self._strategy_name_for_symbol(sym), symbol=sym, for_live=True
            )
            min_order = float(eff.portfolio.get("min_order_amount", 10.0) or 10.0)
            try:
                price = float(self.client.get_ticker(sym).get("last") or 0.0)
            except Exception:
                price = 0.0
            if price > 0 and total_bal * price < min_order:
                return  # below tradeable size — treat as dust
            msg = (
                f"🚨 *RECONCILE NEEDED* {sym}: exchange holds {total_bal:.8f} {base_asset} "
                f"but local state is idle. Possible fill during a crash. New entries are "
                f"HALTED until you verify the position/stop-loss on the exchange."
            )
            logger.error(msg)
            sc = self._sc(sym)
            sc.set_trading_halted(True, f"orphan spot balance {total_bal:.8f} {base_asset}")
            self.send_telegram_alert(msg)
        except Exception as e:
            logger.error("[%s] Spot orphan detection failed: %s", sym, e, exc_info=True)

    def _reconcile_derivative_position(self, sym: str, state):
        """Reconcile against exchange positions, never spot balances."""
        state = self._state_dict(state)
        positions = self.client.get_positions([sym])
        live = next(
            (
                position
                for position in positions
                if str(position.get("symbol", ""))
                .split(":", 1)[0]
                .upper()
                .replace("/", "")
                .replace("_", "")
                == sym
            ),
            None,
        )
        local_open = state.get("state") == "bought"
        if live and not local_open:
            live_side = str(live.get("position_side") or "").upper()
            live_qty = abs(float(live.get("quantity") or 0.0))
            entry_price = float(live.get("entry_price") or live.get("mark_price") or 0.0)
            if live_side not in {"LONG", "SHORT"} or live_qty <= 0:
                logger.error("[%s] Ignoring malformed derivative position: %r", sym, live)
                return
            now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            self.db.save_trade_state(
                symbol=sym,
                state="bought",
                entry_price=entry_price,
                stop_loss=0.0,
                take_profit=0.0,
                highest_price_seen=entry_price,
                quantity=live_qty,
                opened_at=now_iso,
                last_transition_at=now_iso,
                position_side=live_side,
                leverage=float(live.get("leverage") or 1.0),
                margin_mode=str(live.get("margin_mode") or "isolated"),
                liquidation_price=float(live.get("liquidation_price") or 0.0),
                funding_paid=0.0,
                management_mode="manual",
                exchange_position_id=live.get("exchange_position_id"),
                partial_tp_taken=False,
            )
            msg = (
                f"MANUAL DERIVATIVE POSITION DETECTED {sym} {live_side} "
                f"qty={live_qty:.8f}; adopted as manual-managed"
            )
            logger.warning(msg)
            self.send_telegram_alert(msg)
            return
        if local_open and not live:
            exchange_id = self._exchange_id()
            key = reconciliation_key(exchange_id, sym, state)
            pending = self.db.queue_exchange_close_reconciliation(
                reconciliation_key=key,
                symbol=sym,
                exchange_id=exchange_id,
                state_snapshot=state,
                exchange_position_id=state.get("exchange_position_id"),
                reset_position=True,
            )
            reason = "Exchange position is flat; realized PnL verification is pending"
            self._sc(sym).set_exchange_reconcile_pending(True, reason)
            self._set_exchange_wait_meta(sym, reason)
            logger.warning("[%s] %s", sym, reason)
            self._cancel_orphan_orders(symbol=sym)
            self._complete_pending_exchange_close(sym, pending)
            return
        if not live:
            self._retry_pending_exchange_closures(sym)
            self._cancel_orphan_orders(symbol=sym)
            return
        live_side = str(live.get("position_side") or "").upper()
        local_side = str(state.get("position_side") or "LONG").upper()
        if live_side != local_side:
            if str(state.get("management_mode") or "strategy").lower() == "manual":
                logger.warning(
                    "[%s] Manual derivative side changed on exchange: %s -> %s",
                    sym,
                    local_side,
                    live_side,
                )
                state = dict(state)
                state["entry_price"] = float(
                    live.get("entry_price")
                    or live.get("mark_price")
                    or state.get("entry_price")
                    or 0.0
                )
                state["opened_at"] = (
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                )
            else:
                msg = f"POSITION SIDE MISMATCH {sym}: local={local_side} exchange={live_side}"
                logger.error(msg)
                sc = self._sc(sym)
                sc.set_feed_degraded(True, msg)
                sc.set_trading_halted(True, msg)
                self.send_telegram_alert(msg)
                return
        self.db.save_trade_state(
            symbol=sym, state="bought",
            entry_price=float(live.get("entry_price") or state.get("entry_price") or 0.0),
            stop_loss=state.get("stop_loss", 0.0), take_profit=state.get("take_profit", 0.0),
            highest_price_seen=state.get("highest_price_seen", state.get("entry_price", 0.0)),
            quantity=float(live.get("quantity") or 0.0), opened_at=state.get("opened_at"),
            last_transition_at=state.get("last_transition_at"),
            stop_loss_order_id=state.get("stop_loss_order_id"), position_side=live_side,
            leverage=float(live.get("leverage") or state.get("leverage") or 1.0),
            margin_mode=str(live.get("margin_mode") or "isolated"),
            liquidation_price=float(live.get("liquidation_price") or 0.0),
            funding_paid=float(state.get("funding_paid") or 0.0),
            management_mode=str(state.get("management_mode") or "strategy"),
            exchange_position_id=live.get("exchange_position_id")
            or state.get("exchange_position_id"),
            partial_tp_taken=bool(state.get("partial_tp_taken")),
        )

    def reconcile_startup_state(self):
        if self.simulate_only:
            logger.info("Simulation mode: skipping startup state reconciliation.")
            return

        for spec in self._pair_registry.active():
            # Sim-mode symbols hold paper positions with no exchange balance;
            # reconciling them against the real account would wipe the sim
            # position back to idle on every restart.
            if self._execution_mode(spec.symbol) != "live":
                logger.info(
                    "[%s] Sim execution mode: skipping startup reconciliation.",
                    spec.symbol,
                )
                continue
            self._reconcile_single_symbol(spec.symbol)
