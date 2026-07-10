"""Tests for canonical 3-level trading config resolver."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.runtime.strategy_pair_config import backtest_data_proxy_for_symbol
from xauby.runtime.trading_config import (
    canonical_runtime_config,
    resolve_trading_config,
    strategy_name_for_symbol,
)


class TestTradingConfigResolver(unittest.TestCase):
    def test_canonical_runtime_config_exposes_symbols_and_exchange(self):
        with tempfile.TemporaryDirectory() as tmp:
            whitelist_path = os.path.join(tmp, "coin_whitelist.json")
            with open(whitelist_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "quote_asset": "USDT",
                        "assets": [
                            {
                                "symbol": "XAUT",
                                "enabled": True,
                                "strategy": "cdc_action_zone",
                                "primary_timeframe": "4h",
                                "confirm_timeframe": "1d",
                                "mode": "sim",
                            }
                        ],
                    },
                    f,
                )
            cfg = {
                "simulate_only": True,
                "read_only": False,
                "exchange": {"id": "binance_th"},
                "data": {
                    "hybrid_dynamic_coin_config": {
                        "whitelist_json_path": "coin_whitelist.json",
                        "require_supported_market": False,
                    }
                },
                "strategy": {
                    "active": "cdc_action_zone",
                    "config": {"cdc_action_zone": {"sl_atr_mult": 2.0}},
                },
                "portfolio": {
                    "quote_asset": "USDT",
                    "position_sizing": {
                        "risk_pct": 0.02,
                        "max_position_per_trade_pct": 25.0,
                        "min_order_amount": 10.0,
                    },
                },
            }

            view = canonical_runtime_config(cfg, project_root=tmp)
            data = view.to_dict()

        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["exchange_id"], "binance_th")
        self.assertEqual(data["quote_asset"], "USDT")
        self.assertTrue(data["simulate_only"])
        self.assertIn("XAUTUSDT", data["symbols"])
        self.assertEqual(data["symbols"]["XAUTUSDT"]["strategy_name"], "xauby_actionzone")
        self.assertEqual(data["symbols"]["XAUTUSDT"]["execution_mode"], "sim")
        self.assertEqual(data["symbols"]["XAUTUSDT"]["portfolio"]["risk_pct"], 0.02)
        self.assertNotIn("architecture", cfg)

    def test_strategy_symbols_override_without_whitelist_strategy_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "coin_whitelist.json"), "w", encoding="utf-8") as f:
                json.dump({"quote_asset": "USDT", "assets": [{"symbol": "XAUT"}]}, f)

            cfg = {
                "strategy": {
                    "active": "cdc_action_zone",
                    "config": {
                        "cdc_action_zone": {
                            "sl_atr_mult": 2.5,
                            "rsi_min": 0,
                            "rsi_max": 100,
                            "vol_min_ratio": 0,
                            "primary_timeframe": "4h",
                            "confirm_timeframe": "1d",
                        }
                    },
                    "symbols": {
                        "XAUTUSDT": {
                            "sl_atr_mult": 2.0,
                            "backtest_data_proxy": "PAXGUSDT",
                        }
                    },
                },
                "portfolio": {
                    "position_sizing": {
                        "risk_pct": 0.45,
                        "max_position_per_trade_pct": 45.0,
                        "min_order_amount": 10.0,
                    }
                },
            }

            eff = resolve_trading_config(
                cfg,
                "cdc_action_zone",
                symbol="XAUTUSDT",
                project_root=tmp,
                for_live=False,
            )

            self.assertEqual(eff.strategy["sl_atr_mult"], 2.0)
            self.assertEqual(eff.strategy["backtest_data_proxy"], "PAXGUSDT")
            self.assertEqual(eff.portfolio["risk_pct"], 0.45)
            self.assertEqual(eff.primary_timeframe, "4h")

    def test_backtest_proxy_prefers_bot_config_strategy_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bot_config.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "portfolio:\n"
                    "  quote_asset: USDT\n"
                    "strategy:\n"
                    "  symbols:\n"
                    "    XAUTUSDT:\n"
                    "      backtest_data_proxy: PAXGUSDT\n"
                )
            with open(os.path.join(tmp, "coin_whitelist.json"), "w", encoding="utf-8") as f:
                json.dump({"quote_asset": "USDT", "assets": [{"symbol": "XAUT"}]}, f)

            self.assertEqual(
                backtest_data_proxy_for_symbol("XAUTUSDT", project_root=tmp),
                "PAXGUSDT",
            )

    def test_symbol_can_select_strategy_without_leaking_selector_into_params(self):
        cfg = {
            "strategy": {
                "active": "cdc_action_zone",
                "config": {
                    "cdc_action_zone": {"sl_atr_mult": 2.5},
                    "ict_lite_strategy": {"sl_atr_mult": 2.0, "ema_fast": 20},
                },
                "symbols": {
                    "XAUTUSDT": {"strategy": "cdc_action_zone", "sl_atr_mult": 2.0},
                    "BTCUSDT": {
                        "strategy": "ict_lite_strategy",
                        "sl_atr_mult": 3.0,
                        "ema_fast": 21,
                    },
                },
            }
        }

        self.assertEqual(strategy_name_for_symbol(cfg, "XAUTUSDT"), "xauby_actionzone")
        self.assertEqual(strategy_name_for_symbol(cfg, "BTCUSDT"), "ict_lite_strategy")

        # Isolate from the repo's real coin_whitelist.json: the legacy pair
        # overlay (strategy_config_block -> merge_strategy_config) reads it from
        # project_root, and a live whitelist entry for BTCUSDT must not leak its
        # params into this synthetic-config assertion.
        with tempfile.TemporaryDirectory() as tmp:
            eff = resolve_trading_config(cfg, symbol="BTCUSDT", project_root=tmp)
        self.assertEqual(eff.strategy_name, "ict_lite_strategy")
        self.assertEqual(eff.strategy["sl_atr_mult"], 3.0)
        self.assertEqual(eff.strategy["ema_fast"], 21)
        self.assertNotIn("strategy", eff.strategy)

    def test_portfolio_symbol_budget_overrides_global_sizing(self):
        cfg = {
            "portfolio": {
                "position_sizing": {
                    "risk_pct": 0.45,
                    "max_position_per_trade_pct": 25.0,
                    "min_order_amount": 10.0,
                },
                "symbols": {
                    "BTCUSDT": {
                        "allocation_pct": 50.0,
                        "position_sizing": {
                            "risk_pct": 0.45,
                            "max_position_per_trade_pct": 20.0,
                            "min_order_amount": 12.0,
                        },
                    }
                },
            },
            "strategy": {
                "active": "cdc_action_zone",
                "config": {"cdc_action_zone": {}},
            },
        }

        xaut = resolve_trading_config(cfg, symbol="XAUTUSDT")
        btc = resolve_trading_config(cfg, symbol="BTCUSDT")

        self.assertEqual(xaut.portfolio["risk_pct"], 0.45)
        self.assertEqual(xaut.portfolio["max_position_per_trade_pct"], 25.0)
        self.assertEqual(btc.portfolio["risk_pct"], 0.45)
        self.assertEqual(btc.portfolio["max_position_per_trade_pct"], 20.0)
        self.assertEqual(btc.portfolio["min_order_amount"], 12.0)
        self.assertEqual(btc.portfolio["allocation_pct"], 50.0)

    def test_router_strategy_does_not_inherit_base_strategy_symbol_params(self):
        cfg = {
            "strategy": {
                "active": "xauby_donchian_trend",
                "config": {
                    "xauby_donchian_trend": {"vol_min_ratio": 0.0},
                    "bbkc_squeeze": {"fixed_tp_pct": 0.0},
                },
                "symbols": {
                    "BTCUSDT": {
                        "strategy": "xauby_donchian_trend",
                        "rsi_min": 45.0,
                        "rsi_max": 70.0,
                        "vol_min_ratio": 1.2,
                        "sl_atr_mult": 1.8,
                    }
                },
            }
        }

        eff = resolve_trading_config(cfg, "bbkc_squeeze", symbol="BTCUSDT")

        self.assertEqual(eff.strategy["rsi_min"], 40.0)
        self.assertEqual(eff.strategy["rsi_max"], 75.0)
        self.assertEqual(eff.strategy["vol_min_ratio"], 0.8)
        self.assertEqual(eff.strategy["sl_atr_mult"], 2.0)
        self.assertEqual(eff.strategy["trailing_atr_mult"], 1.5)
        self.assertTrue(eff.strategy["breakeven_sl_enabled"])


if __name__ == "__main__":
    unittest.main()
