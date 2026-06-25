from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class NotificationSettings:
    send_alerts: bool = True
    notify_position_updates: bool = True
    notify_guard_blocks: bool = True
    notify_regime_changes: bool = True
    alert_channel: str = "telegram"
    telegram_command_polling_enabled: bool = False
    regime_score_threshold: int = 10
    semi_auto_confirm_timeout_seconds: int = 60

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "NotificationSettings":
        n = config.get("notifications") or {}
        return cls(
            send_alerts=bool(n.get("send_alerts", True)),
            notify_position_updates=bool(n.get("notify_position_updates", True)),
            notify_guard_blocks=bool(n.get("notify_guard_blocks", True)),
            notify_regime_changes=bool(n.get("notify_regime_changes", True)),
            alert_channel=str(n.get("alert_channel", "telegram")).lower(),
            telegram_command_polling_enabled=bool(n.get("telegram_command_polling_enabled", False)),
            regime_score_threshold=int(n.get("regime_score_threshold", 10)),
            semi_auto_confirm_timeout_seconds=int(n.get("semi_auto_confirm_timeout_seconds", 60)),
        )
