"""Research-only Backtrader candidates; deliberately separate from live plugins.

Modes: 0 = Donchian trend, 1 = EMA/session-VWAP pullback, 2 = BB/RSI range.
All decisions use closed bars; market orders execute on the next bar open.
ATR stops/trailing stops are CLOSE-BASED signals, not intrabar exchange stops.
"""
import math

import backtrader as bt


class IntradayCandidate(bt.Strategy):
    params = (
        ("mode", 0),
        ("risk_fraction", 0.005),
        ("max_exposure", 0.25),
        ("atr_multiple", 2.0),
        ("trail_multiple", 4.0),
        ("channel_period", 48),
        ("max_hold_bars", 96),
        ("quantity_step", 0.001),
        ("trade_start", 20260312),
        ("trade_end", 20260908),
    )

    def __init__(self):
        self.fast = bt.ind.EMA(self.data.close, period=20)
        self.slow = bt.ind.EMA(self.data.close, period=50)
        self.trend = bt.ind.EMA(self.data.close, period=200)
        self.atr = bt.ind.ATR(self.data, period=14)
        self.bb = bt.ind.BollingerBands(self.data.close, period=20, devfactor=2.0)
        self.rsi = bt.ind.RSI(self.data.close, period=14, safediv=True)
        self.upper = bt.ind.Highest(self.data.high(-1), period=self.p.channel_period)
        self.lower = bt.ind.Lowest(self.data.low(-1), period=self.p.channel_period)
        self.change = abs(self.data.close - self.data.close(-1))
        self.path = bt.ind.SumN(self.change, period=20)
        self.pending = None
        self.entry_atr = 0.0
        self.entry_bar = 0
        self.extreme = 0.0
        self.day = None
        self.pv = 0.0
        self.volume = 0.0
        self.vwap = 0.0
        self.exit_bar = -1000

    def _session(self):
        day = self.data.datetime.date(0)
        if day != self.day:
            self.day, self.pv, self.volume = day, 0.0, 0.0
        volume = max(0.0, float(self.data.volume[0]))
        typical = (self.data.high[0] + self.data.low[0] + self.data.close[0]) / 3.0
        self.pv += typical * volume
        self.volume += volume
        self.vwap = self.pv / self.volume if self.volume else float(self.data.close[0])

    def prenext(self):
        self._session()

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted, order.Partial):
            return
        if order.status == order.Completed:
            if self.position:
                self.entry_bar = len(self)
                self.extreme = float(order.executed.price)
            else:
                self.exit_bar = len(self)
        self.pending = None

    def next(self):
        self._session()
        if self.pending:
            return
        close = float(self.data.close[0])
        atr = float(self.atr[0])
        if not math.isfinite(atr) or atr <= 0.0 or close <= 0.0:
            return
        now = self.data.datetime.datetime(0)
        date_key = now.year * 10000 + now.month * 100 + now.day
        # No entries during the final two days. This leaves execution time for
        # a market close even across short exchange data gaps.
        deadline = date_key >= int(self.p.trade_end)
        if self.position:
            side = 1 if self.position.size > 0 else -1
            self.extreme = max(self.extreme, close) if side > 0 else min(self.extreme, close)
            adverse = side * (close - self.position.price)
            stop = adverse <= -self.p.atr_multiple * self.entry_atr
            trailing = side * (close - self.extreme) <= -self.p.trail_multiple * atr
            timed = len(self) - self.entry_bar >= self.p.max_hold_bars
            if int(self.p.mode) == 2:
                signal_exit = side * (close - self.bb.mid[0]) >= 0
            else:
                signal_exit = side * (close - self.slow[0]) < 0
            if stop or trailing or timed or signal_exit or deadline:
                self.pending = self.close()
            return
        if date_key < int(self.p.trade_start) or deadline or len(self) - self.exit_bar < 4:
            return
        # Do not infer activity from exchange-carried zero-volume candles.
        if float(self.data.volume[0]) <= 0.0:
            return
        path = float(self.path[0])
        efficiency = abs(close - float(self.data.close[-20])) / path if path > 0 else 0.0
        bullish = close > self.trend[0] and self.fast[0] > self.slow[0]
        bearish = close < self.trend[0] and self.fast[0] < self.slow[0]
        side = 0
        mode = int(self.p.mode)
        if mode == 0 and efficiency >= 0.30:
            if bullish and close > self.upper[0]:
                side = 1
            elif bearish and close < self.lower[0]:
                side = -1
        elif mode == 1 and efficiency >= 0.25:
            if bullish and close > self.vwap and self.data.close[-1] <= self.fast[-1] and close > self.fast[0]:
                side = 1
            elif bearish and close < self.vwap and self.data.close[-1] >= self.fast[-1] and close < self.fast[0]:
                side = -1
        elif mode == 2 and efficiency <= 0.25:
            # Require re-entry into the band after an oversold/overbought bar;
            # a touch alone is not a reversal signal.
            if self.data.close[-1] < self.bb.bot[-1] and self.rsi[-1] < 35 and close > self.bb.bot[0]:
                side = 1
            elif self.data.close[-1] > self.bb.top[-1] and self.rsi[-1] > 65 and close < self.bb.top[0]:
                side = -1
        if not side:
            return
        equity = float(self.broker.getvalue())
        size = min(equity * self.p.risk_fraction / (self.p.atr_multiple * atr), equity * self.p.max_exposure / close)
        size = math.floor(size / self.p.quantity_step) * self.p.quantity_step
        if size <= 0.0:
            return
        self.entry_atr = atr
        self.pending = self.buy(size=size) if side > 0 else self.sell(size=size)
