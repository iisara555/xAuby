import unittest

from xauby.analytics.realised_parity import build_realised_parity_report


def _trade(**overrides):
    row = {
        "symbol": "BTCUSDT",
        "amount": 1.0,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "total_fees": 0.105,
        "funding_fee": 0.0,
        "net_pnl": 8.95,
        "closed_at": "2026-07-28T01:00:00+00:00",
    }
    row.update(overrides)
    return row


def _event(seq, event_type, payload):
    return {
        "run_id": "r1",
        "seq": seq,
        "ts": f"2026-07-28T00:{seq:02d}:00+00:00",
        "event_type": event_type,
        "symbol": "BTCUSDT",
        "payload": payload,
    }


class TestRealisedParity(unittest.TestCase):
    def test_reconciles_fees_slippage_and_net_pnl(self):
        report = build_realised_parity_report(
            [_trade()],
            [
                _event(1, "position_opened", {"slippage_bps": 1.0}),
                _event(2, "position_closed", {"slippage_bps": 1.0}),
            ],
            fee_rates={"BTCUSDT": 0.0005},
            model_slippage_bps=2.0,
            tolerance_bps=10.0,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["trade_count"], 1)
        self.assertEqual(report["slippage_coverage_pct"], 100.0)
        row = report["trades"][0]
        self.assertAlmostEqual(row["model_fees"], 0.105)
        self.assertAlmostEqual(row["actual_slippage_cost"], 0.021)
        self.assertAlmostEqual(row["model_slippage_cost"], 0.042)
        self.assertAlmostEqual(row["variance_pnl"], 0.021)

    def test_restored_position_is_honestly_incomplete(self):
        report = build_realised_parity_report(
            [_trade()],
            [
                _event(1, "position_restored", {"position_side": "SHORT"}),
                _event(2, "position_closed", {"slippage_bps": 3.0}),
            ],
            fee_rates={"*": 0.0005},
            model_slippage_bps=2.0,
        )

        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertEqual(report["slippage_coverage_pct"], 0.0)
        self.assertIsNone(report["trades"][0]["entry_slippage_bps"])

    def test_restore_does_not_erase_retained_open_slippage(self):
        report = build_realised_parity_report(
            [_trade()],
            [
                _event(1, "position_opened", {"slippage_bps": 1.0}),
                _event(2, "position_restored", {"position_side": "SHORT"}),
                _event(3, "position_closed", {"slippage_bps": 3.0}),
            ],
            fee_rates={"*": 0.0005},
            model_slippage_bps=2.0,
        )
        self.assertEqual(report["slippage_coverage_pct"], 100.0)
        self.assertEqual(report["trades"][0]["entry_slippage_bps"], 1.0)

    def test_no_trades_is_not_a_false_failure(self):
        report = build_realised_parity_report(
            [], [], fee_rates={"*": 0.0005}, model_slippage_bps=2.0
        )
        self.assertEqual(report["status"], "no_trades")
        self.assertEqual(report["trade_count"], 0)

    def test_partial_close_keeps_entry_observation_for_the_remainder(self):
        report = build_realised_parity_report(
            [_trade(closed_at="2026-07-28T00:02:00+00:00"), _trade()],
            [
                _event(1, "position_opened", {"slippage_bps": 1.0}),
                _event(2, "position_closed", {"slippage_bps": 2.0, "partial": True}),
                _event(3, "position_closed", {"slippage_bps": 3.0}),
            ],
            fee_rates={"*": 0.0005},
            model_slippage_bps=2.0,
        )
        self.assertEqual(report["slippage_coverage_pct"], 100.0)
        self.assertEqual(
            [row["entry_slippage_bps"] for row in report["trades"]], [1.0, 1.0]
        )


if __name__ == "__main__":
    unittest.main()
