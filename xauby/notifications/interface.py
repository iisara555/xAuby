from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional


class AlertLevel(str, Enum):
    CRITICAL = "critical"
    TRADE = "trade"
    POSITION = "position"
    INFO = "info"


class INotificationService(ABC):
    """Abstract interface for system alert notifications."""

    @abstractmethod
    def send_alert(
        self,
        message: str,
        level: AlertLevel = AlertLevel.TRADE,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send alert message to external channels (e.g. Telegram)."""

    def stop(self) -> None:
        """Release background resources."""
