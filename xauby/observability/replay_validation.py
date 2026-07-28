"""Replay validation — re-run strategy at logged ticks and diff vs live events."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

from xauby.observability.incidents import load_run_timeline
from xauby.observability.event_coverage import audit_event_coverage, EventCoverageReport
from xauby.observability.replay import ContextBuilder
from xauby.runtime.candle_utils import drop_forming_bar, use_closed_candles
from xauby.runtime.trading_config import resolve_trading_config, strategy_name_for_symbol
from xauby.strategies import load_strategy
from xauby.strategies.sandbox import StrategyRunner


def _payload(ev: Dict[str, Any]) -> Dict[str, Any]:
    p = ev.get("payload")
    return p if isinstance(p, dict) else {}


def _event_type(ev: Dict[str, Any]) -> str:
    return str(ev.get("event_type") or ev.get("event") or "")


def parse_event_ts_unix(iso_ts: str) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        s = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def load_bot_config(path: str = "bot_config.yaml") -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def strategy_config_from_yaml(cfg: Dict[str, Any], strategy_name: str) -> Dict[str, Any]:
    block = (cfg.get("strategies") or {}).get(strategy_name) or {}
    strat = load_strategy(strategy_name, block)
    return dict(strat.config)


def candles_to_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df.set_index("datetime", inplace=True)
    return df.sort_index()


def slice_df_at_ts(df: pd.DataFrame, iso_ts: str) -> pd.DataFrame:
    """Keep candles whose open time is at or before the event timestamp."""
    if df.empty:
        return df
    ts_unix = parse_event_ts_unix(iso_ts)
    if ts_unix is None:
        return df
    cutoff = pd.Timestamp.fromtimestamp(ts_unix, tz="UTC")
    return df[df.index <= cutoff].copy()


@dataclass
class ReplayValidationState:
    has_position: bool = False
    position_side: Optional[str] = None
    stop_loss: float = 0.0
    sl_breach_count: int = 0

    def apply(self, ev: Dict[str, Any], *, tick_price: Optional[float] = None) -> None:
        etype = _event_type(ev)
        pl = _payload(ev)
        if etype in {"position_opened", "position_restored"}:
            self.has_position = True
            # Default LONG for events written before position_side was emitted
            # on the long path; SHORT has always carried it explicitly.
            side = str(pl.get("position_side") or ev.get("position_side") or "LONG")
            self.position_side = side.upper()
            self.stop_loss = float(pl.get("stop_loss") or ev.get("stop_loss") or 0.0)
            self.sl_breach_count = 0
        elif etype == "position_closed":
            self.has_position = False
            self.position_side = None
            self.stop_loss = 0.0
            self.sl_breach_count = 0
        elif etype == "stop_loss_updated":
            self.stop_loss = float(pl.get("new_sl") or ev.get("new_sl") or self.stop_loss)
        elif etype == "tick" and tick_price is not None:
            # A short's stop sits ABOVE entry, so it is breached when price
            # rises. The single `<=` test counted a short's stop as breached
            # whenever price fell — the exact opposite — which silently
            # corrupted sl_confirmed for every short position in replay.
            if self.has_position and self.stop_loss > 0:
                breached = (
                    tick_price >= self.stop_loss
                    if self.position_side == "SHORT"
                    else tick_price <= self.stop_loss
                )
            else:
                breached = False
            self.sl_breach_count = self.sl_breach_count + 1 if breached else 0



def _describe(action: str, intent: str, side: str) -> str:
    """Render a decision so a short is distinguishable from a long in reports."""
    if intent and side:
        return f"{action}({intent}/{side})"
    if intent:
        return f"{action}({intent})"
    return action


@dataclass
class SignalMismatch:
    tick_id: str
    seq: int
    ts: str
    price: float
    live_action: str
    replay_action: str
    live_reason: str
    replay_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick_id": self.tick_id,
            "seq": self.seq,
            "ts": self.ts,
            "price": self.price,
            "live_action": self.live_action,
            "replay_action": self.replay_action,
            "live_reason": self.live_reason,
            "replay_reason": self.replay_reason,
        }


@dataclass
class ReplayValidationReport:
    run_id: str
    symbol: str
    strategy_name: str
    total_signals: int = 0
    matched: int = 0
    mismatched: int = 0
    skipped: int = 0
    short_signals_checked: int = 0
    match_rate_pct: float = 100.0
    mismatches: List[SignalMismatch] = field(default_factory=list)
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    coverage: Optional[EventCoverageReport] = None

    @property
    def ok(self) -> bool:
        coverage_ok = self.coverage.integrity_ok if self.coverage else True
        if self.mismatched > 0:
            return False
        checked = self.matched + self.mismatched
        if checked == 0:
            return coverage_ok
        return coverage_ok

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "symbol": self.symbol,
            "strategy_name": self.strategy_name,
            "total_signals": self.total_signals,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "skipped": self.skipped,
            "short_signals_checked": self.short_signals_checked,
            "short_side_checked": self.short_signals_checked > 0,
            "match_rate_pct": round(self.match_rate_pct, 2),
            "ok": self.ok,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "skip_reasons": dict(self.skip_reasons),
            "coverage": self.coverage.to_dict() if self.coverage else None,
        }


def _skip(report: ReplayValidationReport, reason: str) -> None:
    report.skipped += 1
    report.skip_reasons[reason] = report.skip_reasons.get(reason, 0) + 1


def validate_run(
    store,
    db,
    run_id: str,
    *,
    symbol: Optional[str] = None,
    config_path: str = "bot_config.yaml",
    strategy_name: Optional[str] = None,
    min_bars: int = 100,
    max_signals: int = 200,
) -> ReplayValidationReport:
    """Re-run the strategy plugin at each logged tick and compare signal_evaluated events."""
    events = load_run_timeline(store, run_id, limit=10_000, notable_only=False)
    coverage = audit_event_coverage(events, run_id=run_id)
    if not events:
        report = ReplayValidationReport(run_id=run_id, symbol="", strategy_name="")
        report.coverage = coverage
        return report

    resolved_symbol = symbol
    if not resolved_symbol:
        for ev in events:
            if ev.get("symbol"):
                resolved_symbol = ev.get("symbol")
                break
    if not resolved_symbol:
        raise ValueError(f"No symbol metadata found in events for run_id={run_id}, and no symbol was explicitly provided.")
    
    symbol = resolved_symbol
    cfg = load_bot_config(config_path)
    project_root = os.path.dirname(os.path.abspath(config_path))
    # Replay must resolve the same per-symbol strategy and whitelist overrides
    # as the live engine. Falling back to strategy.active here made a BTC
    # replay silently run the global/XAU strategy and could produce a false
    # PASS for a multi-pair run.
    strat_name = strategy_name or strategy_name_for_symbol(
        cfg,
        symbol,
        project_root=project_root,
    )
    effective = resolve_trading_config(
        cfg,
        strat_name,
        symbol=symbol,
        project_root=project_root,
        for_live=True,
    )
    strat_cfg = dict(effective.strategy)
    trading_cfg = cfg.get("trading") or {}
    sl_confirm_ticks = int(trading_cfg.get("sl_confirm_ticks", 3))
    tf_primary = str(effective.primary_timeframe or trading_cfg.get("timeframe") or "4h")
    tf_regime = str(
        effective.confirm_timeframe
        or (cfg.get("strategy_mode") or {}).get("trend_only", {}).get("confirm_timeframe")
        or "1d"
    )
    use_d1 = any(
        bool(strat_cfg.get(key, False))
        for key in (
            "use_d1_regime_filter",
            "use_d1_regime_filter_long",
            "use_d1_regime_filter_short",
        )
    )

    strategy = load_strategy(strat_name, strat_cfg)
    runner = StrategyRunner(strategy, timeout=30.0, check_imports=False)

    tick_by_id: Dict[tuple[str, str], Dict[str, Any]] = {}
    for ev in events:
        if _event_type(ev) == "tick" and ev.get("tick_id"):
            tick_symbol = str(ev.get("symbol") or "").upper().replace("_", "")
            tick_by_id[(str(ev["tick_id"]), tick_symbol)] = ev

    wanted_symbol = str(symbol or "").upper().replace("_", "")
    sig_events = [
        e for e in events
        if _event_type(e) == "signal_evaluated"
        and str(e.get("symbol") or "").upper().replace("_", "") == wanted_symbol
    ]
    if len(sig_events) > max_signals:
        sig_events = sig_events[-max_signals:]

    report = ReplayValidationReport(
        run_id=run_id,
        symbol=symbol,
        strategy_name=strat_name,
        total_signals=len(sig_events),
        coverage=coverage,
    )

    df_primary_by_symbol = {}
    df_regime_by_symbol = {}

    for sig in sig_events:
        tick_id = str(sig.get("tick_id") or "")
        sig_symbol = str(sig.get("symbol") or symbol).upper().replace("_", "")
        tick_ev = tick_by_id.get((tick_id, sig_symbol))
        if not tick_ev:
            _skip(report, "no_tick_pair")
            continue

        pl_tick = _payload(tick_ev)
        price = float(pl_tick.get("price") or tick_ev.get("price") or 0.0)
        if price <= 0:
            _skip(report, "bad_price")
            continue

        if sig_symbol not in df_primary_by_symbol:
            df_primary_by_symbol[sig_symbol] = candles_to_df(db.get_candles(sig_symbol, tf_primary, limit=300))
            if tf_regime and tf_regime != tf_primary:
                df_regime_by_symbol[sig_symbol] = candles_to_df(db.get_candles(sig_symbol, tf_regime, limit=300))
            else:
                df_regime_by_symbol[sig_symbol] = None

        df_primary = df_primary_by_symbol[sig_symbol]
        df_regime = df_regime_by_symbol[sig_symbol]

        if df_primary.empty:
            _skip(report, "no_candles")
            continue

        tick_seq = int(tick_ev.get("seq") or 0)
        rstate = ReplayValidationState()
        for ev in events:
            if int(ev.get("seq") or 0) >= tick_seq:
                break
            event_symbol = str(ev.get("symbol") or "").upper().replace("_", "")
            if event_symbol and event_symbol != sig_symbol:
                continue
            rstate.apply(ev)
        rstate.apply(tick_ev, tick_price=price)
        if rstate.has_position and rstate.position_side == "SHORT":
            report.short_signals_checked += 1

        sig_ts = sig.get("ts") or tick_ev.get("ts") or ""
        df_p = slice_df_at_ts(df_primary, sig_ts)
        df_r = slice_df_at_ts(df_regime, sig_ts) if df_regime is not None else None

        # Mirror the live engine: with strategy.use_closed_candles the bar
        # still forming at signal time never reaches the strategy.
        last_bar_is_forming = True
        if use_closed_candles(cfg):
            ts_unix = parse_event_ts_unix(sig_ts)
            df_p, _ = drop_forming_bar(df_p, tf_primary, now_ts=ts_unix)
            if df_r is not None:
                df_r, _ = drop_forming_bar(df_r, tf_regime, now_ts=ts_unix)
            last_bar_is_forming = False

        if len(df_p) < min_bars:
            _skip(report, "insufficient_bars")
            continue

        sl_confirmed = rstate.sl_breach_count >= sl_confirm_ticks
        ctx = ContextBuilder.build(
            symbol=sig_symbol,
            timeframe_primary=tf_primary,
            timeframe_regime=tf_regime if use_d1 else None,
            df_primary=df_p,
            df_regime=df_r if use_d1 else None,
            current_price=price,
            has_position=rstate.has_position,
            # Without this the strategy cannot tell a long from a short and
            # picks the wrong exit branch, so every short tick replayed as if a
            # long were open. Mirrors loop.py's ContextBuilder.build call.
            position_side=(rstate.position_side or "LONG") if rstate.has_position else None,
            stop_loss=rstate.stop_loss,
            sl_confirmed=sl_confirmed,
            config=strat_cfg,
            engine_config=cfg,
            extras={
                "use_d1_regime_filter": use_d1,
                "last_bar_is_forming": last_bar_is_forming,
            },
        )

        try:
            replay_sig = runner.run(ctx)
        except Exception as exc:
            _skip(report, f"strategy_error:{exc.__class__.__name__}")
            continue

        pl_sig = _payload(sig)
        live_action = str(pl_sig.get("action") or sig.get("action") or "HOLD").upper()
        replay_action = str(replay_sig.action or "HOLD").upper()
        live_reason = str(pl_sig.get("reason") or sig.get("reason") or "")
        replay_reason = str(replay_sig.reason or "")

        # Compare the SEMANTIC decision, not just the order verb. BUY/SELL are
        # ambiguous on the short side — open_short and a long exit are both
        # SELL, close_short and a long entry are both BUY — so an action-only
        # comparison scores "SELL == SELL" as a match even when live opened a
        # short and replay closed a long. Older events carry no intent /
        # position_side; those degrade to the action-only comparison rather
        # than reporting false mismatches for every pre-upgrade run.
        live_intent = str(pl_sig.get("intent") or sig.get("intent") or "").upper()
        live_side = str(pl_sig.get("position_side") or sig.get("position_side") or "").upper()
        replay_intent = str(getattr(replay_sig, "intent", "") or "").upper()
        replay_side = str(getattr(replay_sig, "position_side", "") or "").upper()

        matched = live_action == replay_action
        if matched and live_intent and replay_intent:
            matched = live_intent == replay_intent
        if matched and live_side and replay_side:
            matched = live_side == replay_side

        if matched:
            report.matched += 1
        else:
            report.mismatched += 1
            report.mismatches.append(
                SignalMismatch(
                    tick_id=tick_id,
                    seq=int(sig.get("seq") or 0),
                    ts=str(sig.get("ts") or ""),
                    price=price,
                    live_action=_describe(live_action, live_intent, live_side),
                    replay_action=_describe(replay_action, replay_intent, replay_side),
                    live_reason=live_reason[:120],
                    replay_reason=replay_reason[:120],
                )
            )

    checked = report.matched + report.mismatched
    report.match_rate_pct = (report.matched / checked * 100.0) if checked else 0.0
    return report
