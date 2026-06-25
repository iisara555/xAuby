import unittest
from unittest.mock import MagicMock

from xauby.ui.tradelog import _compute_tradelog_layout
from xauby.ui.tradelog_presenter import build_tradelog_view_model


class TestTradelogLayout(unittest.TestCase):
    def test_layout_shrinks_on_small_body(self):
        layout = _compute_tradelog_layout(12, is_phone=True, is_wide=False, trade_count=50)
        self.assertLessEqual(layout["max_trades"], 10)
        self.assertIsInstance(layout["show_analytics"], bool)

    def test_empty_trades_view_model(self):
        db = MagicMock()
        db.get_closed_trades.return_value = []
        vm = build_tradelog_view_model(
            db,
            {"symbol": "BTCUSDT", "regime": {}},
            100,
            40,
        )
        self.assertEqual(vm.total_trades, 0)
        self.assertEqual(vm.table_rows, [])

    def test_trade_cards_include_execution_context_and_small_qty(self):
        db = MagicMock()
        db.get_closed_trades.return_value = [
            {
                "id": 7,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "amount": 0.00074,
                "entry_price": 64440.94,
                "exit_price": 63990.0,
                "entry_cost": 47.6862956,
                "gross_exit": 47.3526,
                "total_fees": 0.0,
                "net_pnl": -0.3336956,
                "net_pnl_pct": -0.6997725,
                "trigger": "Exchange-Side Stop Loss",
                "opened_at": "2026-06-13T22:00:37.964366",
                "closed_at": "2026-06-14T14:34:45.680423",
                "entry_regime": "BULL_TREND_WEAK",
                "exit_regime": "BULL_TREND_WEAK",
                "strategy_name": "donchian_trend",
                "execution_mode": "live",
            }
        ]

        vm = build_tradelog_view_model(
            db,
            {"symbol": "BTCUSDT", "regime": {}},
            100,
            40,
        )
        text = "\n".join(vm.trade_card_lines)

        self.assertIn("BTCUSDT", text)
        self.assertIn("0.00074", text)
        self.assertIn("Exchange-Side Stop", text)
        self.assertIn("BULL_WEAK", text)

    def test_trade_cards_keep_partial_exits_separate(self):
        db = MagicMock()
        db.get_closed_trades.return_value = [
            {
                "id": 7,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "amount": 0.00074,
                "entry_price": 64440.94,
                "exit_price": 63990.0,
                "entry_cost": 47.6862956,
                "gross_exit": 47.3526,
                "net_pnl": -0.3336956,
                "net_pnl_pct": -0.6997725,
                "trigger": "Exchange-Side Stop Loss",
                "opened_at": "2026-06-13T22:00:37.964366",
                "closed_at": "2026-06-14T14:34:45.680423",
                "entry_regime": "BULL_TREND_WEAK",
                "exit_regime": "BULL_TREND_WEAK",
                "strategy_name": "donchian_trend",
                "execution_mode": "live",
            },
            {
                "id": 6,
                "symbol": "BTCUSDT",
                "side": "SELL",
                "amount": 0.00073,
                "entry_price": 64440.94,
                "exit_price": 64648.39,
                "entry_cost": 47.0418862,
                "gross_exit": 47.1933247,
                "net_pnl": 0.1514385,
                "net_pnl_pct": 0.3219226783,
                "trigger": "Manual reduce 50%",
                "opened_at": "2026-06-13T22:00:37.964366",
                "closed_at": "2026-06-14T01:19:24.754854",
                "entry_regime": "BULL_TREND_WEAK",
                "exit_regime": "BULL_TREND_WEAK",
                "strategy_name": "manual_reduce",
                "execution_mode": "live",
            },
        ]

        vm = build_tradelog_view_model(
            db,
            {"symbol": "BTCUSDT", "regime": {}},
            70,
            40,
        )
        text = "\n".join(vm.trade_card_lines)

        self.assertEqual(len(vm.trade_card_lines), 2)
        self.assertIn("-0.334U", text)
        self.assertIn("+0.151U", text)
        self.assertIn("SL", text)
        self.assertIn("Manual", text)

    def test_tradelog_filter_and_all_scope(self):
        db = MagicMock()
        db.get_closed_trades.return_value = [
            {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "amount": 1,
                "entry_price": 100,
                "exit_price": 90,
                "entry_cost": 100,
                "gross_exit": 90,
                "net_pnl": -10,
                "net_pnl_pct": -10,
                "trigger": "Exchange-Side Stop Loss",
                "opened_at": "2026-06-13T00:00:00",
                "closed_at": "2026-06-13T01:00:00",
                "execution_mode": "live",
            },
            {
                "symbol": "XAUTUSDT",
                "side": "SELL",
                "amount": 1,
                "entry_price": 100,
                "exit_price": 110,
                "entry_cost": 100,
                "gross_exit": 110,
                "net_pnl": 10,
                "net_pnl_pct": 10,
                "trigger": "Manual reduce 50%",
                "opened_at": "2026-06-13T00:00:00",
                "closed_at": "2026-06-13T02:00:00",
                "execution_mode": "live",
            },
        ]

        vm = build_tradelog_view_model(
            db,
            {"symbol": "BTCUSDT", "regime": {}},
            70,
            40,
            focus_symbol="ALL",
            is_multi=True,
            trade_filter="manual",
        )
        text = "\n".join(vm.trade_card_lines)

        self.assertEqual(vm.scope_label, "All pairs")
        self.assertEqual(vm.filter_label, "Manual exits")
        self.assertIn("XAUT", text)
        self.assertNotIn("BTCUSDT", text)


if __name__ == "__main__":
    unittest.main()
