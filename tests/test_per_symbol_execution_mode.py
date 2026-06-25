"""Per-symbol execution mode resolution tests."""
import unittest

from xauby.runtime.pair_registry import PairRegistry, _parse_execution_mode, _pair_fields_from_entry


class TestPerSymbolExecutionMode(unittest.TestCase):
    def test_parse_mode(self):
        self.assertEqual(_parse_execution_mode({"mode": "sim"}), "sim")
        self.assertEqual(_parse_execution_mode({"mode": "live"}), "live")
        self.assertIsNone(_parse_execution_mode({}))

    def test_pair_fields_from_whitelist(self):
        fields = _pair_fields_from_entry(
            {
                "mode": "sim",
                "regime_router_enabled": True,
                "regime_router_live_confirmed": False,
                "sim_fee_pct": 0.1,
            }
        )
        self.assertEqual(fields["execution_mode"], "sim")
        self.assertTrue(fields["regime_router_enabled"])
        self.assertAlmostEqual(fields["sim_fee_pct"], 0.001)


if __name__ == "__main__":
    unittest.main()
