import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from xauby.saas.app import create_app
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.strategy_pool import (
    DEFAULT_POLICY,
    append_candidate,
    candidate_from_preset,
    new_pool,
    promotion_eligibility,
    score_metrics,
)
from xauby.saas.supervisor import TenantSupervisor


def _preset(preset_id: str = "strategy-a") -> dict:
    return {
        "id": preset_id,
        "label": preset_id,
        "symbol": "BTCUSDT",
        "target_id": "okx-swap",
        "strategy": preset_id,
        "certification_status": "certified",
        "live_certified": True,
        "backtest": {
            "score_label": "PF 1.50",
            "max_drawdown_pct": 10,
            "trades": 100,
        },
    }


class StrategyPoolTests(unittest.TestCase):
    def test_score_matches_existing_selector_shape(self):
        self.assertEqual(
            score_metrics({
                "profit_factor": 1.5,
                "net_return_pct": 10,
                "max_drawdown_pct": 5,
                "trades": 30,
            }),
            160.5,
        )

    def test_pool_keeps_challengers_for_the_same_pair(self):
        pool = new_pool("BTCUSDT", "okx-swap", _preset())
        append_candidate(pool, _preset("strategy-b"))
        self.assertEqual(pool["champion_id"], "strategy-a")
        self.assertEqual([item["preset_id"] for item in pool["candidates"]], ["strategy-a", "strategy-b"])
        self.assertEqual(pool["candidates"][1]["mode"], "shadow")

    def test_promotion_requires_forward_sample_and_margin(self):
        eligible, reasons = promotion_eligibility(
            {
                "source": "forward_sim",
                "forward_days": DEFAULT_POLICY["min_forward_days"],
                "trades": DEFAULT_POLICY["min_forward_trades"],
                "profit_factor": 1.3,
                "max_drawdown_pct": 8,
                "score": 125,
            },
            champion_score=100,
            winning_evaluations=DEFAULT_POLICY["winning_evaluations"],
        )
        self.assertTrue(eligible, reasons)

        eligible, reasons = promotion_eligibility(
            {"source": "certificate", "score": 200},
            champion_score=100,
        )
        self.assertFalse(eligible)
        self.assertIn("forward SIM evaluation is required", reasons)

    def test_candidate_baseline_is_not_promotion_ready(self):
        candidate = candidate_from_preset(_preset(), role="challenger")
        self.assertFalse(candidate["eligible_for_promotion"])
        self.assertEqual(candidate["evaluation"]["source"], "certificate")


class StrategyPoolApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        project = Path(__file__).resolve().parents[1]
        settings = SaaSSettings(
            project_root=project,
            data_root=root / "data",
            tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime",
            database_path=root / "control.db",
            public_base_url="http://testserver",
            session_secret="test-session-secret-that-is-long-enough",
            systemctl_bin="mock",
            cookie_secure=False,
            dev_login_enabled=True,
        )
        self.store = ControlPlaneStore(settings.database_path)
        self.store.migrate()
        self.store.bootstrap_owner("owner@example.com", "owner-itsara")
        self.client = TestClient(create_app(settings, store=self.store, supervisor=TenantSupervisor(settings)))
        login = self.client.post("/auth/dev-login", params={"email": "owner@example.com"})
        self.headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_profile_creates_certified_pair_pool_without_changing_runtime(self):
        saved = self.client.put(
            "/api/v1/profile",
            headers=self.headers,
            json={
                "preset_ids": ["okx-xau-actionzone-v1"],
                "active_preset_id": "okx-xau-actionzone-v1",
                "risk": {},
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        response = self.client.get("/api/v1/strategy-pools")
        self.assertEqual(response.status_code, 200, response.text)
        pool = response.json()["pools"][0]
        self.assertEqual(pool["symbol"], "XAUUSDT")
        self.assertEqual(pool["champion_id"], "okx-xau-actionzone-v1")
        self.assertEqual(pool["candidates"][0]["evaluation"]["source"], "certificate")

    def test_candidate_add_requires_a_passing_certificate(self):
        response = self.client.post(
            "/api/v1/strategy-pools/BTCUSDT/candidates",
            headers=self.headers,
            json={"preset_id": "binance-btc-supertrend-v1"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("certified", response.json()["detail"])
