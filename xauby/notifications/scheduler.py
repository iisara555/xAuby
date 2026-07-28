import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from xauby.notifications.report_formatter import (
    build_daily_digest_message,
    build_period_report_message,
    format_weekly_review_md,
)
from xauby.notifications.interface import AlertLevel

logger = logging.getLogger("report_scheduler")


class ReportScheduler:
    """Weekly and daily Telegram/Markdown report scheduler."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._weekly_cfg = config.get("weekly_review") or {}
        self._daily_cfg = config.get("daily_digest") or {}
        self._self_audit_cfg = config.get("self_audit") or {}
        self._monthly_cfg = config.get("monthly_report") or {}
        self._last_weekly_key: Optional[str] = None
        self._last_daily_key: Optional[str] = None
        self._last_audit_key: Optional[str] = None
        self._last_monthly_key: Optional[str] = None

    def _slot_key(self, day_of_week: int, hour_utc: int, now: datetime) -> str:
        if now.weekday() != day_of_week or now.hour != hour_utc:
            return ""
        return now.strftime("%Y-%m-%d-%H")

    def _daily_slot_key(self, hour_utc: int, now: datetime) -> str:
        if now.hour != hour_utc:
            return ""
        return now.strftime("%Y-%m-%d")

    def _save_weekly_file(self, content: str, now: datetime) -> None:
        if not self._weekly_cfg.get("save_to_file", True):
            return
        path_tpl = str(self._weekly_cfg.get("file_path", "weekly_reviews/review_{date}.md"))
        path = path_tpl.format(date=now.strftime("%Y-%m-%d"))
        if not os.path.isabs(path):
            from xauby.runtime.paths import runtime_path
            path = runtime_path(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Saved weekly review to %s", path)

    def check_and_run(self, engine: Any, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        poll = int(self._weekly_cfg.get("scheduler_poll_seconds", 60))
        if poll <= 0:
            poll = 60
        _ = poll  # engine loop owns sleep interval

        self._maybe_run_weekly(engine, now)
        self._maybe_run_daily(engine, now)
        self._maybe_run_self_audit(engine, now)
        self._maybe_run_monthly(engine, now)

    def _monthly_slot_key(self, day: int, hour_utc: int, now: datetime) -> str:
        if now.day != day or now.hour != hour_utc:
            return ""
        return now.strftime("%Y-%m")

    @staticmethod
    def _month_bounds(now: datetime, *, current_month: bool) -> tuple[datetime, datetime]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        first_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if current_month:
            return first_current, now
        previous_last = first_current - timedelta(seconds=1)
        start = previous_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, first_current

    def _maybe_run_monthly(self, engine: Any, now: datetime) -> None:
        if not self._monthly_cfg.get("enabled", False):
            return
        day = int(self._monthly_cfg.get("day_of_month", 1))
        hour = int(self._monthly_cfg.get("hour_utc", 12))
        slot = self._monthly_slot_key(day, hour, now)
        if not slot or slot == self._last_monthly_key:
            return
        self._last_monthly_key = slot
        try:
            self.run_monthly_now(
                engine, now=now, current_month=False, send=True
            )
        except Exception as exc:
            logger.error("Monthly track-record/parity report failed: %s", exc)
            if self._monthly_cfg.get("send_telegram", True):
                engine.send_telegram_alert(
                    f"Monthly track-record/parity report failed: {exc}",
                    level=AlertLevel.CRITICAL,
                )

    def run_monthly_now(
        self,
        engine: Any,
        *,
        now: Optional[datetime] = None,
        current_month: bool = False,
        send: bool = False,
    ) -> Dict[str, Any]:
        """Build and persist one exact calendar-month operational artifact.

        ``current_month=True`` is the safe self-test path after deployment; the
        scheduled day-1 run always reports the completed previous month.
        """
        from xauby.analytics.realised_parity import build_realised_parity_report
        from xauby.observability.events import EventType
        from xauby.track_record.generator import _parse_date, generate_report
        from xauby.utils.atomic_io import atomic_json_write

        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        start, end = self._month_bounds(now, current_month=current_month)
        all_trades = engine.db.get_closed_trades(None, limit=5000)
        trades = [
            trade for trade in all_trades
            if start <= _parse_date(trade.get("closed_at")) < end
            and str(trade.get("execution_mode") or "live").lower() == "live"
        ]
        initial_balance = float(
            getattr(engine, "initial_balance", 1000.0) or 1000.0
        )
        day_count = max(1, int((end - start).total_seconds() // 86_400))
        track = generate_report(
            trades,
            day_count,
            start.strftime("%Y-%m"),
            initial_balance=initial_balance,
            as_of=end,
            period_start=start,
        )

        event_rows = []
        opened_dates = [
            _parse_date(trade.get("opened_at"))
            for trade in trades if trade.get("opened_at")
        ]
        event_start = min([start, *opened_dates]) if opened_dates else start
        for event_type in (
            EventType.POSITION_OPENED,
            EventType.POSITION_RESTORED,
            EventType.POSITION_CLOSED,
        ):
            event_rows.extend(engine.db.query_events(
                event_type=event_type,
                since_ts=(
                    event_start.isoformat()
                    if event_type in {EventType.POSITION_OPENED, EventType.POSITION_RESTORED}
                    else start.isoformat()
                ),
                until_ts=(end - timedelta(microseconds=1)).isoformat(),
                limit=5000,
                order="asc",
            ))

        default_fee = float(
            (self.config.get("exchange") or {}).get("fee_pct", 0.0) or 0.0
        )
        fee_rates: Dict[str, float] = {"*": default_fee}
        for symbol in self._active_symbols(engine):
            spec = engine._pair_registry.get(symbol) if hasattr(
                engine, "_pair_registry"
            ) else None
            configured = getattr(spec, "sim_fee_pct", None)
            fee_rates[str(symbol)] = float(
                configured if configured is not None else default_fee
            )
        parity = build_realised_parity_report(
            trades,
            event_rows,
            fee_rates=fee_rates,
            model_slippage_bps=float(
                (self.config.get("backtest") or {}).get("slippage_bps", 0.0) or 0.0
            ),
            tolerance_bps=float(
                self._monthly_cfg.get("parity_tolerance_bps", 10.0)
            ),
        )
        month = start.strftime("%Y-%m")
        bundle = {
            "schema_version": 1,
            "month": month,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "generated_at": now.astimezone(timezone.utc).isoformat(),
            "initial_balance": initial_balance,
            "track_record": asdict(track),
            "realised_parity": parity,
        }

        if self._monthly_cfg.get("save_to_file", True):
            path_tpl = str(self._monthly_cfg.get(
                "file_path", "monthly_reports/report_{month}.json"
            ))
            path = path_tpl.format(month=month)
            if not os.path.isabs(path):
                from xauby.runtime.paths import runtime_path
                path = runtime_path(path)
            atomic_json_write(path, bundle, indent=2, mode=0o660)
            bundle["file_path"] = path

        if send and self._monthly_cfg.get("send_telegram", True):
            sign = "+" if track.net_pnl >= 0 else ""
            message = (
                f"Monthly xAuby report {month}\n"
                f"Trades: {track.total_trades} | Net: {sign}{track.net_pnl:.2f} USDT | "
                f"Max DD: {track.max_drawdown_pct:.2f}%\n"
                f"Realised parity: {parity['status']} | "
                f"slippage coverage {parity['slippage_coverage_pct']:.0f}% | "
                f"variance {parity['variance_bps']:.2f} bps"
            )
            level = (
                AlertLevel.CRITICAL
                if parity["status"] in {"check", "insufficient_evidence"}
                else AlertLevel.INFO
            )
            engine.send_telegram_alert(message, level=level)
        logger.info(
            "Monthly report %s written: trades=%s parity=%s",
            month, track.total_trades, parity["status"],
        )
        return bundle

    def _maybe_run_self_audit(self, engine: Any, now: datetime) -> None:
        """Replay the event log against the trade table and alert on drift.

        Roadmap P1.6. The auditor shipped but nothing ever called it: it was
        reachable only from a unit test, so the one check that would notice the
        trade table disagreeing with the event log never ran against real data.
        """
        if not self._self_audit_cfg.get("enabled", False):
            return
        hour = int(self._self_audit_cfg.get("hour_utc", 11))
        slot = self._daily_slot_key(hour, now)
        if not slot or slot == self._last_audit_key:
            return
        self._last_audit_key = slot
        try:
            from xauby.track_record.validator import audit_track_record

            ok, discrepancies, stats = audit_track_record(engine.db)
            if ok:
                logger.info(
                    "Self-audit clean: %s trades matched across %s",
                    stats.get("matching_trades"), stats.get("symbols") or "no symbols",
                )
                return
            # Alerting on a failed audit is the whole point: a silent
            # reconciliation failure is indistinguishable from a healthy system.
            head = discrepancies[:5]
            more = len(discrepancies) - len(head)
            body = "\n".join(f"- {d}" for d in head)
            if more > 0:
                body += f"\n- ...and {more} more"
            logger.warning("Self-audit found %s discrepancies", len(discrepancies))
            if self._self_audit_cfg.get("send_telegram", True):
                engine.send_telegram_alert(
                    f"\u26a0\ufe0f Track-record self-audit found "
                    f"{len(discrepancies)} discrepancies\n{body}",
                    level=AlertLevel.CRITICAL,
                )
        except Exception as exc:  # never let reporting take the engine down
            logger.error("Self-audit failed to run: %s", exc)
            if self._self_audit_cfg.get("send_telegram", True):
                engine.send_telegram_alert(
                    f"Track-record self-audit failed to run: {exc}",
                    level=AlertLevel.CRITICAL,
                )

    def _maybe_run_weekly(self, engine: Any, now: datetime) -> None:
        if not self._weekly_cfg.get("enabled", False):
            return
        day = int(self._weekly_cfg.get("day_of_week", 6))
        hour = int(self._weekly_cfg.get("hour_utc", 17))
        slot = self._slot_key(day, hour, now)
        if not slot or slot == self._last_weekly_key:
            return
        self._last_weekly_key = slot
        self._send_weekly(engine)

    def _maybe_run_daily(self, engine: Any, now: datetime) -> None:
        if not self._daily_cfg.get("enabled", False):
            return
        hour = int(self._daily_cfg.get("hour_utc", 10))
        slot = self._daily_slot_key(hour, now)
        if not slot or slot == self._last_daily_key:
            return
        self._last_daily_key = slot
        self._send_daily(engine)

    def _regime_dict(self, engine: Any) -> Optional[Dict[str, Any]]:
        reg = getattr(engine, "current_regime", None)
        if reg is None:
            return None
        return reg.to_dict() if hasattr(reg, "to_dict") else dict(reg)

    def _active_symbols(self, engine: Any) -> list:
        if hasattr(engine, "_active_pair_symbols"):
            syms = engine._active_pair_symbols()
            if syms:
                return syms
        sym = getattr(engine, "symbol", "")
        if sym:
            return [sym]
        from xauby.ui.state_view import default_symbol_from_whitelist

        return [default_symbol_from_whitelist()]

    def _send_weekly(self, engine: Any) -> None:
        try:
            symbols = self._active_symbols(engine)
            period_days = int(self._weekly_cfg.get("period_days", 7))
            initial_balance = float(getattr(engine, "initial_balance", 1000.0) or 1000.0)
            from xauby.track_record.generator import generate_report

            if len(symbols) > 1:
                from xauby.notifications.multi_pair_format import build_multi_weekly_review

                msg = build_multi_weekly_review(
                    symbols,
                    engine.db.get_closed_trades,
                    engine.get_regime_dict,
                    period_days=period_days,
                    initial_balance=initial_balance,
                )
                trades = engine.db.get_closed_trades(None, limit=5000)
                regime = engine.get_regime_dict(symbols[0]) if hasattr(
                    engine, "get_regime_dict"
                ) else self._regime_dict(engine)
                report = generate_report(
                    trades, period_days, "Weekly",
                    initial_balance=initial_balance,
                )
                md_symbol = ", ".join(symbols)
            else:
                trades = engine.db.get_closed_trades(engine.symbol, limit=1000)
                regime = (
                    engine.get_regime_dict()
                    if hasattr(engine, "get_regime_dict")
                    else self._regime_dict(engine)
                )
                msg = build_period_report_message(
                    trades, period_days, "Weekly Review", regime,
                    initial_balance=initial_balance,
                )
                report = generate_report(
                    trades, period_days, "Weekly",
                    initial_balance=initial_balance,
                )
                md_symbol = engine.symbol
            md = format_weekly_review_md(report, regime, md_symbol)
            self._save_weekly_file(md, datetime.now(timezone.utc))
            if self._weekly_cfg.get("send_telegram", True):
                engine.send_telegram_alert(msg, level=AlertLevel.INFO)
            logger.info("Weekly review report sent")
        except Exception as e:
            logger.error("Weekly review failed: %s", e)

    def _send_daily(self, engine: Any) -> None:
        try:
            symbols = self._active_symbols(engine)
            equity = engine.get_equity()
            initial_balance = float(getattr(engine, "initial_balance", 1000.0) or 1000.0)
            if len(symbols) > 1:
                from xauby.notifications.multi_pair_format import build_multi_daily_digest

                def _pos_line(sym: str) -> str:
                    state = engine.db.get_trade_state(sym)
                    if state.get("state") == "bought":
                        side = str(state.get("position_side") or "LONG").upper()
                        return (
                            f"{side} {state['quantity']:.4f} @ {state['entry_price']:.2f}"
                        )
                    return "IDLE"

                msg = build_multi_daily_digest(
                    symbols,
                    equity,
                    engine.db.get_closed_trades,
                    _pos_line,
                    engine.get_regime_dict,
                    initial_balance=initial_balance,
                )
            else:
                sym = symbols[0]
                trades = engine.db.get_closed_trades(sym, limit=1000)
                state = engine.db.get_trade_state(sym)
                side = str(state.get("position_side") or "LONG").upper()
                pos = (
                    f"{side} {state['quantity']:.4f} @ {state['entry_price']:.2f}"
                    if state.get("state") == "bought"
                    else "IDLE"
                )
                regime = (
                    engine.get_regime_dict(sym)
                    if hasattr(engine, "get_regime_dict")
                    else self._regime_dict(engine)
                )
                msg = build_daily_digest_message(
                    trades, sym, equity, pos, regime,
                    initial_balance=initial_balance,
                )
            if self._daily_cfg.get("send_telegram", True):
                engine.send_telegram_alert(msg, level=AlertLevel.INFO)
            logger.info("Daily digest sent")
        except Exception as e:
            logger.error("Daily digest failed: %s", e)
