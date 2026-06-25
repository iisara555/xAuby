"""Candle-boundary gate for the regime router.

The engine ticks every ``trading.interval_seconds`` (default 60s) but trades on
higher timeframes (e.g. BTC on 1h). The RegimeRouter / RegimeDebouncer were
advanced on every tick, so ``debounce_candles: 3`` confirmed after ~3 *ticks*
(~3 minutes) instead of 3 *candles* — the multi-candle persistence filter was
effectively bypassed and strategy switches fired far more often than intended.

This gate restricts router/debounce advancement to once per *closed* candle, so
the configured candle counts (debounce, recovery, force-close) mean candles
again. Classification itself still runs every tick to keep the dashboard live.
"""
from __future__ import annotations


def should_route_on_candle(last_candle_ts: int, last_routed_ts: int) -> bool:
    """True only when a new closed candle has appeared since the last routing.

    ``last_candle_ts`` is the open timestamp of the latest closed candle the tick
    is working on; ``last_routed_ts`` is the timestamp the router last advanced
    on. A zero/unknown candle ts never routes (insufficient data).
    """
    current = int(last_candle_ts or 0)
    if current <= 0:
        return False
    return current != int(last_routed_ts or 0)
