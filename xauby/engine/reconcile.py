import logging

logger = logging.getLogger("lite_bot")


class ReconcileMixin:
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
        positions = self.client.get_positions([sym])
        live = next((p for p in positions if str(p.get("symbol", "")).upper() == sym), None)
        local_open = state.get("state") == "bought"
        if live and not local_open:
            msg = f"ORPHAN DERIVATIVE POSITION {sym} {live.get('position_side')}; trading blocked until reconciled"
            logger.error(msg)
            sc = self._sc(sym)
            sc.set_feed_degraded(True, msg)
            # Durable halt: a WS reconnect must not silently clear this.
            sc.set_trading_halted(True, msg)
            self.send_telegram_alert(msg)
            return
        if local_open and not live:
            logger.warning("[%s] Local derivative position is absent on exchange; resetting local state", sym)
            self.db.save_trade_state(sym, "idle")
            self._cancel_orphan_orders(symbol=sym)
            return
        if not live:
            self._cancel_orphan_orders(symbol=sym)
            return
        live_side = str(live.get("position_side") or "").upper()
        local_side = str(state.get("position_side") or "LONG").upper()
        if live_side != local_side:
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
