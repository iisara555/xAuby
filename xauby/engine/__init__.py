"""Engine package with a lazy public engine import.

Keeping this lazy lets broker/domain tools run without loading the full pandas,
YAML, notification and exchange runtime.
"""

__all__ = ["LiteTradingEngine"]


def __getattr__(name):
    if name == "LiteTradingEngine":
        from xauby.engine.trading import LiteTradingEngine

        return LiteTradingEngine
    raise AttributeError(name)
