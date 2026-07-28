import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from xauby.track_record.generator import generate_report
from xauby.track_record.validator import TRACK_RECORD_EPOCH, audit_track_record


def _db(events, trades):
    """A stub LiteDB serving one event list and one closed-trade list."""
    db = MagicMock()
    db.query_events.side_effect = lambda event_type, **kwargs: [
        e for e in events if e["event_type"] == event_type
    ]
    db.get_closed_trades.return_value = trades
    return db


def _opened(symbol, ts, seq, price, qty, run="r1"):
    return {"event_type": "position_opened", "run_id": run, "ts": ts, "seq": seq,
            "symbol": symbol, "payload": {"entry_price": price, "quantity": qty}}


def _closed(symbol, ts, seq, price, run="r1"):
    return {"event_type": "position_closed", "run_id": run, "ts": ts, "seq": seq,
            "symbol": symbol, "payload": {"exit_price": price}}


def _restored(symbol, ts, seq, price, qty, opened_at, run="r2"):
    return {
        "event_type": "position_restored", "run_id": run, "ts": ts, "seq": seq,
        "symbol": symbol,
        "payload": {
            "entry_price": price, "quantity": qty, "opened_at": opened_at,
        },
    }


def _trade(symbol, closed_at, entry, exit_price, qty, pnl=0.0):
    return {"symbol": symbol, "amount": qty, "entry_price": entry,
            "exit_price": exit_price, "closed_at": closed_at, "net_pnl": pnl}


class TestReportGenerator(unittest.TestCase):
    def test_report_generator(self):
        # Use dates relative to now so both trades fall inside the period window
        # regardless of the current date (generate_report filters by closed_at).
        now = datetime.now(timezone.utc)
        d1 = now - timedelta(days=10)
        d2 = now - timedelta(days=5)
        trades = [
            {"net_pnl": 50.0, "entry_cost": 500.0, "net_pnl_pct": 10.0,
             "opened_at": d1.isoformat(), "closed_at": (d1 + timedelta(hours=2)).isoformat()},
            {"net_pnl": -20.0, "entry_cost": 500.0, "net_pnl_pct": -4.0,
             "opened_at": d2.isoformat(), "closed_at": (d2 + timedelta(hours=3)).isoformat()}
        ]
        report = generate_report(trades, period_days=30, report_name="Test 30d")
        self.assertEqual(report.total_trades, 2)
        self.assertEqual(report.win_rate, 50.0)
        self.assertEqual(report.net_pnl, 30.0)
        self.assertEqual(report.average_duration_hours, 2.5)

    def test_entry_drawdown_is_computed_not_hardcoded(self):
        # Every entry reported drawdown_pct 0.0 under a comment claiming it was
        # "calculated chronologically at entry level". It was not calculated.
        now = datetime.now(timezone.utc)
        trades = [
            {"net_pnl": 100.0, "entry_cost": 1000.0,
             "opened_at": (now - timedelta(days=9)).isoformat(),
             "closed_at": (now - timedelta(days=9)).isoformat()},
            {"net_pnl": -40.0, "entry_cost": 1000.0,
             "opened_at": (now - timedelta(days=8)).isoformat(),
             "closed_at": (now - timedelta(days=8)).isoformat()},
            {"net_pnl": 10.0, "entry_cost": 1000.0,
             "opened_at": (now - timedelta(days=7)).isoformat(),
             "closed_at": (now - timedelta(days=7)).isoformat()},
        ]
        report = generate_report(trades, period_days=30, report_name="dd")
        drawdowns = [e.drawdown_pct for e in report.entries]
        # With the real 1000 starting balance, peak equity is 1100 and the
        # 40 loss is a 3.6364% drawdown — not the old 40% obtained by silently
        # pretending the account started at zero.
        self.assertEqual(drawdowns[0], 0.0)
        self.assertAlmostEqual(drawdowns[1], 3.6364, places=4)
        self.assertAlmostEqual(drawdowns[2], 2.7273, places=4)

    def test_drawdown_is_chronological_regardless_of_input_order(self):
        now = datetime.now(timezone.utc)
        early = {"net_pnl": 100.0, "entry_cost": 1000.0,
                 "opened_at": (now - timedelta(days=9)).isoformat(),
                 "closed_at": (now - timedelta(days=9)).isoformat()}
        late = {"net_pnl": -40.0, "entry_cost": 1000.0,
                "opened_at": (now - timedelta(days=8)).isoformat(),
                "closed_at": (now - timedelta(days=8)).isoformat()}
        shuffled = generate_report([late, early], 30, "dd")
        by_pnl = {e.pnl: e.drawdown_pct for e in shuffled.entries}
        self.assertEqual(by_pnl[100.0], 0.0)
        self.assertAlmostEqual(by_pnl[-40.0], 3.6364, places=4)

    def test_report_uses_supplied_account_baseline(self):
        now = datetime.now(timezone.utc)
        trades = [{
            "net_pnl": -20.0,
            "entry_cost": 100.0,
            "opened_at": (now - timedelta(days=2)).isoformat(),
            "closed_at": (now - timedelta(days=1)).isoformat(),
        }]

        report = generate_report(
            trades, 30, "real balance", initial_balance=200.0
        )

        self.assertAlmostEqual(report.max_drawdown_pct, 10.0, places=4)
        self.assertAlmostEqual(report.entries[0].drawdown_pct, 10.0, places=4)

    def test_explicit_calendar_start_does_not_drop_first_day_hours(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
        trade = {
            "net_pnl": 1.0,
            "entry_cost": 100.0,
            "opened_at": "2026-07-01T00:05:00+00:00",
            "closed_at": "2026-07-01T00:10:00+00:00",
        }
        report = generate_report(
            [trade], 27, "calendar month", as_of=end, period_start=start
        )
        self.assertEqual(report.total_trades, 1)


class TestAuditorSinglePair(unittest.TestCase):
    def test_matching_events_and_trades_pass(self):
        db = _db(
            [_opened("XAUUSDT", "2026-07-25T12:00:00", 1, 2000.0, 1.0),
             _closed("XAUUSDT", "2026-07-25T14:00:00", 2, 2050.0)],
            [_trade("XAUUSDT", "2026-07-25T14:00:00", 2000.0, 2050.0, 1.0, 50.0)],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["matching_trades"], 1)
        self.assertEqual(stats["symbols"], ["XAUUSDT"])

    def test_engine_legacy_entry_exit_and_qty_field_names_are_understood(self):
        events = [
            {"event_type": "position_opened", "run_id": "r1",
             "ts": "2026-07-25T10:00:00", "seq": 1, "symbol": "XAUUSDT",
             "payload": {"entry": 100.0, "qty": 0.5}},
            {"event_type": "position_closed", "run_id": "r1",
             "ts": "2026-07-25T11:00:00", "seq": 2, "symbol": "XAUUSDT",
             "payload": {"exit": 110.0}},
        ]
        db = _db(
            events,
            [_trade("XAUUSDT", "2026-07-25T11:00:00", 100.0, 110.0, 0.5)],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["matching_trades"], 1)

    def test_a_price_mismatch_is_reported_with_its_symbol(self):
        db = _db(
            [_opened("XAUUSDT", "2026-07-25T12:00:00", 1, 2000.0, 1.0),
             _closed("XAUUSDT", "2026-07-25T14:00:00", 2, 2050.0)],
            [_trade("XAUUSDT", "2026-07-25T14:00:00", 2000.0, 1999.0, 1.0)],
        )
        success, discrepancies, _ = audit_track_record(db)
        self.assertFalse(success)
        self.assertIn("[XAUUSDT]", discrepancies[0])
        self.assertIn("exit price", discrepancies[0])

    def test_restored_position_seeds_a_new_run_without_duplicate_open(self):
        opened_at = "2026-07-24T10:00:00"
        db = _db(
            [_restored("BTCUSDT", "2026-07-25T10:00:00", 1, 100.0, 1.0, opened_at),
             _closed("BTCUSDT", "2026-07-25T12:00:00", 2, 90.0, "r2")],
            [_trade("BTCUSDT", "2026-07-25T12:00:00", 100.0, 90.0, 1.0)],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["matching_trades"], 1)

    def test_matching_restore_during_open_position_is_not_a_second_entry(self):
        db = _db(
            [_opened("BTCUSDT", "2026-07-24T10:00:00", 1, 100.0, 1.0, "r1"),
             _restored("BTCUSDT", "2026-07-25T10:00:00", 1, 100.0, 1.0,
                       "2026-07-24T10:00:00", "r2"),
             _closed("BTCUSDT", "2026-07-25T12:00:00", 2, 90.0, "r2")],
            [_trade("BTCUSDT", "2026-07-25T12:00:00", 100.0, 90.0, 1.0)],
        )
        success, discrepancies, _ = audit_track_record(db)
        self.assertTrue(success, discrepancies)

    def test_partial_close_retains_remaining_position_for_final_close(self):
        partial = {
            "event_type": "position_closed", "run_id": "r1",
            "ts": "2026-07-25T11:00:00", "seq": 2, "symbol": "XAUUSDT",
            "payload": {
                "exit": 110.0, "quantity": 0.4,
                "remaining_quantity": 0.6, "partial": True,
            },
        }
        db = _db(
            [_opened("XAUUSDT", "2026-07-25T10:00:00", 1, 100.0, 1.0),
             partial,
             _closed("XAUUSDT", "2026-07-25T12:00:00", 3, 120.0)],
            [_trade("XAUUSDT", "2026-07-25T11:00:00", 100.0, 110.0, 0.4),
             _trade("XAUUSDT", "2026-07-25T12:00:00", 100.0, 120.0, 0.6)],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["matching_trades"], 2)


class TestAuditorMultiPair(unittest.TestCase):
    """Two live pairs. Every one of these failed before P1.6."""

    def test_interleaved_positions_are_not_an_anomaly(self):
        # XAU opens, BTC opens, XAU closes, BTC closes. With one global
        # active_position this raised "fired while a position was already open"
        # and then paired XAU's entry with BTC's exit.
        db = _db(
            [_opened("XAUUSDT", "2026-07-25T10:00:00", 1, 2000.0, 1.0, "rx"),
             _opened("BTCUSDT", "2026-07-25T11:00:00", 2, 60000.0, 0.1, "rb"),
             _closed("XAUUSDT", "2026-07-25T12:00:00", 3, 2050.0, "rx"),
             _closed("BTCUSDT", "2026-07-25T13:00:00", 4, 61000.0, "rb")],
            [_trade("XAUUSDT", "2026-07-25T12:00:00", 2000.0, 2050.0, 1.0),
             _trade("BTCUSDT", "2026-07-25T13:00:00", 60000.0, 61000.0, 0.1)],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["matching_trades"], 2)
        self.assertEqual(stats["per_symbol"]["XAUUSDT"]["matching"], 1)
        self.assertEqual(stats["per_symbol"]["BTCUSDT"]["matching"], 1)

    def test_a_reopen_on_the_same_symbol_is_still_an_anomaly(self):
        db = _db(
            [_opened("XAUUSDT", "2026-07-25T10:00:00", 1, 2000.0, 1.0, "r1"),
             _opened("XAUUSDT", "2026-07-25T11:00:00", 2, 2010.0, 1.0, "r2")],
            [],
        )
        success, discrepancies, _ = audit_track_record(db)
        self.assertFalse(success)
        self.assertTrue(any("already had a position open" in d for d in discrepancies))

    def test_a_close_without_an_open_names_the_right_symbol(self):
        db = _db(
            [_opened("XAUUSDT", "2026-07-25T10:00:00", 1, 2000.0, 1.0),
             _closed("BTCUSDT", "2026-07-25T11:00:00", 2, 61000.0)],
            [],
        )
        success, discrepancies, _ = audit_track_record(db)
        self.assertFalse(success)
        self.assertTrue(any("[BTCUSDT]" in d and "no open position" in d
                            for d in discrepancies))

    def test_an_extra_trade_on_one_pair_does_not_corrupt_the_other(self):
        # Index alignment across a merged list made one extra event on one pair
        # shift every later comparison on every pair.
        db = _db(
            [_opened("XAUUSDT", "2026-07-25T10:00:00", 1, 2000.0, 1.0, "rx"),
             _closed("XAUUSDT", "2026-07-25T11:00:00", 2, 2050.0, "rx"),
             _opened("BTCUSDT", "2026-07-25T12:00:00", 3, 60000.0, 0.1, "rb"),
             _closed("BTCUSDT", "2026-07-25T13:00:00", 4, 61000.0, "rb")],
            # XAU has an extra database trade the event log does not know about.
            [_trade("XAUUSDT", "2026-07-25T09:00:00", 1990.0, 1995.0, 1.0),
             _trade("XAUUSDT", "2026-07-25T11:00:00", 2000.0, 2050.0, 1.0),
             _trade("BTCUSDT", "2026-07-25T13:00:00", 60000.0, 61000.0, 0.1)],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertFalse(success)
        self.assertTrue(any("[XAUUSDT] count mismatch" in d for d in discrepancies))
        # BTC is untouched by XAU's problem.
        self.assertEqual(stats["per_symbol"]["BTCUSDT"]["matching"], 1)
        self.assertFalse(any("[BTCUSDT]" in d for d in discrepancies))

    def test_an_open_position_is_reported_not_flagged(self):
        db = _db(
            [_opened("BTCUSDT", "2026-07-25T12:00:00", 1, 60000.0, 0.1)],
            [],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["open_at_audit"], ["BTCUSDT"])


class TestAuditorEpoch(unittest.TestCase):
    def test_position_crossing_epoch_is_seeded_before_its_close(self):
        trade = _trade(
            "XAUUSDT", "2026-07-20T12:00:00+00:00",
            4018.6, 4019.9, 0.029,
        )
        trade["opened_at"] = "2026-07-19T16:00:00+00:00"
        db = _db(
            [_closed(
                "XAUUSDT", "2026-07-20T12:00:00+00:00", 1, 4019.9,
            )],
            [trade],
        )

        success, discrepancies, stats = audit_track_record(db)

        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["matching_trades"], 1)
        self.assertEqual(stats["open_at_audit"], [])
        self.assertEqual(stats["epoch_seeded_symbols"], ["XAUUSDT"])

    def test_restore_hydrates_entry_missing_from_legacy_open_event(self):
        legacy_open = {
            "event_type": "position_opened",
            "run_id": "legacy",
            "ts": "2026-07-28T00:01:00+00:00",
            "seq": 1,
            "symbol": "BTCUSDT",
            "payload": {"qty": 0.0008, "position_side": "SHORT"},
        }
        restored = _restored(
            "BTCUSDT", "2026-07-28T04:02:33+00:00", 2,
            63733.9, 0.0008, "2026-07-28T00:01:00+00:00", "new-run",
        )
        db = _db([legacy_open, restored], [])

        success, discrepancies, stats = audit_track_record(db)

        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["open_at_audit"], ["BTCUSDT"])

    def test_pre_epoch_history_is_excluded(self):
        # XAUTUSDT on the old venue is not the same instrument as XAUUSDT on
        # OKX, so blending them into one track record compares nothing.
        db = _db(
            [_opened("XAUTUSDT", "2026-05-15T12:00:00", 1, 2000.0, 1.0),
             _closed("XAUTUSDT", "2026-05-15T14:00:00", 2, 2050.0)],
            [_trade("XAUTUSDT", "2026-05-15T14:00:00", 2000.0, 2050.0, 1.0)],
        )
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["events_analyzed"], 0)
        self.assertEqual(stats["database_count"], 0)
        self.assertEqual(stats["epoch"], TRACK_RECORD_EPOCH)

    def test_the_epoch_can_be_lifted_explicitly(self):
        db = _db(
            [_opened("XAUTUSDT", "2026-05-15T12:00:00", 1, 2000.0, 1.0),
             _closed("XAUTUSDT", "2026-05-15T14:00:00", 2, 2050.0)],
            [_trade("XAUTUSDT", "2026-05-15T14:00:00", 2000.0, 2050.0, 1.0)],
        )
        success, discrepancies, stats = audit_track_record(db, epoch=None)
        self.assertTrue(success, discrepancies)
        self.assertEqual(stats["matching_trades"], 1)


if __name__ == "__main__":
    unittest.main()
