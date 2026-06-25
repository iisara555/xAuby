"""Market context handed to a Strategy on every evaluation.

Strategies receive everything they need to make a decision from this object
and must NOT reach back into the engine. This keeps coupling one-way:
    Engine ──► MarketContext ──► Strategy ──► Signal ──► Engine
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd


@dataclass
class MarketContext:
    """Read-only snapshot of market state for the current tick.

    Attributes:
        symbol:            Trading symbol, e.g. "XAUTUSDT".
        timeframe_primary: Primary candle timeframe, e.g. "4h".
        timeframe_regime:  Optional higher timeframe, e.g. "1d".
        df_primary:        Candles for the primary timeframe (OHLCV).
        df_regime:         Candles for the regime timeframe (may be None).
        current_price:     Latest tick / ticker price.
        has_position:      True if engine currently holds the asset.
        position_side:     "LONG" or "SHORT" when a position is open, else None.
                           Lets a stop-and-reverse strategy decide whether an
                           exit means "close long" or "close short".
        stop_loss:         Active SL price (0.0 if none).
        sl_confirmed:      Engine has already confirmed an SL breach across
                           multiple ticks (used by strategy to decide SELL).
        config:            Strategy-specific config block (whatever the
                           strategy author placed under
                           ``strategies.<name>`` in bot_config.yaml).
        engine_config:     Full engine config (read-only); strategies should
                           prefer ``config`` and use this only for cross-
                           cutting flags.
        extras:            Free-form bag of extras supplied by the engine
                           (e.g. macro guard score). Strategies may ignore.
    """

    symbol: str
    timeframe_primary: str
    df_primary: pd.DataFrame
    current_price: float
    has_position: bool = False
    position_side: Optional[str] = None
    stop_loss: float = 0.0
    sl_confirmed: bool = False
    timeframe_regime: Optional[str] = None
    df_regime: Optional[pd.DataFrame] = None
    config: Dict[str, Any] = field(default_factory=dict)
    engine_config: Dict[str, Any] = field(default_factory=dict)
    extras: Dict[str, Any] = field(default_factory=dict)
