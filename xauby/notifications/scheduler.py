import logging
import os
from datetime import datetime, timezone
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
        self._last_weekly_key: Optional[str] = None
        self._last_daily_key: Optional[str] = None

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
            from xauby.track_record.generator import generate_report

            if len(symbols) > 1:
                from xauby.notifications.multi_pair_format import build_multi_weekly_review

                msg = build_multi_weekly_review(
                    symbols,
                    engine.db.get_closed_trades,
                    engine.get_regime_dict,
                    period_days=period_days,
                )
                trades = engine.db.get_closed_trades(None, limit=5000)
                regime = engine.get_regime_dict(symbols[0]) if hasattr(
                    engine, "get_regime_dict"
                ) else self._regime_dict(engine)
                report = generate_report(trades, period_days, "Weekly")
                md_symbol = ", ".join(symbols)
            else:
                trades = engine.db.get_closed_trades(engine.symbol, limit=1000)
                regime = (
                    engine.get_regime_dict()
                    if hasattr(engine, "get_regime_dict")
                    else self._regime_dict(engine)
                )
                msg = build_period_report_message(trades, period_days, "Weekly Review", regime)
                report = generate_report(trades, period_days, "Weekly")
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
            if len(symbols) > 1:
                from xauby.notifications.multi_pair_format import build_multi_daily_digest

                def _pos_line(sym: str) -> str:
                    state = engine.db.get_trade_state(sym)
                    if state.get("state") == "bought":
                        return (
                            f"LONG {state['quantity']:.4f} @ {state['entry_price']:.2f}"
                        )
                    return "IDLE"

                msg = build_multi_daily_digest(
                    symbols,
                    equity,
                    engine.db.get_closed_trades,
                    _pos_line,
                    engine.get_regime_dict,
                )
            else:
                sym = symbols[0]
                trades = engine.db.get_closed_trades(sym, limit=1000)
                state = engine.db.get_trade_state(sym)
                pos = (
                    f"LONG {state['quantity']:.4f} @ {state['entry_price']:.2f}"
                    if state.get("state") == "bought"
                    else "IDLE"
                )
                regime = (
                    engine.get_regime_dict(sym)
                    if hasattr(engine, "get_regime_dict")
                    else self._regime_dict(engine)
                )
                msg = build_daily_digest_message(trades, sym, equity, pos, regime)
            if self._daily_cfg.get("send_telegram", True):
                engine.send_telegram_alert(msg, level=AlertLevel.INFO)
            logger.info("Daily digest sent")
        except Exception as e:
            logger.error("Daily digest failed: %s", e)
