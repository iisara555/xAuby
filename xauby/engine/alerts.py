import logging
from typing import Dict, Any, Optional
from xauby.notifications.interface import AlertLevel
from xauby.observability.events import EventType
from xauby.meta import PRODUCT_NAME

logger = logging.getLogger("lite_bot")


class AlertMixin:
    def _should_send_alert(self, level: AlertLevel) -> bool:
        if level == AlertLevel.CRITICAL:
            return True
        if self._notif_settings.alert_channel == "console":
            return True
        if not self.tg_enabled:
            return False
        if not self._notif_settings.send_alerts:
            return False
        if level == AlertLevel.POSITION and not self._notif_settings.notify_position_updates:
            return False
        return True

    def send_telegram_alert(
        self,
        message: str,
        level: AlertLevel = AlertLevel.TRADE,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._should_send_alert(level):
            return
        if level == AlertLevel.CRITICAL and reply_markup is None:
            try:
                from xauby.notifications.telegram_bot import CRITICAL_ALERT_KEYBOARD

                reply_markup = CRITICAL_ALERT_KEYBOARD
            except Exception:
                reply_markup = None
        if self._notif_settings.alert_channel == "console":
            logger.info("[ALERT/%s] %s", level.value, message.replace("\n", " | "))
            return
        if hasattr(self, "_tg_service"):
            self._tg_service.send_alert(message, level=level, reply_markup=reply_markup)

    def set_telegram_trading_paused(self, paused: bool, *, actor: str = "telegram") -> str:
        from xauby.runtime.telegram_control import set_trading_paused

        state = set_trading_paused(
            paused,
            reason="paused by Telegram" if paused else "resumed by Telegram",
            actor=actor,
        )
        mode = "PAUSED" if state.get("trading_paused") else "ACTIVE"
        return (
            f"🛑 *Trading {mode}*\n"
            f"New BUY orders are {'blocked' if paused else 'allowed'}.\n"
            f"Updated: `{str(state.get('updated_at') or '')[:19]}`"
        )

    def format_telegram_health(self) -> str:
        from xauby.runtime.telegram_control import load_control_state

        control = load_control_state()
        paused = bool(control.get("trading_paused", False))
        active = self._pair_registry.active()
        with self._ws_status_lock:
            ws_down = bool(getattr(self, "_ws_disconnected_at", 0.0) or 0.0)
        open_positions = 0
        unhealthy = []
        for spec in active:
            sc = self._sc(spec.symbol)
            feed_status = sc.feed_snapshot()
            state = self.db.get_trade_state(spec.symbol)
            if state.get("state") == "bought":
                open_positions += 1
            if feed_status["feed_degraded"]:
                unhealthy.append(f"{spec.symbol}: feed degraded")
            if feed_status["candle_stale"]:
                unhealthy.append(f"{spec.symbol}: candle stale")
            if feed_status["trading_halted"]:
                unhealthy.append(f"{spec.symbol}: halted")
        lines = [
            f"🩺 *{PRODUCT_NAME} Health*",
            f"Engine: `{'RUNNING' if getattr(self, '_engine_loop_started', False) else 'INITIALIZED'}`",
            f"Mode: `{'SIM' if self.simulate_only else 'LIVE'}` │ Read-only: `{bool(self.read_only)}`",
            f"Telegram pause: `{'ON' if paused else 'OFF'}`",
            f"Pairs: `{len(active)}` │ Open positions: `{open_positions}` │ WS: `{'DOWN' if ws_down else 'OK'}`",
        ]
        if paused:
            lines.append(f"Pause reason: `{control.get('reason', '')}`")
        if unhealthy:
            lines.append("*Issues*")
            lines.extend(f"• {item}" for item in unhealthy[:6])
        else:
            lines.append("Issues: `none`")
        return "\n".join(lines)

    def get_regime_dict(self, symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        reg = getattr(self._sc(symbol), "current_regime", None)
        if reg is None:
            return None
        return reg.to_dict() if hasattr(reg, "to_dict") else dict(reg)

    def format_telegram_status_multi(self) -> str:
        lines = [f"📡 *{PRODUCT_NAME} Status* — `{len(self._pair_registry.active())} active pair(s)`"]
        equity = self.get_equity()
        lines.append(f"Equity: `{equity:.2f} USDT` │ Mode: `{'SIM' if self.simulate_only else 'LIVE'}`")
        for spec in self._pair_registry.active():
            sc = self._sc(spec.symbol)
            tick = sc.get_tick_snapshot()
            price = tick.get("last", 0.0)
            state = self.db.get_trade_state(spec.symbol)
            pos = (
                f"LONG {state['quantity']:.4f}"
                if state.get("state") == "bought"
                else "IDLE"
            )
            act = (sc.last_signal_meta or {}).get("action", "—")
            lines.append(
                f"• `{spec.symbol}` {spec.primary_timeframe} │ `{price:.2f}` │ {pos} │ {act}"
            )
        return "\n".join(lines)

    def format_telegram_status(self) -> str:
        if len(self._pair_registry.active()) > 1:
            return self.format_telegram_status_multi()
        tick = self._get_tick_snapshot()
        price = tick.get("last", 0.0)
        if price <= 0:
            try:
                price = float(self.client.get_ticker(self.symbol).get("last", 0.0))
            except Exception:
                price = 0.0
        equity = self.get_equity()
        state = self.db.get_trade_state(self.symbol)
        pos = (
            f"LONG {state['quantity']:.4f} @ {state['entry_price']:.2f}"
            if state.get("state") == "bought"
            else "IDLE"
        )
        reg = self.get_regime_dict() or {}
        return (
            f"📡 *Status* — `{self.symbol}`\n"
            f"Price: `{price:.2f} USDT` │ Equity: `{equity:.2f} USDT`\n"
            f"Position: `{pos}` │ Mode: `{'SIM' if self.simulate_only else 'LIVE'}`\n"
            f"Regime: `{reg.get('regime', 'UNKNOWN')}` │ Score: `{reg.get('gold_score', 50)}/100`"
        )

    def _active_pair_symbols(self) -> list:
        return [s.symbol for s in self._pair_registry.active()]

    def _multi_pair_telegram(self) -> bool:
        return len(self._active_pair_symbols()) > 1

    def format_telegram_regime(self) -> str:
        if self._multi_pair_telegram():
            from xauby.notifications.multi_pair_format import format_multi_regime

            return format_multi_regime(
                self._active_pair_symbols(),
                self.get_regime_dict,
            )
        reg = self.get_regime_dict()
        if not reg:
            return "Regime data unavailable."
        lines = [
            f"📊 *Market Regime* — `{self.symbol}`",
            f"Regime: `{reg.get('regime', 'UNKNOWN')}` │ Score: `{reg.get('gold_score', 50)}/100`",
            f"Trend: `{reg.get('trend', 'NEUTRAL')}` │ Vol: `{reg.get('volatility', 'NORMAL')}`",
            f"Macro: `{reg.get('macro_bias', 'NEUTRAL')}` │ Conf: `{float(reg.get('confidence', 0.5)):.0%}`",
        ]
        reasons = reg.get("reasons") or []
        for item in reasons[:4]:
            mark = "✓" if item.get("supportive") else "✗"
            lines.append(f"  {mark} {item.get('label', '')}")
        return "\n".join(lines)

    def format_telegram_pnl(self) -> str:
        if self._multi_pair_telegram():
            from xauby.notifications.multi_pair_format import format_multi_pnl

            return format_multi_pnl(
                self._active_pair_symbols(),
                self.db.get_closed_trades,
            )
        from xauby.notifications.report_formatter import build_period_report_message

        trades = self.db.get_closed_trades(self.symbol, limit=1000)
        regime = self.get_regime_dict()
        w7 = build_period_report_message(trades, 7, "7-Day PnL", regime)
        w30 = build_period_report_message(trades, 30, "30-Day PnL", regime)
        return f"{w7}\n\n{w30}"

    def format_telegram_last_trades(self, limit: int = 3) -> str:
        if self._multi_pair_telegram():
            from xauby.notifications.multi_pair_format import format_multi_last_trades

            per_pair = max(2, limit // 2)
            return format_multi_last_trades(
                self._active_pair_symbols(),
                lambda sym, n: self.db.get_closed_trades(sym, limit=n),
                limit_per_pair=per_pair,
            )
        trades = self.db.get_closed_trades(self.symbol, limit=limit)
        if not trades:
            return "No closed trades yet."
        lines = [f"📋 *Last {min(limit, len(trades))} Trades* — `{self.symbol}`"]
        for t in trades[:limit]:
            pnl = float(t.get("net_pnl") or 0.0)
            sign = "+" if pnl >= 0 else ""
            dt = str(t.get("closed_at") or "")[:16]
            trigger = t.get("trigger") or "N/A"
            lines.append(f"• `{dt}` {trigger}: `{sign}{pnl:.2f} USDT`")
        return "\n".join(lines)

    def _check_regime_change_alert(
        self, gold_regime: Any, symbol: Optional[str] = None
    ) -> None:
        if not self._notif_settings.notify_regime_changes:
            return
        sc = self._sc(symbol)
        snap = {
            "regime": gold_regime.regime,
            "gold_score": gold_regime.gold_score,
        }
        prev = sc._last_regime_snapshot
        sc._last_regime_snapshot = snap
        if prev is None:
            return
        regime_changed = prev.get("regime") != snap.get("regime")
        score_delta = abs(int(snap.get("gold_score", 50)) - int(prev.get("gold_score", 50)))
        if not regime_changed and score_delta < self._notif_settings.regime_score_threshold:
            return
        reasons = []
        for item in (gold_regime.reasons or [])[:3]:
            mark = "✓" if item.get("supportive") else "✗"
            reasons.append(f"  {mark} {item.get('label', '')}")
        reason_block = "\n".join(reasons) if reasons else ""
        msg = (
            f"🔄 *Regime Update* — `{sc.symbol}`\n"
            f"`{prev.get('regime')}` ({prev.get('gold_score')}/100) → "
            f"`{snap.get('regime')}` ({snap.get('gold_score')}/100)"
        )
        if reason_block:
            msg += f"\n{reason_block}"
        self.send_telegram_alert(msg, level=AlertLevel.INFO)

    def send_heartbeat(self):
        try:
            equity = self.get_equity()
            if self._multi_pair_telegram():
                from xauby.notifications.multi_pair_format import format_multi_heartbeat

                def _price(sym: str) -> float:
                    tick = self._sc(sym).get_tick_snapshot()
                    price = float(tick.get("last", 0.0) or 0.0)
                    if price <= 0:
                        price = float(self.client.get_ticker(sym).get("last", 0.0))
                    return price

                def _pos_line(sym: str) -> str:
                    st = self.db.get_trade_state(sym)
                    if st.get("state") == "bought":
                        return (
                            f"LONG {st['quantity']:.4f} @ {st['entry_price']:.2f}"
                        )
                    return "IDLE"

                msg = format_multi_heartbeat(
                    self._active_pair_symbols(),
                    equity,
                    _price,
                    _pos_line,
                    self.get_regime_dict,
                )
                ticker_price = _price(self.focus_symbol)
                pos_str = _pos_line(self.focus_symbol)
            else:
                tick_snap = self._get_tick_snapshot()
                ticker_price = tick_snap.get("last", 0.0)
                if ticker_price <= 0:
                    ticker_price = self.client.get_ticker(self.symbol)["last"]
                state = self.db.get_trade_state(self.symbol)
                pos_str = (
                    f"LONG {state['quantity']:.4f} @ {state['entry_price']:.2f}"
                    if state["state"] == "bought"
                    else "IDLE"
                )
                msg = (
                    f"💚 *{PRODUCT_NAME} Heartbeat*\n"
                    f"• Symbol: `{self.symbol}`\n"
                    f"• Price: `{ticker_price:.2f} USDT`\n"
                    f"• Equity: `{equity:.2f} USDT`\n"
                    f"• Position: `{pos_str}`\n"
                    f"• Status: `Running normally`"
                )
                reg = self.get_regime_dict()
                if reg:
                    msg += (
                        f"\n• Regime: `{reg.get('regime', 'UNKNOWN')}` "
                        f"│ Score: `{reg.get('gold_score', 50)}/100`"
                    )
            self.send_telegram_alert(msg, level=AlertLevel.INFO)
            logger.info("Heartbeat alert sent to Telegram.")
            self._emit_event(
                EventType.HEARTBEAT,
                price=round(ticker_price, 2),
                equity=round(equity, 2),
                position=pos_str,
            )
        except Exception as e:
            logger.error(f"Failed to send heartbeat: {e}")
            self._emit_event(EventType.ERROR, context="heartbeat", error=str(e))
