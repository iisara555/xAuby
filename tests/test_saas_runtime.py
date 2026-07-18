import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

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
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE closed_trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, "
                "net_pnl REAL, total_fees REAL, closed_at TEXT)"
            )
            conn.executemany(
                "INSERT INTO closed_trades VALUES (?,?,?,?,?,?)",
                [(1, "XAUUSDT", "SHORT", 3.0, 0.2, "2026-07-15"),
                 (2, "BTCUSDT", "LONG", -1.0, 0.1, "2026-07-16")],
            )
        gateway = RuntimeGateway(self.settings, self.supervisor)
        payload = gateway.trades("customer-one", outcome="all", limit=50)
        self.assertEqual([item["symbol"] for item in payload["items"]], ["BTCUSDT", "XAUUSDT"])
        self.assertEqual(payload["summary"]["wins"], 1)
        self.assertEqual(payload["summary"]["losses"], 1)
        self.assertEqual(payload["summary"]["net_pnl"], 2.0)


if __name__ == "__main__":
    unittest.main()
