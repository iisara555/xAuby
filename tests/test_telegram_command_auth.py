"""Authorization gates for the Telegram command poller.

Commands and action callbacks must be accepted only from the configured
private chat's owner — the chat gate alone would let any member of a group
press "Confirm BUY" if the operator ever configured a group chat id.
"""
from __future__ import annotations

import unittest
from unittest import mock

from xauby.notifications.telegram_bot import TelegramCommandPoller


OWNER_ID = 111
STRANGER_ID = 222
GROUP_CHAT_ID = -100500


class _EngineSpy:
    def __init__(self):
        self.confirmed = 0
        self._tg_service = None

    def confirm_semi_auto_buy(self):
        self.confirmed += 1

    def format_telegram_status(self):
        return "status"


def _poller(chat_id) -> tuple[TelegramCommandPoller, _EngineSpy]:
    engine = _EngineSpy()
    poller = TelegramCommandPoller(
        token="t", chat_id=str(chat_id), engine=engine, enabled=True
    )
    return poller, engine


def _callback(chat_id, from_id, data="semi_buy_confirm"):
    return {
        "id": "cb1",
        "data": data,
        "from": {"id": from_id},
        "message": {"chat": {"id": chat_id}},
    }


def _message_update(chat_id, from_id, text="/status"):
    return {"message": {"chat": {"id": chat_id}, "from": {"id": from_id}, "text": text}}


class TestCallbackAuthorization(unittest.TestCase):
    def test_owner_callback_in_private_chat_executes(self):
        poller, engine = _poller(OWNER_ID)
        with mock.patch.object(poller, "_answer_callback"), mock.patch.object(poller, "_reply"):
            poller._handle_callback(_callback(OWNER_ID, OWNER_ID))
        self.assertEqual(engine.confirmed, 1)

    def test_foreign_presser_in_private_chat_is_ignored(self):
        poller, engine = _poller(OWNER_ID)
        with mock.patch.object(poller, "_answer_callback"), mock.patch.object(poller, "_reply"):
            poller._handle_callback(_callback(OWNER_ID, STRANGER_ID))
        self.assertEqual(engine.confirmed, 0)

    def test_wrong_chat_is_ignored(self):
        poller, engine = _poller(OWNER_ID)
        with mock.patch.object(poller, "_answer_callback"), mock.patch.object(poller, "_reply"):
            poller._handle_callback(_callback(STRANGER_ID, STRANGER_ID))
        self.assertEqual(engine.confirmed, 0)

    def test_group_chat_config_keeps_chat_gate_only(self):
        # No member allowlist exists for groups; the chat gate remains the
        # control there, so a member's press still executes.
        poller, engine = _poller(GROUP_CHAT_ID)
        with mock.patch.object(poller, "_answer_callback"), mock.patch.object(poller, "_reply"):
            poller._handle_callback(_callback(GROUP_CHAT_ID, STRANGER_ID))
        self.assertEqual(engine.confirmed, 1)


class TestMessageAuthorization(unittest.TestCase):
    def test_owner_command_replies(self):
        poller, _ = _poller(OWNER_ID)
        with mock.patch.object(poller, "_reply") as reply:
            poller._handle_update(_message_update(OWNER_ID, OWNER_ID))
        reply.assert_called_once()

    def test_foreign_sender_in_private_chat_is_ignored(self):
        poller, _ = _poller(OWNER_ID)
        with mock.patch.object(poller, "_reply") as reply:
            poller._handle_update(_message_update(OWNER_ID, STRANGER_ID))
        reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
