"""Regime-driven strategy routing (separate from classifier)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from xauby.engine.regime_debounce import RegimeDebouncer
from xauby.engine.symbol_context import SymbolContext
from xauby.regime.models import GoldRegime
from xauby.runtime.architecture_config import (
    regime_confidence_filter,
    regime_confidence_threshold,
    regime_router_mapping,
)
from xauby.runtime.pair_registry import PairSpec

logger = logging.getLogger("xauby.engine.regime_router")

RECOVERY_CANDLES = 3
FORCE_CLOSE_CANDLES = 6


@dataclass
class RouteResult:
    symbol: str
    regime: str
    strategy_name: Optional[str]
    previous_strategy: str
    strategy_changed: bool = False
    no_trade: bool = False
    no_trade_state: str = "ACTIVE"
    handoff: bool = False
    trailing_atr_mult: float = 2.0
    message: str = ""
    log_history: bool = False
    old_regime: str = ""
    candles_confirmed: int = 0
    confidence: float = 0.0
    force_close: bool = False
    fields: Dict[str, Any] = field(default_factory=dict)


class RegimeRouter:
    """Map confirmed regimes to strategies with NO_TRADE and handoff rules."""

    NO_TRADE_STATES = frozenset({"NO_TRADE_PENDING", "NO_TRADE", "HANDOFF"})

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        debouncer: Optional[RegimeDebouncer] = None,
        history_count_fn: Optional[Callable[[], int]] = None,
    ):
        self.config = config
        self.mapping = regime_router_mapping(config)
        block = config.get("regime_router") or {}
        self.recovery_candles = max(1, int(block.get("recovery_candles", RECOVERY_CANDLES)))
        self.force_close_candles = int(block.get("force_close_candles", FORCE_CLOSE_CANDLES))
        # When flat (no open position) there is no trade to protect, so the
        # debounce / recovery wait can be skipped and the regime switch applied
        # on the first confirming candle. Tunable; defaults to enabled.
        self.instant_switch_when_flat = bool(block.get("instant_switch_when_flat", True))
        self.debouncer = debouncer or RegimeDebouncer(
            threshold=int(block.get("debounce_candles", 3))
        )
        self._history_count_fn = history_count_fn or (lambda: 0)
        self._confidence_filter = regime_confidence_filter(config)
        self._confidence_threshold = regime_confidence_threshold(config)
        self.validate_mapping()

    def validate_mapping(self) -> list[str]:
        """Warn if a mapped regime target is not a registered strategy.

        The spec table uses display aliases (e.g. ``supertrend`` and
        ``BBRSIMeanReversion``); the canonical registered plugin ids are
        ``supertrend_ema200`` and ``bbrsi_mean_reversion``. Targets must
        resolve to a real strategy plugin or ``None`` (NO_TRADE).
        """
        from xauby.strategies.registry import available_strategies

        known = set(available_strategies())
        unknown: list[str] = []
        for regime_label, target in self.mapping.items():
            if target in (None, "None", ""):
                continue
            if str(target) not in known:
                unknown.append(f"{regime_label}->{target}")
        if unknown:
            logger.warning(
                "RegimeRouter mapping has unknown strategy targets: %s (known: %s)",
                unknown,
                sorted(known),
            )
        return unknown

    def _target_strategy(self, regime_label: str) -> Optional[str]:
        val = self.mapping.get(regime_label)
        if val in (None, "None", ""):
            return None
        return str(val)

    def _confidence_ok(self, confidence: float) -> bool:
        if not self._confidence_filter:
            return True
        if self._history_count_fn() < 30:
            return True
        return float(confidence) >= self._confidence_threshold

    def _handle_no_trade_recovery(
        self,
        sc: SymbolContext,
        regime_label: str,
        target: Optional[str],
        has_open_position: bool,
        result: RouteResult,
        *,
        recovery_candles: Optional[int] = None,
    ) -> Optional[RouteResult]:
        """Recovery from NO_TRADE when regime becomes tradeable again."""
        recovery_threshold = self.recovery_candles if recovery_candles is None else recovery_candles
        if sc.no_trade_state not in ("NO_TRADE", "NO_TRADE_PENDING"):
            return None
        if target is None:
            sc.no_trade_candle_count += 1
            # Do NOT reset recovery_count here — regime may oscillate back to
            # tradeable next candle, and wiping the counter causes infinite retry.
            if sc.no_trade_candle_count > self.force_close_candles and has_open_position:
                result.force_close = True
                result.message = (
                    f"NO_TRADE force close after {sc.no_trade_candle_count} candles ({regime_label})"
                )
            return result

        sc.no_trade_recovery_count += 1
        if sc.no_trade_recovery_count < recovery_threshold:
            result.no_trade = True
            result.no_trade_state = sc.no_trade_state
            result.message = (
                f"NO_TRADE recovery pending ({sc.no_trade_recovery_count}/{recovery_threshold})"
            )
            return result

        sc.no_trade_state = "ACTIVE"
        sc.no_trade_candle_count = 0
        sc.no_trade_recovery_count = 0
        sc.trailing_atr_mult = 2.0
        result.no_trade_state = "ACTIVE"
        result.message = f"NO_TRADE recovered → ACTIVE ({regime_label})"
        return None

    def evaluate(
        self,
        symbol: str,
        regime: GoldRegime,
        sc: SymbolContext,
        spec: PairSpec,
        *,
        has_open_position: bool,
    ) -> RouteResult:
        sym = symbol.upper().replace("_", "")
        regime_label = str(regime.regime or "UNKNOWN")
        prev_strategy = sc.strategy_name or spec.strategy_name
        prev_regime = str(getattr(sc, "confirmed_regime", "") or regime_label)
        # No open position → nothing to protect, so confirm the regime switch on
        # the first differing candle instead of waiting out the debounce.
        flat_fast = self.instant_switch_when_flat and not has_open_position
        deb = self.debouncer.update(sc, regime_label, immediate=flat_fast)

        result = RouteResult(
            symbol=sym,
            regime=regime_label,
            strategy_name=prev_strategy,
            previous_strategy=prev_strategy,
            old_regime=prev_regime if deb.confirmed else prev_regime,
            confidence=float(regime.confidence or 0.0),
        )

        if not deb.confirmed:
            result.message = f"Regime pending {deb.pending_regime} ({deb.candles_seen}/{self.debouncer.threshold})"
            return result

        target = self._target_strategy(regime_label)

        # Confidence gate: only suppress switching INTO a different *tradeable*
        # strategy on a low-confidence read. NO_TRADE / defensive transitions
        # (target is None) must never be blocked — protection always wins, even
        # when the panic/bear read carries low confidence.
        if (
            target is not None
            and target != prev_strategy
            and not self._confidence_ok(result.confidence)
        ):
            result.message = (
                f"Regime switch blocked: confidence {result.confidence:.2f} "
                f"< {self._confidence_threshold:.2f}"
            )
            sc.confirmed_regime = prev_regime or regime_label
            return result

        result.candles_confirmed = deb.candles_seen
        result.old_regime = deb.previous_regime or prev_regime
        result.log_history = True

        recovery_threshold = 1 if flat_fast else self.recovery_candles
        recovery = self._handle_no_trade_recovery(
            sc, regime_label, target, has_open_position, result,
            recovery_candles=recovery_threshold,
        )
        if recovery is not None and sc.no_trade_state in ("NO_TRADE", "NO_TRADE_PENDING"):
            if target is None:
                sc.confirmed_regime = regime_label
                return recovery
            if sc.no_trade_recovery_count < recovery_threshold:
                sc.confirmed_regime = regime_label
                return recovery

        if target is None:
            result.no_trade = True
            result.strategy_name = None
            state = str(getattr(sc, "no_trade_state", "ACTIVE") or "ACTIVE")
            if state == "ACTIVE":
                sc.no_trade_state = "NO_TRADE_PENDING"
                sc.no_trade_candle_count = 0
                sc.no_trade_recovery_count = 0
                result.no_trade_state = "NO_TRADE_PENDING"
                result.trailing_atr_mult = 1.0
                result.message = f"NO_TRADE pending for {regime_label}"
            elif state == "NO_TRADE_PENDING":
                sc.no_trade_state = "NO_TRADE"
                result.no_trade_state = "NO_TRADE"
                result.trailing_atr_mult = 1.0
                result.message = f"NO_TRADE active ({regime_label})"
            else:
                result.no_trade_state = state
                result.trailing_atr_mult = 1.0 if state in ("NO_TRADE", "HANDOFF") else 2.0
            sc.confirmed_regime = regime_label
            return result

        if target != prev_strategy:
            if has_open_position:
                already_handoff = (
                    sc.no_trade_state == "HANDOFF"
                    and sc.handoff_from_strategy == prev_strategy
                    and sc.handoff_to_strategy == target
                )
                sc.no_trade_state = "HANDOFF"
                sc.no_trade_candle_count = 0
                sc.no_trade_recovery_count = 0
                sc.handoff_from_strategy = prev_strategy
                sc.handoff_to_strategy = target
                result.handoff = not already_handoff
                result.no_trade_state = "HANDOFF"
                result.trailing_atr_mult = 1.0
                result.strategy_name = prev_strategy
                result.fields["new_strategy_for_entries"] = target
                result.message = f"Handoff: keep {prev_strategy} until position closes, then switch to {target}"
            else:
                sc.no_trade_state = "ACTIVE"
                sc.no_trade_candle_count = 0
                sc.no_trade_recovery_count = 0
                sc.handoff_from_strategy = ""
                sc.handoff_to_strategy = ""
                result.no_trade_state = "ACTIVE"
                result.trailing_atr_mult = 2.0
                result.strategy_name = target
                result.strategy_changed = True
                sc.strategy_name = target
                result.message = f"Strategy switched to {target} ({regime_label})"
        else:
            sc.no_trade_state = "ACTIVE"
            sc.no_trade_candle_count = 0
            sc.no_trade_recovery_count = 0
            sc.handoff_from_strategy = ""
            sc.handoff_to_strategy = ""
            result.no_trade_state = "ACTIVE"
            result.trailing_atr_mult = 2.0
            result.strategy_name = prev_strategy

        sc.confirmed_regime = regime_label
        return result

    @staticmethod
    def live_gate_blocked(spec: PairSpec) -> bool:
        """True when a live regime-router symbol is being forced into sim mode."""
        return bool(
            spec.regime_router_enabled
            and (spec.execution_mode or "").lower() == "live"
            and not spec.regime_router_live_confirmed
        )

    @staticmethod
    def apply_live_confirmation(spec: PairSpec) -> PairSpec:
        """Force sim when regime router live not confirmed."""
        if not spec.regime_router_enabled:
            return spec
        mode = (spec.execution_mode or "").lower()
        if mode == "live" and not spec.regime_router_live_confirmed:
            logger.warning(
                "%s: regime_router live not confirmed — forcing mode=sim",
                spec.symbol,
            )
            return PairSpec(
                symbol=spec.symbol,
                enabled=spec.enabled,
                strategy_name=spec.strategy_name,
                primary_timeframe=spec.primary_timeframe,
                confirm_timeframe=spec.confirm_timeframe,
                use_d1_regime_filter=spec.use_d1_regime_filter,
                degraded=True,
                degrade_reason=spec.degrade_reason or "regime_router_live_unconfirmed",
                execution_mode="sim",
                regime_router_enabled=spec.regime_router_enabled,
                regime_router_live_confirmed=False,
                sim_fee_pct=spec.sim_fee_pct,
            )
        return spec

    @staticmethod
    def live_banner_message(spec: PairSpec) -> str:
        if (
            spec.regime_router_enabled
            and (spec.execution_mode or "").lower() == "live"
            and spec.regime_router_live_confirmed
        ):
            return f"[!] RegimeRouter LIVE active on: {spec.symbol} — monitor closely"
        return ""

    @staticmethod
    def live_gate_warning(spec: PairSpec) -> str:
        if (
            spec.regime_router_enabled
            and (spec.execution_mode or "").lower() == "live"
            and not spec.regime_router_live_confirmed
        ):
            return (
                f"[!] {spec.symbol}: RegimeRouter live not confirmed — running in SIM mode"
            )
        return ""
