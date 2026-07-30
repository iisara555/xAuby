import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import yaml

from xauby.saas.runtime import RuntimeGateway
from xauby.saas.settings import SaaSSettings
from xauby.saas.supervisor import TenantSupervisor


class RuntimeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = SaaSSettings(
            project_root=root,
            data_root=root / "data",
            tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime",
            database_path=root / "control.db",
            public_base_url="http://testserver",
            session_secret="test-session-secret",
            systemctl_bin="mock",
            cookie_secure=False,
            dev_login_enabled=True,
        )
        self.supervisor = TenantSupervisor(self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_whitelist_ip_list_parses_comma_separated_env(self):
        base = dict(
            project_root=Path(self.temp.name),
            data_root=Path(self.temp.name) / "d",
            tenant_config_root=Path(self.temp.name) / "c",
            tenant_runtime_root=Path(self.temp.name) / "r",
            database_path=Path(self.temp.name) / "x.db",
            public_base_url="http://t",
            session_secret="s",
        )
        self.assertEqual(
            SaaSSettings(**base, api_whitelist_ips=" 203.0.113.7, 198.51.100.9 ").whitelist_ip_list(),
            ["203.0.113.7", "198.51.100.9"],
        )
        self.assertEqual(SaaSSettings(**base).whitelist_ip_list(), [])

    def test_snapshot_reads_tenant_runtime_directory(self):
        gateway = RuntimeGateway(self.settings, self.supervisor)
        payload = gateway.snapshot("customer-one")
        self.assertEqual(payload["source"], "tenant_engine")
        self.assertFalse(payload["read_only"])

    def test_native_snapshot_exposes_portfolio_currency(self):
        tenant_runtime = self.supervisor.runtime_dir("customer-one")
        runtime = tenant_runtime / "logs"
        runtime.mkdir(parents=True)
        (tenant_runtime / "usd_thb_rate.json").write_text(json.dumps({
            "rate": 33.4,
            "source": "test-cache",
        }))
        (runtime / "xauby_bot_state.json").write_text(json.dumps({
            "focus_symbol": "XAUUSDT",
            "total_equity_usdt": 180.31,
            "portfolio": {"USDT": 180.31, "XAU": 0.0},
            "equity_breakdown": {
                "portfolio_total_usdt": 180.31,
                "usdt_balance_usdt": 180.31,
                "base_asset": "XAU",
                "base_quantity": 0.0,
                "base_value_usdt": 0.0,
                "unrealized_pnl_usdt": 2.39,
                "symbol_exposure_usdt": 2.39,
            },
        }))
        payload = RuntimeGateway(self.settings, self.supervisor).snapshot("customer-one")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["currency"]["equity_usdt"], 180.31)
        self.assertEqual(payload["currency"]["symbol_exposure_usdt"], 2.39)
        self.assertEqual(payload["currency"]["usd_thb_rate"], 33.4)
        self.assertEqual(payload["currency"]["rate_source"], "tenant_cache")
        self.assertAlmostEqual(payload["currency"]["equity_thb"], 6022.354)
        self.assertAlmostEqual(payload["currency"]["unrealized_pnl_thb"], 79.826)

    def test_native_trade_log_defaults_to_all_symbols_and_summarizes(self):
        path = self.supervisor.runtime_dir("customer-one") / "xauby.db"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "CREATE TABLE closed_trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, "
                "net_pnl REAL, total_fees REAL, closed_at TEXT)"
            )
            conn.executemany(
                "INSERT INTO closed_trades VALUES (?,?,?,?,?,?)",
                [(1, "XAUUSDT", "SHORT", 3.0, 0.2, "2026-07-15"),
                 (2, "BTCUSDT", "LONG", -1.0, 0.1, "2026-07-16")],
            )
            conn.commit()
        gateway = RuntimeGateway(self.settings, self.supervisor)
        payload = gateway.trades("customer-one", outcome="all", limit=50)
        self.assertEqual([item["symbol"] for item in payload["items"]], ["BTCUSDT", "XAUUSDT"])
        self.assertEqual(payload["summary"]["wins"], 1)
        self.assertEqual(payload["summary"]["losses"], 1)
        self.assertEqual(payload["summary"]["net_pnl"], 2.0)

    def test_native_candles_use_the_symbols_strategy_indicator_registry(self):
        slug = "customer-one"
        config_dir = self.supervisor.config_dir(slug)
        config_dir.mkdir(parents=True)
        (config_dir / "bot_config.yaml").write_text(
            yaml.safe_dump({
                "architecture": {
                    "indicator_registry_enabled": True,
                    "strategy_chart_indicators": {
                        "supertrend_ema200": ["supertrend", "ema200"],
                    },
                },
                "strategy": {
                    "active": "supertrend_ema200",
                    "config": {
                        "supertrend_ema200": {
                            "ema_period": 200,
                            "atr_period": 10,
                            "supertrend_mult": 4.0,
                        },
                    },
                },
            }),
            encoding="utf-8",
        )
        logs = self.supervisor.runtime_dir(slug) / "logs"
        logs.mkdir(parents=True)
        (logs / "xauby_bot_state.json").write_text(json.dumps({
            "focus_symbol": "BTCUSDT",
            "by_symbol": {
                "BTCUSDT": {
                    "strategy_name": "supertrend_ema200",
                    "signal_meta": {"strategy_name": "supertrend_ema200"},
                },
            },
        }), encoding="utf-8")

        path = self.supervisor.runtime_dir(slug) / "xauby.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "CREATE TABLE prices (symbol TEXT, timeframe TEXT, timestamp INTEGER, "
                "open REAL, high REAL, low REAL, close REAL, volume REAL)"
            )
            rows = []
            for index in range(260):
                close = 60_000 + index * 15
                rows.append((
                    "BTCUSDT", "4h", 1_700_000_000 + index * 14_400,
                    close - 20, close + 80, close - 90, close, 100 + index,
                ))
            conn.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?)", rows)
            conn.commit()

        payload = RuntimeGateway(self.settings, self.supervisor).candles(
            slug, symbol="BTCUSDT", timeframe="4h", limit=120
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["strategy_name"], "supertrend_ema200")
        self.assertEqual(len(payload["candles"]), 120)
        self.assertEqual(
            [item["key"] for item in payload["chart"]["lines"]],
            ["supertrend", "ema200"],
        )
        self.assertIn("supertrend", payload["candles"][-1])
        self.assertIn("ema200", payload["candles"][-1])
        self.assertNotEqual(payload["candles"][0]["ema200"], payload["candles"][0]["close"])
        self.assertNotIn("ema12", payload["candles"][-1])


if __name__ == "__main__":
    unittest.main()
