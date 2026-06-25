"""Strategy plugin framework.

Public surface:
    - :class:`Strategy`        – base class every plugin must inherit
    - :class:`StrategyMeta`    – rich plugin metadata
    - :class:`Signal`          – standard return type
    - :class:`MarketContext`   – input handed to ``Strategy.analyze``
    - :func:`register`         – decorator to register a plugin id
    - :func:`load_strategy`    – factory used by the engine
    - :func:`available_strategies` – list every discovered plugin
"""
from xauby.strategies.base import Strategy, StrategyMeta
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import (
    available_strategy_manifests,
    available_strategies,
    load_strategy,
    register,
    strategy_manifest,
)
from xauby.strategies.sandbox import StrategyRunner, StrategySandboxError
from xauby.strategies.signal import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_SELL,
    Signal,
    buy,
    hold,
    sell,
    open_short,
    close_short,
)

__all__ = [
    "Strategy",
    "StrategyMeta",
    "StrategyRunner",
    "StrategySandboxError",
    "Signal",
    "MarketContext",
    "register",
    "load_strategy",
    "available_strategies",
    "strategy_manifest",
    "available_strategy_manifests",
    "ACTION_BUY",
    "ACTION_SELL",
    "ACTION_HOLD",
    "buy",
    "sell",
    "hold",
    "open_short",
    "close_short",
]
