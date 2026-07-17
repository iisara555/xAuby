import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from xauby.runtime.manual_orders import (
    claim_manual_order_request,
    write_manual_order_request,
)


class ManualOrderIPCTests(unittest.TestCase):
    def test_request_opens_acl_read_mask_for_isolated_engine(self):
        with tempfile.TemporaryDirectory() as root:
            write_manual_order_request("BTCUSDT", "BUY", project_root=root)
            path = os.path.join(root, "core", "manual_order_request.json")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)

    def test_matching_request_is_claimed_once(self):
        with tempfile.TemporaryDirectory() as root:
            request = write_manual_order_request("btcusdt", "buy", project_root=root)
            claimed = claim_manual_order_request(
                "BTCUSDT", project_root=root, now=request["created_at"] + 1
            )
            self.assertEqual(claimed["action"], "BUY")
            self.assertEqual(claimed["symbol"], "BTCUSDT")
            self.assertIsNone(claim_manual_order_request("BTCUSDT", project_root=root))

    def test_other_symbol_does_not_consume_request(self):
        with tempfile.TemporaryDirectory() as root:
            request = write_manual_order_request("BTCUSDT", "SELL", project_root=root)
            self.assertIsNone(claim_manual_order_request("XAUTUSDT", project_root=root))
            claimed = claim_manual_order_request(
                "BTCUSDT", project_root=root, now=request["created_at"] + 1
            )
            self.assertEqual(claimed["action"], "SELL")

    def test_stale_request_is_consumed_without_execution(self):
        with tempfile.TemporaryDirectory() as root:
            request = write_manual_order_request("BTCUSDT", "BUY", project_root=root)
            self.assertIsNone(
                claim_manual_order_request(
                    "BTCUSDT", project_root=root, now=request["created_at"] + 121
                )
            )
            self.assertIsNone(claim_manual_order_request("BTCUSDT", project_root=root))

    def test_invalid_action_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                write_manual_order_request("BTCUSDT", "CLOSE", project_root=root)

    def test_buy_request_can_be_manual_managed(self):
        with tempfile.TemporaryDirectory() as root:
            request = write_manual_order_request(
                "BTCUSDT", "BUY", management_mode="manual", project_root=root
            )
            claimed = claim_manual_order_request(
                "BTCUSDT", project_root=root, now=request["created_at"] + 1
            )
            self.assertEqual(claimed["action"], "BUY")
            self.assertEqual(claimed["management_mode"], "manual")

    def test_request_can_wait_for_strategy_handoff(self):
        with tempfile.TemporaryDirectory() as root:
            request = write_manual_order_request(
                "XAUUSDT",
                "OPEN_LONG",
                management_mode="strategy_handoff",
                project_root=root,
            )
            claimed = claim_manual_order_request(
                "XAUUSDT", project_root=root, now=request["created_at"] + 1
            )
            self.assertEqual(claimed["management_mode"], "strategy_handoff")

    def test_invalid_management_mode_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                write_manual_order_request(
                    "BTCUSDT", "BUY", management_mode="robot", project_root=root
                )

    def test_versioned_short_intent_round_trips(self):
        with tempfile.TemporaryDirectory() as root:
            request = write_manual_order_request(
                "XAUUSDT", "OPEN_SHORT", request_id="command-1", project_root=root
            )
            claimed = claim_manual_order_request(
                "XAUUSDT", project_root=root, now=request["created_at"] + 1
            )
            self.assertEqual(claimed["version"], 2)
            self.assertEqual(claimed["intent"], "OPEN_SHORT")
            self.assertEqual(claimed["position_side"], "SHORT")

    def test_unreadable_request_is_discarded_with_an_error_log(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch("builtins.open", side_effect=PermissionError("denied")), \
                mock.patch("xauby.runtime.manual_orders.os.unlink") as unlink, \
                self.assertLogs("xauby.runtime.manual_orders", level="ERROR") as logs:
            self.assertIsNone(
                claim_manual_order_request("BTCUSDT", project_root=root)
            )

        unlink.assert_called_once()
        self.assertIn("Discarding unreadable manual order request", logs.output[0])


class ManualOrderEngineTests(unittest.TestCase):
    def _engine(self):
        from xauby.engine.loop import LoopMixin

        engine = LoopMixin()
        engine._project_root = "."
        engine.config = {"trading": {"max_open_positions": 1}}
        engine.last_log_message = ""
        engine._emit_event = mock.Mock()
        engine.send_telegram_alert = mock.Mock()
        engine.execute_buy = mock.Mock(return_value=True)
        engine.execute_open_short = mock.Mock(return_value=True)
        engine.execute_sell = mock.Mock(return_value=True)
        engine.execute_close_short = mock.Mock(return_value=True)
        engine._is_buy_blocked_by_cooldown = mock.Mock(return_value=(False, ""))
        spec = mock.Mock(allowed_sides=("long",))
        engine._pair_registry = mock.Mock()
        engine._pair_registry.get.return_value = spec
        engine._pair_registry.active.return_value = [mock.Mock(symbol="BTCUSDT")]
        engine.db = mock.Mock()
        engine.db.get_trade_state.return_value = {"state": "idle"}
        sc = mock.Mock()
        sc.feed_snapshot.return_value = {"feed_degraded": False}
        sc.blocks_new_entries.return_value = False
        engine._sc = mock.Mock(return_value=sc)
        return engine

    @mock.patch("xauby.engine.loop.claim_manual_order_request")
    def test_manual_sell_uses_tracked_state(self, claim):
        claim.return_value = {
            "request_id": "req1",
            "symbol": "BTCUSDT",
            "action": "SELL",
        }
        engine = self._engine()
        state = {"state": "bought", "position_side": "LONG", "quantity": 0.01}
        result = engine._process_manual_order_request(
            state, 60000.0, 500.0, symbol="BTCUSDT"
        )
        self.assertTrue(result)
        engine.execute_sell.assert_called_once_with(
            state,
            60000.0,
            trigger_reason="Manual SELL (TUI F8)",
            symbol="BTCUSDT",
        )

    @mock.patch("xauby.engine.loop.claim_manual_order_request")
    def test_manual_buy_rejects_when_position_exists(self, claim):
        claim.return_value = {
            "request_id": "req2",
            "symbol": "BTCUSDT",
            "action": "BUY",
        }
        engine = self._engine()
        result = engine._process_manual_order_request(
            {"state": "bought"}, 60000.0, 500.0, symbol="BTCUSDT"
        )
        self.assertFalse(result)
        engine.execute_buy.assert_not_called()

    @mock.patch("xauby.engine.loop.claim_manual_order_request")
    def test_manual_buy_passes_management_mode(self, claim):
        claim.return_value = {
            "request_id": "req3",
            "symbol": "BTCUSDT",
            "action": "BUY",
            "management_mode": "manual",
        }
        engine = self._engine()
        result = engine._process_manual_order_request(
            {"state": "idle"}, 60000.0, 500.0, symbol="BTCUSDT"
        )
        self.assertTrue(result)
        engine.execute_buy.assert_called_once_with(
            60000.0,
            500.0,
            symbol="BTCUSDT",
            management_mode="manual",
        )

    @mock.patch("xauby.engine.loop.claim_manual_order_request")
    def test_manual_short_passes_strategy_handoff_mode(self, claim):
        claim.return_value = {
            "request_id": "req4",
            "symbol": "BTCUSDT",
            "action": "SELL",
            "intent": "OPEN_SHORT",
            "management_mode": "strategy_handoff",
        }
        engine = self._engine()
        engine._pair_registry.get.return_value.manual_allowed_sides = ("long", "short")
        result = engine._process_manual_order_request(
            {"state": "idle"}, 60000.0, 500.0, symbol="BTCUSDT"
        )
        self.assertTrue(result)
        engine.execute_open_short.assert_called_once()
        self.assertEqual(
            engine.execute_open_short.call_args.kwargs["management_mode"],
            "strategy_handoff",
        )


if __name__ == "__main__":
    unittest.main()
