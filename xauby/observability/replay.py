"""Replay foundation: ContextBuilder, PositionSimulator, ReplayEngine."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from xauby.runtime.exits import minimal_roi_pct, next_trailing_stop
from xauby.strategies.context import MarketContext
from xauby.strategies.sandbox import StrategyRunner
from xauby.strategies.signal import Signal, hold as _hold_signal

logger = logging.getLogger("xauby.observability.replay")


def _slice_regime_by_timestamp(
    df_regime: pd.DataFrame, cutoff_ts: int
) -> pd.DataFrame:
    """Keep regime candles at or before the primary-bar cutoff timestamp."""
    if df_regime is None or df_regime.empty:
        return df_regime
    if "timestamp" not in df_regime.columns:
        return df_regime.iloc[:0].copy()
    return df_regime[df_regime["timestamp"] <= cutoff_ts].copy()


class ContextBuilder:
    """Build MarketContext slices for live ticks and historical replay."""

    @staticmethod
    def build(
        *,
        symbol: str,
        timeframe_primary: str,
        df_primary: pd.DataFrame,
        current_price: float,
        has_position: bool = False,
        position_side: Optional[str] = None,
        stop_loss: float = 0.0,
        sl_confirmed: bool = False,
        df_regime: Optional[pd.DataFrame] = None,
        timeframe_regime: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        engine_config: Optional[Dict[str, Any]] = None,
        extras: Optional[Dict[str, Any]] = None,
        end_index: Optional[int] = None,
    ) -> MarketContext:
        """Slice candles up to ``end_index`` (inclusive) for bar-by-bar replay."""
        if end_index is not None:
            df_p = df_primary.iloc[: end_index + 1].copy()
            if df_regime is not None and not df_regime.empty and not df_p.empty:
                cutoff_ts = int(df_p.iloc[-1].get("timestamp", 0) or 0)
                df_r = _slice_regime_by_timestamp(df_regime, cutoff_ts)
            else:
                df_r = None
        else:
            df_p = df_primary
            df_r = df_regime

        return MarketContext(
            symbol=symbol,
            timeframe_primary=timeframe_primary,
            df_primary=df_p,
            df_regime=df_r,
            current_price=current_price,
            has_position=has_position,
            position_side=position_side,
            stop_loss=stop_loss,
            sl_confirmed=sl_confirmed,
            timeframe_regime=timeframe_regime,
            config=config or {},
            engine_config=engine_config or {},
            extras=extras or {},
        )


@dataclass
class SimPosition:
    entry_price: float = 0.0
    stop_loss: float = 0.0
    highest_price: float = 0.0
    qty: float = 0.0
    entry_time: int = 0
    initial_sl: float = 0.0
    funding_paid: float = 0.0  # cumulative perp funding cost (>0 = paid out)
    side: int = 1  # +1 long, -1 short
    lowest_price: float = 0.0  # peak-favourable extreme for short trailing
    # Partial take-profit bookkeeping: one banked leg per position. The final
    # SimTrade carries the COMBINED net PnL so trade counts (and win rate)
    # stay comparable with non-partial runs.
    original_qty: float = 0.0
    partial_taken: bool = False
    banked_pnl: float = 0.0


@dataclass
class SimTrade:
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    trigger: str


class PositionSimulator:
    """Portable SL / trailing / BE / fee math for replay and backtest."""

    def __init__(
        self,
        initial_balance: float = 1000.0,
        fee_pct: float = 0.001,
        sl_atr_mult: float = 2.5,
        trailing_atr_mult: float = 1.5,
        be_enabled: bool = False,
        be_activation_atr_mult: float = 1.5,
        be_buffer_atr_mult: float = 0.1,
        risk_pct: float = 0.01,
        max_position_pct: float = 0.0,
        sl_confirm_ticks: int = 3,
        slippage_bps: float = 0.0,
        fixed_tp_pct: float = 0.0,
        disable_stop_loss: bool = False,
        position_pct: float = 1.0,
        funding_rate_8h: float = 0.0,
        bar_hours: float = 0.0,
        minimal_roi: Optional[List[Tuple[float, float]]] = None,
        partial_tp_pct: float = 0.0,
        partial_tp_fraction: float = 0.5,
        throttle_after_losses: int = 0,
        throttle_scale: float = 0.5,
        entry_size_scale: Optional[Callable[[int], float]] = None,
    ):
        self.balance = initial_balance
        self._initial_balance = initial_balance
        self.fee_pct = fee_pct
        self.slippage_bps = max(0.0, slippage_bps)
        # Perpetual-swap funding. ``funding_rate_8h`` is the average rate longs
        # pay shorts each 8h window; per-bar accrual scales it by bar_hours/8.
        # Default 0.0 keeps spot backtests (and existing results) unchanged.
        self.funding_rate_8h = float(funding_rate_8h or 0.0)
        self.bar_hours = max(0.0, float(bar_hours or 0.0))
        self.sl_atr_mult = sl_atr_mult
        self.trailing_atr_mult = trailing_atr_mult
        self.fixed_tp_pct = max(0.0, float(fixed_tp_pct or 0.0))
        # Sorted [(age_minutes, roi_pct)] ladder from resolve_minimal_roi().
        self.minimal_roi: List[Tuple[float, float]] = list(minimal_roi or [])
        # One-shot partial TP: close `fraction` of the position at +pct.
        self.partial_tp_pct = max(0.0, float(partial_tp_pct or 0.0))
        self.partial_tp_fraction = float(partial_tp_fraction or 0.0)
        self._partial_enabled = (
            self.partial_tp_pct > 0 and 0.0 < self.partial_tp_fraction < 1.0
        )
        # Chop-defence sizing: halve (throttle_scale) entries after N straight
        # losing positions until a winner resets the streak, and/or scale by a
        # caller-supplied per-bar factor (e.g. regime/ADX). Entries and exits
        # are untouched — only qty scales, so per-trade pnl% and win rate stay
        # identical to an unthrottled run.
        self.throttle_after_losses = max(0, int(throttle_after_losses or 0))
        self.throttle_scale = min(1.0, max(0.0, float(throttle_scale or 0.5)))
        self.entry_size_scale = entry_size_scale
        self._loss_streak = 0
        self.be_enabled = be_enabled
        self.be_activation_atr_mult = be_activation_atr_mult
        self.be_buffer_atr_mult = be_buffer_atr_mult
        self.risk_pct = risk_pct
        self.max_position_pct = max_position_pct
        self.sl_confirm_ticks = max(1, sl_confirm_ticks)
        # CDC-pure mode: no SL/trailing, fixed-fraction sizing (exit only on RED).
        self.disable_stop_loss = bool(disable_stop_loss)
        self.position_pct = max(0.0, min(float(position_pct), 1.0))
        self.sl_breach_bars: int = 0
        self.position: Optional[SimPosition] = None
        self.trades: List[SimTrade] = []
        self._equity_curve: List[float] = []  # per-bar mark-to-close equity
        # Research callers may request the aligned per-bar position state.  It
        # is kept beside the equity curve so two isolated sleeves can report
        # how often they would oppose each other without touching broker code.
        self._position_side_curve: List[Optional[str]] = []
        self.bars_in_position: int = 0  # bars held — for exposure %

    def _entry_scale(self, bar_time: int) -> float:
        """Combined chop-defence size multiplier for an entry at ``bar_time``."""
        scale = 1.0
        if self.throttle_after_losses > 0 and self._loss_streak >= self.throttle_after_losses:
            scale *= self.throttle_scale
        if self.entry_size_scale is not None:
            try:
                scale *= max(0.0, min(1.0, float(self.entry_size_scale(bar_time))))
            except Exception:
                pass
        return scale

    @property
    def has_position(self) -> bool:
        return self.position is not None

    @property
    def sl_confirmed(self) -> bool:
        return self.sl_breach_bars >= self.sl_confirm_ticks

    def current_equity(self, mark_price: float) -> float:
        """Mark-to-market equity: balance + unrealized PnL (both fees included)."""
        if not self.has_position:
            return self.balance
        pos = self.position
        unrealized = pos.side * (mark_price - pos.entry_price) * pos.qty
        entry_fee = pos.entry_price * pos.qty * self.fee_pct
        exit_fee_est = mark_price * pos.qty * self.fee_pct
        return self.balance + unrealized - entry_fee - exit_fee_est - pos.funding_paid

    def accrue_funding(self) -> float:
        """Accrue one bar of perpetual funding onto the open position.

        Long positions pay (``funding_paid`` rises) when ``funding_rate_8h`` is
        positive; shorts receive it. Notional uses the entry price — a flat,
        config-driven approximation rather than a real funding-history lookup.
        Returns the increment (0.0 when not configured or flat).
        """
        if not self.has_position or self.funding_rate_8h == 0.0 or self.bar_hours <= 0.0:
            return 0.0
        pos = self.position
        notional = pos.entry_price * pos.qty
        funding = notional * self.funding_rate_8h * (self.bar_hours / 8.0)
        sign = -1.0 if getattr(pos, "side", 1) < 0 else 1.0  # short receives
        pos.funding_paid += sign * funding
        return sign * funding

    def update_sl_breach(self, price: float) -> None:
        """Update consecutive breach count (mirrors live tick SL confirm).

        A long stop is breached from above (price <= sl); a short stop from
        below (price >= sl). Callers pass the bar extreme on the breach side
        (low for longs, high for shorts).
        """
        if not self.has_position:
            self.sl_breach_bars = 0
            return
        pos = self.position
        sl = pos.stop_loss
        breached = (price >= sl) if pos.side < 0 else (price <= sl)
        if sl > 0 and breached:
            self.sl_breach_bars += 1
        else:
            self.sl_breach_bars = 0

    def _sl_distance(
        self,
        entry_price: float,
        atr: float,
        signal: Optional[Signal],
    ) -> float:
        if signal is not None:
            if (
                signal.stop_loss_distance is not None
                and signal.stop_loss_distance > 0
            ):
                return float(signal.stop_loss_distance)
            if (
                signal.stop_loss_price is not None
                and signal.stop_loss_price > 0
            ):
                return entry_price - float(signal.stop_loss_price)
        if atr > 0:
            return atr * self.sl_atr_mult
        return 0.0

    def try_open(
        self,
        entry_price: float,
        atr: float,
        bar_time: int,
        signal: Optional[Signal] = None,
    ) -> bool:
        if self.has_position or entry_price <= 0:
            return False

        # Apply buy-side slippage: market buy fills slightly above quote price
        slip_factor = 1.0 + self.slippage_bps / 10000.0
        fill_price = entry_price * slip_factor

        if self.disable_stop_loss:
            buy_amount_usdt = self.balance * self.position_pct * self._entry_scale(bar_time)
            qty = buy_amount_usdt / fill_price if fill_price > 0 else 0.0
            if qty <= 0:
                return False
            self.position = SimPosition(
                entry_price=fill_price,
                stop_loss=0.0,
                highest_price=fill_price,
                qty=qty,
                entry_time=bar_time,
                initial_sl=0.0,
            )
            self.position.original_qty = self.position.qty
            self.sl_breach_bars = 0
            return True

        sl_distance = self._sl_distance(fill_price, atr, signal)
        has_strategy_sl = (
            signal is not None
            and signal.stop_loss_price is not None
            and signal.stop_loss_price > 0
        )
        if sl_distance <= 0 and not has_strategy_sl:
            return False

        risk_amount = self.balance * self.risk_pct * self._entry_scale(bar_time)
        qty = risk_amount / sl_distance
        buy_amount_usdt = qty * fill_price

        if self.max_position_pct > 0:
            max_usdt = self.balance * self.max_position_pct
            if buy_amount_usdt > max_usdt:
                buy_amount_usdt = max_usdt
                qty = buy_amount_usdt / fill_price

        if qty <= 0:
            return False

        if has_strategy_sl:
            sl = float(signal.stop_loss_price)
        else:
            sl = fill_price - sl_distance

        self.position = SimPosition(
            entry_price=fill_price,
            stop_loss=sl,
            highest_price=fill_price,
            qty=qty,
            entry_time=bar_time,
            initial_sl=sl,
        )
        self.position.original_qty = self.position.qty
        self.sl_breach_bars = 0
        return True

    def try_open_short(
        self,
        entry_price: float,
        atr: float,
        bar_time: int,
        signal: Optional[Signal] = None,
    ) -> bool:
        """Open a short. Mirror of :meth:`try_open`: the stop sits ABOVE entry
        and the entry fills slightly below quote (a market sell)."""
        if self.has_position or entry_price <= 0:
            return False

        slip_factor = 1.0 - self.slippage_bps / 10000.0
        fill_price = entry_price * slip_factor

        if self.disable_stop_loss:
            sell_amount_usdt = self.balance * self.position_pct * self._entry_scale(bar_time)
            qty = sell_amount_usdt / fill_price if fill_price > 0 else 0.0
            if qty <= 0:
                return False
            self.position = SimPosition(
                entry_price=fill_price, stop_loss=0.0, highest_price=fill_price,
                lowest_price=fill_price, qty=qty, entry_time=bar_time,
                initial_sl=0.0, side=-1,
            )
            self.position.original_qty = self.position.qty
            self.sl_breach_bars = 0
            return True

        # Distance from entry up to the stop (positive number).
        sl_distance = 0.0
        has_strategy_sl = (
            signal is not None
            and signal.stop_loss_price is not None
            and signal.stop_loss_price > 0
        )
        if signal is not None and signal.stop_loss_distance and signal.stop_loss_distance > 0:
            sl_distance = float(signal.stop_loss_distance)
        elif has_strategy_sl:
            sl_distance = float(signal.stop_loss_price) - fill_price
        elif atr > 0:
            sl_distance = atr * self.sl_atr_mult
        if sl_distance <= 0 and not has_strategy_sl:
            return False

        qty = (
            (self.balance * self.risk_pct * self._entry_scale(bar_time)) / sl_distance
            if sl_distance > 0
            else 0.0
        )
        notional = qty * fill_price
        if self.max_position_pct > 0:
            max_usdt = self.balance * self.max_position_pct
            if notional > max_usdt:
                qty = max_usdt / fill_price
        if qty <= 0:
            return False

        sl = float(signal.stop_loss_price) if has_strategy_sl else fill_price + sl_distance
        self.position = SimPosition(
            entry_price=fill_price, stop_loss=sl, highest_price=fill_price,
            lowest_price=fill_price, qty=qty, entry_time=bar_time,
            initial_sl=sl, side=-1,
        )
        self.position.original_qty = self.position.qty
        self.sl_breach_bars = 0
        return True

    def _close(self, exit_price: float, bar_time: int, trigger: str) -> None:
        if not self.position:
            return
        pos = self.position
        # Closing a long is a market SELL (fills slightly below quote); closing
        # a short is a market BUY (fills slightly above). Slippage hurts both.
        slip = self.slippage_bps / 10000.0
        slip_factor = (1.0 - slip) if pos.side > 0 else (1.0 + slip)
        fill_price = exit_price * slip_factor
        pnl = pos.side * (fill_price - pos.entry_price) * pos.qty
        fee = (pos.entry_price * pos.qty * self.fee_pct) + (
            fill_price * pos.qty * self.fee_pct
        )
        net = pnl - fee - pos.funding_paid
        self.balance += net
        # One SimTrade per position: combine any banked partial-TP leg so the
        # trade count (and thus win rate) matches non-partial runs, and use the
        # ORIGINAL notional as the percent base.
        total_net = net + pos.banked_pnl
        cost = pos.entry_price * (pos.original_qty or pos.qty)
        self.trades.append(
            SimTrade(
                entry_time=pos.entry_time,
                exit_time=bar_time,
                entry_price=pos.entry_price,
                exit_price=fill_price,
                pnl=total_net,
                pnl_pct=(total_net / cost * 100) if cost > 0 else 0.0,
                trigger=trigger,
            )
        )
        self.position = None
        self.sl_breach_bars = 0
        # Loss-streak throttle bookkeeping: a losing POSITION (combined with
        # any banked partial leg) extends the streak; a winner resets it.
        if total_net <= 0:
            self._loss_streak += 1
        else:
            self._loss_streak = 0

    def _take_partial(
        self,
        pos: SimPosition,
        fill_basis: float,
        bar_time: int,
        events: List[Dict[str, Any]],
    ) -> None:
        """Bank ``partial_tp_fraction`` of the position at ``fill_basis``.

        The banked leg realizes its own fees and its share of accrued funding;
        the remainder keeps riding with proportionally reduced qty/funding so
        later accrual and the final close stay exact.
        """
        slip = self.slippage_bps / 10000.0
        fill = fill_basis * ((1.0 - slip) if pos.side > 0 else (1.0 + slip))
        qty_p = pos.qty * self.partial_tp_fraction
        pnl = pos.side * (fill - pos.entry_price) * qty_p
        fee = (pos.entry_price * qty_p * self.fee_pct) + (fill * qty_p * self.fee_pct)
        funding_share = pos.funding_paid * self.partial_tp_fraction
        net = pnl - fee - funding_share
        self.balance += net
        pos.banked_pnl += net
        pos.funding_paid -= funding_share
        pos.qty -= qty_p
        pos.partial_taken = True
        events.append(
            {
                "event_type": "partial_tp_triggered",
                "price": fill,
                "qty": qty_p,
                "banked_pnl": net,
                "bar_time": bar_time,
            }
        )

    def on_bar(
        self,
        *,
        open_p: float,
        high: float,
        low: float,
        close: float,
        atr: float,
        zone: str,
        bar_time: int,
        signal: Signal,
    ) -> List[Dict[str, Any]]:
        """Process one bar; return list of synthetic events."""
        events: List[Dict[str, Any]] = []
        events.append(
            {
                "event_type": "signal_evaluated",
                "action": signal.action,
                "reason": signal.reason,
                "bar_time": bar_time,
            }
        )

        if not self.has_position:
            opens_short = signal.is_short and signal.intent == "OPEN"
            opens_long = signal.action == "BUY" and not signal.is_short
            opened = False
            if opens_long:
                opened = self.try_open(open_p, atr, bar_time, signal=signal)
            elif opens_short:
                opened = self.try_open_short(open_p, atr, bar_time, signal=signal)
            if opened:
                events.append(
                    {
                        "event_type": "position_opened",
                        "entry": open_p,
                        "stop_loss": self.position.stop_loss,
                        "qty": self.position.qty,
                        "side": "SHORT" if self.position.side < 0 else "LONG",
                    }
                )
            if not self.has_position:
                return events

        pos = self.position
        if pos.side < 0:
            return self._manage_short(events, pos, open_p, high, low, atr, bar_time, signal)
        return self._manage_long(events, pos, open_p, high, atr, bar_time, signal)

    @staticmethod
    def _reverse_side(signal: Signal) -> str:
        metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
        return str(metadata.get("reverse_to_position_side") or "").upper()

    def _open_reverse_from_signal(
        self,
        events: List[Dict[str, Any]],
        *,
        open_p: float,
        atr: float,
        bar_time: int,
        signal: Signal,
    ) -> None:
        side = self._reverse_side(signal)
        opened = False
        if side == "SHORT":
            opened = self.try_open_short(open_p, atr, bar_time, signal=signal)
        elif side == "LONG":
            opened = self.try_open(open_p, atr, bar_time, signal=signal)
        if opened:
            events.append(
                {
                    "event_type": "position_opened",
                    "entry": open_p,
                    "stop_loss": self.position.stop_loss,
                    "qty": self.position.qty,
                    "side": side,
                    "trigger": "REVERSE",
                }
            )

    def _manage_long(
        self,
        events: List[Dict[str, Any]],
        pos: SimPosition,
        open_p: float,
        high: float,
        atr: float,
        bar_time: int,
        signal: Signal,
    ) -> List[Dict[str, Any]]:
        # Gap-through stop at bar open (exchange SL; no tick confirm wait).
        if pos.stop_loss > 0 and open_p <= pos.stop_loss:
            exit_p = open_p
            events.append(
                {"event_type": "stop_loss_triggered", "price": exit_p, "stop_loss": pos.stop_loss}
            )
            # TRAILING_STOP: stop trailed to at-or-above entry (profit protected / BE).
            # STOP_LOSS: stop still below entry — initial risk stop was hit.
            trigger_type = "TRAILING_STOP" if pos.stop_loss >= pos.entry_price else "STOP_LOSS"
            self._close(exit_p, bar_time, trigger_type)
            events.append({"event_type": "position_closed", "exit": exit_p})
            return events

        # Strategy-driven exit (SL confirm, zone RED, etc.).
        if signal.action == "SELL":
            trigger_type = "SIGNAL"
            if "Stop loss confirmed" in str(signal.reason):
                trigger_type = "TRAILING_STOP" if pos.stop_loss >= pos.entry_price else "STOP_LOSS"
            self._close(open_p, bar_time, trigger_type)
            events.append({"event_type": "position_closed", "exit": open_p})
            if trigger_type == "SIGNAL":
                self._open_reverse_from_signal(
                    events,
                    open_p=open_p,
                    atr=atr,
                    bar_time=bar_time,
                    signal=signal,
                )
            return events

        # Engine-managed fixed take-profit (mirrors live ``price >= take_profit``).
        if self.fixed_tp_pct > 0 and pos.entry_price > 0:
            tp_price = pos.entry_price * (1.0 + self.fixed_tp_pct / 100.0)
            if high >= tp_price:
                events.append({"event_type": "take_profit_triggered", "price": tp_price})
                self._close(tp_price, bar_time, "FIXED_TP")
                events.append({"event_type": "position_closed", "exit": tp_price})
                return events

        # Minimal ROI ladder (engine-managed, mirrors live tick check). When the
        # active threshold is already met at the open (e.g. it just stepped
        # down), exit at the open like a live market order; otherwise treat the
        # threshold price as a limit filled intrabar.
        if self.minimal_roi and pos.entry_price > 0:
            age_minutes = max(0.0, (bar_time - pos.entry_time) / 60.0)
            roi_pct = minimal_roi_pct(self.minimal_roi, age_minutes)
            if roi_pct > 0:
                roi_price = pos.entry_price * (1.0 + roi_pct / 100.0)
                exit_p = open_p if open_p >= roi_price else (roi_price if high >= roi_price else 0.0)
                if exit_p > 0:
                    events.append({"event_type": "minimal_roi_triggered",
                                   "price": exit_p, "roi_pct": roi_pct,
                                   "age_minutes": age_minutes})
                    self._close(exit_p, bar_time, "MINIMAL_ROI")
                    events.append({"event_type": "position_closed", "exit": exit_p})
                    return events

        # One-shot partial TP for a long: target sits ABOVE entry. A bar that
        # opens beyond it fills at the (better) open; otherwise the target acts
        # as a resting limit filled intrabar.
        if self._partial_enabled and not pos.partial_taken and pos.entry_price > 0:
            target = pos.entry_price * (1.0 + self.partial_tp_pct / 100.0)
            basis = open_p if open_p >= target else (target if high >= target else 0.0)
            if basis > 0:
                self._take_partial(pos, basis, bar_time, events)

        pos.highest_price = max(pos.highest_price, high)
        # CDC-pure mode: no trailing stop — exit is driven only by the RED zone.
        if self.disable_stop_loss:
            return events
        # Shared with the live engine (roadmap P1.7): the two used to compute
        # this separately and disagreed on trail_distance and on atr == 0.
        candidate_sl = next_trailing_stop(
            side="long",
            entry_price=pos.entry_price,
            extreme_price=pos.highest_price,
            current_sl=pos.stop_loss,
            atr=atr,
            trailing_atr_mult=self.trailing_atr_mult,
            trail_distance=getattr(signal, "trail_distance", None),
            breakeven_enabled=self.be_enabled,
            breakeven_activation_atr_mult=self.be_activation_atr_mult,
            breakeven_buffer_atr_mult=self.be_buffer_atr_mult,
        )
        if candidate_sl > pos.stop_loss:
            old = pos.stop_loss
            pos.stop_loss = candidate_sl
            events.append(
                {"event_type": "stop_loss_updated", "old_sl": old,
                 "new_sl": candidate_sl, "peak": pos.highest_price}
            )
        return events

    def _manage_short(
        self,
        events: List[Dict[str, Any]],
        pos: SimPosition,
        open_p: float,
        high: float,
        low: float,
        atr: float,
        bar_time: int,
        signal: Signal,
    ) -> List[Dict[str, Any]]:
        # Mirror of _manage_long: a short stop sits ABOVE entry, so it gaps
        # through when the bar opens at-or-above it; profit grows as price falls.
        if pos.stop_loss > 0 and open_p >= pos.stop_loss:
            exit_p = open_p
            events.append(
                {"event_type": "stop_loss_triggered", "price": exit_p, "stop_loss": pos.stop_loss}
            )
            trigger_type = "TRAILING_STOP" if pos.stop_loss <= pos.entry_price else "STOP_LOSS"
            self._close(exit_p, bar_time, trigger_type)
            events.append({"event_type": "position_closed", "exit": exit_p})
            return events

        # Strategy-driven cover (close_short uses action BUY).
        if signal.action == "BUY":
            trigger_type = "SIGNAL"
            if "Stop loss confirmed" in str(signal.reason):
                trigger_type = "TRAILING_STOP" if pos.stop_loss <= pos.entry_price else "STOP_LOSS"
            self._close(open_p, bar_time, trigger_type)
            events.append({"event_type": "position_closed", "exit": open_p})
            if trigger_type == "SIGNAL":
                self._open_reverse_from_signal(
                    events,
                    open_p=open_p,
                    atr=atr,
                    bar_time=bar_time,
                    signal=signal,
                )
            return events

        # Fixed take-profit sits BELOW entry for a short.
        if self.fixed_tp_pct > 0 and pos.entry_price > 0:
            tp_price = pos.entry_price * (1.0 - self.fixed_tp_pct / 100.0)
            if low <= tp_price:
                events.append({"event_type": "take_profit_triggered", "price": tp_price})
                self._close(tp_price, bar_time, "FIXED_TP")
                events.append({"event_type": "position_closed", "exit": tp_price})
                return events

        # Minimal ROI ladder for a short: profit target sits BELOW entry.
        if self.minimal_roi and pos.entry_price > 0:
            age_minutes = max(0.0, (bar_time - pos.entry_time) / 60.0)
            roi_pct = minimal_roi_pct(self.minimal_roi, age_minutes)
            if roi_pct > 0:
                roi_price = pos.entry_price * (1.0 - roi_pct / 100.0)
                exit_p = open_p if open_p <= roi_price else (roi_price if low <= roi_price else 0.0)
                if exit_p > 0:
                    events.append({"event_type": "minimal_roi_triggered",
                                   "price": exit_p, "roi_pct": roi_pct,
                                   "age_minutes": age_minutes})
                    self._close(exit_p, bar_time, "MINIMAL_ROI")
                    events.append({"event_type": "position_closed", "exit": exit_p})
                    return events

        # One-shot partial TP for a short: target sits BELOW entry.
        if self._partial_enabled and not pos.partial_taken and pos.entry_price > 0:
            target = pos.entry_price * (1.0 - self.partial_tp_pct / 100.0)
            basis = open_p if open_p <= target else (target if low <= target else 0.0)
            if basis > 0:
                self._take_partial(pos, basis, bar_time, events)

        pos.lowest_price = min(pos.lowest_price or pos.entry_price, low)
        if self.disable_stop_loss:
            return events
        candidate_sl = next_trailing_stop(
            side="short",
            entry_price=pos.entry_price,
            extreme_price=pos.lowest_price,
            current_sl=pos.stop_loss,
            atr=atr,
            trailing_atr_mult=self.trailing_atr_mult,
            trail_distance=getattr(signal, "trail_distance", None),
            breakeven_enabled=self.be_enabled,
            breakeven_activation_atr_mult=self.be_activation_atr_mult,
            breakeven_buffer_atr_mult=self.be_buffer_atr_mult,
        )
        if 0 < candidate_sl < pos.stop_loss:
            old = pos.stop_loss
            pos.stop_loss = candidate_sl
            events.append(
                {"event_type": "stop_loss_updated", "old_sl": old,
                 "new_sl": candidate_sl, "trough": pos.lowest_price}
            )
        return events


class ReplayEngine:
    """Deterministic bar-by-bar replay using the real strategy plugin."""

    def __init__(
        self,
        runner: StrategyRunner,
        simulator: PositionSimulator,
        symbol: str = "XAUTUSDT",
        timeframe_primary: str = "4h",
        timeframe_regime: Optional[str] = None,
        strategy_config: Optional[Dict[str, Any]] = None,
        engine_config: Optional[Dict[str, Any]] = None,
    ):
        self.runner = runner
        self.simulator = simulator
        self.symbol = symbol
        self.timeframe_primary = timeframe_primary
        self.timeframe_regime = timeframe_regime
        self.strategy_config = strategy_config or {}
        self.engine_config = engine_config or {}

    def replay_bars(
        self,
        df: pd.DataFrame,
        *,
        df_regime: Optional[pd.DataFrame] = None,
        min_bars: int = 100,
        zone_col: Optional[str] = None,
        atr_col: str = "atr",
    ) -> Tuple[List[SimTrade], List[Dict[str, Any]], float, List[Dict[str, Any]]]:
        all_events: List[Dict[str, Any]] = []
        last_checklist: List[Dict[str, Any]] = []
        use_d1 = bool(self.strategy_config.get("use_d1_regime_filter", False))
        regime_tf = self.timeframe_regime if use_d1 else None
        regime_df = df_regime if use_d1 else None
        # Bars sliced via end_index are all closed; mirror the live engine's
        # strategy.use_closed_candles resolution so live == backtest.
        from xauby.runtime.candle_utils import use_closed_candles

        last_bar_is_forming = not use_closed_candles(self.engine_config)

        # Regime filter: gate BUY signals by market regime when enabled.
        # Controlled by strategy_config["use_regime_filter"] (default False).
        use_regime_filter = bool(self.strategy_config.get("use_regime_filter", False))
        target_regimes: Optional[set] = None
        if use_regime_filter:
            import sys as _sys
            _strat_mod = _sys.modules.get(type(self.runner.strategy).__module__)
            target_regimes = getattr(_strat_mod, "TARGET_REGIMES", None)
            if not target_regimes:
                target_regimes = set(self.strategy_config.get("target_regimes") or [])
            if not target_regimes:
                use_regime_filter = False  # no regimes configured — filter is a no-op
        current_regime = "UNKNOWN"
        _regime_update_every = max(1, int(self.strategy_config.get("regime_update_bars", 24)))
        _regime_window = 200  # bars fed to classifier

        for i in range(min_bars, len(df)):
            row = df.iloc[i]
            open_p = float(row["open"])

            # NOTE: the breach counter is advanced once per bar at the *bottom*
            # of the loop using the bar extreme (low for longs). Do NOT also
            # update it here with the open price — the open is usually back above
            # the stop, so it would reset the consecutive-breach count every bar
            # and ``sl_confirmed`` (needs sl_confirm_ticks bars) could never be
            # reached. By the time we build ctx for bar i, the counter already
            # reflects the closed bars up to i-1, which is exactly what the
            # strategy is allowed to see.
            _pos = self.simulator.position
            position_side = ("SHORT" if _pos.side < 0 else "LONG") if _pos else None
            ctx = ContextBuilder.build(
                symbol=self.symbol,
                timeframe_primary=self.timeframe_primary,
                timeframe_regime=regime_tf,
                df_primary=df,
                df_regime=regime_df,
                current_price=open_p,
                has_position=self.simulator.has_position,
                position_side=position_side,
                stop_loss=(
                    self.simulator.position.stop_loss
                    if self.simulator.has_position
                    else 0.0
                ),
                sl_confirmed=self.simulator.sl_confirmed,
                config=self.strategy_config,
                engine_config=self.engine_config,
                extras={
                    "use_d1_regime_filter": use_d1,
                    "last_bar_is_forming": last_bar_is_forming,
                },
                end_index=i - 1,
            )
            signal = self.runner.run(ctx)

            # Re-classify market regime periodically and gate BUY signals.
            if use_regime_filter and target_regimes and (i - min_bars) % _regime_update_every == 0:
                try:
                    from xauby.regime.classifier import classify_market
                    _start = max(0, i - _regime_window)
                    _price_rows = (
                        df.iloc[_start:i][["open", "high", "low", "close", "volume"]]
                        .to_dict("records")
                    )
                    if len(_price_rows) >= 50:
                        _result = classify_market(_price_rows, timeframe=self.timeframe_primary)
                        current_regime = _result.regime
                except Exception:
                    pass

            if (
                use_regime_filter
                and target_regimes
                and signal.action == "BUY"
                and not self.simulator.has_position
                and current_regime not in target_regimes
            ):
                signal = _hold_signal(
                    f"Regime filter: {current_regime}",
                    volatility=signal.volatility,
                    indicators=signal.indicators,
                    checklist=signal.checklist,
                    strategy_name=signal.strategy_name,
                    timeframe=signal.timeframe,
                )

            last_checklist = list(signal.checklist or [])
            atr = float(signal.volatility or 0.0)
            bar_events = self.simulator.on_bar(
                open_p=open_p,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                atr=atr,
                zone=str(row.get(zone_col, "UNKNOWN")) if zone_col else "UNKNOWN",
                bar_time=int(row.get("timestamp", i)),
                signal=signal,
            )
            all_events.extend(bar_events)

            if self.simulator.has_position:
                # Breach side: longs are stopped by the low, shorts by the high.
                breach_px = (
                    float(row["high"])
                    if self.simulator.position.side < 0
                    else float(row["low"])
                )
                self.simulator.update_sl_breach(breach_px)
                self.simulator.bars_in_position += 1
                self.simulator.accrue_funding()

            # Record mark-to-close equity every bar for accurate drawdown
            self.simulator._equity_curve.append(
                self.simulator.current_equity(float(row["close"]))
            )
            position = self.simulator.position
            self.simulator._position_side_curve.append(
                "SHORT" if position and position.side < 0
                else "LONG" if position
                else None
            )

        if self.simulator.has_position and len(df) > 0:
            last = df.iloc[-1]
            self.simulator._close(
                float(last["close"]),
                int(last.get("timestamp", 0)),
                "END_OF_DATA",
            )

        return self.simulator.trades, all_events, self.simulator.balance, last_checklist

    @staticmethod
    def _max_drawdown_pct(
        trades: List[SimTrade],
        initial: float,
        equity_curve: Optional[List[float]] = None,
    ) -> float:
        """Compute max drawdown. Uses per-bar equity_curve when available (more accurate)."""
        if equity_curve:
            peak = initial
            max_dd = 0.0
            for eq in equity_curve:
                if eq > peak:
                    peak = eq
                dd = ((peak - eq) / peak) * 100.0 if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
            return max_dd
        # Fallback: trade-close equity only (understates intra-trade drawdowns)
        equity = initial
        peak = initial
        max_dd = 0.0
        for t in trades:
            equity += t.pnl
            if equity > peak:
                peak = equity
            dd = ((peak - equity) / peak) * 100.0 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def stats(
        trades: List[SimTrade],
        initial: float,
        final: float,
        equity_curve: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        n = len(trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = n - wins
        gp = sum(t.pnl for t in trades if t.pnl > 0)
        gl = abs(sum(t.pnl for t in trades if t.pnl < 0))
        return {
            "net_profit_pct": (final - initial) / initial * 100 if initial else 0.0,
            "win_rate": (wins / n * 100) if n else 0.0,
            "total_trades": n,
            "profit_factor": (gp / gl) if gl > 0 else (99.9 if gp > 0 else 0.0),
            "max_drawdown_pct": ReplayEngine._max_drawdown_pct(trades, initial, equity_curve),
            "avg_win": (gp / wins) if wins > 0 else 0.0,
            "avg_loss": (gl / losses) if losses > 0 else 0.0,
        }


def replay_incident(
    store,
    run_id: str,
    *,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return chronological events for a run_id (incident debugging)."""
    return store.iter_run(run_id, limit=limit)
