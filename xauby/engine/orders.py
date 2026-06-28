import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

from xauby.api.utils import make_client_id, round_step
from xauby.api.errors import ExchangeAPIError
from xauby.observability import EventType
from xauby.notifications.interface import AlertLevel
from xauby.runtime.trading_config import resolve_trading_config
from xauby.runtime.exits import fixed_take_profit_price

logger = logging.getLogger("lite_bot")


class OrderMixin:
    def execute_open_short(self, signal: Any, ticker_price: float, symbol: Optional[str] = None) -> bool:
        """Open an isolated one-way SHORT; live mode is explicit and fail-closed."""
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        spec = self._pair_registry.get(sym)
        if not spec or "short" not in spec.allowed_sides:
            logger.warning("SHORT blocked for %s: not present in allowed_sides", sym)
            return False
        if getattr(self._sc(sym), "trading_halted", False):
            logger.warning("SHORT blocked for %s: trading halted (%s)", sym, self._sc(sym).halt_reason)
            return False
        if getattr(self._sc(sym), "candle_stale", False):
            logger.warning("SHORT blocked for %s: candle feed stale", sym)
            return False
        if self._sc(sym).feed_degraded:
            logger.warning("SHORT blocked for %s: market feed degraded", sym)
            return False
        live = self._execution_mode(sym) == "live"
        if live and not spec.short_live_enabled:
            logger.warning("SHORT live blocked for %s: short_live_enabled=false", sym)
            return False
        leverage = max(1.0, min(float(spec.leverage or 1.0), 3.0))
        equity = max(0.0, float(self.get_equity(symbol=sym)))
        if ticker_price <= 0:
            logger.warning("SHORT blocked for %s: invalid price", sym)
            return False
        eff_cfg = resolve_trading_config(
            self.config, self._strategy_name_for_symbol(sym), symbol=sym, for_live=True
        )
        min_order = float(eff_cfg.portfolio.get("min_order_amount", 10.0) or 10.0)
        disable_sl = bool(eff_cfg.strategy.get("disable_stop_loss", False))
        if disable_sl:
            # CDC-pure: no stop loss, size by position_pct of equity so the SHORT
            # side mirrors the LONG side of the same strategy (symmetric SAR).
            position_pct = max(0.0, min(float(eff_cfg.strategy.get("position_pct", 1.0) or 1.0), 1.0))
            notional = equity * position_pct
            if self._use_sim_broker(sym):
                notional /= 1.0 + self._symbol_fee_pct(sym)
            stop_loss = 0.0
        else:
            risk_pct = float(eff_cfg.portfolio.get("risk_pct", 0.03) or 0.03)
            distance = float(signal.stop_loss_distance or 0.0)
            if distance <= 0:
                distance = ticker_price * float((self.config.get("risk") or {}).get("stop_loss_pct", 2.0)) / 100.0
            if distance <= 0:
                logger.warning("SHORT blocked for %s: invalid SL distance", sym)
                return False
            notional = (equity * risk_pct) / distance * ticker_price
            max_pos_pct = float(eff_cfg.portfolio.get("max_position_per_trade_pct", 28.0)) / 100.0
            notional = min(notional, equity * max_pos_pct * leverage)
            stop_loss = float(signal.stop_loss_price or (ticker_price + distance))
        if notional < min_order:
            logger.warning("SHORT blocked for %s: notional %.2f below %.2f", sym, notional, min_order)
            return False
        qty = notional / ticker_price
        tp_pct = float((self.config.get("risk") or {}).get("take_profit_pct", 0.0) or 0.0)
        take_profit = ticker_price * (1.0 - tp_pct / 100.0) if tp_pct > 0 else 0.0
        broker = self._broker_for_symbol(sym)
        try:
            if live:
                caps = getattr(self.client, "capabilities", {}) or {}
                if not all(caps.get(k) for k in ("swap", "positions", "reduce_only")):
                    raise RuntimeError("exchange lacks swap/position/reduce-only capabilities")
                self.client.set_margin_mode(sym, "isolated")
                self.client.set_leverage(sym, leverage)
                derivatives = self.config.get("derivatives", {}) or {}
                if derivatives.get("funding_guard_enabled", True) and hasattr(self.client, "get_funding_rate"):
                    funding_rate = abs(float(self.client.get_funding_rate(sym).get("funding_rate") or 0.0))
                    max_rate = float(derivatives.get("max_abs_funding_rate", 0.003) or 0.003)
                    if funding_rate > max_rate:
                        raise RuntimeError(
                            f"funding rate {funding_rate:.6f} exceeds guard {max_rate:.6f}"
                        )
            result = broker.execute_open(sym, "SHORT", qty, ticker_price, notional, leverage)
        except Exception as exc:
            logger.error("SHORT open failed for %s: %s", sym, exc, exc_info=True)
            return False
        if not result.success:
            logger.error(result.error or "SHORT open failed")
            return False
        fill_price = float(result.price or ticker_price)
        fill_qty = float(result.qty or qty)
        # Opening a short is a market SELL; report fill vs the quote (live only —
        # sim fills are modelled, so their "slippage" is not a real venue cost).
        slip_bps = self._report_slippage(
            side="SELL", ref_price=ticker_price, fill_price=fill_price,
            symbol=sym, kind="short entry",
        ) if live else 0.0
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self.db.save_trade_state(
            symbol=sym, state="bought", entry_price=fill_price,
            stop_loss=stop_loss, take_profit=take_profit,
            highest_price_seen=fill_price, quantity=fill_qty, opened_at=now_iso,
            position_side="SHORT", leverage=leverage, margin_mode="isolated",
            funding_paid=0.0,
        )
        self._emit_event(EventType.POSITION_OPENED, symbol=sym, position_side="SHORT",
                         side="SELL", qty=fill_qty, leverage=leverage, slippage_bps=slip_bps)
        self.send_telegram_alert(
            f"SHORT OPEN {'LIVE' if live else 'PAPER'} {sym} qty={fill_qty:.6f} "
            f"@ {fill_price:.4f} {leverage:.0f}x isolated"
        )
        return True

    def execute_close_short(self, state: Dict[str, Any], ticker_price: float,
                            trigger_reason: str, symbol: Optional[str] = None) -> bool:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        qty = float(state.get("quantity") or 0.0)
        entry = float(state.get("entry_price") or 0.0)
        funding = float(state.get("funding_paid") or 0.0)
        if qty <= 0 or entry <= 0:
            return False
        if self._use_sim_broker(sym):
            ticker_price = self._sim_fill_price(sym, ticker_price)
        broker = self._broker_for_symbol(sym)
        try:
            result = broker.execute_close(sym, "SHORT", qty, ticker_price, entry, entry * qty, funding)
        except Exception as exc:
            logger.error("SHORT close failed for %s: %s", sym, exc, exc_info=True)
            return False
        if not result.success:
            logger.error(result.error or "SHORT close failed")
            return False
        exit_price = float(result.price or ticker_price)
        entry_notional = entry * qty
        exit_notional = exit_price * qty
        fee_pct = self._symbol_fee_pct(sym)
        entry_fee = entry_notional * fee_pct
        exit_fee = float(result.fees or exit_notional * fee_pct)
        net_pnl = (entry - exit_price) * qty - entry_fee - exit_fee - funding
        net_pct = net_pnl / entry_notional * 100.0 if entry_notional else 0.0
        ok = self.db.close_position_atomic(
            sym, side="SHORT", amount=qty, entry_price=entry, exit_price=exit_price,
            entry_cost=entry_notional, gross_exit=exit_notional,
            entry_fee=entry_fee, exit_fee=exit_fee, total_fees=entry_fee + exit_fee,
            net_pnl=net_pnl, net_pnl_pct=net_pct, trigger=trigger_reason,
            opened_at=state.get("opened_at"), strategy_name=self._strategy_name_for_symbol(sym),
            execution_mode=self._execution_mode(sym),
        )
        if ok:
            slip_bps = self._report_slippage(
                side="BUY", ref_price=ticker_price, fill_price=exit_price,
                symbol=sym, kind="short exit",
            ) if not self._use_sim_broker(sym) else 0.0
            self._emit_event(EventType.POSITION_CLOSED, symbol=sym, position_side="SHORT",
                             exit=exit_price, pnl=net_pnl, pnl_pct=net_pct,
                             trigger=trigger_reason, slippage_bps=slip_bps)
            self.send_telegram_alert(
                f"SHORT CLOSE {sym} @ {exit_price:.4f} | PnL {net_pnl:+.2f} ({net_pct:+.2f}%)"
            )
        return ok

    def _report_slippage(
        self,
        *,
        side: str,
        ref_price: float,
        fill_price: float,
        symbol: str,
        kind: str = "entry",
    ) -> float:
        """Signed execution slippage in basis points (positive = adverse to us).

        A BUY filling above the intended price, or a SELL filling below it, is a
        cost and reports positive bps. Surfaces what was previously invisible:
        the fill price is recorded but never compared to the quote. Alerts when
        the adverse slippage exceeds ``execution.max_slippage_bps``.
        """
        if ref_price <= 0 or fill_price <= 0:
            return 0.0
        direction = 1.0 if str(side).upper() == "BUY" else -1.0
        bps = round(direction * (fill_price - ref_price) / ref_price * 1e4, 2)
        threshold = float((self.config.get("execution") or {}).get("max_slippage_bps", 50.0) or 0.0)
        if threshold > 0 and bps > threshold:
            self.send_telegram_alert(
                f"⚠️ *High Slippage* {symbol} {side} {kind}: {bps:.1f} bps "
                f"(ref {ref_price:.4f} → fill {fill_price:.4f})",
                level=AlertLevel.POSITION,
            )
        return bps

    def _closed_pnl_after_fees(
        self,
        *,
        entry_cost: float,
        gross_exit: float,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
    ) -> Tuple[float, float]:
        total_fees = float(entry_fee or 0.0) + float(exit_fee or 0.0)
        net_pnl = float(gross_exit or 0.0) - float(entry_cost or 0.0) - total_fees
        net_pnl_pct = (net_pnl / entry_cost) * 100 if entry_cost > 0 else 0.0
        return net_pnl, net_pnl_pct

    def _fixed_take_profit_price(
        self,
        entry_price: float,
        strategy_name: Optional[str],
        strategy_cfg: Dict[str, Any],
    ) -> float:
        """Return engine-managed fixed TP price, or 0 when disabled.

        Delegates to the shared resolver so live and backtest stay in parity.
        """
        return fixed_take_profit_price(entry_price, strategy_name, strategy_cfg)

    def _min_tradeable_base_qty(self, symbol: str, ref_price: float = 0.0) -> float:
        """Return the smallest base qty worth tracking as an actionable spot position."""
        sym = symbol.upper().replace("_", "")
        try:
            filters = self.client.get_symbol_filters(sym)
        except Exception:
            filters = {}
        min_qty = float(filters.get("minQty") or 0.0)
        step_size = float(filters.get("stepSize") or 0.0)
        min_notional = float(filters.get("minNotional") or 0.0)
        price = float(ref_price or 0.0)
        if price <= 0:
            try:
                price = float(self.client.get_ticker(sym).get("last") or 0.0)
            except Exception:
                price = 0.0
        min_notional_qty = (min_notional / price) if min_notional > 0 and price > 0 else 0.0
        return max(min_qty, step_size, min_notional_qty, 0.0)

    def _record_partial_closed_trade(
        self,
        state: Dict[str, Any],
        filled_qty: float,
        filled_price: float,
        trigger_reason: str,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
        symbol: Optional[str] = None,
    ) -> bool:
        """Record a partial exit without resetting the remaining position."""
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        entry_price = float(state.get("entry_price", 0.0))
        entry_cost = filled_qty * entry_price
        gross_exit = filled_qty * filled_price
        total_fees = float(entry_fee or 0.0) + float(exit_fee or 0.0)
        net_pnl, net_pnl_pct = self._closed_pnl_after_fees(
            entry_cost=entry_cost,
            gross_exit=gross_exit,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
        )
        closed_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        opened_at = state.get("opened_at")
        try:
            entry_regime = self.db.get_regime_at(opened_at or closed_at, sym)
            exit_regime = self.db.get_regime_at(closed_at, sym)
        except Exception:
            entry_regime = None
            exit_regime = None
        try:
            self.db.save_closed_trade(
                sym,
                side="BUY",
                amount=filled_qty,
                entry_price=entry_price,
                exit_price=filled_price,
                entry_cost=entry_cost,
                gross_exit=gross_exit,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                total_fees=total_fees,
                net_pnl=net_pnl,
                net_pnl_pct=net_pnl_pct,
                trigger=trigger_reason,
                opened_at=opened_at,
                closed_at=closed_at,
                entry_regime=entry_regime,
                exit_regime=exit_regime,
                strategy_name=self._strategy_name_for_symbol(sym),
                execution_mode=self._execution_mode(sym),
            )
            return True
        except Exception as e:
            logger.error("Failed to record partial closed trade for %s: %s", sym, e)
            return False

    def _resolve_sell_order_type(self, trigger_reason: str) -> str:
        """Pick the order type for a live SELL.

        Urgent exits (CDC red zone, NO_TRADE force close) must execute as
        MARKET so the position is not left exposed; otherwise use the
        configured default (LIMIT).
        """
        reason_lc = (trigger_reason or "").lower()
        force_market_exit = (
            "red" in reason_lc
            or "no_trade" in reason_lc
            or "force close" in reason_lc
        )
        if force_market_exit:
            return "MARKET"
        return self.config.get("execution", {}).get("order_type", "LIMIT").upper()

    def _poll_order_adaptive(
        self,
        order_id: str,
        symbol: Optional[str] = None,
    ) -> Tuple[str, float, Dict[str, Any]]:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        timeout = float(self.config.get("execution", {}).get("order_timeout_seconds", 10.0))
        poll_interval = 0.5
        elapsed = 0.0
        status = ""
        filled_qty = 0.0
        order_details: Dict[str, Any] = {}

        while elapsed < timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval
            poll_interval = min(2.0, poll_interval * 1.5)
            try:
                order_details = self.client.get_order(sym, order_id)
                status = order_details.get("status", "").upper()
                filled_qty = float(order_details.get("executedQty", 0.0) or order_details.get("filled", 0.0))
                if status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    break
            except Exception as pe:
                logger.error(f"Error polling order {order_id}: {pe}")

        return status, filled_qty, order_details

    def _place_sl_with_retry(
        self, qty: float, stop_loss: float, max_attempts: int = 3, symbol: Optional[str] = None
    ) -> Optional[Tuple[str, float]]:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        if stop_loss <= 0 or qty <= 0:
            return None
        attempt_qty = qty
        try:
            balances = self.client.get_balances()
            base_coin = self._get_base_asset(sym)
            base_bal = balances.get(base_coin) or {}
            available = float(base_bal.get("available", 0.0) or 0.0)
            reserved = float(base_bal.get("reserved", 0.0) or 0.0)
            if 0 < available < attempt_qty:
                filters = self.client.get_symbol_filters(sym)
                step = float(filters.get("stepSize") or 0.0)
                capped_qty = round_step(available, step) if step > 0 else available
                min_qty = float(filters.get("minQty") or 0.0)
                min_notional = float(filters.get("minNotional") or 0.0)
                min_allowed = max(min_qty, step, 0.0)
                if capped_qty < min_allowed or (
                    min_notional > 0 and capped_qty * float(stop_loss) < min_notional
                ):
                    logger.warning(
                        "Available %s balance %.8f is below exchange SL minimum "
                        "(reserved %.8f); skipping dust SL placement",
                        base_coin,
                        available,
                        reserved,
                    )
                    return None
                if capped_qty >= max(min_qty, step, 0.0):
                    logger.warning(
                        "Capping SL qty for %s from %.8f to available %.8f (rounded %.8f)",
                        sym,
                        attempt_qty,
                        available,
                        capped_qty,
                    )
                    attempt_qty = capped_qty
        except Exception as e:
            logger.warning("Unable to cap SL qty from available balance for %s: %s", sym, e)
        for attempt in range(max_attempts):
            try:
                client_id = make_client_id(f"sl{attempt}")
                sl_res = self.client.place_order(
                    symbol=sym,
                    side="SELL",
                    order_type="STOP_LOSS_LIMIT",
                    amount=attempt_qty,
                    price=stop_loss * 0.995,
                    stop_price=stop_loss,
                    client_id=client_id,
                )
                sl_order_id = str(sl_res.get("orderId") or sl_res.get("id") or "")
                if sl_order_id:
                    logger.info(f"Exchange-side STOP_LOSS_LIMIT placed (ID: {sl_order_id}, qty={attempt_qty:.6f})")
                    return (sl_order_id, attempt_qty)
                logger.error(
                    "Exchange-side STOP_LOSS_LIMIT response missing order id for %s (qty=%.6f)",
                    sym,
                    attempt_qty,
                )
                return None
            except ExchangeAPIError as e:
                if e.code in (-2010, -2018, -1013, -1111) and attempt < max_attempts - 1:
                    logger.warning(
                        f"SL place attempt {attempt + 1} failed ({e.code}); "
                        f"shrinking qty from {attempt_qty:.6f} -> {attempt_qty * 0.995:.6f}"
                    )
                    attempt_qty *= 0.995
                    time.sleep(1.0)
                elif e.code == -1021:
                    if hasattr(self.client, "check_clock_sync"):
                        self.client.check_clock_sync()
                    time.sleep(0.5)
                else:
                    logger.error(f"Failed to place exchange-side STOP_LOSS_LIMIT: {e}")
                    break
            except Exception as sle:
                logger.error(f"Failed to place exchange-side STOP_LOSS_LIMIT: {sle}")
                time.sleep(2.0 ** attempt)
        return None

    def _ensure_sl_protected(self, state: Dict[str, Any], symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        if self._execution_mode(sym) != "live" or state.get("state") != "bought":
            return state

        sl_order_id = state.get("stop_loss_order_id")
        stop_loss = float(state.get("stop_loss", 0.0))
        qty = float(state.get("quantity", 0.0))
        if stop_loss <= 0 or qty <= 0:
            return state
        tradeable_qty = self._min_tradeable_base_qty(sym, stop_loss)
        try:
            balances = self.client.get_balances()
            base_coin = self._get_base_asset(sym)
            base_bal = balances.get(base_coin) or {}
            total_base = float(base_bal.get("available", 0.0) or 0.0) + float(
                base_bal.get("reserved", 0.0) or 0.0
            )
        except Exception:
            total_base = qty
        if tradeable_qty > 0 and max(qty, total_base) < tradeable_qty:
            logger.warning(
                "Tracked %s remainder %.8f is below tradeable threshold %.8f; clearing dust state.",
                sym,
                qty,
                tradeable_qty,
            )
            self.db.save_trade_state(sym, "idle")
            return self.db.get_trade_state(sym)

        sl_alive = False
        if sl_order_id:
            try:
                od = self.client.get_order(sym, sl_order_id)
                if od.get("status", "").upper() in ("NEW", "PARTIALLY_FILLED"):
                    sl_alive = True
            except ExchangeAPIError as e:
                if e.code not in (-2013, -2026):
                    logger.error(f"Error verifying SL order {sl_order_id}: {e}")
                    return state
                logger.warning(f"SL order {sl_order_id} not found on exchange.")

        if not sl_alive:
            logger.warning("Position missing exchange SL — attempting restore...")
            self._cancel_orphan_orders(tracked_sl_id=sl_order_id, symbol=sym)
            new_sl_res = self._place_sl_with_retry(qty, stop_loss, symbol=sym)
            new_sl_id = new_sl_res[0] if new_sl_res else None
            if new_sl_id:
                self.db.save_trade_state(
                    symbol=sym,
                    state="bought",
                    entry_price=state.get("entry_price", 0.0),
                    stop_loss=stop_loss,
                    take_profit=state.get("take_profit", 0.0),
                    highest_price_seen=state.get("highest_price_seen", 0.0),
                    quantity=qty,
                    opened_at=state.get("opened_at"),
                    last_transition_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    stop_loss_order_id=new_sl_id,
                )
                state = self.db.get_trade_state(sym)
            else:
                self.send_telegram_alert(
                    "🚨 *CRITICAL*: Failed to restore exchange SL. Position relies on local monitoring only.",
                    level=AlertLevel.CRITICAL,
                )
        return state

    def _cancel_orphan_orders(self, tracked_sl_id: Optional[str] = None, symbol: Optional[str] = None):
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        if self._execution_mode(sym) != "live":
            return
        try:
            opens = self.client.get_open_orders(sym)
            for o in opens:
                oid = str(o.get("orderId", ""))
                if tracked_sl_id and oid == str(tracked_sl_id):
                    continue
                side = o.get("side", "")
                otype = str(o.get("type", "")).upper()
                if side == "SELL" and "STOP" in otype:
                    logger.warning(f"Cancelling orphan SL order {oid} ({side} {otype})")
                    try:
                        self.client.cancel_order(sym, oid)
                    except Exception as ce:
                        logger.error(f"Failed to cancel orphan order {oid}: {ce}")
        except Exception as e:
            logger.error(f"Failed to enumerate open orders: {e}")

    def _restore_stop_loss(self, qty: float, stop_loss: float, symbol: Optional[str] = None) -> Optional[str]:
        sl_res = self._place_sl_with_retry(qty, stop_loss, symbol=symbol)
        sl_id = sl_res[0] if sl_res else None
        if not sl_id:
            self.send_telegram_alert(
                "⚠️ *Restore Stop Loss Failed*: Position relies on local monitoring until SL is restored.",
                level=AlertLevel.POSITION,
            )
        return sl_id

    def _restore_stop_loss_state(self, state: Dict[str, Any], qty: float, symbol: Optional[str] = None):
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        stop_loss = state.get("stop_loss", 0.0)
        new_sl_id = self._restore_stop_loss(qty, stop_loss, symbol=sym)
        self.db.save_trade_state(
            symbol=sym,
            state=state.get("state", "bought"),
            entry_price=state.get("entry_price", 0.0),
            stop_loss=stop_loss,
            take_profit=state.get("take_profit", 0.0),
            highest_price_seen=state.get("highest_price_seen", 0.0),
            quantity=qty,
            opened_at=state.get("opened_at"),
            last_transition_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            stop_loss_order_id=new_sl_id
        )

    def _handle_exchange_sl_order(self, state: Dict[str, Any], ticker_price: float, symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        if self._execution_mode(sym) != "live" or state.get("state") != "bought":
            return state

        sl_order_id = state.get("stop_loss_order_id")
        if not sl_order_id:
            return self._ensure_sl_protected(state, symbol=sym)

        try:
            order_details = self.client.get_order(sym, sl_order_id)
            status = order_details.get("status", "").upper()
            filled_qty = float(order_details.get("executedQty", 0.0) or 0.0)
            cummulative_quote_qty = float(order_details.get("cummulativeQuoteQty", 0.0) or 0.0)
            filled_price = (
                cummulative_quote_qty / filled_qty if filled_qty > 0 and cummulative_quote_qty > 0
                else ticker_price
            )

            if status == "FILLED" and filled_qty > 0:
                logger.info(f"⚡ [EXCHANGE STOP LOSS FILLED] Stop-loss order {sl_order_id} hit on exchange.")
                entry_cost = filled_qty * state["entry_price"]
                gross_exit = filled_qty * filled_price
                fee_pct = self._symbol_fee_pct(sym)
                entry_fee = entry_cost * fee_pct
                exit_fee = gross_exit * fee_pct
                net_pnl, net_pnl_pct = self._closed_pnl_after_fees(
                    entry_cost=entry_cost,
                    gross_exit=gross_exit,
                    entry_fee=entry_fee,
                    exit_fee=exit_fee,
                )
                if not self._record_closed_trade_atomic(
                    state,
                    filled_qty,
                    filled_price,
                    "Exchange-Side Stop Loss",
                    entry_fee=entry_fee,
                    exit_fee=exit_fee,
                    symbol=sym,
                ):
                    logger.error("Failed to atomically close position after exchange SL fill")
                else:
                    base_coin = self._get_base_asset(sym)
                    msg = (
                        f"🔴 [EXCHANGE STOP LOSS HIT] Exit {filled_qty:.6f} {base_coin} @ {filled_price:.2f} USDT "
                        f"| PnL: {net_pnl:+.2f} USDT ({net_pnl_pct:+.2f}%)"
                    )
                    logger.info(msg)
                    self.last_log_message = msg
                    self.send_telegram_alert(msg)
                    self._emit_event(
                        EventType.ORDER_FILLED,
                        side="SELL",
                        order_id=sl_order_id,
                        qty=round(filled_qty, 6),
                        price=round(filled_price, 2),
                        status="FILLED",
                        symbol=sym,
                    )
                    self._emit_event(
                        EventType.POSITION_CLOSED,
                        exit=round(filled_price, 2),
                        pnl=round(net_pnl, 2),
                        pnl_pct=round(net_pnl_pct, 2),
                        trigger="Exchange-Side Stop Loss",
                        symbol=sym,
                    )
                return self.db.get_trade_state(sym)

            if status == "PARTIALLY_FILLED" and filled_qty > 0:
                logger.warning(f"Exchange SL order {sl_order_id} partially filled ({filled_qty:.6f})")
                remaining = float(state.get("quantity", 0.0)) - filled_qty
                entry_cost = filled_qty * float(state.get("entry_price", 0.0))
                gross_exit = filled_qty * filled_price
                fee_pct = self._symbol_fee_pct(sym)
                self._record_partial_closed_trade(
                    state,
                    filled_qty,
                    filled_price,
                    "Exchange-Side Stop Loss Partial Fill",
                    entry_fee=entry_cost * fee_pct,
                    exit_fee=gross_exit * fee_pct,
                    symbol=sym,
                )
                self._emit_event(
                    EventType.ORDER_FILLED,
                    side="SELL",
                    order_id=sl_order_id,
                    qty=round(filled_qty, 6),
                    price=round(filled_price, 2),
                    status="PARTIALLY_FILLED",
                    symbol=sym,
                )
                if remaining > 0.0001:
                    try:
                        self.client.cancel_order(sym, sl_order_id)
                    except Exception as ce:
                        logger.error(f"Failed to cancel partial SL order {sl_order_id}: {ce}")
                    new_sl_res = self._place_sl_with_retry(remaining, float(state.get("stop_loss", 0.0)), symbol=sym)
                    new_sl_id = new_sl_res[0] if new_sl_res else None
                    self.db.save_trade_state(
                        symbol=sym,
                        state="bought",
                        entry_price=state.get("entry_price", 0.0),
                        stop_loss=state.get("stop_loss", 0.0),
                        take_profit=state.get("take_profit", 0.0),
                        highest_price_seen=state.get("highest_price_seen", 0.0),
                        quantity=remaining,
                        opened_at=state.get("opened_at"),
                        last_transition_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        stop_loss_order_id=new_sl_id,
                    )
                return self.db.get_trade_state(sym)

            if status in ("CANCELED", "REJECTED", "EXPIRED"):
                logger.warning(f"Exchange stop-loss order {sl_order_id} was {status}. Restoring SL...")
                new_sl_res = self._place_sl_with_retry(
                    float(state.get("quantity", 0.0)),
                    float(state.get("stop_loss", 0.0)),
                    symbol=sym,
                )
                new_sl_id = new_sl_res[0] if new_sl_res else None
                self.db.save_trade_state(
                    symbol=sym,
                    state="bought",
                    entry_price=state.get("entry_price", 0.0),
                    stop_loss=state.get("stop_loss", 0.0),
                    take_profit=state.get("take_profit", 0.0),
                    highest_price_seen=state.get("highest_price_seen", 0.0),
                    quantity=state.get("quantity", 0.0),
                    opened_at=state.get("opened_at"),
                    last_transition_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    stop_loss_order_id=new_sl_id,
                )
                return self.db.get_trade_state(sym)

        except ExchangeAPIError as e:
            if e.code in (-2013, -2026):
                logger.warning(f"SL order {sl_order_id} not found on exchange; restoring...")
                new_sl_res = self._place_sl_with_retry(
                    float(state.get("quantity", 0.0)),
                    float(state.get("stop_loss", 0.0)),
                    symbol=sym,
                )
                new_sl_id = new_sl_res[0] if new_sl_res else None
                self.db.save_trade_state(
                    symbol=sym,
                    state="bought",
                    entry_price=state.get("entry_price", 0.0),
                    stop_loss=state.get("stop_loss", 0.0),
                    take_profit=state.get("take_profit", 0.0),
                    highest_price_seen=state.get("highest_price_seen", 0.0),
                    quantity=state.get("quantity", 0.0),
                    opened_at=state.get("opened_at"),
                    last_transition_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    stop_loss_order_id=new_sl_id,
                )
                return self.db.get_trade_state(sym)
            logger.error(f"Error checking exchange stop-loss order status: {e}")
        except Exception as e:
            logger.error(f"Error checking exchange stop-loss order status: {e}")

        return state

    def _asset_balance_total(self, balances: Dict[str, Any], asset: str) -> float:
        raw = balances.get(asset.upper(), 0.0)
        if isinstance(raw, dict):
            return float(raw.get("available", 0.0) or 0.0) + float(raw.get("reserved", 0.0) or 0.0)
        try:
            return float(raw or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _symbol_allocation_remaining_usdt(
        self,
        eff_cfg: Any,
        balances: Dict[str, Any],
        ticker_price: float,
        equity: float,
        symbol: str,
    ) -> Optional[float]:
        allocation_pct = float(eff_cfg.portfolio.get("allocation_pct") or 0.0)
        if allocation_pct <= 0:
            return None
        base_coin = self._get_base_asset(symbol)
        current_qty = self._asset_balance_total(balances, base_coin)
        current_exposure = current_qty * ticker_price
        budget = equity * (allocation_pct / 100.0)
        return max(0.0, budget - current_exposure)

    def execute_buy(
        self,
        ticker_price: float,
        atr: float,
        signal_stop_loss_price: Optional[float] = None,
        signal_stop_loss_distance: Optional[float] = None,
        symbol: Optional[str] = None,
        risk_pct_override: Optional[float] = None,
        sl_atr_mult_delta: float = 0.0,
        management_mode: str = "strategy",
    ) -> bool:
        """Open a position sized so that hitting SL loses ``equity * risk_pct``.

        ``risk_pct_override``/``sl_atr_mult_delta`` let the regime policy layer
        shrink risk or widen the initial SL without touching config.

        ``risk_pct`` is a fraction (0.02 = 2% of equity per trade), never a
        percent — startup validation in trading_config.validate_risk_config
        rejects values above 0.10.
        """
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        position_management_mode = str(management_mode or "strategy").lower()
        if position_management_mode not in {"strategy", "manual"}:
            position_management_mode = "strategy"
        manual_management = position_management_mode == "manual"
        from xauby.runtime.telegram_control import trading_pause_reason

        paused, pause_reason = trading_pause_reason()
        if paused:
            logger.warning("BUY blocked for %s: Telegram pause active (%s)", sym, pause_reason)
            self.last_log_message = f"Blocked: Telegram pause ({pause_reason})"
            self._emit_event(
                EventType.RISK_REJECTED,
                reason=f"telegram pause: {pause_reason}",
                symbol=sym,
            )
            return False
        try:
            sc = self._sc(sym)
        except (AttributeError, KeyError):
            sc = None  # minimal stubs / unknown symbol: no per-symbol safety state
        if sc is not None:
            if getattr(sc, "trading_halted", False):
                logger.warning("BUY blocked for %s: trading halted (%s)", sym, sc.halt_reason)
                self._emit_event(
                    EventType.RISK_REJECTED,
                    reason=f"trading halted: {sc.halt_reason}",
                    symbol=sym,
                )
                return False
            if getattr(sc, "candle_stale", False):
                logger.warning("BUY blocked for %s: candle feed stale", sym)
                self._emit_event(EventType.RISK_REJECTED, reason="candle feed stale", symbol=sym)
                return False
        if self._use_sim_broker(sym):
            ticker_price = self._sim_fill_price(sym, ticker_price)
        trade_state = self.db.get_trade_state(sym)
        if trade_state.get("state") != "idle":
            logger.warning(
                "BUY aborted for %s: position state is %s",
                sym,
                trade_state.get("state"),
            )
            return False

        equity = self.get_equity(ticker_price, symbol=sym)
        if equity <= 0:
            logger.error("Equity is 0 or negative. Cannot place BUY order.")
            return False

        allowed, reason = self.check_daily_protections(equity, symbol=sym)
        if allowed:
            try:
                allowed, reason = self.check_drawdown_guard(equity, symbol=sym)
            except TypeError:
                allowed, reason = self.check_drawdown_guard(equity)
        if not allowed:
            logger.warning(f"BUY order blocked: {reason}")
            self.last_log_message = f"Blocked: {reason}"
            self.send_telegram_alert(f"⚠️ *Trading Blocked by Protection*\nReason: {reason}")
            self._emit_event(
                EventType.RISK_REJECTED,
                reason=reason,
                equity=round(equity, 2),
                symbol=sym,
            )
            return False

        active_strategy = None
        if hasattr(self, "_strategy_name_for_symbol"):
            active_strategy = self._strategy_name_for_symbol(sym)
        eff_cfg = resolve_trading_config(
            self.config,
            active_strategy,
            symbol=sym,
            for_live=True,
        )
        risk_pct = float(eff_cfg.portfolio.get("risk_pct", 0.01))
        if risk_pct_override is not None and risk_pct_override > 0:
            risk_pct = float(risk_pct_override)
        sl_atr_mult = float(eff_cfg.strategy.get("sl_atr_mult") or 2.0) + float(sl_atr_mult_delta or 0.0)

        # CDC-pure mode: no stop loss, fixed-fraction sizing. The strategy exits
        # only on a RED zone (signal SELL), so position size can no longer be
        # derived from an SL distance — use ``position_pct`` of equity instead.
        disable_sl = bool(eff_cfg.strategy.get("disable_stop_loss", False))
        if disable_sl:
            position_pct = float(eff_cfg.strategy.get("position_pct", 1.0) or 1.0)
            position_pct = max(0.0, min(position_pct, 1.0))
            sl_distance = 0.0
            buy_amount_usdt = (equity * position_pct) if ticker_price > 0 else 0.0
            if self._use_sim_broker(sym):
                # SimBroker debits quote notional plus the entry fee. Reserve
                # that fee so a 100% fixed-fraction order remains executable.
                buy_amount_usdt /= 1.0 + self._symbol_fee_pct(sym)
            qty = buy_amount_usdt / ticker_price if ticker_price > 0 else 0.0
        else:
            if signal_stop_loss_distance is not None and signal_stop_loss_distance > 0:
                sl_distance = signal_stop_loss_distance
            elif signal_stop_loss_price is not None and signal_stop_loss_price > 0:
                sl_distance = ticker_price - signal_stop_loss_price
            else:
                sl_distance = atr * sl_atr_mult

            if sl_distance <= 0:
                logger.error(f"Invalid SL distance: {sl_distance:.4f} (atr={atr:.4f}). Aborting BUY.")
                return False

            risk_amount = equity * risk_pct
            qty = risk_amount / sl_distance
            buy_amount_usdt = qty * ticker_price

            max_pos_pct = float(eff_cfg.portfolio.get("max_position_per_trade_pct", 28.0)) / 100.0
            max_pos_usdt = equity * max_pos_pct
            if buy_amount_usdt > max_pos_usdt:
                logger.info(f"Capping buy amount from {buy_amount_usdt:.2f} USDT to max position size {max_pos_usdt:.2f} USDT ({max_pos_pct*100:.1f}%)")
                buy_amount_usdt = max_pos_usdt
                qty = buy_amount_usdt / ticker_price

        min_order_amt = float(eff_cfg.portfolio.get("min_order_amount", 10.0))
        if buy_amount_usdt < min_order_amt:
            msg = f"BUY order value {buy_amount_usdt:.2f} USDT is below minimum {min_order_amt:.2f} USDT. Aborting."
            logger.warning(msg)
            self.last_log_message = msg
            self._emit_event(
                EventType.RISK_REJECTED,
                reason=f"Order value {buy_amount_usdt:.2f} USDT below minimum {min_order_amt:.2f} USDT",
                equity=round(equity, 2),
                notional=round(buy_amount_usdt, 2),
                symbol=sym,
            )
            return False

        self._emit_event(
            EventType.RISK_CHECK_PASSED,
            equity=round(equity, 2),
            qty=round(qty, 6),
            notional=round(buy_amount_usdt, 2),
            sl_distance=round(sl_distance, 4),
            risk_pct=risk_pct,
        )

        if self._use_sim_broker(sym):
            broker = self._broker_for_symbol(sym)
            result = broker.execute_buy(sym, qty, ticker_price, buy_amount_usdt)
            if not result.success:
                msg = result.error or "SimBroker buy failed"
                logger.warning(msg)
                self.last_log_message = msg
                return False
            stop_loss = 0.0 if (disable_sl or manual_management) else (ticker_price - sl_distance)
            take_profit = (
                0.0
                if manual_management
                else self._fixed_take_profit_price(ticker_price, eff_cfg.strategy_name, eff_cfg.strategy)
            )
            now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            self._emit_event(
                EventType.ORDER_SUBMITTED,
                side="BUY",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                notional=round(buy_amount_usdt, 2),
                order_type="PAPER",
            )
            self._emit_event(
                EventType.ORDER_FILLED,
                side="BUY",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                status="FILLED",
            )
            
            self.db.save_trade_state(
                symbol=sym,
                state="bought",
                entry_price=ticker_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                highest_price_seen=ticker_price,
                quantity=qty,
                opened_at=now_iso,
                last_transition_at=now_iso,
                management_mode=position_management_mode,
            )
            
            base_coin = self._get_base_asset(sym)
            if manual_management:
                msg = f"🟢 [PAPER BUY] Filled {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT (manual sell only)"
            elif disable_sl:
                msg = f"🟢 [PAPER BUY] Filled {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT (no SL — CDC exit on RED)"
            else:
                msg = f"🟢 [PAPER BUY] Filled {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT (Risk: {risk_pct*100}%, SL: {stop_loss:.2f})"
            logger.info(msg)
            self.last_log_message = msg
            self.send_telegram_alert(msg)
            self._emit_event(
                EventType.POSITION_OPENED,
                entry=round(ticker_price, 2),
                stop_loss=round(stop_loss, 2),
                take_profit=round(take_profit, 2),
                qty=round(qty, 6),
            )
            return True

        if self.simulate_only:
            fee_pct = self._symbol_fee_pct(sym)
            fees = buy_amount_usdt * fee_pct
            total_spent = buy_amount_usdt + fees

            with self._sim_balance_lock:
                sim_bal = self.get_simulated_balance()
                if sim_bal < total_spent:
                    msg = f"Insufficient simulated balance for BUY. Need {total_spent:.2f} USDT, have {sim_bal:.2f} USDT."
                    logger.warning(msg)
                    self.last_log_message = msg
                    return False

                self.save_simulated_balance(sim_bal - total_spent)

            stop_loss = 0.0 if (disable_sl or manual_management) else (ticker_price - sl_distance)
            take_profit = (
                0.0
                if manual_management
                else self._fixed_take_profit_price(ticker_price, eff_cfg.strategy_name, eff_cfg.strategy)
            )
            now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            self._emit_event(
                EventType.ORDER_SUBMITTED,
                side="BUY",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                notional=round(buy_amount_usdt, 2),
                order_type="PAPER",
            )
            self._emit_event(
                EventType.ORDER_FILLED,
                side="BUY",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                status="FILLED",
            )

            self.db.save_trade_state(
                symbol=sym,
                state="bought",
                entry_price=ticker_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                highest_price_seen=ticker_price,
                quantity=qty,
                opened_at=now_iso,
                last_transition_at=now_iso,
                management_mode=position_management_mode,
            )

            base_coin = self._get_base_asset(sym)
            if manual_management:
                msg = f"🟢 [PAPER BUY] Filled {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT (manual sell only)"
            elif disable_sl:
                msg = f"🟢 [PAPER BUY] Filled {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT (no SL - CDC exit on RED)"
            else:
                msg = f"🟢 [PAPER BUY] Filled {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT (Risk: {risk_pct*100}%, SL: {stop_loss:.2f})"
            logger.info(msg)
            self.last_log_message = msg
            self.send_telegram_alert(msg)
            self._emit_event(
                EventType.POSITION_OPENED,
                entry=round(ticker_price, 2),
                stop_loss=round(stop_loss, 2),
                take_profit=round(take_profit, 2),
                qty=round(qty, 6),
            )
            return True
        else:
            if self.read_only:
                logger.info("[READ ONLY] Skipping live BUY order execution.")
                return False

            try:
                b = self.client.get_balances()
                avail_usdt = b.get(self._quote_asset(), {}).get("available", 0.0)
                base_coin = self._get_base_asset(sym)
                existing_base_qty = self._asset_balance_total(b, base_coin)
                existing_base_value = existing_base_qty * ticker_price
                if existing_base_value >= min_order_amt:
                    msg = (
                        f"BUY aborted for {sym}: exchange already holds "
                        f"{existing_base_qty:.8f} {base_coin} (~{existing_base_value:.2f} USDT)."
                    )
                    logger.warning(msg)
                    self.last_log_message = msg
                    self._emit_event(
                        EventType.RISK_REJECTED,
                        reason=msg,
                        equity=round(equity, 2),
                        symbol=sym,
                    )
                    return False

                remaining_alloc = self._symbol_allocation_remaining_usdt(
                    eff_cfg, b, ticker_price, equity, sym
                )
                if remaining_alloc is not None and buy_amount_usdt > remaining_alloc:
                    logger.info(
                        "Capping %s BUY from %.2f USDT to remaining allocation %.2f USDT",
                        sym,
                        buy_amount_usdt,
                        remaining_alloc,
                    )
                    buy_amount_usdt = remaining_alloc
                    qty = buy_amount_usdt / ticker_price if ticker_price > 0 else 0.0
                    if buy_amount_usdt < min_order_amt:
                        msg = (
                            f"BUY aborted for {sym}: remaining allocation "
                            f"{buy_amount_usdt:.2f} USDT is below minimum {min_order_amt:.2f} USDT."
                        )
                        logger.warning(msg)
                        self.last_log_message = msg
                        self._emit_event(
                            EventType.RISK_REJECTED,
                            reason=msg,
                            equity=round(equity, 2),
                            notional=round(buy_amount_usdt, 2),
                            symbol=sym,
                        )
                        return False

                if avail_usdt < buy_amount_usdt:
                    logger.warning(f"Live USDT balance {avail_usdt:.2f} is less than required {buy_amount_usdt:.2f}. Capping to available.")
                    buy_amount_usdt = avail_usdt * 0.98
                    qty = buy_amount_usdt / ticker_price
                    if buy_amount_usdt < min_order_amt:
                        logger.error("Capped buy amount below exchange minimum. Aborting.")
                        return False

                order_type_config = self.config.get("execution", {}).get("order_type", "LIMIT").upper()
                buy_client_id = make_client_id("buy")
                logger.info(
                    f"Placing LIVE BUY order: {buy_amount_usdt:.2f} USDT using {order_type_config} "
                    f"@ {ticker_price:.2f} USDT (clientId={buy_client_id})..."
                )
                self._emit_event(
                    EventType.ORDER_SUBMITTED,
                    side="BUY",
                    client_id=buy_client_id,
                    qty=round(qty, 6),
                    price=round(ticker_price, 2),
                    notional=round(buy_amount_usdt, 2),
                    order_type=order_type_config,
                )
                res = self.client.place_order(
                    symbol=sym,
                    side="BUY",
                    order_type=order_type_config,
                    amount=buy_amount_usdt,
                    price=ticker_price,
                    client_id=buy_client_id,
                )

                order_id = str(res.get("orderId") or res.get("id") or "")
                status = res.get("status", "").upper()
                filled_qty = float(res.get("executedQty", 0.0) or res.get("filled", 0.0))
                order_details = res

                if status not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    status, filled_qty, order_details = self._poll_order_adaptive(order_id, symbol=sym)

                if status not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    logger.warning(f"Order {order_id} timeout reached (status: {status}). Cancelling order...")
                    try:
                        self.client.cancel_order(sym, order_id)
                    except Exception as ce:
                        logger.error(f"Error cancelling order {order_id}: {ce}")
                    try:
                        order_details = self.client.get_order(sym, order_id)
                        status = order_details.get("status", "").upper()
                        filled_qty = float(order_details.get("executedQty", 0.0) or order_details.get("filled", 0.0))
                    except Exception as pe:
                        logger.error(f"Error fetching final order state after cancellation: {pe}")

                # Entry market fallback: a LIMIT entry that did not fully fill
                # before the timeout is cancelled above, which would leave the
                # strategy without the position the backtest assumed it holds —
                # the PositionSimulator fills entries at market, so a silent skip
                # breaks live/backtest parity and the bot misses the move. When
                # enabled, top up the unfilled remainder with a MARKET order.
                fallback_quote_spent = 0.0
                entry_market_fallback = bool(
                    self.config.get("execution", {}).get("entry_market_fallback", True)
                )
                limit_quote_spent = float(order_details.get("cummulativeQuoteQty", 0.0) or 0.0)
                remaining_quote = buy_amount_usdt - limit_quote_spent
                if (
                    entry_market_fallback
                    and order_type_config != "MARKET"
                    and status != "FILLED"
                    and remaining_quote >= min_order_amt
                ):
                    fb_client_id = make_client_id("buyfb")
                    logger.warning(
                        f"LIMIT entry {order_id} unfilled ({filled_qty:.6f} filled). "
                        f"Market fallback for {remaining_quote:.2f} USDT (clientId={fb_client_id})..."
                    )
                    self._emit_event(
                        EventType.ORDER_SUBMITTED,
                        side="BUY",
                        client_id=fb_client_id,
                        notional=round(remaining_quote, 2),
                        order_type="MARKET",
                        context="entry_market_fallback",
                    )
                    try:
                        fb_res = self.client.place_order(
                            symbol=sym,
                            side="BUY",
                            order_type="MARKET",
                            amount=remaining_quote,
                            client_id=fb_client_id,
                        )
                        fb_id = str(fb_res.get("orderId") or fb_res.get("id") or "")
                        fb_status = fb_res.get("status", "").upper()
                        if fb_status not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                            fb_status, _fb_qty, fb_res = self._poll_order_adaptive(fb_id, symbol=sym)
                        fb_filled_qty = float(fb_res.get("executedQty", 0.0) or fb_res.get("filled", 0.0))
                        fallback_quote_spent = float(fb_res.get("cummulativeQuoteQty", 0.0) or 0.0)
                        if fb_filled_qty > 0:
                            filled_qty += fb_filled_qty
                            order_id = fb_id or order_id
                            if fb_status == "FILLED":
                                status = "FILLED"
                    except ExchangeAPIError as fe:
                        logger.error(f"Entry market fallback failed: {fe.msg} (code={fe.code})")
                    except Exception as fe:
                        logger.error(f"Entry market fallback exception: {fe}", exc_info=True)

                cummulative_quote_qty = (
                    float(order_details.get("cummulativeQuoteQty", 0.0) or 0.0) + fallback_quote_spent
                )
                if filled_qty > 0 and cummulative_quote_qty > 0:
                    filled_price = cummulative_quote_qty / filled_qty
                else:
                    filled_price = float(order_details.get("price", 0.0) or ticker_price)
                if filled_price <= 0:
                    filled_price = ticker_price

                if filled_qty > 0:
                    stop_loss = 0.0 if (disable_sl or manual_management) else (filled_price - sl_distance)
                    take_profit = (
                        0.0
                        if manual_management
                        else self._fixed_take_profit_price(
                            filled_price,
                            eff_cfg.strategy_name,
                            eff_cfg.strategy,
                        )
                    )
                    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

                    slip_bps = self._report_slippage(
                        side="BUY", ref_price=ticker_price, fill_price=filled_price,
                        symbol=sym, kind="entry",
                    )
                    self._emit_event(
                        EventType.ORDER_FILLED,
                        side="BUY",
                        order_id=order_id,
                        client_id=buy_client_id,
                        qty=round(filled_qty, 6),
                        price=round(filled_price, 2),
                        ref_price=round(ticker_price, 2),
                        slippage_bps=slip_bps,
                        status=status,
                    )

                    sl_order_id = None
                    if not disable_sl and not manual_management:
                        sl_res = self._place_sl_with_retry(filled_qty, stop_loss, symbol=sym)
                        sl_order_id = sl_res[0] if sl_res else None
                        if not sl_order_id:
                            self.send_telegram_alert(
                                "⚠️ *Exchange Stop Loss Failed*: Position relies on local monitoring until SL is restored.",
                                level=AlertLevel.CRITICAL,
                            )

                    self.db.save_trade_state(
                        symbol=sym,
                        state="bought",
                        entry_price=filled_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        highest_price_seen=filled_price,
                        quantity=filled_qty,
                        opened_at=now_iso,
                        last_transition_at=now_iso,
                        stop_loss_order_id=sl_order_id,
                        management_mode=position_management_mode,
                    )
                    
                    base_coin = self._get_base_asset(sym)
                    if manual_management:
                        sl_msg = "manual sell only; no bot SL"
                    else:
                        sl_msg = "no SL — CDC exit on RED" if disable_sl else f"SL set to {stop_loss:.2f}"
                    slip_msg = f" | slip {slip_bps:+.1f}bps"
                    if status == "FILLED":
                        msg = f"🚀 [LIVE BUY FILLED] {filled_qty:.6f} {base_coin} @ {filled_price:.2f} USDT. {sl_msg}{slip_msg}"
                    else:
                        msg = f"⚠️ [LIVE BUY PARTIAL FILL] {filled_qty:.6f} {base_coin} @ {filled_price:.2f} USDT (Ordered: {qty:.6f}). Status: {status}. {sl_msg}{slip_msg}"
                    
                    logger.info(msg)
                    self.last_log_message = msg
                    self.send_telegram_alert(msg)
                    self._emit_event(
                        EventType.POSITION_OPENED,
                        entry=round(filled_price, 2),
                        stop_loss=round(stop_loss, 2),
                        take_profit=round(take_profit, 2),
                        qty=round(filled_qty, 6),
                        status=status,
                    )
                    return True
                else:
                    self.last_log_message = f"BUY order not filled. Status: {status}."
                    logger.warning(f"BUY order {order_id} not filled. Status: {status}.")
                    return False

            except ExchangeAPIError as e:
                msg = f"❌ LIVE BUY API Error: {e.msg} (code={e.code})"
                logger.error(msg)
                self.last_log_message = msg
                self.send_telegram_alert(msg)
                self._emit_event(
                    EventType.ORDER_FAILED,
                    context="execute_buy",
                    error=msg,
                    code=getattr(e, "code", None),
                    symbol=sym,
                )
                return False
            except Exception as e:
                msg = f"❌ LIVE BUY Exception: {e}"
                logger.error(msg, exc_info=True)
                self.last_log_message = msg
                self._emit_event(
                    EventType.ORDER_FAILED,
                    context="execute_buy",
                    error=str(e),
                    symbol=sym,
                )
                return False

    def execute_sell(self, state: Dict[str, Any], ticker_price: float, trigger_reason: str, symbol: Optional[str] = None) -> bool:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        if self._use_sim_broker(sym):
            ticker_price = self._sim_fill_price(sym, ticker_price)
        qty = state["quantity"]
        # Immutable tracked quantity. ``qty`` below may be capped to the
        # exchange-available balance for the order, but remaining-position and
        # SL-restore math must use the original tracked size or a partial fill
        # can silently close (orphan) the untraded remainder.
        state_qty = qty
        entry_price = state["entry_price"]
        opened_at = state["opened_at"]

        if qty <= 0:
            logger.error("No quantity to sell. Aborting SELL.")
            return False

        if "stop loss" in trigger_reason.lower() or "stop_loss" in trigger_reason.lower():
            self._emit_event(
                EventType.STOP_LOSS_TRIGGERED,
                price=round(ticker_price, 2),
                stop_loss=round(float(state.get("stop_loss", 0.0)), 2),
                reason=trigger_reason,
            )

        if self._use_sim_broker(sym):
            broker = self._broker_for_symbol(sym)
            entry_cost = qty * entry_price
            result = broker.execute_sell(sym, qty, ticker_price, entry_price, entry_cost)
            if not result.success:
                logger.error(result.error or "SimBroker sell failed")
                return False
            entry_fee = entry_cost * broker._fee_pct(sym)
            gross_exit = qty * ticker_price
            net_pnl, net_pnl_pct = self._closed_pnl_after_fees(
                entry_cost=entry_cost,
                gross_exit=gross_exit,
                entry_fee=entry_fee,
                exit_fee=result.fees,
            )
            self._emit_event(
                EventType.ORDER_SUBMITTED,
                side="SELL",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                order_type="PAPER",
                trigger=trigger_reason,
            )
            self._emit_event(
                EventType.ORDER_FILLED,
                side="SELL",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                status="FILLED",
            )
            if not self._record_closed_trade_atomic(
                state,
                qty,
                ticker_price,
                trigger_reason,
                entry_fee=entry_fee,
                exit_fee=result.fees,
                symbol=sym,
            ):
                logger.error("Failed to atomically close sim-broker position")
                return False
            base_coin = self._get_base_asset(sym)
            msg = (
                f"🔴 [PAPER SELL] Exit {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT | "
                f"Trigger: {trigger_reason} | PnL: {net_pnl:+.2f} USDT ({net_pnl_pct:+.2f}%)"
            )
            logger.info(msg)
            self.last_log_message = msg
            self.send_telegram_alert(msg)
            self._emit_event(
                EventType.POSITION_CLOSED,
                exit=round(ticker_price, 2),
                pnl=round(net_pnl, 2),
                pnl_pct=round(net_pnl_pct, 2),
                trigger=trigger_reason,
            )
            return True

        if self.simulate_only:
            gross_exit = qty * ticker_price
            fee_pct = self._symbol_fee_pct(sym)
            fees = gross_exit * fee_pct
            net_exit = gross_exit - fees
            
            entry_cost = qty * entry_price
            entry_fee = entry_cost * fee_pct
            net_pnl, net_pnl_pct = self._closed_pnl_after_fees(
                entry_cost=entry_cost,
                gross_exit=gross_exit,
                entry_fee=entry_fee,
                exit_fee=fees,
            )
            
            
            with self._sim_balance_lock:
                sim_bal = self.get_simulated_balance()
                self.save_simulated_balance(sim_bal + net_exit)

            self._emit_event(
                EventType.ORDER_SUBMITTED,
                side="SELL",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                order_type="PAPER",
                trigger=trigger_reason,
            )
            self._emit_event(
                EventType.ORDER_FILLED,
                side="SELL",
                qty=round(qty, 6),
                price=round(ticker_price, 2),
                status="FILLED",
            )

            if not self._record_closed_trade_atomic(
                state,
                qty,
                ticker_price,
                trigger_reason,
                entry_fee=entry_fee,
                exit_fee=fees,
                symbol=sym,
            ):
                logger.error("Failed to atomically close paper position")
                return False
            
            base_coin = self._get_base_asset(sym)
            msg = f"🔴 [PAPER SELL] Exit {qty:.6f} {base_coin} @ {ticker_price:.2f} USDT | Trigger: {trigger_reason} | PnL: {net_pnl:+.2f} USDT ({net_pnl_pct:+.2f}%)"
            logger.info(msg)
            self.last_log_message = msg
            self.send_telegram_alert(msg)
            self._emit_event(
                EventType.POSITION_CLOSED,
                exit=round(ticker_price, 2),
                pnl=round(net_pnl, 2),
                pnl_pct=round(net_pnl_pct, 2),
                trigger=trigger_reason,
            )
            return True
        else:
            if self.read_only:
                logger.info("[READ ONLY] Skipping live SELL order execution.")
                return False

            sl_order_id = state.get("stop_loss_order_id")
            sell_succeeded = False
            position_cleared = False

            try:
                if sl_order_id:
                    logger.info(f"Cancelling active exchange-side stop-loss order {sl_order_id} before normal SELL...")
                    try:
                        self.client.cancel_order(sym, sl_order_id)
                    except Exception as e:
                        logger.error(f"Failed to cancel exchange-side stop-loss order {sl_order_id}: {e}")

                base_coin = self._get_base_asset(sym)
                avail_base = 0.0
                reserved_base = 0.0
                if sl_order_id:
                    for attempt in range(1, 7):
                        b = self.client.get_balances()
                        bal = b.get(base_coin, {}) or {}
                        avail_base = float(bal.get("available", 0.0) or 0.0)
                        reserved_base = float(bal.get("reserved", 0.0) or 0.0)
                        if avail_base >= qty:
                            break
                        logger.info(
                            "Waiting for %s balance to unlock after SL cancel "
                            "(attempt %s/6, available=%.8f, reserved=%.8f, target=%.8f)",
                            base_coin,
                            attempt,
                            avail_base,
                            reserved_base,
                            qty,
                        )
                        time.sleep(0.5)
                else:
                    b = self.client.get_balances()
                    bal = b.get(base_coin, {}) or {}
                    avail_base = float(bal.get("available", 0.0) or 0.0)
                    reserved_base = float(bal.get("reserved", 0.0) or 0.0)
                if avail_base < qty:
                    logger.warning(
                        f"Live {base_coin} balance {avail_base:.6f} is less than state quantity "
                        f"{qty:.6f} (reserved={reserved_base:.6f}). Selling available balance instead."
                    )
                    qty = avail_base

                f = self.client.get_symbol_filters(sym)
                min_qty = float(f.get("minQty") or 0.0)
                step_size = float(f.get("stepSize") or 0.0001)
                min_notional = float(f.get("minNotional") or 0.0)
                min_notional_qty = (
                    min_notional / float(ticker_price)
                    if min_notional > 0 and float(ticker_price) > 0
                    else 0.0
                )
                dust_limit = max(min_qty, step_size, min_notional_qty)

                if qty < dust_limit:
                    if state_qty >= dust_limit:
                        # The exchange shows almost no available balance, yet we
                        # track a real position. Usually the SL cancel above has
                        # not released the reserved balance yet, the cancel
                        # failed, or DB/exchange have drifted. Clearing state
                        # here would orphan a real position, so keep state and
                        # alert for manual reconcile instead of silently zeroing.
                        msg = (
                            f"🚨 *RECONCILE NEEDED* {sym}: {base_coin} available "
                            f"{avail_base:.6f} but tracked position is {state_qty:.6f}. "
                            f"Not selling/clearing — verify the position and stop-loss on the exchange."
                        )
                        logger.error(msg)
                        self.send_telegram_alert(msg, level=AlertLevel.CRITICAL)
                        return False
                    logger.error(f"{base_coin} quantity {qty:.6f} too small to sell (below exchange limit/step {dust_limit:.6f}). Clearing position state.")
                    self.db.save_trade_state(sym, "idle")
                    position_cleared = True
                    return False

                order_type_config = self._resolve_sell_order_type(trigger_reason)
                sell_client_id = make_client_id("sell")

                logger.info(
                    f"Placing LIVE SELL order: {qty:.6f} {base_coin} using {order_type_config} "
                    f"@ {ticker_price:.2f} USDT... Trigger: {trigger_reason}"
                )
                self._emit_event(
                    EventType.ORDER_SUBMITTED,
                    side="SELL",
                    client_id=sell_client_id,
                    qty=round(qty, 6),
                    price=round(ticker_price, 2),
                    order_type=order_type_config,
                    trigger=trigger_reason,
                )
                res = self.client.place_order(
                    symbol=sym,
                    side="SELL",
                    order_type=order_type_config,
                    amount=qty,
                    price=ticker_price,
                    client_id=sell_client_id,
                )

                order_id = str(res.get("orderId") or res.get("id") or "")
                status = res.get("status", "").upper()
                filled_qty = float(res.get("executedQty", 0.0) or res.get("filled", 0.0))
                order_details = res

                if status not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    status, filled_qty, order_details = self._poll_order_adaptive(order_id, symbol=sym)

                if status not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    logger.warning(f"Order {order_id} timeout reached (status: {status}). Cancelling order...")
                    try:
                        self.client.cancel_order(sym, order_id)
                    except Exception as ce:
                        logger.error(f"Error cancelling order {order_id}: {ce}")
                    try:
                        order_details = self.client.get_order(sym, order_id)
                        status = order_details.get("status", "").upper()
                        filled_qty = float(order_details.get("executedQty", 0.0) or order_details.get("filled", 0.0))
                    except Exception as pe:
                        logger.error(f"Error fetching final order state after cancellation: {pe}")

                cummulative_quote_qty = float(order_details.get("cummulativeQuoteQty", 0.0) or 0.0)
                if filled_qty > 0 and cummulative_quote_qty > 0:
                    filled_price = cummulative_quote_qty / filled_qty
                else:
                    filled_price = float(order_details.get("price", 0.0) or ticker_price)
                if filled_price <= 0:
                    filled_price = ticker_price

                if filled_qty > 0:
                    sell_succeeded = True
                    slip_bps = self._report_slippage(
                        side="SELL", ref_price=ticker_price, fill_price=filled_price,
                        symbol=sym, kind="exit",
                    )
                    self._emit_event(
                        EventType.ORDER_FILLED,
                        side="SELL",
                        order_id=order_id,
                        client_id=sell_client_id,
                        qty=round(filled_qty, 6),
                        price=round(filled_price, 2),
                        ref_price=round(ticker_price, 2),
                        slippage_bps=slip_bps,
                        status=status,
                    )

                    entry_cost = filled_qty * entry_price
                    gross_exit = filled_qty * filled_price
                    fee_pct = self._symbol_fee_pct(sym)
                    entry_fee = entry_cost * fee_pct
                    exit_fee = gross_exit * fee_pct
                    net_pnl, net_pnl_pct = self._closed_pnl_after_fees(
                        entry_cost=entry_cost,
                        gross_exit=gross_exit,
                        entry_fee=entry_fee,
                        exit_fee=exit_fee,
                    )

                    remaining_qty = max(0.0, state_qty - filled_qty)
                    min_remaining_qty = self._min_tradeable_base_qty(sym, filled_price)
                    remaining_is_tradeable = (
                        remaining_qty > 0.0001
                        and (min_remaining_qty <= 0 or remaining_qty >= min_remaining_qty)
                    )
                    if remaining_is_tradeable:
                        self._record_partial_closed_trade(
                            state,
                            filled_qty,
                            filled_price,
                            trigger_reason,
                            entry_fee=entry_fee,
                            exit_fee=exit_fee,
                            symbol=sym,
                        )
                        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                        new_sl_res = self._place_sl_with_retry(
                            remaining_qty, float(state.get("stop_loss", 0.0)), symbol=sym
                        )
                        new_sl_order_id = new_sl_res[0] if new_sl_res else None
                        self.db.save_trade_state(
                            symbol=sym,
                            state="bought",
                            entry_price=entry_price,
                            stop_loss=state.get("stop_loss", 0.0),
                            take_profit=state.get("take_profit", 0.0),
                            highest_price_seen=state.get("highest_price_seen", entry_price),
                            quantity=remaining_qty,
                            opened_at=opened_at,
                            last_transition_at=now_iso,
                            stop_loss_order_id=new_sl_order_id,
                        )
                        msg = (
                            f"🔴 [LIVE SELL PARTIAL] Exit {filled_qty:.6f} {base_coin} @ {filled_price:.2f} USDT "
                            f"| Remaining: {remaining_qty:.6f} {base_coin} | Trigger: {trigger_reason} "
                            f"| PnL: {net_pnl:+.2f} USDT"
                        )
                    else:
                        if remaining_qty > 0.0001 and min_remaining_qty > 0:
                            logger.warning(
                                "Treating %s live sell remainder %.8f %s as dust "
                                "(tradeable threshold %.8f); clearing local state.",
                                sym,
                                remaining_qty,
                                base_coin,
                                min_remaining_qty,
                            )
                        if not self._record_closed_trade_atomic(
                            state,
                            filled_qty,
                            filled_price,
                            trigger_reason,
                            entry_fee=entry_fee,
                            exit_fee=exit_fee,
                            symbol=sym,
                        ):
                            logger.error("Failed to atomically close live position")
                            return False
                        msg = (
                            f"🎉 [LIVE SELL FILLED] Exit {filled_qty:.6f} {base_coin} @ {filled_price:.2f} USDT "
                            f"| Trigger: {trigger_reason} | PnL: {net_pnl:+.2f} USDT ({net_pnl_pct:+.2f}%)"
                        )
                        self._emit_event(
                            EventType.POSITION_CLOSED,
                            exit=round(filled_price, 2),
                            pnl=round(net_pnl, 2),
                            pnl_pct=round(net_pnl_pct, 2),
                            trigger=trigger_reason,
                        )

                    logger.info(msg)
                    self.last_log_message = msg
                    self.send_telegram_alert(msg)
                    return True
                else:
                    self.last_log_message = f"SELL order not filled. Status: {status}."
                    logger.warning(f"SELL order {order_id} not filled. Status: {status}.")
                    return False

            except ExchangeAPIError as e:
                msg = f"❌ LIVE SELL API Error: {e.msg} (code={e.code})"
                logger.error(msg)
                self.last_log_message = msg
                self.send_telegram_alert(msg)
                self._emit_event(
                    EventType.ORDER_FAILED,
                    context="execute_sell",
                    error=msg,
                    code=getattr(e, "code", None),
                    symbol=sym,
                )
                return False
            except Exception as e:
                msg = f"❌ LIVE SELL Exception: {e}"
                logger.error(msg, exc_info=True)
                self.last_log_message = msg
                self._emit_event(
                    EventType.ORDER_FAILED,
                    context="execute_sell",
                    error=str(e),
                    symbol=sym,
                )
                return False
            finally:
                # position_cleared: state was already set to idle (dust) —
                # restoring here would resurrect the position as "bought".
                if not sell_succeeded and not position_cleared:
                    try:
                        # Restore SL for the full tracked size, not the
                        # possibly-capped order quantity.
                        self._restore_stop_loss_state(state, state_qty, symbol=sym)
                    except Exception as restore_err:
                        logger.error(f"Failed to restore stop-loss after failed sell: {restore_err}")
