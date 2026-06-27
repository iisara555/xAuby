import logging
import threading
import time
from typing import Any, Optional

import requests

logger = logging.getLogger("telegram_bot")

SEMI_AUTO_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "Confirm BUY", "callback_data": "semi_buy_confirm"},
            {"text": "Skip", "callback_data": "semi_buy_skip"},
        ]
    ]
}

PAUSE_CONFIRM_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "Confirm PAUSE", "callback_data": "op_pause_confirm"},
            {"text": "Cancel", "callback_data": "op_pause_cancel"},
        ]
    ]
}

RESUME_CONFIRM_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "Confirm RESUME", "callback_data": "op_resume_confirm"},
            {"text": "Cancel", "callback_data": "op_resume_cancel"},
        ]
    ]
}

CRITICAL_ALERT_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "Status", "callback_data": "critical_status"},
            {"text": "Ack", "callback_data": "critical_ack"},
        ]
    ]
}


class TelegramCommandPoller:
    """Long-poll Telegram updates for commands and semi-auto callbacks."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        engine: Any,
        enabled: bool = True,
    ):
        self.token = token
        self.chat_id = str(chat_id)
        self.engine = engine
        self.enabled = enabled
        self._offset = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled or not self.token or not self.chat_id:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Telegram command poller started")

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _poll_loop(self) -> None:
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        while not self._stop.is_set():
            try:
                resp = requests.get(
                    url,
                    params={"offset": self._offset, "timeout": 25},
                    timeout=30,
                )
                if resp.status_code != 200:
                    time.sleep(2)
                    continue
                for update in resp.json().get("result", []):
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    try:
                        self._handle_update(update)
                    except Exception as e:
                        logger.error("Telegram update handler failed: %s", e, exc_info=True)
                        self._reply_authorized_failure(update)
            except Exception as e:
                logger.error("Telegram poll error: %s", e)
                time.sleep(2)

    def _is_authorized(self, chat_id: Any) -> bool:
        return str(chat_id) == self.chat_id

    def _reply(self, chat_id: str, text: str, reply_markup: Optional[dict] = None) -> None:
        svc = getattr(self.engine, "_tg_service", None)
        if svc and hasattr(svc, "send_immediate"):
            svc.send_immediate(text, reply_markup=reply_markup)
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(
            url,
            json=payload,
            timeout=10,
        )

    def _answer_callback(self, callback_id: str, text: str = "") -> None:
        url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass

    def _reply_authorized_failure(self, update: dict) -> None:
        chat_id = None
        callback_id = ""
        if "callback_query" in update:
            callback = update.get("callback_query") or {}
            callback_id = callback.get("id", "")
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
        else:
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
        if not self._is_authorized(chat_id):
            return
        if callback_id:
            self._answer_callback(callback_id, "Failed")
        self._reply(str(chat_id), "⚠️ *Telegram command failed* — check engine logs.")

    def _handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if not self._is_authorized(chat.get("id")):
            return
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return
        cmd = text.split()[0].lower().split("@")[0]
        handlers = {
            "/status": self._cmd_status,
            "/pnl": self._cmd_pnl,
            "/regime": self._cmd_regime,
            "/last": self._cmd_last,
            "/health": self._cmd_health,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/start": self._cmd_start,
            "/help": self._cmd_start,
        }
        handler = handlers.get(cmd)
        if handler:
            handler(str(chat.get("id")))

    def _handle_callback(self, callback: dict) -> None:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        if not self._is_authorized(chat.get("id")):
            return
        data = callback.get("data") or ""
        cb_id = callback.get("id", "")
        if data == "semi_buy_confirm":
            self.engine.confirm_semi_auto_buy()
            self._answer_callback(cb_id, "Confirmed")
            self._reply(str(chat.get("id")), "✅ *BUY confirmed* — engine will execute on next tick.")
        elif data == "semi_buy_skip":
            self.engine.skip_semi_auto_buy()
            self._answer_callback(cb_id, "Skipped")
            self._reply(str(chat.get("id")), "⏭ *BUY skipped*.")
        elif data == "op_pause_confirm":
            msg = self.engine.set_telegram_trading_paused(True, actor="telegram")
            self._answer_callback(cb_id, "Paused")
            self._reply(str(chat.get("id")), msg)
        elif data == "op_resume_confirm":
            msg = self.engine.set_telegram_trading_paused(False, actor="telegram")
            self._answer_callback(cb_id, "Resumed")
            self._reply(str(chat.get("id")), msg)
        elif data in ("op_pause_cancel", "op_resume_cancel"):
            self._answer_callback(cb_id, "Cancelled")
            self._reply(str(chat.get("id")), "Cancelled.")
        elif data == "critical_status":
            self._answer_callback(cb_id, "Status")
            self._reply(str(chat.get("id")), self.engine.format_telegram_health())
        elif data == "critical_ack":
            self._answer_callback(cb_id, "Acknowledged")
            self._reply(str(chat.get("id")), "✅ *Critical alert acknowledged*.")

    def _cmd_start(self, chat_id: str) -> None:
        self._reply(
            chat_id,
            "🤖 *xAuby Bot Commands*\n"
            "/status — price, position, signal (all pairs)\n"
            "/pnl — 7d & 30d portfolio + per pair\n"
            "/regime — regime per active pair\n"
            "/last — recent closed trades per pair\n"
            "/health — engine health snapshot\n"
            "/pause — confirm emergency pause for new BUYs\n"
            "/resume — confirm allowing new BUYs again",
        )

    def _cmd_status(self, chat_id: str) -> None:
        self._reply(chat_id, self.engine.format_telegram_status())

    def _cmd_pnl(self, chat_id: str) -> None:
        self._reply(chat_id, self.engine.format_telegram_pnl())

    def _cmd_regime(self, chat_id: str) -> None:
        self._reply(chat_id, self.engine.format_telegram_regime())

    def _cmd_last(self, chat_id: str) -> None:
        self._reply(chat_id, self.engine.format_telegram_last_trades(limit=3))

    def _cmd_health(self, chat_id: str) -> None:
        self._reply(chat_id, self.engine.format_telegram_health())

    def _cmd_pause(self, chat_id: str) -> None:
        self._reply(
            chat_id,
            "🛑 *Confirm emergency pause?*\nNew BUY orders will be blocked until `/resume` is confirmed.",
            reply_markup=PAUSE_CONFIRM_KEYBOARD,
        )

    def _cmd_resume(self, chat_id: str) -> None:
        self._reply(
            chat_id,
            "✅ *Confirm resume?*\nNew BUY orders will be allowed again.",
            reply_markup=RESUME_CONFIRM_KEYBOARD,
        )
