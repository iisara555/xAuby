import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Optional

import requests

from xauby.notifications.interface import AlertLevel, INotificationService

logger = logging.getLogger("telegram_notification")


class TelegramNotificationService(INotificationService):
    """Concrete notification service implementing INotificationService using Telegram."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        enabled: bool = True,
        failure_log_path: Optional[str] = None,
        max_retries: int = 3,
    ):
        from xauby.runtime.paths import log_path
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled
        self.failure_log_path = failure_log_path or log_path("telegram_failures.log")
        self.max_retries = max(1, max_retries)
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _log_failure(self, message: str, error: str, status_code: Optional[int] = None) -> None:
        try:
            os.makedirs(os.path.dirname(self.failure_log_path) or ".", exist_ok=True)
            with open(self.failure_log_path, "a", encoding="utf-8") as f:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                code = status_code if status_code is not None else "-"
                preview = message.replace("\n", " ")[:160]
                f.write(f"{ts} | status={code} | {error} | {preview}\n")
        except Exception as log_err:
            logger.error("Failed to write telegram failure log: %s", log_err)

    def _post_message(self, message: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        last_error = ""
        last_status: Optional[int] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                last_status = resp.status_code
                if resp.status_code == 200:
                    return True
                last_error = resp.text[:300]
                logger.warning(
                    "Telegram send failed (attempt %s/%s): HTTP %s",
                    attempt,
                    self.max_retries,
                    resp.status_code,
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Telegram send exception (attempt %s/%s): %s",
                    attempt,
                    self.max_retries,
                    e,
                )
            if attempt < self.max_retries:
                time.sleep(min(2 ** (attempt - 1), 4))
        self._log_failure(message, last_error, last_status)
        return False

    def _worker(self) -> None:
        while True:
            try:
                item = self._queue.get()
                if item is None:
                    break
                if isinstance(item, tuple):
                    message, _level, reply_markup = item
                else:
                    message, reply_markup = str(item), None
                if not self.enabled or not self.token or not self.chat_id:
                    continue
                self._post_message(message, reply_markup=reply_markup)
            except Exception as e:
                logger.error("Failed to send Telegram alert: %s", e)
            finally:
                try:
                    self._queue.task_done()
                except Exception:
                    pass

    def send_alert(
        self,
        message: str,
        level: AlertLevel = AlertLevel.TRADE,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Queue message to be sent asynchronously to Telegram."""
        if not self.enabled or not self.token or not self.chat_id:
            return
        try:
            self._queue.put_nowait((message, level, reply_markup))
        except Exception as e:
            logger.error("Failed to queue Telegram alert: %s", e)

    def send_immediate(
        self,
        message: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send synchronously (used by command replies)."""
        if not self.enabled or not self.token or not self.chat_id:
            return False
        return self._post_message(message, reply_markup=reply_markup)

    def stop(self) -> None:
        """Stop the background worker thread."""
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
