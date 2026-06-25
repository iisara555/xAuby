"""3-candle regime confirmation debounce per symbol."""
from __future__ import annotations

from dataclasses import dataclass

from xauby.engine.symbol_context import SymbolContext


@dataclass
class DebounceResult:
    confirmed: bool
    candles_seen: int
    pending_regime: str
    previous_regime: str


class RegimeDebouncer:
    """Confirm regime switch only after consecutive candles on pending regime."""

    def __init__(self, threshold: int = 3):
        self.threshold = max(1, int(threshold))

    def update(
        self,
        sc: SymbolContext,
        new_regime: str,
        *,
        immediate: bool = False,
    ) -> DebounceResult:
        prev = str(getattr(sc, "confirmed_regime", "") or "")
        pending = str(getattr(sc, "pending_regime", "") or "")
        count = int(getattr(sc, "regime_debounce_count", 0) or 0)

        if not new_regime:
            return DebounceResult(False, count, pending, prev)

        if new_regime == prev:
            sc.pending_regime = ""
            sc.regime_debounce_count = 0
            return DebounceResult(False, 0, "", prev)

        # Skip the multi-candle wait when requested (e.g. no open position):
        # confirm the new regime on the first differing candle. There is no live
        # trade to protect, so adapting immediately avoids missing the move.
        if immediate:
            sc.confirmed_regime = new_regime
            sc.pending_regime = ""
            sc.regime_debounce_count = 0
            return DebounceResult(True, 1, new_regime, prev)

        if new_regime == pending:
            count += 1
        else:
            pending = new_regime
            count = 1

        sc.pending_regime = pending
        sc.regime_debounce_count = count

        if count >= self.threshold:
            sc.confirmed_regime = pending
            sc.pending_regime = ""
            sc.regime_debounce_count = 0
            return DebounceResult(True, self.threshold, pending, prev)

        return DebounceResult(False, count, pending, prev)
