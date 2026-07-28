"""Withdrawal permission is asked, not assumed. Roadmap P2.4.

`/api/v1/exchange/test` used to set ``withdraw_disabled_attested = True``
unconditionally after any successful probe, and store it inside the
``capabilities`` dict beside things the probe had actually measured. Nothing had
ever asked the exchange. The probe itself was honest — it emitted
``withdraw_permission_checked: False`` — and the API layer overwrote that.

The tests that matter most here are the negative ones: every failure path must
report *unknown*, because the only outcome worse than not checking is reporting
a pass you did not earn.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.saas.app import withdrawal_permission_verified  # noqa: E402
from xauby.saas.withdraw_check import SUPPORTED, check_withdraw_disabled  # noqa: E402


class _Client:
    def __init__(self, exchange):
        self.exchange = exchange


def _okx(perm):
    exchange = MagicMock()
    exchange.privateGetAccountConfig.return_value = {"data": [{"perm": perm}]}
    return _Client(exchange)


def _binance(enabled):
    exchange = MagicMock()
    exchange.sapiGetAccountApiRestrictions.return_value = {"enableWithdrawals": enabled}
    return _Client(exchange)


class TestOkx(unittest.TestCase):
    def test_a_read_and_trade_key_is_confirmed_disabled(self):
        result = check_withdraw_disabled(_okx("read_only,trade"), "okx")
        self.assertIs(result["withdraw_disabled"], True)
        self.assertTrue(result["checked"])

    def test_a_key_with_withdraw_is_reported_as_enabled(self):
        # A finding, not a formality: this key can move funds out.
        result = check_withdraw_disabled(_okx("read_only,trade,withdraw"), "okx")
        self.assertIs(result["withdraw_disabled"], False)
        self.assertTrue(result["checked"])
        self.assertIn("ENABLED", result["detail"])

    def test_permissions_are_matched_case_and_space_insensitively(self):
        result = check_withdraw_disabled(_okx(" Read_Only , Trade "), "okx")
        self.assertIs(result["withdraw_disabled"], True)

    def test_an_empty_permission_string_is_unknown(self):
        result = check_withdraw_disabled(_okx(""), "okx")
        self.assertIsNone(result["withdraw_disabled"])
        self.assertFalse(result["checked"])

    def test_an_empty_data_array_is_unknown(self):
        exchange = MagicMock()
        exchange.privateGetAccountConfig.return_value = {"data": []}
        result = check_withdraw_disabled(_Client(exchange), "okx")
        self.assertIsNone(result["withdraw_disabled"])


class TestBinance(unittest.TestCase):
    def test_withdrawals_off_is_confirmed(self):
        self.assertIs(
            check_withdraw_disabled(_binance(False), "binance")["withdraw_disabled"], True)

    def test_withdrawals_on_is_reported(self):
        result = check_withdraw_disabled(_binance(True), "binance")
        self.assertIs(result["withdraw_disabled"], False)
        self.assertIn("ENABLED", result["detail"])

    def test_the_futures_venue_uses_the_same_endpoint(self):
        self.assertIs(
            check_withdraw_disabled(_binance(False), "binanceusdm")["withdraw_disabled"], True)

    def test_a_response_without_the_field_is_unknown(self):
        exchange = MagicMock()
        exchange.sapiGetAccountApiRestrictions.return_value = {"somethingElse": 1}
        result = check_withdraw_disabled(_Client(exchange), "binance")
        self.assertIsNone(result["withdraw_disabled"])
        self.assertFalse(result["checked"])


class TestEveryFailurePathIsUnknown(unittest.TestCase):
    """None of these may ever produce True. That is the whole point."""

    def test_an_unsupported_venue(self):
        result = check_withdraw_disabled(_okx("read_only"), "kraken")
        self.assertIsNone(result["withdraw_disabled"])
        self.assertFalse(result["checked"])
        self.assertIn("kraken", result["detail"])

    def test_a_venue_that_raises(self):
        exchange = MagicMock()
        exchange.privateGetAccountConfig.side_effect = RuntimeError("network down")
        result = check_withdraw_disabled(_Client(exchange), "okx")
        self.assertIsNone(result["withdraw_disabled"])
        self.assertFalse(result["checked"])
        self.assertIn("network down", result["detail"])

    def test_a_key_not_permitted_to_read_its_own_restrictions(self):
        # Being unable to read the restriction is not evidence that it is safe.
        exchange = MagicMock()
        exchange.sapiGetAccountApiRestrictions.side_effect = PermissionError("denied")
        result = check_withdraw_disabled(_Client(exchange), "binance")
        self.assertIsNone(result["withdraw_disabled"])

    def test_a_client_with_no_venue_handle(self):
        result = check_withdraw_disabled(object(), "okx")
        self.assertIsNone(result["withdraw_disabled"])
        self.assertFalse(result["checked"])

    def test_an_empty_exchange_id(self):
        result = check_withdraw_disabled(_okx("read_only"), "")
        self.assertIsNone(result["withdraw_disabled"])

    def test_no_failure_path_returns_true(self):
        exchange = MagicMock()
        exchange.privateGetAccountConfig.side_effect = Exception("boom")
        exchange.sapiGetAccountApiRestrictions.side_effect = Exception("boom")
        broken = _Client(exchange)
        for venue in list(SUPPORTED) + ["kraken", "", None]:
            with self.subTest(venue=venue):
                self.assertIsNot(
                    check_withdraw_disabled(broken, venue)["withdraw_disabled"], True)


class TestApiKeepsTheClaimApartFromTheCheck(unittest.TestCase):
    def test_live_gate_requires_both_checked_and_disabled(self):
        safe = {
            "capabilities": {
                "withdraw_permission_checked": True,
                "withdraw_disabled_verified": True,
            }
        }
        self.assertTrue(withdrawal_permission_verified(safe))
        for capabilities in (
            {},
            {"withdraw_permission_checked": False, "withdraw_disabled_verified": True},
            {"withdraw_permission_checked": True, "withdraw_disabled_verified": False},
            {"withdraw_permission_checked": True, "withdraw_disabled_verified": None},
        ):
            with self.subTest(capabilities=capabilities):
                self.assertFalse(
                    withdrawal_permission_verified({"capabilities": capabilities})
                )

    def test_the_endpoint_no_longer_asserts_an_unconditional_pass(self):
        import inspect

        from xauby.saas import app as app_module

        source = inspect.getsource(app_module)
        self.assertNotIn('capabilities["withdraw_disabled_attested"] = True', source,
                         "a self-declared checkbox is being stored as a probed capability")
        self.assertIn('capabilities["withdraw_disabled_verified"]', source)

    def test_the_probe_reports_what_it_actually_checked(self):
        import inspect

        from xauby.saas import probe

        source = inspect.getsource(probe)
        self.assertIn("check_withdraw_disabled", source)
        self.assertIn("withdraw_permission_checked", source)


if __name__ == "__main__":
    unittest.main()
