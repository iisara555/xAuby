import json
import os
import tempfile
import unittest
from unittest.mock import patch

from xauby.ui.textual_tui.state_sync import (
    bot_state_fingerprint,
    bot_state_file_path,
    fallback_bot_state,
    load_bot_state_from_disk,
    checklist_panel_fingerprint,
    portfolio_panel_fingerprint,
    positions_panel_fingerprint,
    header_fingerprint,
)


class TestTextualStateSync(unittest.TestCase):
    def test_header_renders_exchange_from_runtime_state(self):
        from xauby.ui.textual_tui.widgets import AppHeader

        state = {
            "symbol": "BTCUSDT",
            "current_price": 100.0,
            "simulate_only": False,
            "strategy_name": "cdc_action_zone",
            "exchange": {"id": "kraken", "name": "kraken"},
        }
        header = AppHeader()
        header._state = state
        self.assertIn("KRAKEN", header._build_text(120).plain)
        self.assertIn("KRAKEN", header._build_text(45).plain)

        changed = json.loads(json.dumps(state))
        changed["exchange"] = {"id": "coinbase", "name": "coinbase"}
        self.assertNotEqual(
            header_fingerprint(state, 120),
            header_fingerprint(changed, 120),
        )

    def test_checklist_fingerprint_tracks_threshold_and_strategy_changes(self):
        base = {
            "symbol": "BTCUSDT",
            "signal_meta": {
                "strategy_name": "bbkc_squeeze",
                "action": "HOLD",
                "reason": "waiting",
                "checklist": [
                    {
                        "label": "RSI",
                        "value": "55.0",
                        "ok": True,
                        "hint": "40-75",
                        "bar": {"low": 40.0, "high": 75.0, "value": 55.0},
                    }
                ],
            },
        }
        threshold_changed = json.loads(json.dumps(base))
        threshold_changed["signal_meta"]["checklist"][0]["hint"] = "45-70"
        strategy_changed = json.loads(json.dumps(base))
        strategy_changed["signal_meta"]["strategy_name"] = "bbrsi_mean_reversion"

        self.assertNotEqual(
            checklist_panel_fingerprint(base, 100),
            checklist_panel_fingerprint(threshold_changed, 100),
        )
        self.assertNotEqual(
            checklist_panel_fingerprint(base, 100),
            checklist_panel_fingerprint(strategy_changed, 100),
        )

    def test_chart_and_bot_fingerprints_track_closed_candle(self):
        from xauby.ui.textual_tui.state_sync import chart_panel_fingerprint

        base = {
            "symbol": "BTCUSDT",
            "strategy_name": "bbkc_squeeze",
            "primary_timeframe": "1h",
            "current_price": 63000.0,
            "last_candle_timestamp": 100,
        }
        changed = dict(base, last_candle_timestamp=200)

        self.assertNotEqual(
            chart_panel_fingerprint(base, 100),
            chart_panel_fingerprint(changed, 100),
        )
        self.assertNotEqual(
            bot_state_fingerprint(base, {}, "BTCUSDT"),
            bot_state_fingerprint(changed, {}, "BTCUSDT"),
        )
    def test_fallback_state_has_symbol(self):
        st = fallback_bot_state(focus_symbol="btcusdt")
        self.assertEqual(st["symbol"], "BTCUSDT")

    def test_load_missing_file_returns_empty(self):
        with patch.object(
            __import__("xauby.ui.textual_tui.state_sync", fromlist=["state_sync"]),
            "bot_state_file_path",
            return_value="/nonexistent/xauby_bot_state.json",
        ):
            state, env, focus, pairs = load_bot_state_from_disk()
        self.assertEqual(state, {})
        self.assertEqual(focus, "")

    def test_load_reads_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "core", "logs")
            os.makedirs(logs)
            path = os.path.join(logs, "xauby_bot_state.json")
            payload = {
                "symbol": "XAUTUSDT",
                "regime": {"label": "RANGE"},
                "run_id": "run-abc",
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            old = os.getcwd()
            try:
                os.chdir(tmp)
                state, _, focus, _ = load_bot_state_from_disk()
            finally:
                os.chdir(old)
            self.assertEqual(state.get("symbol"), "XAUTUSDT")
            self.assertTrue(bot_state_file_path().endswith("xauby_bot_state.json"))

    def test_stale_state_is_not_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "core", "logs")
            os.makedirs(logs)
            path = os.path.join(logs, "xauby_bot_state.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"symbol": "BTCUSDT", "strategy_name": "donchian_trend"}, f)
            os.utime(path, (1, 1))
            old = os.getcwd()
            try:
                os.chdir(tmp)
                state, envelope, focus, pairs = load_bot_state_from_disk()
            finally:
                os.chdir(old)
        self.assertEqual((state, envelope, focus, pairs), ({}, {}, "", []))

    def test_fingerprint_tracks_detailed_regime(self):
        base = {
            "symbol": "BTCUSDT",
            "current_price": 100.0,
            "regime": {
                "regime": "LOW_VOL_RANGE",
                "confidence": 0.65,
                "gold_score": 65,
                "phase": "ACCUMULATION",
                "risk_state": "QUIET_RISK",
                "trend_strength": "CHOPPY",
                "volatility_state": "QUIET",
                "liquidity_state": "SURGE",
                "transition_risk": "LOW",
                "strategy_bias": {"family": "range_or_accumulation", "posture": "selective"},
            },
        }
        changed = json.loads(json.dumps(base))
        changed["regime"]["risk_state"] = "CHOP_RISK"
        changed["regime"]["strategy_bias"]["family"] = "chop_filter"

        self.assertNotEqual(
            bot_state_fingerprint(base, {}, "BTCUSDT"),
            bot_state_fingerprint(changed, {}, "BTCUSDT"),
        )

    def test_positions_fingerprint_tracks_position_state_changes(self):
        idle = {
            "by_symbol": {
                "XAUTUSDT": {
                    "position": {
                        "state": "idle",
                        "quantity": 0.0,
                        "entry_price": 0.0,
                        "stop_loss": 0.0,
                    }
                }
            }
        }
        bought = {
            "by_symbol": {
                "XAUTUSDT": {
                    "position": {
                        "state": "bought",
                        "quantity": 0.0113,
                        "entry_price": 4217.34,
                        "stop_loss": 4179.08,
                        "unrealized_pnl": 0.12,
                        "opened_at": "2026-06-14T12:11:40",
                        "stop_loss_order_id": "112474021",
                    }
                }
            }
        }
        self.assertNotEqual(
            positions_panel_fingerprint(idle, 100),
            positions_panel_fingerprint(bought, 100),
        )

    def test_positions_fingerprint_tracks_live_mark_price(self):
        base = {
            "by_symbol": {
                "BTCUSDT": {
                    "current_price": 65525.0,
                    "bid": 65524.5,
                    "ask": 65525.5,
                    "position": {
                        "state": "bought",
                        "quantity": 0.00073,
                        "entry_price": 65432.96,
                        "stop_loss": 64768.48,
                        "highest_price_seen": 65818.27,
                        "unrealized_pnl": 0.07,
                        "unrealized_pnl_pct": 0.14,
                    },
                }
            }
        }
        changed = json.loads(json.dumps(base))
        changed["by_symbol"]["BTCUSDT"]["current_price"] = 65526.0

        self.assertNotEqual(
            positions_panel_fingerprint(base, 100),
            positions_panel_fingerprint(changed, 100),
        )

    def test_portfolio_fingerprint_tracks_equity_and_wallet_changes(self):
        base = {
            "aggregate": {"total_equity_usdt": 100.0, "open_positions": 1},
            "by_symbol": {
                "XAUTUSDT": {
                    "current_price": 4300.0,
                    "equity_breakdown": {
                        "portfolio_total_usdt": 100.0,
                        "usdt_balance_usdt": 50.0,
                        "base_quantity": 0.01,
                        "base_value_usdt": 43.0,
                        "symbol_exposure_usdt": 43.0,
                    },
                    "portfolio": {"USDT": 50.0, "XAUT": 0.01},
                }
            },
        }

        equity_changed = json.loads(json.dumps(base))
        equity_changed["aggregate"]["total_equity_usdt"] = 101.0

        wallet_changed = json.loads(json.dumps(base))
        wallet_changed["by_symbol"]["XAUTUSDT"]["portfolio"]["USDT"] = 49.5

        self.assertNotEqual(
            portfolio_panel_fingerprint(base, 100),
            portfolio_panel_fingerprint(equity_changed, 100),
        )
        self.assertNotEqual(
            portfolio_panel_fingerprint(base, 100),
            portfolio_panel_fingerprint(wallet_changed, 100),
        )

    def test_bot_fingerprint_tracks_portfolio_wallet_changes(self):
        state = {"symbol": "XAUTUSDT", "current_price": 4300.0}
        base = {
            "aggregate": {"total_equity_usdt": 100.0, "open_positions": 1},
            "by_symbol": {
                "XAUTUSDT": {
                    "current_price": 4300.0,
                    "position": {
                        "state": "bought",
                        "quantity": 0.01,
                        "entry_price": 4200.0,
                    },
                    "equity_breakdown": {
                        "portfolio_total_usdt": 100.0,
                        "usdt_balance_usdt": 50.0,
                        "base_quantity": 0.01,
                        "base_value_usdt": 43.0,
                        "symbol_exposure_usdt": 43.0,
                    },
                    "portfolio": {"USDT": 50.0, "XAUT": 0.01},
                }
            },
        }
        changed = json.loads(json.dumps(base))
        changed["by_symbol"]["XAUTUSDT"]["portfolio"]["USDT"] = 49.5
        changed["by_symbol"]["XAUTUSDT"]["equity_breakdown"]["usdt_balance_usdt"] = 49.5

        self.assertNotEqual(
            bot_state_fingerprint(state, base, "XAUTUSDT"),
            bot_state_fingerprint(state, changed, "XAUTUSDT"),
        )

    def test_bot_fingerprint_tracks_position_state_changes(self):
        state = {"symbol": "XAUTUSDT", "current_price": 4217.34}
        idle_env = {
            "by_symbol": {
                "XAUTUSDT": {"position": {"state": "idle", "quantity": 0.0}}
            }
        }
        bought_env = {
            "by_symbol": {
                "XAUTUSDT": {
                    "position": {
                        "state": "bought",
                        "quantity": 0.0113,
                        "entry_price": 4217.34,
                    }
                }
            }
        }
        self.assertNotEqual(
            bot_state_fingerprint(state, idle_env, "XAUTUSDT"),
            bot_state_fingerprint(state, bought_env, "XAUTUSDT"),
        )


if __name__ == "__main__":
    unittest.main()
