import os
import time
import json
import logging
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import pandas as pd
from typing import Dict, Any, Optional

from xauby.observability import EventType, TickContext
from xauby.observability.replay import ContextBuilder
from xauby.notifications.interface import AlertLevel
from xauby.api.errors import ExchangeAPIError
from xauby.runtime.paths import dashboard_focus_path, sentiment_guard_state_path
from xauby.runtime.manual_orders import claim_manual_order_request
from xauby.engine.regime_policy import apply_regime_policy, regime_policy_enabled
from xauby.runtime.candle_utils import candle_is_stale, drop_forming_bar, use_closed_candles
from xauby.runtime.exits import (
    minimal_roi_pct,
    next_trailing_stop,
    resolve_minimal_roi,
    resolve_partial_tp,
)
from xauby.runtime.trading_config import resolve_trading_config
from xauby.utils.atomic_io import atomic_json_write
from xauby.api.utils import round_step
from xauby.analytics.calculator import position_excursions_pct

logger = logging.getLogger("lite_bot")


def estimate_net_unrealized_pnl(
    state: Dict[str, Any],
    *,
    mark_price: float,
    fee_pct: float,
) -> Dict[str, float]:
    entry = float(state.get("entry_price") or 0.0)
    qty = float(state.get("quantity") or 0.0)
    mark = float(mark_price or 0.0)
    fee = max(0.0, float(fee_pct or 0.0))
    if entry <= 0 or qty <= 0 or mark <= 0 or str(state.get("state") or "") != "bought":
        return {
            "gross_pnl": 0.0,
            "entry_fee": 0.0,
            "exit_fee": 0.0,
            "total_fees": 0.0,
            "funding_paid": float(state.get("funding_paid") or 0.0),
            "net_pnl": 0.0,
            "net_pnl_pct": 0.0,
        }
    side = str(state.get("position_side") or "LONG").upper()
    direction = -1.0 if side == "SHORT" else 1.0
    entry_notional = entry * qty
    exit_notional = mark * qty
    gross = direction * (mark - entry) * qty
    entry_fee = entry_notional * fee
    exit_fee = exit_notional * fee
    funding_paid = float(state.get("funding_paid") or 0.0)
    net = gross - entry_fee - exit_fee - funding_paid
    return {
        "gross_pnl": gross,
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "total_fees": entry_fee + exit_fee,
        "funding_paid": funding_paid,
        "net_pnl": net,
        "net_pnl_pct": (net / entry_notional * 100.0) if entry_notional > 0 else 0.0,
    }


class LoopMixin:
    def _wait_for_sl_replacement_balance(
        self,
        symbol: str,
        qty: float,
        stop_loss: float,
        *,
        timeout_s: float = 6.0,
        interval_s: float = 0.5,
    ) -> None:
        """Wait briefly for a canceled spot SL order to release locked base.

        Some spot venues acknowledge cancel before the locked base balance is
        immediately usable for the replacement STOP_LOSS_LIMIT.  Without this
        short wait, the replacement path sees only fee dust as available and
        emits noisy failure alerts despite protection being restored seconds
        later.
        """
        try:
            base_coin = self._get_base_asset(symbol)
            filter_fn = getattr(self, "_symbol_filters_cached", self.client.get_symbol_filters)
            filters = filter_fn(symbol)
            step = float(filters.get("stepSize") or 0.0)
            min_qty = float(filters.get("minQty") or 0.0)
            min_notional = float(filters.get("minNotional") or 0.0)
            min_allowed = max(min_qty, step, 0.0)
            deadline = time.time() + max(0.0, float(timeout_s))
            while time.time() < deadline:
                balances = self.client.get_balances()
                entry = balances.get(base_coin) or {}
                available = float(entry.get("available", 0.0) or 0.0)
                rounded = available
                if step > 0:
                    try:
                        rounded = round_step(available, step)
                    except Exception:
                        rounded = available
                if rounded >= min_allowed and (
                    min_notional <= 0 or rounded * float(stop_loss) >= min_notional
                ):
                    if rounded + max(step, 0.0) >= min(float(qty), available):
                        return
                    if rounded >= float(qty) * 0.99:
                        return
                time.sleep(max(0.1, float(interval_s)))
        except Exception as e:
            logger.debug("SL replacement balance wait skipped for %s: %s", symbol, e)

    def _process_manual_order_request(
        self,
        state: Dict[str, Any],
        ticker_price: float,
        atr: float,
        *,
        symbol: str,
    ) -> Optional[bool]:
        """Execute one confirmed local manual request, or return ``None``."""
        request = claim_manual_order_request(symbol, project_root=self._project_root)
        if request is None:
            return None

        action = request["action"]
        intent = str(request.get("intent") or ("OPEN_LONG" if action == "BUY" else "CLOSE_POSITION"))
        management_mode = str(request.get("management_mode") or "strategy").lower()
        if management_mode not in {"strategy", "strategy_handoff", "manual"}:
            management_mode = "strategy"
        request_id = str(request.get("request_id") or "")
        reason = ""
        success = False

        if intent in {"OPEN_LONG", "OPEN_SHORT"}:
            spec = self._pair_registry.get(symbol)
            manual_sides = getattr(spec, "manual_allowed_sides", None)
            if not isinstance(manual_sides, (list, tuple, set)):
                manual_sides = getattr(spec, "allowed_sides", ())
            if state.get("state") != "idle":
                reason = f"Manual order rejected: {symbol} already has an open position"
            elif not spec or intent.removeprefix("OPEN_").lower() not in manual_sides:
                reason = f"Manual order rejected: {intent.removeprefix('OPEN_')} is disabled for {symbol}"
            elif self._sc(symbol).feed_snapshot()["feed_degraded"]:
                reason = f"Manual order rejected: market feed is degraded for {symbol}"
            elif self._sc(symbol).blocks_new_entries():
                reason = f"Manual order rejected: RegimeRouter {self._sc(symbol).no_trade_state}"
            else:
                max_open = int(
                    self.config.get("trading", {}).get(
                        "max_open_positions", len(self._pair_registry.active()) or 1
                    )
                )
                open_count = sum(
                    1
                    for item in self._pair_registry.active()
                    if self.db.get_trade_state(item.symbol).get("state") == "bought"
                )
                blocked, block_reason = self._is_buy_blocked_by_cooldown(symbol=symbol)
                if open_count >= max_open:
                    reason = f"Manual order rejected: max open positions ({max_open})"
                elif blocked:
                    reason = f"Manual order rejected: {block_reason}"
                elif intent == "OPEN_SHORT":
                    signal = SimpleNamespace(
                        stop_loss_distance=max(float(atr or 0.0), ticker_price * 0.02),
                        stop_loss_price=0.0,
                    )
                    success = self.execute_open_short(
                        signal,
                        ticker_price,
                        symbol=symbol,
                        manual=True,
                        management_mode=management_mode,
                    )
                    reason = "Manual SHORT submitted" if success else "Manual SHORT execution failed"
                else:
                    success = self.execute_buy(
                        ticker_price,
                        atr,
                        symbol=symbol,
                        management_mode=management_mode,
                    )
                    label = {
                        "strategy": "strategy-managed",
                        "strategy_handoff": "waiting for strategy alignment",
                        "manual": "manual-managed",
                    }[management_mode]
                    reason = f"Manual BUY submitted ({label})" if success else "Manual BUY execution failed"
        elif state.get("state") != "bought":
            reason = f"Manual close rejected: {symbol} has no tracked position"
        elif str(state.get("position_side") or "LONG").upper() == "SHORT":
            success = self.execute_close_short(
                state, ticker_price, trigger_reason="Manual SELL (TUI F8)", symbol=symbol
            )
            reason = "Manual SHORT close submitted" if success else "Manual SHORT close failed"
        else:
            success = self.execute_sell(
                state, ticker_price, trigger_reason="Manual SELL (TUI F8)", symbol=symbol
            )
            reason = "Manual SELL submitted" if success else "Manual SELL execution failed"

        self.last_log_message = reason
        self._emit_event(
            "manual_order_executed" if success else "manual_order_rejected",
            symbol=symbol,
            action=action,
            intent=intent,
            management_mode=management_mode,
            request_id=request_id,
            reason=reason,
        )
        if not success:
            logger.warning("%s (request_id=%s)", reason, request_id)
            self.send_telegram_alert(f"Manual order not executed: {reason}")
        return success

    def _apply_fixed_tp_exit(
        self,
        state: Dict[str, Any],
        action: str,
        reason: str,
        ticker_price: float,
        *,
        sl_confirmed: bool,
    ) -> tuple[str, str]:
        """Turn HOLD into SELL when an engine-managed fixed TP is reached."""
        take_profit = float(state.get("take_profit", 0.0) or 0.0)
        is_short = str(state.get("position_side") or "LONG").upper() == "SHORT"
        tp_hit = ticker_price <= take_profit if is_short else ticker_price >= take_profit
        if (
            state.get("state") == "bought"
            and action != "SELL"
            and not sl_confirmed
            and take_profit > 0
            and tp_hit
        ):
            op = "<=" if is_short else ">="
            return "SELL", f"Fixed TP reached ({ticker_price:.2f} {op} {take_profit:.2f})"
        return action, reason

    def _apply_minimal_roi_exit(
        self,
        state: Dict[str, Any],
        action: str,
        reason: str,
        ticker_price: float,
        *,
        sl_confirmed: bool,
        strat_conf: Dict[str, Any],
    ) -> tuple[str, str]:
        """Turn HOLD into SELL when the minimal-ROI ladder threshold is met.

        Freqtrade-style time-decaying take-profit: ``minimal_roi`` in strategy
        config maps position age (minutes) to the ROI percent that locks in
        the profit. Shares the resolver with the backtest PositionSimulator so
        replay results reflect the exits that actually happen live.
        """
        if (
            state.get("state") != "bought"
            or action == "SELL"
            or sl_confirmed
            or ticker_price <= 0
        ):
            return action, reason
        steps = resolve_minimal_roi(strat_conf)
        if not steps:
            return action, reason
        entry = float(state.get("entry_price", 0.0) or 0.0)
        if entry <= 0:
            return action, reason
        age_minutes = self._position_age_minutes(state.get("opened_at"))
        threshold = minimal_roi_pct(steps, age_minutes)
        if threshold <= 0:
            return action, reason
        is_short = str(state.get("position_side") or "LONG").upper() == "SHORT"
        roi_pct = ((entry - ticker_price) if is_short else (ticker_price - entry)) / entry * 100.0
        if roi_pct >= threshold:
            return "SELL", (
                f"Minimal ROI reached (+{roi_pct:.2f}% >= {threshold:.2f}% "
                f"after {age_minutes / 60.0:.1f}h)"
            )
        return action, reason

    def _maybe_take_partial_tp(
        self,
        state: Dict[str, Any],
        action: str,
        ticker_price: float,
        strat_conf: Dict[str, Any],
        *,
        symbol: str,
    ) -> Dict[str, Any]:
        """Bank the one-shot partial TP when its threshold is reached.

        Runs after the exit pipeline so a full exit (SELL) always wins the
        tick; on success returns the refreshed state (reduced quantity +
        partial_tp_taken) for the rest of the tick to work with.
        """
        if (
            action == "SELL"
            or state.get("state") != "bought"
            or bool(state.get("partial_tp_taken"))
            or ticker_price <= 0
        ):
            return state
        threshold_pct, fraction = resolve_partial_tp(strat_conf)
        if threshold_pct <= 0:
            return state
        entry = float(state.get("entry_price", 0.0) or 0.0)
        if entry <= 0:
            return state
        is_short = str(state.get("position_side") or "LONG").upper() == "SHORT"
        roi_pct = ((entry - ticker_price) if is_short else (ticker_price - entry)) / entry * 100.0
        if roi_pct < threshold_pct:
            return state
        try:
            done = self.execute_partial_tp(
                state,
                ticker_price,
                fraction=fraction,
                threshold_pct=threshold_pct,
                symbol=symbol,
            )
        except Exception as exc:
            logger.error("Partial TP execution error for %s: %s", symbol, exc, exc_info=True)
            return state
        if done:
            return self.db.get_trade_state(symbol)
        return state

    def _apply_drawdown_force_close(
        self,
        state: Dict[str, Any],
        action: str,
        reason: str,
        *,
        symbol: str,
    ) -> tuple[str, str]:
        """Force-close an open position when the drawdown guard trips (opt-in).

        Only active when ``risk.drawdown_guard.close_positions`` is true; the
        guard otherwise just blocks new BUYs at the order gate.
        """
        if state.get("state") != "bought" or action == "SELL":
            return action, reason
        cfg = (self.config.get("risk", {}) or {}).get("drawdown_guard", {}) or {}
        if not cfg.get("enabled", False) or not cfg.get("close_positions", False):
            return action, reason
        equity = self.get_equity(symbol=symbol)
        try:
            allowed, dd_reason = self.check_drawdown_guard(equity, symbol=symbol)
        except TypeError:
            allowed, dd_reason = self.check_drawdown_guard(equity)
        if not allowed:
            return "SELL", f"Drawdown guard force-close ({dd_reason})"
        return action, reason

    def _on_ws_status(self, status: Dict[str, Any]):
        event = status.get("event")
        if event == "ws_disconnected":
            reason = str(status.get("error", status.get("reason", "unknown")))
            with self._ws_status_lock:
                now = time.time()
                if self._ws_disconnected_at <= 0:
                    self._ws_disconnected_at = now
                    self._ws_disconnect_alerted_for = 0.0
                started_at = self._ws_disconnected_at
                self._ws_disconnect_reason = reason
            downtime = status.get("downtime_sec", 0)
            self._emit_event(
                EventType.WS_DISCONNECTED,
                reason=reason,
                downtime_sec=downtime,
            )
            self._schedule_ws_disconnect_alert(started_at)
        elif event == "ws_reconnected":
            downtime = float(status.get("downtime_sec", 0))
            with self._ws_status_lock:
                self._ws_disconnected_at = 0.0
                disconnect_was_alerted = self._ws_disconnect_alerted_for > 0
                self._ws_disconnect_reason = "unknown"
                self._ws_disconnect_alerted_for = 0.0
                self._ws_disconnect_alert_pending_for = 0.0
                alert_timer = self._ws_disconnect_alert_timer
                self._ws_disconnect_alert_timer = None
            if alert_timer is not None:
                alert_timer.cancel()
            self._emit_event(EventType.WS_RECONNECTED, downtime_sec=downtime)
            threshold = float(getattr(self, "_ws_disconnect_alert_after_seconds", 45.0))
            if disconnect_was_alerted or downtime >= threshold:
                self.send_telegram_alert(
                    f"✅ *WebSocket Reconnected*\nSymbol: `{self.symbol}`\nDowntime: `{downtime:.0f}s`"
                )
            for sc in self.contexts.values():
                sc.set_feed_degraded(False)
        elif event in ("market_channel_degraded", "order_book_resync_required"):
            sym = str(status.get("symbol") or "").upper().replace("_", "")
            if sym in self.contexts:
                self.contexts[sym].set_feed_degraded(
                    True,
                    str(status.get("error") or event),
                )

    def _schedule_ws_disconnect_alert(self, started_at: float) -> None:
        delay = max(0.0, float(getattr(self, "_ws_disconnect_alert_after_seconds", 45.0)))
        if delay <= 0:
            self._send_ws_disconnect_alert_if_current(started_at)
            return
        with self._ws_status_lock:
            if self._ws_disconnect_alert_pending_for == started_at:
                return
            self._ws_disconnect_alert_pending_for = started_at
            timer = threading.Timer(
                delay,
                self._send_ws_disconnect_alert_if_current,
                args=(started_at,),
            )
            timer.daemon = True
            self._ws_disconnect_alert_timer = timer
        timer.start()

    def _send_ws_disconnect_alert_if_current(self, started_at: float) -> None:
        with self._ws_status_lock:
            if self._ws_disconnected_at != started_at:
                return
            if self._ws_disconnect_alerted_for == started_at:
                return
            self._ws_disconnect_alerted_for = started_at
            self._ws_disconnect_alert_pending_for = 0.0
            self._ws_disconnect_alert_timer = None
            reason = self._ws_disconnect_reason
        self.send_telegram_alert(
            f"⚠️ *WebSocket Disconnected*\nSymbol: `{self.symbol}`\nReason: `{reason}`"
        )

    def _refresh_guard_async(self):
        with self._guard_spawn_lock:
            if self._guard_thread and self._guard_thread.is_alive():
                return

            def _guard_worker():
                try:
                    from xauby.macro.sentiment_guard import evaluate_sentiment_guard
                    res = evaluate_sentiment_guard(self.config)
                    with self._guard_lock:
                        self._guard_score = float(res.get("score", 0.0))
                        self._guard_last_run = time.time()
                except Exception as e:
                    logger.error(f"Sentiment guard async error: {e}")
                    with self._guard_lock:
                        self._guard_last_run = time.time()

            self._guard_thread = threading.Thread(target=_guard_worker, daemon=True)
            self._guard_thread.start()

    def _price_for_symbol(self, symbol: str) -> float:
        sym = symbol.upper().replace("_", "")
        sc = self._sc(sym)
        tick_snap = sc.get_tick_snapshot()
        ws_mono = tick_snap.get("monotonic_ts", 0)
        is_ws_stale = time.monotonic() - ws_mono > 15.0
        price = float(tick_snap.get("last", 0.0) or 0.0)
        if price > 0 and not is_ws_stale:
            return price
        try:
            t = self.client.get_ticker(sym)
            t["timestamp"] = time.time()
            t["monotonic_ts"] = time.monotonic()
            # sc.set_tick only: assigning self.latest_tick would route through
            # _sc() / _active_tick_symbol and can land on a different symbol's
            # context when called outside that symbol's tick.
            sc.set_tick(t)
            return float(t.get("last", 0.0) or 0.0)
        except Exception as e:
            logger.debug("price fallback for %s failed: %s", sym, e)
            return price

    def _all_symbols_sim(self) -> bool:
        """True when every active symbol is running in sim/paper mode."""
        from xauby.runtime.architecture_config import per_symbol_execution_mode
        if self.simulate_only:
            return True
        if not per_symbol_execution_mode(self.config):
            return False
        return all(
            self._execution_mode(spec.symbol) == "sim"
            for spec in self._pair_registry.active()
        )

    def _balance_totals_map(self) -> Dict[str, float]:
        # All-sim path: return SimBroker USDT + DB-tracked base quantities.
        # When ANY symbol is live, use exchange balances for the real portfolio.
        # Sim-gated symbols in a mixed portfolio have no real exchange holdings;
        # their virtual SimBroker USDT is kept separate and not mixed in here.
        if self._all_symbols_sim():
            out: Dict[str, float] = {"USDT": float(self.get_simulated_balance())}
            for spec in self._pair_registry.active():
                sym = spec.symbol
                base = self._get_base_asset(sym)
                st = self.db.get_trade_state(sym)
                if st.get("state") == "bought" and str(st.get("position_side") or "LONG").upper() == "SHORT":
                    ledger = self._sim_broker.get_ledger(sym)
                    out["USDT"] += float(ledger.margin_reserved) + float(ledger.unrealized_pnl)
                    out[base] = 0.0
                else:
                    out[base] = float(st["quantity"]) if st.get("state") == "bought" else 0.0
            return out

        # Snapshot cache state under the lock; do the (slow) REST call outside
        # so the WS price-flush thread is never blocked behind network I/O.
        b, cache_age = self._balance_cache_snapshot()
        if not b or cache_age >= self._balance_cache_ttl:
            try:
                fresh = self.client.get_balances()
                self._store_balance_cache(fresh)
                b = fresh
            except Exception as e:
                logger.error(f"Failed to get balances for equity: {e}")
            if not b:
                return {}
        totals: Dict[str, float] = {}
        for asset, entry in (b or {}).items():
            if isinstance(entry, dict):
                totals[asset.upper()] = float(entry.get("available", 0.0) or 0.0) + float(
                    entry.get("reserved", 0.0) or 0.0
                )
        return totals

    def get_portfolio_equity_total(self) -> float:
        totals = self._balance_totals_map()
        if not totals:
            with self._balance_lock:
                last_equity = self._last_equity
            if last_equity is not None:
                logger.warning("Using last known portfolio equity (no balance data)")
                return last_equity
            return 0.0
        usdt = float(totals.get(self._quote_asset(), 0.0) or 0.0)
        total = usdt
        counted_bases = set()
        for spec in self._pair_registry.active():
            base = self._get_base_asset(spec.symbol)
            if base in counted_bases:
                continue
            counted_bases.add(base)
            qty = float(totals.get(base, 0.0) or 0.0)
            if qty <= 0:
                continue
            price = self._price_for_symbol(spec.symbol)
            if price > 0:
                total += qty * price
        with self._balance_lock:
            self._last_equity = total
        return total

    def _sim_portfolio_equity_total(self) -> float:
        """Virtual-pool equity total for sim-gated pairs.

        In a mixed sim+live run ``get_portfolio_equity_total`` reports only the
        REAL exchange portfolio — the SimBroker's virtual capital is deliberately
        kept out of it (see ``_balance_totals_map``). A sim pair's equity card
        must therefore total its OWN virtual pool, otherwise it shows the live
        portfolio total sitting next to a virtual cash balance and two cards for
        different pools never reconcile. Mirrors the all-sim accounting in
        ``_balance_totals_map``, scoped to sim symbols.
        """
        total = float(self.get_simulated_balance())
        for spec in self._pair_registry.active():
            sym = spec.symbol
            if not self._use_sim_broker(sym):
                continue
            st = self.db.get_trade_state(sym)
            if st.get("state") != "bought":
                continue
            if str(st.get("position_side") or "LONG").upper() == "SHORT":
                ledger = self._sim_broker.get_ledger(sym)
                total += float(ledger.margin_reserved) + float(ledger.unrealized_pnl)
            else:
                price = self._price_for_symbol(sym)
                if price > 0:
                    total += float(st["quantity"]) * price
        return total

    def get_symbol_equity_breakdown(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper().replace("_", "")
        base = self._get_base_asset(sym)
        price = self._price_for_symbol(sym)
        # Sim-gated symbols have no real exchange holdings — use SimBroker
        # balance and DB-tracked quantity so the UI reflects virtual state.
        if self._use_sim_broker(sym):
            usdt = float(self.get_simulated_balance())
            st = self.db.get_trade_state(sym)
            is_short = str(st.get("position_side") or "LONG").upper() == "SHORT"
            base_qty = float(st["quantity"]) if st.get("state") == "bought" and not is_short else 0.0
            # A sim pair totals its virtual pool, not the real live portfolio,
            # so portfolio_total_usdt stays consistent with the virtual cash above.
            portfolio_total = self._sim_portfolio_equity_total()
        else:
            totals = self._balance_totals_map()
            usdt = float(totals.get(self._quote_asset(), 0.0) or 0.0)
            base_qty = float(totals.get(base, 0.0) or 0.0)
            st = self.db.get_trade_state(sym)
            portfolio_total = self.get_portfolio_equity_total()
        base_value = base_qty * price if price > 0 else 0.0
        pnl = estimate_net_unrealized_pnl(
            st,
            mark_price=price,
            fee_pct=self._symbol_fee_pct(sym),
        )
        # Derivative positions hold no spot base asset (swap shorts hold none
        # either), so exposure sizes from the open position's notional at mark;
        # spot holdings remain the fallback when no position is tracked.
        open_qty = (
            float(st.get("quantity") or 0.0)
            if str(st.get("state") or "") == "bought"
            else 0.0
        )
        if open_qty > 0 and price > 0:
            exposure_notional = open_qty * price
        else:
            exposure_notional = base_value
        exposure = exposure_notional + float(pnl["net_pnl"])
        return {
            "portfolio_total_usdt": round(portfolio_total, 2),
            "usdt_balance_usdt": round(usdt, 2),
            "base_asset": base,
            "base_quantity": round(base_qty, 8),
            "base_value_usdt": round(base_value, 2),
            "unrealized_pnl_usdt": round(float(pnl["net_pnl"]), 2),
            "unrealized_pnl_gross_usdt": round(float(pnl["gross_pnl"]), 2),
            "estimated_total_fees_usdt": round(float(pnl["total_fees"]), 2),
            "symbol_exposure_usdt": round(exposure, 2),
        }

    def get_equity(self, coin_price: Optional[float] = None, symbol: Optional[str] = None) -> float:
        _ = coin_price
        # For a sim-mode symbol return its own SimBroker balance so that
        # position sizing uses virtual capital, not the live exchange equity.
        if symbol:
            sym = symbol.upper().replace("_", "")
            if self._use_sim_broker(sym):
                return float(self.get_simulated_balance())
        return self.get_portfolio_equity_total()

    def _load_global_guard_state(self) -> Dict[str, Any]:
        if os.path.exists(sentiment_guard_state_path()):
            try:
                with open(sentiment_guard_state_path(), "r", encoding="utf-8") as gf:
                    return json.load(gf) or {}
            except Exception:
                pass
        return {}

    def _macro_guard_applies_to(self, symbol: str) -> bool:
        sym = symbol.upper().replace("_", "")
        guard_cfg = self.config.get("macro_sentiment_guard", {}) or {}
        explicit = guard_cfg.get("apply_to_symbols")
        if explicit is not None:
            allowed = {str(s).upper().replace("_", "") for s in explicit}
            return sym in allowed
        return True

    def _build_macro_guard_for_symbol(self, symbol: str) -> Dict[str, Any]:
        guard_cfg = self.config.get("macro_sentiment_guard", {}) or {}
        guard_state = self._load_global_guard_state()
        enabled = bool(guard_cfg.get("enabled", False))
        applies = self._macro_guard_applies_to(symbol)
        score = float(guard_state.get("score", 0.0))
        with self._guard_lock:
            if self._guard_last_run > 0:
                score = float(self._guard_score)
        threshold = float(guard_cfg.get("blocking_threshold", -0.5))
        blocks_buy = bool(enabled and applies and score < threshold)
        return {
            "enabled": enabled,
            "applies_to_symbol": applies,
            "scope": "gold_macro" if applies else "not_applicable",
            "blocks_buy": blocks_buy,
            "score": score,
            "blocking_threshold": threshold,
            "dxy_score": guard_state.get("dxy_score", 0.0),
            "dxy_price": guard_state.get("dxy_price", 0.0),
            "fred_score": guard_state.get("fred_score", 0.0),
            "fred_rate": guard_state.get("fred_rate", 0.0),
            "news_score": guard_state.get("news_score", 0.0),
            "news_reason": guard_state.get("news_reason", "N/A"),
            "headlines": guard_state.get("headlines", []),
            "summary": guard_state.get("summary", "N/A"),
        }

    def _refresh_balance_cache(self) -> None:
        if self._all_symbols_sim():
            return
        with self._balance_lock:
            generation = int(getattr(self, "_balance_cache_generation", 0) or 0)
        try:
            b = self.client.get_balances()
            self._store_balance_cache(b, expected_generation=generation)
        except Exception as e:
            logger.debug(f"Background balance refresh failed: {e}")

    def _balance_cache_snapshot(self) -> tuple[Dict[str, Any], float]:
        with self._balance_lock:
            return dict(self._balance_cache or {}), time.time() - self._balance_last_update

    def _store_balance_cache(
        self,
        balances: Dict[str, Any],
        *,
        expected_generation: Optional[int] = None,
    ) -> bool:
        with self._balance_lock:
            current_generation = int(getattr(self, "_balance_cache_generation", 0) or 0)
            if expected_generation is not None and expected_generation != current_generation:
                return False
            self._balance_cache = dict(balances or {})
            self._balance_last_update = time.time()
            return True

    def _invalidate_balance_cache(self) -> None:
        """Discard balances after a live fill changes available margin."""
        with self._balance_lock:
            self._balance_cache_generation = (
                int(getattr(self, "_balance_cache_generation", 0) or 0) + 1
            )
            self._balance_cache = {}
            self._balance_last_update = 0.0

    def _maybe_start_balance_refresh(self) -> None:
        if self._all_symbols_sim():
            return
        with self._balance_lock:
            cache_age = time.time() - self._balance_last_update
            if cache_age < self._balance_cache_ttl:
                return
            if self._balance_refresh_thread and self._balance_refresh_thread.is_alive():
                return
            thread = threading.Thread(target=self._refresh_balance_cache, daemon=True)
            self._balance_refresh_thread = thread
        thread.start()

    def get_simulated_balance(self) -> float:
        from xauby.runtime.architecture_config import sim_broker_enabled

        # SimBroker owns the balance file when enabled ({"USDT", "ledgers"}
        # schema); the legacy {"balance"} schema below would read stale data
        # and overwrite the broker's ledgers.
        if sim_broker_enabled(self.config):
            return float(self._sim_broker.get_usdt_balance())
        if not os.path.exists(self.sim_balance_file):
            self.save_simulated_balance(self.initial_balance)
            return self.initial_balance
        try:
            with open(self.sim_balance_file, "r") as f:
                data = json.load(f)
                return float(data.get("balance", self.initial_balance))
        except Exception:
            return self.initial_balance

    def save_simulated_balance(self, balance: float):
        from xauby.runtime.architecture_config import sim_broker_enabled

        try:
            if sim_broker_enabled(self.config):
                self._sim_broker.save_usdt_balance(balance)
                return
            data = {
                "balance": round(balance, 4),
                "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            }
            atomic_json_write(self.sim_balance_file, data)
        except Exception as e:
            logger.error(f"Failed to save simulated balance: {e}")

    def sync_candles(self, symbol: Optional[str] = None) -> None:
        started = time.monotonic()
        specs = self._pair_registry.active()
        if symbol:
            sp = self._pair_registry.get(symbol)
            specs = [sp] if sp and sp.enabled else []
        try:
            now_ms = int(time.time() * 1000)
            now_sec = now_ms // 1000
            data_cfg = self.config.get("data", {}) or {}
            dashboard_timeframes = data_cfg.get(
                "dashboard_timeframes", ["1h", "4h", "1d"]
            )
            if not isinstance(dashboard_timeframes, (list, tuple)):
                dashboard_timeframes = ["1h", "4h", "1d"]
            max_sync_interval = float(
                data_cfg.get("candle_sync_max_interval_seconds", 900)
            )
            max_workers_cfg = data_cfg.get("candle_sync_max_workers", 4)
            synced = 0
            skipped = 0
            failed = 0

            def _parse_candles(raw: list, timeframe: str) -> list:
                tf_ms = self._timeframe_ms(timeframe)
                candles = []
                for k in raw:
                    open_ts_ms = int(k[0])
                    if open_ts_ms + tf_ms > now_ms:
                        continue
                    candles.append((
                        open_ts_ms // 1000,
                        float(k[1]),
                        float(k[2]),
                        float(k[3]),
                        float(k[4]),
                        float(k[5]),
                    ))
                return candles

            def _should_sync(sym: str, timeframe: str) -> bool:
                key = (sym, timeframe)
                last_mono = float(self._candle_sync_last.get(key, 0.0) or 0.0)
                try:
                    rows = self.db.get_candles(sym, timeframe, limit=1)
                    latest_ts = int(rows[-1]["timestamp"]) if rows else 0
                except Exception:
                    latest_ts = 0
                if latest_ts <= 0 or last_mono <= 0:
                    return True
                tf_sec = max(60, self._timeframe_ms(timeframe) // 1000)
                # Latest saved candle has open timestamp latest_ts and is closed
                # at latest_ts + tf_sec. The next closed candle is not available
                # until latest_ts + 2*tf_sec, so avoid repeated kline pulls while
                # the current candle is still forming.
                next_closed_due = latest_ts + (2 * tf_sec) <= now_sec
                if next_closed_due:
                    return True
                return (time.monotonic() - last_mono) >= max_sync_interval

            def _fetch_timeframe(sym: str, timeframe: str) -> tuple[str, str, list]:
                raw = self.client.get_candles(sym, timeframe, limit=250)
                return sym, timeframe, _parse_candles(raw, timeframe)

            jobs: list[tuple[str, str]] = []
            seen_jobs: set[tuple[str, str]] = set()

            def _queue_timeframe(sym: str, timeframe: str) -> None:
                nonlocal skipped
                key = (sym, timeframe)
                if key in seen_jobs:
                    return
                seen_jobs.add(key)
                if not _should_sync(sym, timeframe):
                    skipped += 1
                    return
                jobs.append(key)

            for spec in specs:
                if not spec:
                    continue
                sc = self._sc(spec.symbol)
                tf_p = sc.primary_timeframe
                _queue_timeframe(spec.symbol, tf_p)
                tf_r = sc.timeframe_regime
                if tf_r and tf_r != tf_p:
                    _queue_timeframe(spec.symbol, tf_r)
                for dashboard_tf in dashboard_timeframes:
                    tf_ui = str(dashboard_tf or "").strip().lower()
                    if tf_ui in {"1h", "4h", "1d"}:
                        _queue_timeframe(spec.symbol, tf_ui)

            try:
                max_workers = int(max_workers_cfg)
            except (TypeError, ValueError):
                max_workers = 4
            max_workers = max(1, min(max_workers, len(jobs) or 1))

            if max_workers <= 1 or len(jobs) <= 1:
                results = []
                for sym, timeframe in jobs:
                    try:
                        results.append(_fetch_timeframe(sym, timeframe))
                    except Exception as e:
                        failed += 1
                        logger.error(
                            "Failed to fetch candles for %s %s: %s",
                            sym,
                            timeframe,
                            e,
                            exc_info=True,
                        )
                for sym, timeframe, candles in results:
                    self.db.save_candles(sym, timeframe, candles)
                    self._candle_sync_last[(sym, timeframe)] = time.monotonic()
                    synced += 1
            else:
                # REST market-data fetches are I/O-bound. Fetch them concurrently,
                # but keep SQLite writes on this thread so the DB remains a
                # predictable single-writer path as pair count grows.
                results: list[tuple[str, str, list]] = []
                with ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="candle-sync",
                ) as pool:
                    futures = {
                        pool.submit(_fetch_timeframe, sym, timeframe): (sym, timeframe)
                        for sym, timeframe in jobs
                    }
                    for future in as_completed(futures):
                        sym, timeframe = futures[future]
                        try:
                            results.append(future.result())
                        except Exception as e:
                            failed += 1
                            logger.error(
                                "Failed to fetch candles for %s %s: %s",
                                sym,
                                timeframe,
                                e,
                                exc_info=True,
                            )
                for sym, timeframe, candles in results:
                    self.db.save_candles(sym, timeframe, candles)
                    self._candle_sync_last[(sym, timeframe)] = time.monotonic()
                    synced += 1
            self._latency_metrics["candle_sync_fetches"] = synced
            self._latency_metrics["candle_sync_skipped"] = skipped
            self._latency_metrics["candle_sync_failed"] = failed
            self._latency_metrics["candle_sync_workers"] = max_workers
        except Exception as e:
            logger.error(f"Error syncing candles: {e}")
        finally:
            self._latency_metrics["sync_candles_ms"] = int(
                (time.monotonic() - started) * 1000
            )

    def load_candles_df(self, timeframe: str, symbol: Optional[str] = None) -> pd.DataFrame:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        # Keep the live calculation window in sync with the active strategy.
        # EMA200/SuperTrend's certified BTC preset uses a 420-bar calculation
        # window; fetching only the old 250-bar default shortens the EMA warm-up
        # and makes live signals diverge from chart/backtest calculations.
        candle_limit = 250
        try:
            strategy_cfg = self._get_strategy_config(sym)
            configured_limit = int(strategy_cfg.get("max_calc_bars") or 0)
            candle_limit = max(candle_limit, configured_limit)
        except Exception:
            pass
        rows = self.db.get_candles(sym, timeframe, limit=candle_limit)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("datetime", inplace=True)
        return df

    def _build_state_dict(
        self,
        state: Dict[str, Any],
        indicators: Dict[str, Any],
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        sc = self._sc(sym)
        spec = self._pair_registry.get(sym)
        tick_snap = sc.get_tick_snapshot()
        current_price = tick_snap.get("last", 0.0)
        ws_mono = float(tick_snap.get("monotonic_ts") or 0.0)
        ws_age_ms = int(max(0.0, time.monotonic() - ws_mono) * 1000) if ws_mono > 0 else 0
        self._latency_metrics["ws_tick_age_ms"] = ws_age_ms
        client_last_request = dict(getattr(self.client, "last_request", {}) or {})
        latency_block = {
            **dict(getattr(self, "_latency_metrics", {}) or {}),
            "api_latency_ms": int(getattr(self.client, "last_latency", 0.0) * 1000),
            "clock_offset_seconds": getattr(getattr(self.client, "_clock", None), "offset", 0.0),
            "ws_tick_age_ms": ws_age_ms,
            "api_last_path": client_last_request.get("path", ""),
            "api_last_status": client_last_request.get("status_code", 0),
            "api_last_attempt": client_last_request.get("attempt", 0),
        }

        base_coin = self._get_base_asset(sym)
        quote = self._quote_asset()
        if self._use_sim_broker(sym):
            usdt_bal = self.get_simulated_balance()
            base_bal = state["quantity"] if state["state"] == "bought" else 0.0
            portfolio = {"USDT": usdt_bal, base_coin: base_bal}
        else:
            try:
                b = self.client.get_balances()
                usdt_avail = b.get(quote, {}).get("available", 0.0)
                usdt_res = b.get(quote, {}).get("reserved", 0.0)
                base_avail = b.get(base_coin, {}).get("available", 0.0)
                base_res = b.get(base_coin, {}).get("reserved", 0.0)
                portfolio = {
                    "USDT": usdt_avail + usdt_res,
                    base_coin: base_avail + base_res,
                }
            except Exception as e:
                logger.warning(f"Failed to fetch balances for state export: {e}")
                b, _ = self._balance_cache_snapshot()
                if b:
                    usdt_avail = b.get(quote, {}).get("available", 0.0)
                    usdt_res = b.get(quote, {}).get("reserved", 0.0)
                    base_avail = b.get(base_coin, {}).get("available", 0.0)
                    base_res = b.get(base_coin, {}).get("reserved", 0.0)
                    portfolio = {
                        "USDT": usdt_avail + usdt_res,
                        base_coin: base_avail + base_res,
                    }
                else:
                    portfolio = None

        equity_breakdown = self.get_symbol_equity_breakdown(sym)
        portfolio_total = float(equity_breakdown.get("portfolio_total_usdt", 0.0))
        unrealized = estimate_net_unrealized_pnl(
            state,
            mark_price=current_price,
            fee_pct=self._symbol_fee_pct(sym),
        )

        reg = sc.current_regime
        exchange_cfg = self.config.get("exchange") or {}
        exchange_id = str(
            exchange_cfg.get("ccxt_id")
            or exchange_cfg.get("name")
            or exchange_cfg.get("provider")
            or "unknown"
        ).strip().lower()
        exchange_identity = {
            "id": exchange_id,
            "name": str(exchange_cfg.get("name") or exchange_id).strip(),
            "provider": str(exchange_cfg.get("provider") or exchange_id).strip().lower(),
            "market_type": str(exchange_cfg.get("market_type") or "spot").strip().lower(),
            "fees": {
                "maker": self._exchange_maker_fee_pct_by_symbol.get(sym),
                "taker": self._symbol_fee_pct(sym),
                "source": "exchange_api"
                if sym in self._exchange_fee_pct_by_symbol
                else "config_fallback",
            },
        }
        feed_status = sc.feed_snapshot()
        candle_status = sc.candle_snapshot()
        partial_tp_pct, partial_tp_fraction = resolve_partial_tp(self._get_strategy_config(sym))
        partial_tp_trigger_price = 0.0
        current_mae_pct = 0.0
        current_mfe_pct = 0.0
        if state["state"] == "bought" and partial_tp_pct > 0:
            entry = float(state.get("entry_price") or 0.0)
            if entry > 0:
                is_short = str(state.get("position_side") or "LONG").upper() == "SHORT"
                factor = 1.0 - (partial_tp_pct / 100.0) if is_short else 1.0 + (partial_tp_pct / 100.0)
                partial_tp_trigger_price = entry * factor
        if state["state"] == "bought":
            current_mae_pct, current_mfe_pct = position_excursions_pct(
                entry_price=float(state.get("entry_price") or 0.0),
                highest_price_seen=float(state.get("highest_price_seen") or 0.0),
                lowest_price_seen=float(state.get("lowest_price_seen") or 0.0),
                position_side=str(state.get("position_side") or "LONG"),
                exit_price=current_price,
            )
        snap = self._state_exporter.build(
            pid=os.getpid(),
            engine_started_at=self.engine_started_at,
            symbol=sym,
            simulate_only=self.simulate_only,
            read_only=self.read_only,
            interval_seconds=int(self.config.get("trading", {}).get("interval_seconds", 60)),
            current_price=current_price,
            bid=tick_snap.get("bid", 0.0),
            ask=tick_snap.get("ask", 0.0),
            percent_change_24h=tick_snap.get("percent_change_24h", 0.0),
            equity=portfolio_total,
            portfolio=portfolio,
            position={
                "state": state["state"],
                "entry_price": state["entry_price"],
                "stop_loss": state["stop_loss"],
                "take_profit": state["take_profit"],
                "highest_price_seen": state["highest_price_seen"],
                "lowest_price_seen": state.get("lowest_price_seen", 0.0),
                "quantity": state["quantity"],
                "unrealized_pnl": unrealized["net_pnl"],
                "unrealized_pnl_pct": unrealized["net_pnl_pct"],
                "unrealized_pnl_gross": unrealized["gross_pnl"],
                "estimated_entry_fee": unrealized["entry_fee"],
                "estimated_exit_fee": unrealized["exit_fee"],
                "estimated_total_fees": unrealized["total_fees"],
                "opened_at": state["opened_at"],
                "stop_loss_order_id": state.get("stop_loss_order_id"),
                "position_side": state.get("position_side", "LONG"),
                "leverage": state.get("leverage", 1.0),
                "margin_mode": state.get("margin_mode", "spot"),
                "liquidation_price": state.get("liquidation_price", 0.0),
                "funding_paid": state.get("funding_paid", 0.0),
                "management_mode": state.get("management_mode", "strategy"),
                "exchange_position_id": state.get("exchange_position_id"),
                "partial_tp_taken": bool(state.get("partial_tp_taken", False)),
                "partial_tp_pct": partial_tp_pct,
                "partial_tp_fraction": partial_tp_fraction,
                "mae_pct": current_mae_pct,
                "mfe_pct": current_mfe_pct,
                "partial_tp_trigger_price": partial_tp_trigger_price,
                "mark_price": current_price,
                "exchange": exchange_id,
                "market_type": exchange_identity["market_type"].upper(),
                "feed_health": "DEGRADED" if feed_status["feed_degraded"] else "OK",
            },
            indicators=indicators,
            strategy_name=getattr(self._get_strategy_for_symbol(sym), "name", "unknown"),
            strategy_version=getattr(self._get_strategy_for_symbol(sym), "version", "?"),
            signal_meta=dict(sc.last_signal_meta or {}),
            risk=self._build_risk_block(sym),
            macro_guard=self._build_macro_guard_for_symbol(sym),
            last_log_message=self.last_log_message,
            api_latency_ms=int(getattr(self.client, "last_latency", 0.0) * 1000),
            clock_offset_seconds=getattr(getattr(self.client, "_clock", None), "offset", 0.0),
            run_id=self.run_id,
            recent_events=self.get_recent_events(20, symbol=sym),
            latency=latency_block,
            regime={
                "regime": reg.regime if reg else "UNKNOWN",
                "trend": reg.trend if reg else "NEUTRAL",
                "volatility": reg.volatility if reg else "NORMAL",
                "macro_bias": reg.macro_bias if reg else "NEUTRAL",
                "confidence": reg.confidence if reg else 0.5,
                "gold_score": reg.gold_score if reg else 50,
                "reasons": list(reg.reasons) if reg else [],
                "phase": getattr(reg, "phase", "UNKNOWN") if reg else "UNKNOWN",
                "risk_state": getattr(reg, "risk_state", "NORMAL") if reg else "NORMAL",
                "trend_strength": getattr(reg, "trend_strength", "NEUTRAL") if reg else "NEUTRAL",
                "volatility_state": getattr(reg, "volatility_state", "NORMAL") if reg else "NORMAL",
                "liquidity_state": getattr(reg, "liquidity_state", "NORMAL") if reg else "NORMAL",
                "transition_risk": getattr(reg, "transition_risk", "LOW") if reg else "LOW",
                "strategy_bias": dict(getattr(reg, "strategy_bias", {}) or {}) if reg else {},
                "features": dict(getattr(reg, "features", {}) or {}) if reg else {},
            },
        )
        snap["primary_timeframe"] = sc.primary_timeframe
        snap["exchange"] = exchange_identity
        snap["confirm_timeframe"] = sc.confirm_timeframe or ""
        snap["high_24h"] = float(tick_snap.get("high") or 0.0)
        snap["low_24h"] = float(tick_snap.get("low") or 0.0)
        snap["degraded"] = sc.degraded
        snap["degrade_reason"] = feed_status["degrade_reason"]
        snap["last_candle_timestamp"] = candle_status["last_candle_timestamp"]
        snap["equity_breakdown"] = equity_breakdown
        snap["total_equity_usdt"] = portfolio_total
        snap["metrics_context"] = {
            # Minimal/legacy engine adapters may not expose the attribute yet;
            # the production engine always does. Keep state export compatible
            # without falling back when an explicit runtime value exists.
            "initial_balance": float(
                getattr(self, "initial_balance", 1000.0) or 1000.0
            ),
        }
        try:
            closed = self.db.get_closed_trades(sym, limit=1)
            snap["last_closed_trade"] = closed[0] if closed else None
        except Exception:
            snap["last_closed_trade"] = None

        exec_mode = self._execution_mode(sym)
        snap["execution_mode"] = exec_mode
        _rr_spec = self._pair_registry.get(sym)
        snap["regime_router"] = {
            "no_trade_state": getattr(sc, "no_trade_state", "ACTIVE"),
            "confirmed_regime": getattr(sc, "confirmed_regime", ""),
            "pending_regime": getattr(sc, "pending_regime", ""),
            "warning": getattr(sc, "regime_router_warning", ""),
            "enabled": bool(getattr(_rr_spec, "regime_router_enabled", False)) if _rr_spec else False,
            "live_confirmed": bool(getattr(_rr_spec, "regime_router_live_confirmed", False)) if _rr_spec else False,
        }

        try:
            from xauby.runtime.architecture_config import tui_indicator_registry
            from xauby.ui.indicator_display import build_indicator_display_payload, build_indicator_view

            if tui_indicator_registry(self.config):
                strat = getattr(self._get_strategy_for_symbol(sym), "name", sc.strategy_name)
                df_cache = getattr(sc, "chart_df_cache", None)
                if df_cache is not None and not getattr(df_cache, "empty", True):
                    view = build_indicator_view(df_cache, strat, self.config, symbol=sym)
                    snap["indicator_display"] = build_indicator_display_payload(view)
        except Exception:
            pass

        return snap

    def _apply_dashboard_focus_request(self) -> None:
        path = os.path.join(self._project_root, dashboard_focus_path())
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            fs = str(data.get("focus_symbol", "")).upper().replace("_", "")
            if fs:
                self.set_focus_symbol(fs)
        except Exception:
            pass

    def _write_multi_state_json(self) -> None:
        started = time.monotonic()
        try:
            self._apply_dashboard_focus_request()
            active = self._pair_registry.active()
            by_symbol = dict(self._symbol_snapshots)
            open_positions = 0
            for spec in active:
                sym = spec.symbol
                if sym not in by_symbol:
                    st = self.db.get_trade_state(sym)
                    sc = self._sc(sym)
                    by_symbol[sym] = self._build_state_dict(
                        st, sc.indicators_cache or {}, symbol=sym
                    )
                if by_symbol[sym].get("position", {}).get("state") == "bought":
                    open_positions += 1
            focus = self.focus_symbol
            if focus not in by_symbol and active:
                focus = active[0].symbol
                self.focus_symbol = focus
            legacy = dict(by_symbol.get(focus, {}))
            payload = {
                **legacy,
                "schema_version": 2,
                "focus_symbol": focus,
                "pairs": [s.symbol for s in active],
                "aggregate": {
                    "total_equity_usdt": self.get_portfolio_equity_total(),
                    "open_positions": open_positions,
                    "simulate_only": self.simulate_only,
                    "read_only": self.read_only,
                    "macro_guard": self._build_macro_guard_for_symbol(focus),
                },
                "by_symbol": by_symbol,
            }
            self._state_exporter.write(payload)
            self._sync_focus_aliases()
        except Exception as e:
            logger.debug(f"Failed to export multi state JSON: {e}")
        finally:
            self._latency_metrics["state_export_ms"] = int(
                (time.monotonic() - started) * 1000
            )

    def update_state_json(
        self,
        state: Dict[str, Any],
        indicators: Dict[str, Any],
        symbol: Optional[str] = None,
    ):
        try:
            sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
            self._symbol_snapshots[sym] = self._build_state_dict(
                state, indicators, symbol=sym
            )
        except Exception as e:
            logger.debug(f"Failed to export state JSON: {e}")

    def set_focus_symbol(self, symbol: str) -> bool:
        sym = symbol.upper().replace("_", "")
        if sym in self.contexts:
            self.focus_symbol = sym
            os.environ["XAUBY_FOCUS_SYMBOL"] = sym
            self._sync_focus_aliases()
            return True
        return False

    def cycle_focus_symbol(self, direction: int = 1) -> str:
        active = [s.symbol for s in self._pair_registry.active()]
        if not active:
            return self.focus_symbol
        try:
            idx = active.index(self.focus_symbol)
        except ValueError:
            idx = 0
        idx = (idx + direction) % len(active)
        self.set_focus_symbol(active[idx])
        return self.focus_symbol

    def prune_database(self):
        retention = self.config.get("candle_retention", {})
        if not retention.get("enabled", False):
            return
        
        logger.info("Running database candle pruning...")
        now_ts = int(time.time())
        timeframes = retention.get("timeframes", {})
        
        for spec in self._pair_registry.active():
            for tf, days in timeframes.items():
                cutoff_ts = now_ts - (int(days) * 24 * 3600)
                try:
                    self.db.delete_old_candles(spec.symbol, tf, cutoff_ts)
                    logger.info(
                        f"Pruned candles for {spec.symbol} {tf} older than {days} days."
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to prune database candles for {spec.symbol} {tf}: {e}"
                    )

    def run_retention_pass(self, *, startup: bool = False) -> None:
        candle_cfg = self.config.get("candle_retention") or {}
        event_cfg = self.config.get("event_retention") or {}
        backup_cfg = self.config.get("backup_retention") or {}

        if candle_cfg.get("enabled", False):
            if not startup or candle_cfg.get("run_on_startup", False):
                self.prune_database()
                if candle_cfg.get("vacuum_after_cleanup", False):
                    try:
                        self.db.vacuum()
                        logger.info("SQLite VACUUM completed after candle retention")
                    except Exception as e:
                        logger.warning(f"SQLite VACUUM after retention failed: {e}")

        if event_cfg.get("enabled", False):
            if not startup or event_cfg.get("run_on_startup", False):
                from xauby.utils.retention import run_event_retention
                run_event_retention(self.db, self.config)

        if backup_cfg.get("enabled", False):
            if not startup or backup_cfg.get("run_on_startup", False):
                from xauby.utils.retention import run_backup_retention
                run_backup_retention(self.config)

    def _find_semi_auto_pending_symbol(self) -> Optional[str]:
        """Symbol with a pending semi-auto confirmation, if any.

        Telegram callbacks arrive without a symbol; resolving via
        _active_tick_symbol would target whatever pair the engine happens to
        be ticking, not the pair that asked for confirmation.
        """
        with self._semi_auto_lock:
            for sym, sc in self.contexts.items():
                if sc.has_semi_auto_pending():
                    return sym
        return None

    def confirm_semi_auto_buy(self, symbol: Optional[str] = None) -> None:
        sym = symbol or self._find_semi_auto_pending_symbol()
        self._sc(sym).confirm_semi_auto_pending()

    def skip_semi_auto_buy(self, symbol: Optional[str] = None) -> None:
        sym = symbol or self._find_semi_auto_pending_symbol()
        self._clear_semi_auto_pending(notify=False, symbol=sym)

    def _clear_semi_auto_pending(
        self, notify: bool = True, reason: str = "skipped", symbol: Optional[str] = None
    ) -> None:
        sc = self._sc(symbol)
        pending = sc.clear_semi_auto_pending()
        if pending and notify:
            self.send_telegram_alert(
                f"⏭ *Semi-auto BUY {reason}* — `{sc.symbol}`",
                level=AlertLevel.INFO,
            )

    def _queue_semi_auto_buy(
        self, ticker_price: float, atr: float, signal: Any, symbol: Optional[str] = None
    ) -> None:
        sc = self._sc(symbol)
        timeout = self._notif_settings.semi_auto_confirm_timeout_seconds
        sc.set_semi_auto_pending({
            "ticker_price": ticker_price,
            "atr": atr,
            "signal_stop_loss_price": signal.stop_loss_price,
            "signal_stop_loss_distance": signal.stop_loss_distance,
            "expires_at": time.time() + timeout,
        })
        from xauby.notifications.telegram_bot import SEMI_AUTO_KEYBOARD

        msg = (
            f"🟡 *Semi-auto BUY Confirmation*\n"
            f"Symbol: `{sc.symbol}` │ Price: `{ticker_price:.2f} USDT`\n"
            f"Confirm within `{timeout}s` or the signal will be skipped."
        )
        self.send_telegram_alert(msg, level=AlertLevel.TRADE, reply_markup=SEMI_AUTO_KEYBOARD)

    def _execute_confirmed_semi_auto_buy(self, symbol: Optional[str] = None) -> bool:
        sc = self._sc(symbol)
        status, params = sc.pop_confirmed_or_expired_semi_auto(time.time())
        if status != "confirmed" or not params:
            return False
        return self.execute_buy(
            float(params["ticker_price"]),
            float(params["atr"]),
            signal_stop_loss_price=params.get("signal_stop_loss_price"),
            signal_stop_loss_distance=params.get("signal_stop_loss_distance"),
            symbol=sc.symbol,
        )

    def _process_semi_auto_idle(
        self,
        state: Dict[str, Any],
        action: str,
        ticker_price: float,
        atr: float,
        signal: Any,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        sym = self._sym() if symbol is None else symbol
        sc = self._sc(sym)
        if self.trading_mode != "semi_auto" or state.get("state") != "idle":
            return state

        do_timeout_notify = False
        do_execute_buy = False
        do_queue_new = False
        execute_params: Optional[dict] = None

        pending_status, pending = sc.pop_confirmed_or_expired_semi_auto(time.time())
        if pending_status == "expired":
            do_timeout_notify = True
        elif pending_status == "confirmed" and pending:
            execute_params = dict(pending)
            do_execute_buy = True
        elif state.get("state") == "idle" and action == "BUY" and pending_status == "none":
            do_queue_new = True

        # Side-effects outside the lock
        if do_timeout_notify:
            self.send_telegram_alert(
                f"⏭ *Semi-auto BUY timed out* — `{sc.symbol}`",
                level=AlertLevel.INFO,
            )
        if do_execute_buy and execute_params:
            success = self.execute_buy(
                float(execute_params["ticker_price"]),
                float(execute_params["atr"]),
                signal_stop_loss_price=execute_params.get("signal_stop_loss_price"),
                signal_stop_loss_distance=execute_params.get("signal_stop_loss_distance"),
                symbol=sc.symbol,
            )
            if success:
                return self.db.get_trade_state(sym)
        if do_queue_new:
            self._queue_semi_auto_buy(ticker_price, atr, signal, symbol=sym)
        return state

    def _emit_event(self, event: str, **fields: Any) -> None:
        sym = fields.pop("symbol", None) or self._active_tick_symbol or self.focus_symbol
        self._emitter.emit(event, symbol=sym, **fields)
        self._latency_metrics["event_store_ms"] = int(
            getattr(self._emitter, "last_store_ms", 0) or 0
        )

    def get_recent_events(self, limit: int = 20, symbol: Optional[str] = None) -> list:
        sym = symbol or self._active_tick_symbol or self.focus_symbol
        return self._emitter.recent(limit=limit, symbol=sym)

    def get_state_snapshot(self) -> Dict[str, Any]:
        state = self.db.get_trade_state(self.symbol)
        return self._build_state_dict(state, self.indicators_cache or {})

    def _record_closed_trade_atomic(
        self,
        state: Dict[str, Any],
        filled_qty: float,
        filled_price: float,
        trigger_reason: str,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
        symbol: Optional[str] = None,
    ) -> bool:
        sym = self._sym() if symbol is None else symbol.upper().replace("_", "")
        entry_price = float(state.get("entry_price", 0.0))
        entry_cost = filled_qty * entry_price
        gross_exit = filled_qty * filled_price
        total_fees = entry_fee + exit_fee
        net_pnl = gross_exit - entry_cost - total_fees
        net_pnl_pct = (net_pnl / entry_cost) * 100 if entry_cost > 0 else 0.0
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        
        opened_at_str = state.get("opened_at") or now_iso
        entry_regime = self.db.get_regime_at(opened_at_str, sym)
        exit_regime = self.db.get_regime_at(now_iso, sym)

        return self.db.close_position_atomic(
            symbol=sym,
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
            opened_at=state.get("opened_at"),
            closed_at=now_iso,
            entry_regime=entry_regime,
            exit_regime=exit_regime,
            strategy_name=self._strategy_name_for_symbol(sym),
            execution_mode=self._execution_mode(sym),
        )

    def tick(self):
        tick_started = time.monotonic()
        self._tick_counter += 1
        cycle_id = f"{self.run_id}-{self._tick_counter}"
        with TickContext(cycle_id):
            if self._pair_registry.maybe_reload(self.client):
                prev_syms = set(self.contexts.keys())
                self._reload_hot_pair_config()
                self._init_pair_runtime()
                if set(self._pair_registry.active_symbols()) != prev_syms:
                    self._refresh_websocket_symbols()
            self.sync_candles()
            for spec in self._pair_registry.active():
                self._active_tick_symbol = spec.symbol
                # One engine cycle evaluates several symbols.  A shared tick id
                # lets the later symbol overwrite the earlier one in replay's
                # lookup, pairing (for example) a BTC signal with the XAU price.
                # Make the decision boundary explicit per symbol.
                pair_tick_id = f"{cycle_id}-{spec.symbol}"
                self._emitter.set_tick_id(pair_tick_id)
                try:
                    self._tick_body()
                except Exception as e:
                    logger.error(
                        "Tick failed for %s: %s", spec.symbol, e, exc_info=True
                    )
                    self._emit_event(
                        EventType.ERROR,
                        context="tick_symbol",
                        symbol=spec.symbol,
                        error=str(e),
                    )
            self._active_tick_symbol = None
            self._write_multi_state_json()
        self._latency_metrics["tick_duration_ms"] = int(
            (time.monotonic() - tick_started) * 1000
        )

    def _tick_body(self):
        sc = self._sc()
        sym = self._sym()
        logger.debug("Core iteration ticking for %s...", sym)

        if hasattr(self.client, "check_clock_sync"):
            self.client.check_clock_sync()

        tick_snap = sc.get_tick_snapshot()
        ws_mono = tick_snap.get("monotonic_ts", 0)
        ws_stale_after = float(
            (self.config.get("websocket", {}) or {}).get("stale_tick_seconds", 15.0)
        )
        is_ws_stale = (time.monotonic() - ws_mono > ws_stale_after)
        ticker_price = tick_snap.get("last", 0.0)

        if ticker_price <= 0 or is_ws_stale:
            if is_ws_stale and ws_mono > 0:
                logger.warning(
                    f"WebSocket price tick is stale (age: {time.monotonic() - ws_mono:.1f}s). "
                    "Falling back to REST API."
                )
            try:
                t = self.client.get_ticker(sym)
                t["timestamp"] = time.time()
                t["monotonic_ts"] = time.monotonic()
                sc.set_tick(t)
                ticker_price = t["last"]
            except Exception as e:
                logger.error(f"Failed to fetch ticker via REST for {sym}: {e}")
                self.last_log_message = f"Skipping tick {sym}: stale WS + REST fallback failed ({e})"
                try:
                    state = self.db.get_trade_state(sym)
                    self.update_state_json(state, sc.indicators_cache or {}, symbol=sym)
                except Exception:
                    pass
                return

        # Excursion tracking is market-data telemetry, not strategy logic. Keep
        # recording it even when the candle frame is temporarily insufficient
        # and this tick cannot evaluate a signal.
        state = self.db.get_trade_state(sym)
        if state["state"] == "bought":
            update_extrema = getattr(self.db, "update_position_extrema", None)
            if callable(update_extrema) and update_extrema(sym, ticker_price):
                state = self.db.get_trade_state(sym)

        tf_primary = sc.primary_timeframe
        df_4h = self.load_candles_df(tf_primary, symbol=sym)

        # Strategies must only see closed candles: signals computed on the
        # still-forming bar repaint mid-bar and diverge from backtest.
        # SL/trailing keep using the realtime ticker_price untouched.
        closed_only = use_closed_candles(self.config)
        last_bar_is_forming = True
        if closed_only:
            df_4h, _ = drop_forming_bar(df_4h, tf_primary)
            last_bar_is_forming = False

        if df_4h.empty or len(df_4h) < 100:
            logger.warning(
                "Insufficient candles for %s %s. Skipping tick.", sym, tf_primary
            )
            return

        if "timestamp" in df_4h.columns:
            try:
                last_candle_timestamp = int(df_4h["timestamp"].iloc[-1])
            except (TypeError, ValueError):
                last_candle_timestamp = 0
        else:
            last_candle_timestamp = 0

        # Candle-staleness guard: if the newest CLOSED candle is older than
        # candle_staleness_factor timeframes, REST candle sync is not keeping up
        # (a missed candle) even though price ticks may still be arriving — the
        # strategy would compute on stale bars. Block new entries until it
        # refreshes. Normal max age between closed candles is ~2x the timeframe,
        # so the default 2.5 leaves headroom without false positives.
        factor = float((self.config.get("data") or {}).get("candle_staleness_factor", 2.5))
        is_stale = candle_is_stale(last_candle_timestamp, tf_primary, factor=factor)
        was_stale = sc.set_candle_status(last_candle_timestamp, is_stale)
        if is_stale and not was_stale:
            age_s = int(time.time() - last_candle_timestamp)
            logger.warning(
                "[%s] Candle feed stale: newest %s candle is %ds old (> %.1fx timeframe). "
                "Blocking new entries until candles refresh.",
                sym, tf_primary, age_s, factor,
            )
            self._emit_event(
                EventType.FEED_STALE, symbol=sym, timeframe=tf_primary, age_seconds=age_s
            )

        try:
            from xauby.ui.chart_registry import compute_chart_dataframe

            strat_name = sc.strategy_name or self._strategy_name_for_symbol(sym)
            latest_candle_ts = int(df_4h["timestamp"].iloc[-1]) if "timestamp" in df_4h.columns else 0
            indicator_cfg = self._get_strategy_config(sym)
            config_fingerprint = json.dumps(
                indicator_cfg, sort_keys=True, separators=(",", ":"), default=str
            )
            cache_key = (tf_primary, latest_candle_ts, strat_name, config_fingerprint)
            if self._chart_cache_keys.get(sym) != cache_key or getattr(sc, "chart_df_cache", None) is None:
                sc.chart_df_cache = compute_chart_dataframe(
                    df_4h.copy(), sym, strategy_name=strat_name
                )
                self._chart_cache_keys[sym] = cache_key
        except Exception:
            sc.chart_df_cache = df_4h.copy()

        tf_regime = sc.timeframe_regime
        df_d1 = self.load_candles_df(tf_regime, symbol=sym) if tf_regime else None
        if closed_only and df_d1 is not None:
            df_d1, _ = drop_forming_bar(df_d1, tf_regime)

        # Per-symbol gate: a sim-mode symbol (whitelist mode "sim") must never
        # touch real exchange SL orders even when another symbol runs live.
        if (self._execution_mode(sym) == "live" and state["state"] == "bought"
                and str(state.get("position_side") or "LONG").upper() != "SHORT"):
            state = self._handle_exchange_sl_order(state, ticker_price, symbol=sym)

        has_pos = state["state"] == "bought"
        if has_pos and self._use_sim_broker(sym):
            try:
                self._sim_broker.update_unrealized(sym, ticker_price)
            except Exception:
                pass
        stop_loss = float(state.get("stop_loss", 0.0))
        is_short_position = str(state.get("position_side") or "LONG").upper() == "SHORT"
        sl_hit = ticker_price >= stop_loss if is_short_position else ticker_price <= stop_loss
        if has_pos and stop_loss > 0 and sl_hit:
            sc._sl_breach_count += 1
        else:
            sc._sl_breach_count = 0
        sl_confirmed = sc._sl_breach_count >= self._sl_breach_threshold

        try:
            closed_for_strategy = self.db.get_closed_trades(sym, limit=10)
            closed_for_strategy = self._filter_closed_trades_by_execution_mode(
                closed_for_strategy,
                self._risk_execution_mode(sym),
            )
            last_closed_trade = closed_for_strategy[0] if closed_for_strategy else None
        except Exception:
            last_closed_trade = None

        ctx = ContextBuilder.build(
            symbol=sym,
            timeframe_primary=tf_primary,
            timeframe_regime=tf_regime,
            df_primary=df_4h,
            df_regime=df_d1,
            current_price=ticker_price,
            has_position=has_pos,
            position_side=(str(state.get("position_side") or "LONG").upper() if has_pos else None),
            stop_loss=stop_loss,
            sl_confirmed=sl_confirmed,
            config=self._get_strategy_config(sym),
            engine_config=self.config,
            extras={
                "use_d1_regime_filter": sc.use_d1_regime_filter,
                "last_bar_is_forming": last_bar_is_forming,
                "last_closed_trade": last_closed_trade,
            },
        )

        runner = self._get_runner_for_symbol(sym)
        strategy_started = time.monotonic()
        signal = runner.run(ctx)
        self._latency_metrics["strategy_ms"] = int(
            (time.monotonic() - strategy_started) * 1000
        )

        self._emit_event(
            EventType.TICK,
            price=round(ticker_price, 2),
            position=state["state"],
        )

        indicators = dict(signal.indicators) if signal.indicators else {}
        sc.indicators_cache = indicators
        sc.last_signal_meta = {
            "checklist": list(signal.checklist or []),
            "status_summary": signal.status_summary or "",
            "confidence": float(signal.confidence or 0.0),
            "reason": signal.reason or "",
            "action": signal.action,
            "intent": signal.intent,
            "position_side": signal.position_side,
            "timestamp": signal.timestamp,
            "strategy_name": signal.strategy_name,
            "timeframe": signal.timeframe,
            "metadata": dict(signal.metadata or {}),
        }
        if sym == self.focus_symbol:
            self.indicators_cache = sc.indicators_cache
            self.last_signal_meta = sc.last_signal_meta

        manual_result = self._process_manual_order_request(
            state,
            ticker_price,
            float(signal.volatility or 0.0),
            symbol=sym,
        )
        if manual_result is not None:
            updated_state = self.db.get_trade_state(sym)
            self.update_state_json(updated_state, indicators, symbol=sym)
            return

        # Run Gold Regime Classification
        try:
            from xauby.regime.classifier import (
                classify_market,
                resolve_macro_bias_threshold,
                resolve_macro_weights,
            )
            regime_started = time.monotonic()
            
            macro_state = {}
            if os.path.exists(sentiment_guard_state_path()):
                try:
                    with open(sentiment_guard_state_path(), "r", encoding="utf-8") as gf:
                        macro_state = json.load(gf)
                except Exception:
                    pass
            
            candles_list = df_4h.to_dict("records") if not df_4h.empty else []
            gold_regime = classify_market(
                candles_list,
                indicators=indicators,
                macro_state=macro_state,
                timeframe=tf_primary,
                macro_weights=resolve_macro_weights(self.config),
                macro_bias_threshold=resolve_macro_bias_threshold(self.config),
            )
            self._latency_metrics["regime_ms"] = int(
                (time.monotonic() - regime_started) * 1000
            )

            # Optional independent GMM cross-check (advisory only — attached as
            # stat_* features, never overrides the rule-based regime or routing).
            try:
                from xauby.runtime.architecture_config import (
                    regime_statistical_config,
                    regime_statistical_crosscheck,
                )

                if regime_statistical_crosscheck(self.config) and candles_list:
                    from xauby.regime.statistical import (
                        classify_statistical,
                        crosscheck_features,
                    )

                    _stat_cfg = regime_statistical_config(self.config)
                    _stat = classify_statistical(
                        candles_list,
                        components=_stat_cfg["components"],
                        min_bars=_stat_cfg["min_bars"],
                    )
                    gold_regime.features.update(
                        crosscheck_features(gold_regime.regime, _stat)
                    )
            except Exception:
                # Cross-check must never break the trading tick.
                pass

            self.db.save_gold_regime(
                symbol=sym,
                timestamp=int(time.time()),
                regime=gold_regime.regime,
                trend=gold_regime.trend,
                volatility=gold_regime.volatility,
                macro_bias=gold_regime.macro_bias,
                confidence=gold_regime.confidence,
                details={
                    "phase": getattr(gold_regime, "phase", "UNKNOWN"),
                    "risk_state": getattr(gold_regime, "risk_state", "NORMAL"),
                    "trend_strength": getattr(gold_regime, "trend_strength", "NEUTRAL"),
                    "volatility_state": getattr(gold_regime, "volatility_state", "NORMAL"),
                    "liquidity_state": getattr(gold_regime, "liquidity_state", "NORMAL"),
                    "transition_risk": getattr(gold_regime, "transition_risk", "LOW"),
                    "strategy_bias": dict(getattr(gold_regime, "strategy_bias", {}) or {}),
                    "features": dict(getattr(gold_regime, "features", {}) or {}),
                    "gold_score": getattr(gold_regime, "gold_score", 50),
                },
            )
            
            self._emit_event(
                "regime_classified",
                regime=gold_regime.regime,
                trend=gold_regime.trend,
                volatility=gold_regime.volatility,
                macro_bias=gold_regime.macro_bias,
                confidence=gold_regime.confidence,
                phase=getattr(gold_regime, "phase", "UNKNOWN"),
                risk_state=getattr(gold_regime, "risk_state", "NORMAL"),
                trend_strength=getattr(gold_regime, "trend_strength", "NEUTRAL"),
                volatility_state=getattr(gold_regime, "volatility_state", "NORMAL"),
                liquidity_state=getattr(gold_regime, "liquidity_state", "NORMAL"),
                transition_risk=getattr(gold_regime, "transition_risk", "LOW"),
                strategy_family=(getattr(gold_regime, "strategy_bias", {}) or {}).get("family"),
                strategy_posture=(getattr(gold_regime, "strategy_bias", {}) or {}).get("posture"),
            )
            
            sc.current_regime = gold_regime
            if sym == self.focus_symbol:
                self.current_regime = gold_regime
            self._check_regime_change_alert(gold_regime, symbol=sym)

            from xauby.runtime.architecture_config import regime_router_enabled as rr_master
            from xauby.engine.regime_gate import should_route_on_candle
            spec = self._pair_registry.get(sym)
            candle_status = sc.candle_snapshot()
            last_candle_timestamp = candle_status["last_candle_timestamp"]
            # Advance the router/debounce once per CLOSED candle, not once per
            # tick. Without this, debounce_candles counted ticks (~60s) instead
            # of candles, collapsing the multi-candle confirmation and causing
            # the strategy to switch far more often than intended.
            new_closed_candle = should_route_on_candle(
                last_candle_timestamp, candle_status["last_routed_candle_ts"]
            )
            if (
                rr_master(self.config)
                and spec
                and spec.regime_router_enabled
                and self._regime_router
                and new_closed_candle
            ):
                sc.mark_routed_candle(last_candle_timestamp)
                state_for_pos = self.db.get_trade_state(sym)
                has_pos = state_for_pos.get("state") == "bought"
                if (
                    not has_pos
                    and getattr(sc, "no_trade_state", "") == "HANDOFF"
                    and getattr(sc, "handoff_to_strategy", "")
                ):
                    old_strategy = sc.handoff_from_strategy or sc.strategy_name
                    new_strategy = sc.handoff_to_strategy
                    self._strategy_names_by_symbol[sym] = new_strategy
                    self._strategies_by_symbol.pop(sym, None)
                    self._runners_by_symbol.pop(sym, None)
                    self._started_strategy_symbols.discard(sym)
                    strategy = self._load_strategy_for_symbol(sym)
                    self._pair_registry.mark_symbol_strategy_warning(
                        sym, list(strategy.get_meta().required_timeframes)
                    )
                    refreshed_spec = self._pair_registry.get(sym)
                    if refreshed_spec:
                        sc.degraded = refreshed_spec.degraded
                        sc.set_degrade_reason(refreshed_spec.degrade_reason)
                    self._start_loaded_strategies()
                    sc.strategy_name = new_strategy
                    sc.no_trade_state = "ACTIVE"
                    sc.handoff_from_strategy = ""
                    sc.handoff_to_strategy = ""
                    sc.trailing_atr_mult = 2.0
                    self._emit_event(
                        EventType.HANDOFF_COMPLETED,
                        symbol=sym,
                        old_strategy=old_strategy,
                        new_strategy=new_strategy,
                    )
                    self.send_telegram_alert(
                        f"RegimeRouter: {sym} handoff complete {old_strategy} -> {new_strategy}"
                    )
                route = self._regime_router.evaluate(
                    sym, gold_regime, sc, spec, has_open_position=has_pos
                )
                if route.log_history:
                    self.db.insert_regime_history(
                        sym,
                        old_regime=route.old_regime,
                        new_regime=route.regime,
                        candles_confirmed=route.candles_confirmed,
                        strategy_activated=route.strategy_name,
                        confidence=route.confidence,
                    )
                    if route.old_regime and route.old_regime != route.regime:
                        self._emit_event(
                            EventType.REGIME_CHANGED,
                            symbol=sym,
                            old_regime=route.old_regime,
                            new_regime=route.regime,
                            candles_confirmed=route.candles_confirmed,
                            strategy_activated=route.strategy_name,
                            confidence=route.confidence,
                        )
                if route.strategy_changed and route.strategy_name:
                    self._strategy_names_by_symbol[sym] = route.strategy_name
                    self._strategies_by_symbol.pop(sym, None)
                    self._runners_by_symbol.pop(sym, None)
                    self._started_strategy_symbols.discard(sym)
                    strategy = self._load_strategy_for_symbol(sym)
                    self._pair_registry.mark_symbol_strategy_warning(
                        sym, list(strategy.get_meta().required_timeframes)
                    )
                    refreshed_spec = self._pair_registry.get(sym)
                    if refreshed_spec:
                        sc.degraded = refreshed_spec.degraded
                        sc.set_degrade_reason(refreshed_spec.degrade_reason)
                    self._start_loaded_strategies()
                    sc.strategy_name = route.strategy_name
                    self._emit_event(
                        EventType.STRATEGY_SWITCHED,
                        symbol=sym,
                        old_strategy=route.previous_strategy,
                        new_strategy=route.strategy_name,
                        regime=route.regime,
                    )
                    self.send_telegram_alert(
                        f"🔄 RegimeRouter: {sym} strategy → {route.strategy_name} ({route.regime})"
                    )
                if route.handoff:
                    opened_at = state_for_pos.get("opened_at") if has_pos else None
                    open_position_id = f"{sym}:{opened_at}" if opened_at else None
                    self.db.insert_strategy_handoff(
                        sym,
                        old_strategy=route.previous_strategy,
                        new_strategy=str(route.fields.get("new_strategy_for_entries", "")),
                        open_position_id=open_position_id,
                        notes=route.message,
                    )
                if route.no_trade:
                    sc.trailing_atr_mult = route.trailing_atr_mult
                    if route.no_trade_state == "NO_TRADE":
                        self._emit_event(
                            EventType.NO_TRADE_ENTERED,
                            symbol=sym,
                            regime=route.regime,
                        )
                        self.send_telegram_alert(
                            f"⛔ NO_TRADE active on {sym} ({route.regime})"
                        )
                if (
                    route.force_close
                    and has_pos
                    and str(state_for_pos.get("management_mode") or "strategy").lower()
                    not in {"manual", "strategy_handoff"}
                ):
                    if str(state_for_pos.get("position_side") or "LONG").upper() == "SHORT":
                        self.execute_close_short(state_for_pos, ticker_price,
                            trigger_reason="NO_TRADE force close (6+ candles)", symbol=sym)
                    else:
                        self.execute_sell(state_for_pos, ticker_price,
                            trigger_reason="NO_TRADE force close (6+ candles)", symbol=sym)
                    state = self.db.get_trade_state(sym)
                sc.regime_router_warning = route.message or sc.regime_router_warning
        except Exception as re:
            logger.error(f"Error executing gold regime classification for {sym}: {re}")
            sc.current_regime = None
            if sym == self.focus_symbol:
                self.current_regime = None

        # The strategy action is the canonical decision vocabulary persisted
        # for replay.  Execution dispatch is a separate concern: SHORT opens
        # are SELL decisions but the legacy dispatcher routes them through its
        # BUY branch.  Mutating `action` before emitting made live record
        # BUY(OPEN/SHORT) while replay correctly produced SELL(OPEN/SHORT).
        signal_action = str(signal.action or "HOLD").upper()
        action = signal_action
        reason = signal.reason
        signal_side = str(getattr(signal, "position_side", None) or "LONG").upper()
        signal_intent = str(getattr(signal, "intent", None) or "").upper()
        if signal_side == "SHORT":
            if signal_intent == "OPEN":
                action = "BUY"
            elif signal_intent == "CLOSE":
                action = "SELL"

        if sc.consume_exchange_reconcile_wait():
            feed = sc.feed_snapshot()
            reason = (
                feed.get("exchange_reconcile_reason")
                or "Exchange close reconciled; waiting one engine cycle"
            )
            action = "HOLD"
            sc.last_signal_meta.update(
                {
                    "action": "HOLD",
                    "intent": "HOLD",
                    "position_side": None,
                    "reason": reason,
                    "status_summary": reason,
                }
            )
            if sym == self.focus_symbol:
                self.last_signal_meta = sc.last_signal_meta

        # intent + position_side are emitted alongside action because `action`
        # alone is ambiguous on the short side: open_short and a long exit are
        # both SELL, close_short and a long entry are both BUY. Without these
        # two fields replay can only compare SELL-vs-SELL and cannot tell the
        # two apart, so replay output is not evidence for half the live
        # exposure. See docs/roadmap_2026H2.md P0.5.
        self._emit_event(
            EventType.SIGNAL_EVALUATED,
            action=signal_action,
            execution_action=action,
            intent=str(getattr(signal, "intent", "") or ""),
            position_side=str(getattr(signal, "position_side", "") or ""),
            reason=(reason or "")[:240],
            confidence=round(float(signal.confidence or 0.0), 3),
        )

        if (
            state["state"] == "bought"
            and str(state.get("management_mode") or "strategy").lower() == "manual"
        ):
            self.last_log_message = "Manual-managed position: waiting for Manual SELL"
            self.update_state_json(state, indicators, symbol=sym)
            return

        if (
            state["state"] == "bought"
            and str(state.get("management_mode") or "strategy").lower()
            == "strategy_handoff"
        ):
            side = str(state.get("position_side") or "LONG").upper()
            expected_zone = "RED" if side == "SHORT" else "GREEN"
            observed_zone = str(indicators.get("cdc_zone_4h") or "UNKNOWN").upper()
            if observed_zone == expected_zone:
                armed = self.db.transition_management_mode(
                    sym,
                    expected="strategy_handoff",
                    target="strategy",
                )
                if armed:
                    self.last_log_message = (
                        f"Manual {side} aligned with CDC {observed_zone}; "
                        "strategy management starts next tick"
                    )
                    self._emit_event(
                        "manual_strategy_handoff_armed",
                        symbol=sym,
                        position_side=side,
                        zone=observed_zone,
                    )
                    state = self.db.get_trade_state(sym)
                else:
                    self.last_log_message = (
                        f"Manual {side} alignment detected; handoff transition pending"
                    )
            else:
                self.last_log_message = (
                    f"Manual {side} waiting for CDC {expected_zone} before strategy handoff"
                )
            self.update_state_json(state, indicators, symbol=sym)
            return

        if time.time() - self._guard_last_run > self._guard_interval:
            self._refresh_guard_async()
        with self._guard_lock:
            guard_score = self._guard_score

        guard_enabled = self.config.get("macro_sentiment_guard", {}).get("enabled", False)
        guard_applies = self._macro_guard_applies_to(sym)
        if guard_enabled and guard_applies:
            threshold = float(
                self.config.get("macro_sentiment_guard", {}).get("blocking_threshold", -0.5)
            )
            if action == "BUY" and guard_score < threshold:
                logger.warning(
                    f"🚫 [MACRO GUARD BLOCKED] macro score ({guard_score:+.3f}) "
                    f"below threshold ({threshold:+.1f})."
                )
                self._emit_event(
                    EventType.GUARD_BLOCKED,
                    score=round(guard_score, 3),
                    threshold=threshold,
                )
                if self._notif_settings.notify_guard_blocks:
                    self.send_telegram_alert(
                        f"🚫 *Macro Guard Blocked BUY*\n"
                        f"Score: `{guard_score:+.3f}` │ Threshold: `{threshold:+.1f}`\n"
                        f"Symbol: `{sym}`",
                        level=AlertLevel.INFO,
                    )
                action = "HOLD"
                reason = f"Blocked by Macro Sentiment Guard ({guard_score:+.2f})"

        # Regime policy layer (default off): adjust entries by classified regime.
        regime_risk_override: Optional[float] = None
        regime_sl_delta = 0.0
        if regime_policy_enabled(self.config) and sc.current_regime is not None:
            policy = apply_regime_policy(sc.current_regime, action)
            if policy.adjusted:
                for r in policy.reasons:
                    logger.info("⚖️ [REGIME POLICY] %s: %s", sym, r)
                self._emit_event(
                    "regime_adjusted",
                    symbol=sym,
                    original_action=action,
                    adjusted_action=policy.action,
                    risk_pct_multiplier=policy.risk_pct_multiplier,
                    sl_atr_mult_delta=policy.sl_atr_mult_delta,
                    reasons="; ".join(policy.reasons),
                )
                if policy.action != action:
                    action = policy.action
                    reason = f"Regime policy: {'; '.join(policy.reasons)}"
                if policy.risk_pct_multiplier != 1.0:
                    eff = resolve_trading_config(
                        self.config,
                        self._strategy_name_for_symbol(sym),
                        symbol=sym,
                        for_live=True,
                    )
                    base_risk = float(eff.portfolio.get("risk_pct", 0.01))
                    regime_risk_override = base_risk * policy.risk_pct_multiplier
                regime_sl_delta = policy.sl_atr_mult_delta

        max_open = int(
            self.config.get("trading", {}).get(
                "max_open_positions", len(self._pair_registry.active()) or 1
            )
        )
        open_count = sum(
            1
            for s in self._pair_registry.active()
            if self.db.get_trade_state(s.symbol).get("state") == "bought"
        )

        now_ts = time.time()
        if now_ts - self.last_status_time >= 300:
            extra = signal.status_summary or reason
            logger.info(
                f"Status check - Price: {ticker_price:.2f} | "
                f"[{self._strategy_name_for_symbol(sym)}] {extra} | Pos State: {state['state']}"
            )
            self.last_status_time = now_ts

        if action == "BUY" and state["state"] == "idle":
            from xauby.runtime.telegram_control import trading_pause_reason

            paused, pause_reason = trading_pause_reason()
            if paused:
                logger.warning("BUY blocked for %s: Telegram pause active (%s)", sym, pause_reason)
                action = "HOLD"
                reason = f"Blocked: Telegram pause ({pause_reason})"
                self._emit_event(
                    EventType.GUARD_BLOCKED,
                    reason=reason,
                    symbol=sym,
                )
            if sc.blocks_new_entries():
                logger.warning(
                    "BUY blocked for %s: RegimeRouter %s",
                    sym,
                    sc.no_trade_state,
                )
                action = "HOLD"
                reason = f"Blocked: RegimeRouter {sc.no_trade_state}"
                self._emit_event(
                    EventType.GUARD_BLOCKED,
                    reason=reason,
                    symbol=sym,
                )
            if open_count >= max_open:
                logger.warning(
                    "BUY blocked for %s: max_open_positions %s reached",
                    sym,
                    max_open,
                )
                action = "HOLD"
                reason = f"Blocked: max open positions ({max_open})"
            blocked, block_reason = self._is_buy_blocked_by_cooldown(symbol=sym)
            if blocked:
                logger.warning("BUY blocked by cooldown: %s", block_reason)
                action = "HOLD"
                reason = block_reason
                self._emit_event(
                    EventType.COOLDOWN_BLOCKED,
                    reason=block_reason,
                    symbol=sym,
                )
            elif action == "BUY":
                with self._ws_status_lock:
                    ws_disconnected_at = self._ws_disconnected_at
                if ws_disconnected_at > 0 and (
                    time.time() - ws_disconnected_at < self._ws_buy_block_seconds
                ):
                    logger.warning("BUY blocked: WebSocket recently disconnected")
                    action = "HOLD"
                    reason = "Blocked: WebSocket reconnect cooldown"
                    self._emit_event(
                        EventType.COOLDOWN_BLOCKED,
                        reason="WebSocket reconnect cooldown",
                        symbol=sym,
                    )

        if signal.action == "BUY" and action != "BUY":
            self._emit_event(
                EventType.SIGNAL_REJECTED,
                reason=reason,
                price=round(ticker_price, 2),
            )

        strat_conf = self._get_strategy_config(sym)

        action, reason = self._apply_fixed_tp_exit(
            state,
            action,
            reason,
            ticker_price,
            sl_confirmed=sl_confirmed,
        )
        action, reason = self._apply_minimal_roi_exit(
            state,
            action,
            reason,
            ticker_price,
            sl_confirmed=sl_confirmed,
            strat_conf=strat_conf,
        )
        action, reason = self._apply_drawdown_force_close(
            state, action, reason, symbol=sym
        )

        state = self._maybe_take_partial_tp(
            state, action, ticker_price, strat_conf, symbol=sym
        )

        atr = float(signal.volatility) if signal.volatility else 0.0

        def reverse_entry_block_reason(reverse_open_count: int) -> Optional[str]:
            if guard_enabled and guard_applies:
                threshold = float(
                    self.config.get("macro_sentiment_guard", {}).get("blocking_threshold", -0.5)
                )
                if guard_score < threshold:
                    self._emit_event(
                        EventType.GUARD_BLOCKED,
                        score=round(guard_score, 3),
                        threshold=threshold,
                        symbol=sym,
                    )
                    return f"Blocked by Macro Sentiment Guard ({guard_score:+.2f})"
            from xauby.runtime.telegram_control import trading_pause_reason

            paused, pause_reason = trading_pause_reason()
            if paused:
                reason_txt = f"Blocked: Telegram pause ({pause_reason})"
                self._emit_event(EventType.GUARD_BLOCKED, reason=reason_txt, symbol=sym)
                return reason_txt
            if sc.blocks_new_entries():
                reason_txt = f"Blocked: RegimeRouter {sc.no_trade_state}"
                self._emit_event(EventType.GUARD_BLOCKED, reason=reason_txt, symbol=sym)
                return reason_txt
            if reverse_open_count >= max_open:
                return f"Blocked: max open positions ({max_open})"
            with self._ws_status_lock:
                ws_disconnected_at = self._ws_disconnected_at
            if ws_disconnected_at > 0 and (
                time.time() - ws_disconnected_at < self._ws_buy_block_seconds
            ):
                reason_txt = "Blocked: WebSocket reconnect cooldown"
                self._emit_event(
                    EventType.COOLDOWN_BLOCKED,
                    reason="WebSocket reconnect cooldown",
                    symbol=sym,
                )
                return reason_txt
            return None

        def reverse_flat_block_reason(refreshed: Dict[str, Any]) -> Optional[str]:
            if refreshed.get("state") != "idle":
                return f"state after close is {refreshed.get('state')}"
            residual_fields = {
                "quantity": float(refreshed.get("quantity") or 0.0),
                "entry_price": float(refreshed.get("entry_price") or 0.0),
                "stop_loss": float(refreshed.get("stop_loss") or 0.0),
                "take_profit": float(refreshed.get("take_profit") or 0.0),
            }
            dirty = [name for name, value in residual_fields.items() if abs(value) > 1e-12]
            if dirty:
                values = ", ".join(f"{name}={residual_fields[name]:.12g}" for name in dirty)
                return f"local state not flat after close ({values})"
            if refreshed.get("stop_loss_order_id"):
                return "local state still has stop_loss_order_id after close"
            if bool(refreshed.get("partial_tp_taken")):
                return "local state still has partial_tp_taken after close"
            if self._execution_mode(sym) != "live":
                return None
            caps = getattr(self.client, "capabilities", {}) or {}
            if not caps.get("positions") or not hasattr(self.client, "get_positions"):
                return None
            try:
                live_positions = self.client.get_positions([sym]) or []
            except Exception as exc:
                logger.error("Reverse flat confirmation failed for %s: %s", sym, exc, exc_info=True)
                return f"exchange flat confirmation failed: {exc}"
            for pos in live_positions:
                if str(pos.get("symbol", "")).upper().replace("_", "") != sym:
                    continue
                qty = abs(float(pos.get("quantity") or pos.get("contracts") or 0.0))
                if qty > 1e-12:
                    side = str(pos.get("position_side") or "?").upper()
                    return f"exchange still reports {side} position qty={qty:.12g}"
            return None

        def open_reverse_after_close(close_ok: bool) -> None:
            if not close_ok:
                return
            metadata = signal.metadata if isinstance(getattr(signal, "metadata", None), dict) else {}
            reverse_side = str(metadata.get("reverse_to_position_side") or "").upper()
            if reverse_side not in {"LONG", "SHORT"}:
                return
            if self._execution_mode(sym) == "live":
                # The close fill changes isolated available margin. Discard the
                # pre-close snapshot before exchange-flat confirmation and the
                # direct settlement polling used by the reverse entry.
                self._invalidate_balance_cache()
            refreshed = self.db.get_trade_state(sym)
            flat_reason = reverse_flat_block_reason(refreshed)
            if flat_reason:
                logger.warning(
                    "Reverse open blocked for %s: %s",
                    sym,
                    flat_reason,
                )
                return
            reverse_open_count = sum(
                1
                for s in self._pair_registry.active()
                if self.db.get_trade_state(s.symbol).get("state") == "bought"
            )
            block_reason = reverse_entry_block_reason(reverse_open_count)
            if block_reason:
                logger.warning("Reverse open blocked for %s: %s", sym, block_reason)
                return
            reverse_reason = str(metadata.get("reverse_reason") or signal.reason or "")
            logger.info("Stop-and-reverse opening %s on %s: %s", reverse_side, sym, reverse_reason)
            if reverse_side == "SHORT":
                self.execute_open_short(
                    signal,
                    ticker_price,
                    symbol=sym,
                    reverse_entry=True,
                )
                return
            self.execute_buy(
                ticker_price,
                atr,
                signal_stop_loss_price=signal.stop_loss_price,
                signal_stop_loss_distance=signal.stop_loss_distance,
                symbol=sym,
                risk_pct_override=regime_risk_override,
                sl_atr_mult_delta=regime_sl_delta,
                reverse_entry=True,
            )

        if self.trading_mode == "semi_auto":
            state = self._process_semi_auto_idle(
                state, action, ticker_price, atr, signal, symbol=sym
            )
        elif action == "BUY" and state["state"] == "idle" and signal_side != "SHORT":
            # open_short() maps intent OPEN/SHORT to action "BUY"; exclude it here
            # so a short entry is not mis-routed into a LONG execute_buy below.
            self.execute_buy(
                ticker_price,
                atr,
                signal_stop_loss_price=signal.stop_loss_price,
                signal_stop_loss_distance=signal.stop_loss_distance,
                symbol=sym,
                risk_pct_override=regime_risk_override,
                sl_atr_mult_delta=regime_sl_delta,
            )
        if action == "SELL" and state["state"] == "bought":
            if str(state.get("position_side") or "LONG").upper() == "SHORT":
                closed = self.execute_close_short(state, ticker_price, trigger_reason=reason, symbol=sym)
            else:
                closed = self.execute_sell(state, ticker_price, trigger_reason=reason, symbol=sym)
            open_reverse_after_close(closed)
        elif action == "BUY" and state["state"] == "idle" and signal_side == "SHORT":
            self.execute_open_short(signal, ticker_price, symbol=sym)
        elif state["state"] == "bought" and str(state.get("position_side") or "LONG").upper() != "SHORT":
            # Short positions do not use this long-only trailing-stop branch,
            # but they must still reach the state exporter below.  Returning
            # here left by_symbol[SHORT] snapshots frozen while the engine
            # continued evaluating the position.
            highest_seen = max(state.get("highest_price_seen", 0.0), ticker_price)

            current_sl = state.get("stop_loss", 0.0)
            # CDC-pure mode: no stop loss / no trailing — exit only on a RED zone
            # (SELL signal). Keep the SL pinned (0.0) and just track the peak.
            disable_sl = bool(strat_conf.get("disable_stop_loss", False))
            # A NO_TRADE regime tightens the trail; that policy is the engine's,
            # so it is applied to the multiplier here and the shared resolver
            # below computes the price the same way replay does (roadmap P1.7).
            trailing_mult = float(strat_conf.get("trailing_atr_mult", 1.5))
            if sc.no_trade_state in ("NO_TRADE", "NO_TRADE_PENDING", "HANDOFF"):
                trailing_mult = min(trailing_mult, float(sc.trailing_atr_mult or 1.0))
            candidate_sl = next_trailing_stop(
                side="long",
                entry_price=float(state.get("entry_price", 0.0)),
                extreme_price=float(highest_seen),
                current_sl=float(current_sl),
                atr=float(atr),
                trailing_atr_mult=trailing_mult,
                trail_distance=signal.trail_distance,
                breakeven_enabled=bool(strat_conf.get("breakeven_sl_enabled", False)),
                breakeven_activation_atr_mult=float(
                    strat_conf.get("breakeven_activation_atr_mult", 1.5)),
                breakeven_buffer_atr_mult=float(
                    strat_conf.get("breakeven_buffer_atr_mult", 0.1)),
                disable_stop_loss=disable_sl,
            )
            sl_order_id = state.get("stop_loss_order_id")
            state_changed = False
            new_sl = current_sl
            new_sl_order_id = sl_order_id

            if self._trailing_stop_update_allowed(
                state=state,
                strat_conf=strat_conf,
                candidate_sl=float(candidate_sl),
                current_sl=float(current_sl),
                highest_seen=float(highest_seen),
                atr=float(atr),
            ):
                new_sl = candidate_sl
                state_changed = True
                sl_update_succeeded = True
                if self._execution_mode(sym) == "live":
                    old_sl_cancelled = False
                    if sl_order_id:
                        try:
                            self.client.cancel_order(sym, sl_order_id)
                            old_sl_cancelled = True
                        except ExchangeAPIError as ce:
                            if ce.code in (-2013, -2026):
                                logger.warning(
                                    f"Trailing Stop: Old SL {sl_order_id} already filled/cancelled."
                                )
                                old_sl_cancelled = True
                            else:
                                logger.error(
                                    f"Trailing Stop: Failed to cancel old SL {sl_order_id}: {ce}"
                                )
                        except Exception as ce:
                            logger.error(
                                f"Trailing Stop: Failed to cancel old SL {sl_order_id}: {ce}"
                            )

                    if sl_order_id and not old_sl_cancelled:
                        placed_new_sl_res = None
                    else:
                        if old_sl_cancelled:
                            self._wait_for_sl_replacement_balance(
                                sym, state["quantity"], candidate_sl
                            )
                        placed_new_sl_res = self._place_sl_with_retry(
                            state["quantity"], candidate_sl, symbol=sym
                        )

                    if placed_new_sl_res:
                        new_sl_order_id = placed_new_sl_res[0]
                    else:
                        logger.error("Trailing Stop: Failed to place new SL; keeping previous SL")
                        restored_sl_id = None
                        if old_sl_cancelled and current_sl > 0:
                            restored = self._place_sl_with_retry(
                                state["quantity"], float(current_sl), symbol=sym
                            )
                            restored_sl_id = restored[0] if restored else None
                            if not restored_sl_id:
                                self.send_telegram_alert(
                                    "🚨 *CRITICAL*: Trailing Stop update failed and previous exchange SL could not be restored.",
                                    level=AlertLevel.CRITICAL,
                                )
                        elif sl_order_id:
                            try:
                                existing = self.client.get_order(sym, sl_order_id)
                                if str(existing.get("status") or "").upper() in {
                                    "NEW",
                                    "PARTIALLY_FILLED",
                                }:
                                    restored_sl_id = sl_order_id
                            except Exception as verify_exc:
                                logger.error(
                                    "Trailing Stop: could not verify previous SL %s after cancel failure: %s",
                                    sl_order_id,
                                    verify_exc,
                                )
                        new_sl = current_sl
                        new_sl_order_id = restored_sl_id
                        sl_update_succeeded = False
                        if restored_sl_id:
                            logger.warning(
                                "Trailing Stop: update postponed; exchange SL protection remains active "
                                "(order_id=%s, sl=%.2f)",
                                restored_sl_id,
                                float(current_sl),
                            )
                        else:
                            self.send_telegram_alert(
                                "⚠️ *Trailing Stop Failed*: Could not update exchange SL order.",
                                level=AlertLevel.POSITION,
                            )

                if sl_update_succeeded:
                    msg = (
                        f"↗️ [TRAILING STOP] Raised SL for {sym} to {new_sl:.2f} USDT "
                        f"(Peak: {highest_seen:.2f})"
                    )
                    logger.info(msg)
                    self.last_log_message = msg
                    self._emit_event(
                        EventType.STOP_LOSS_UPDATED,
                        old_sl=round(float(current_sl), 2),
                        new_sl=round(float(new_sl), 2),
                        peak=round(float(highest_seen), 2),
                    )
                    if self.config.get("monitoring", {}).get("alert_on_trailing_stop", True):
                        self.send_telegram_alert(
                            f"↗️ *Trailing Stop Updated*\nSymbol: `{sym}`\n"
                            f"New SL: `{new_sl:.2f} USDT`\nPeak: `{highest_seen:.2f} USDT`",
                            level=AlertLevel.POSITION,
                        )
            elif highest_seen > state.get("highest_price_seen", 0.0):
                state_changed = True

            if state_changed:
                self.db.save_trade_state(
                    symbol=sym,
                    state="bought",
                    entry_price=state["entry_price"],
                    stop_loss=new_sl,
                    take_profit=state.get("take_profit", 0.0),
                    highest_price_seen=highest_seen,
                    quantity=state["quantity"],
                    opened_at=state["opened_at"],
                    last_transition_at=state["last_transition_at"],
                    stop_loss_order_id=new_sl_order_id,
                )

        updated_state = self.db.get_trade_state(sym)
        self.update_state_json(updated_state, indicators, symbol=sym)

    def _position_age_minutes(self, opened_at: Any) -> float:
        if not opened_at:
            return 0.0
        try:
            opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_seconds = (
                datetime.now(timezone.utc) - opened.astimezone(timezone.utc)
            ).total_seconds()
            return max(0.0, age_seconds / 60.0)
        except Exception:
            return 0.0

    def _trailing_stop_update_allowed(
        self,
        *,
        state: Dict[str, Any],
        strat_conf: Dict[str, Any],
        candidate_sl: float,
        current_sl: float,
        highest_seen: float,
        atr: float,
    ) -> bool:
        if candidate_sl <= current_sl:
            return False
        if current_sl <= 0:
            return True

        entry_price = float(state.get("entry_price", 0.0) or 0.0)
        try:
            min_minutes = float(strat_conf.get("trail_activation_min_minutes", 0.0) or 0.0)
        except (TypeError, ValueError):
            min_minutes = 0.0
        if min_minutes > 0 and self._position_age_minutes(state.get("opened_at")) < min_minutes:
            return False

        try:
            activation_atr_mult = float(strat_conf.get("trail_activation_atr_mult", 0.0) or 0.0)
        except (TypeError, ValueError):
            activation_atr_mult = 0.0
        if atr > 0 and activation_atr_mult > 0 and highest_seen - entry_price < atr * activation_atr_mult:
            return False

        try:
            min_delta_atr = float(strat_conf.get("trail_update_min_delta_atr", 0.0) or 0.0)
        except (TypeError, ValueError):
            min_delta_atr = 0.0
        if atr > 0 and min_delta_atr > 0 and candidate_sl - current_sl < atr * min_delta_atr:
            return False
        return True

    def stop(self, reason: Optional[str] = None):
        was_running = bool(getattr(self, "_engine_loop_started", False))
        active_syms = ", ".join(self._pair_registry.active_symbols()) or self.focus_symbol
        stop_msg = (
            f"🛑 *Engine Stopped*\n"
            f"Pairs: `{active_syms}`\n"
            f"Focus: `{self.focus_symbol}`\n"
            f"Mode: `{'SIM' if self.simulate_only else 'LIVE'}`"
        )
        if reason:
            stop_msg += f"\nReason: `{reason}`"
        if was_running and self._should_send_alert(AlertLevel.INFO):
            if self._notif_settings.alert_channel == "console":
                logger.info("[ALERT/info] %s", stop_msg.replace("\n", " | "))
            elif hasattr(self._tg_service, "send_immediate"):
                self._tg_service.send_immediate(stop_msg)
            else:
                self.send_telegram_alert(stop_msg, level=AlertLevel.INFO)
        self._engine_loop_started = False
        self._emit_event(EventType.ENGINE_STOPPED, reason=reason or "")
        try:
            for strategy in getattr(self, "_strategies_by_symbol", {}).values():
                strategy.on_stop()
        except Exception as e:
            logger.error(f"strategy.on_stop failed: {e}")
        try:
            if self.ws is not None:
                self.ws.stop()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass
        if hasattr(self, "_tg_bot") and self._tg_bot:
            try:
                self._tg_bot.stop()
            except Exception:
                pass
        if hasattr(self, "_tg_service"):
            try:
                self._tg_service.stop()
            except Exception:
                pass
        self._release_engine_lock()
        self._release_account_lock()

    def start(self):
        from xauby.runtime.exits import validate_exit_config
        from xauby.runtime.trading_config import (
            validate_open_positions_config,
            validate_risk_config,
        )

        validate_risk_config(self.config)
        live_pairs = sum(
            1
            for spec in self._pair_registry.active()
            if getattr(spec, "execution_mode", "sim") == "live"
        )
        validate_open_positions_config(self.config, live_pair_count=live_pairs)
        # Every active pair, not just live ones: a dead partial-TP key misreports
        # what a sim pair does too, and sim is where a config is vetted first.
        for sym in self._pair_registry.active_symbols():
            validate_exit_config(self._get_strategy_config(sym), symbol=sym)
        active_syms = ", ".join(self._pair_registry.active_symbols()) or self.focus_symbol
        logger.info(
            f"Starting Lite Trading Engine. Pairs: {active_syms}, "
            f"Focus: {self.focus_symbol}, "
            f"Mode: {'Simulation (Paper)' if self.simulate_only else 'Live Orders'}, "
            f"Read-only: {self.read_only}"
        )
        self._acquire_engine_lock()
        # Cross-instance guard: fail-closed if another live engine already holds
        # this exchange account (prevents double-counted risk on a shared account).
        self._acquire_account_lock()
        self._engine_loop_started = True
        self._emit_event(
            EventType.ENGINE_STARTED,
            mode="paper" if self.simulate_only else "live",
            symbol=active_syms,
        )
        self.send_telegram_alert(
            f"🤖 *Lite Bot Started*\nPairs: `{active_syms}`\n"
            f"Focus: `{self.focus_symbol}`\n"
            f"Mode: `{'Simulated' if self.simulate_only else 'LIVE'}`"
        )
        
        self.sync_candles()
        self.reconcile_startup_state()
        # Reconciliation can preserve an exchange-backed position across a
        # controlled restart.  That position was opened in the previous run,
        # so seed this run's durable event stream explicitly; otherwise replay
        # evaluates every post-restart short tick as if the account were flat.
        for spec in self._pair_registry.active():
            restored = self.db.get_trade_state(spec.symbol)
            if restored.get("state") != "bought":
                continue
            self._emit_event(
                EventType.POSITION_RESTORED,
                symbol=spec.symbol,
                position_side=str(restored.get("position_side") or "LONG").upper(),
                quantity=float(restored.get("quantity") or 0.0),
                entry_price=float(restored.get("entry_price") or 0.0),
                stop_loss=float(restored.get("stop_loss") or 0.0),
                opened_at=restored.get("opened_at"),
                management_mode=str(restored.get("management_mode") or "strategy"),
            )
        self.run_retention_pass(startup=True)
        if self.ws is not None:
            self.ws.start()
        from xauby.runtime.exchange_switch import verify_pending_exchange_activation
        if not verify_pending_exchange_activation(self):
            raise RuntimeError("new exchange failed post-restart identity/WebSocket health verification")
        
        interval = int(self.config.get("trading", {}).get("interval_seconds", 60))
        heartbeat_interval = int(self.config.get("monitoring", {}).get("heartbeat_interval_minutes", 0)) * 60
        self.last_heartbeat_time = time.time()
        
        cleanup_interval = int(
            self.config.get("candle_retention", {}).get("cleanup_interval_hours", 12)
        ) * 3600
        self.last_cleanup_time = time.time()

        wal_checkpoint_interval = int(
            self.config.get("candle_retention", {}).get("wal_checkpoint_interval_hours", 6)
        ) * 3600
        self.last_wal_checkpoint_time = time.time()

        # A stop-and-reverse (close LONG, open SHORT) can leave local state
        # ahead of the exchange if a swap order silently doesn't fill; startup
        # reconciliation alone misses that until the next restart, so also
        # reconcile periodically while running (trading.runtime_order_reconcile).
        runtime_reconcile_enabled = bool(
            self.config.get("trading", {}).get("runtime_order_reconcile", True)
        )
        reconcile_interval = int(
            self.config.get("trading", {}).get("runtime_reconcile_interval_minutes", 1)
        ) * 60
        self.last_reconcile_time = time.time()

        while True:
            try:
                now = time.time()
                if runtime_reconcile_enabled and reconcile_interval > 0:
                    if now - self.last_reconcile_time >= reconcile_interval:
                        self.reconcile_startup_state()
                        self.last_reconcile_time = now

                self.tick()

                self._maybe_start_balance_refresh()

                now = time.time()
                if now - self.last_cleanup_time >= cleanup_interval:
                    self.run_retention_pass(startup=False)
                    self.last_cleanup_time = now

                if wal_checkpoint_interval > 0:
                    if now - self.last_wal_checkpoint_time >= wal_checkpoint_interval:
                        self.db.wal_checkpoint()
                        self.last_wal_checkpoint_time = now

                if heartbeat_interval > 0:
                    if now - self.last_heartbeat_time >= heartbeat_interval:
                        self.send_heartbeat()
                        self.last_heartbeat_time = now

                self._report_scheduler.check_and_run(self, datetime.now(timezone.utc))
            except Exception as e:
                msg = f"🚨 *CRITICAL ENGINE EXCEPTION*\nSymbol: `{self.symbol}`\nError: `{e}`\nLoop will retry in {interval}s."
                logger.critical(msg, exc_info=True)
                self._emit_event(EventType.ERROR, context="engine_loop", error=str(e))
                self.send_telegram_alert(msg, level=AlertLevel.CRITICAL)
            time.sleep(interval)
