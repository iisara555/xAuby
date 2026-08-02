import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from xauby.saas.app import StrategyEvaluationBody, _explicit_flat_runtime_snapshot, create_app
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.strategy_pool import (
    DEFAULT_POLICY,
    append_candidate,
    append_evaluation,
    candidate_from_preset,
    evaluation_provenance_reasons,
    evaluations_comparable,
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

    @staticmethod
    def _forward_metrics(
        run_id: str,
        *,
        score_boost: float = 0.0,
        config_fingerprint: str = "b" * 16,
    ) -> dict:
        return {
            "forward_days": 30,
            "trades": 30,
            "profit_factor": 1.4 + score_boost,
            "net_return_pct": 8.0,
            "max_drawdown_pct": 8.0,
            "run_id": run_id,
            "artifact_sha256": "a" * 64,
            "config_fingerprint": config_fingerprint,
            "venue": "okx",
            "timeframe": "4h",
            "data_window_start": "2026-01-01",
            "data_window_end": "2026-02-01",
            "fill_model": "candle_close_v1",
            "fees_pct": 0.05,
            "slippage_pct": 0.02,
        }

    def test_forward_history_derives_streak_and_is_idempotent(self):
        pool = new_pool("BTCUSDT", "okx-swap", _preset())
        append_candidate(pool, _preset("strategy-b"))
        champion_fp = pool["candidates"][0]["certificate_config_fingerprint"]
        challenger_fp = pool["candidates"][1]["certificate_config_fingerprint"]
        append_evaluation(
            pool, "strategy-a", self._forward_metrics("champion-1", config_fingerprint=champion_fp)
        )
        for index in range(3):
            pool, record, inserted = append_evaluation(
                pool,
                "strategy-b",
                self._forward_metrics(
                    f"challenger-{index}", score_boost=0.4, config_fingerprint=challenger_fp
                ),
            )
            self.assertTrue(inserted)
        challenger = next(item for item in pool["candidates"] if item["preset_id"] == "strategy-b")
        self.assertEqual(challenger["winning_evaluations"], 3)
        self.assertTrue(challenger["eligible_for_promotion"])
        self.assertTrue(record["eligible_for_promotion"])

        _, duplicate, inserted = append_evaluation(
            pool,
            "strategy-b",
            self._forward_metrics(
                "challenger-2", score_boost=0.4, config_fingerprint=challenger_fp
            ),
        )
        self.assertFalse(inserted)
        self.assertEqual(duplicate["run_id"], "challenger-2")
        self.assertEqual(len(pool["evaluation_history"]), 4)

    def test_incomplete_forward_result_cannot_be_promotion_ready(self):
        pool = new_pool("BTCUSDT", "okx-swap", _preset())
        append_candidate(pool, _preset("strategy-b"))
        pool, record, _ = append_evaluation(
            pool,
            "strategy-b",
            {
                "forward_days": 30,
                "trades": 30,
                "profit_factor": 1.5,
                "net_return_pct": 10,
                "max_drawdown_pct": 5,
                "winning_evaluations": 3,
            },
        )
        self.assertFalse(record["eligible_for_promotion"])
        self.assertEqual(record["winning_evaluations"], 0)
        self.assertTrue(evaluation_provenance_reasons(record))

    def test_flat_runtime_check_is_fail_closed(self):
        base = {
            "ok": True,
            "read_only": False,
            "stale": False,
            "age_sec": 1,
            "state": {
                "by_symbol": {
                    "BTCUSDT": {
                        "position": {
                            "state": "idle",
                            "quantity": 0,
                            "exchange_position_id": None,
                        }
                    }
                }
            },
        }
        self.assertTrue(_explicit_flat_runtime_snapshot(base, "BTCUSDT"))
        self.assertFalse(_explicit_flat_runtime_snapshot({**base, "stale": True}, "BTCUSDT"))
        bought = {
            **base,
            "state": {
                "by_symbol": {
                    "BTCUSDT": {
                        "position": {"state": "bought", "quantity": 0.01}
                    }
                }
            },
        }
        self.assertFalse(_explicit_flat_runtime_snapshot(bought, "BTCUSDT"))
        self.assertFalse(_explicit_flat_runtime_snapshot({**base, "age_sec": "nan"}, "BTCUSDT"))
        self.assertFalse(
            _explicit_flat_runtime_snapshot(
                {
                    **base,
                    "state": {
                        "by_symbol": {
                            "BTCUSDT": {"position": {"state": "idle", "quantity": "nan"}}
                        }
                    },
                },
                "BTCUSDT",
            )
        )

    def test_zero_cost_forward_runs_have_valid_provenance(self):
        metrics = self._forward_metrics("zero-cost")
        metrics["source"] = "forward_sim"
        metrics["fees_pct"] = 0.0
        metrics["slippage_pct"] = 0.0
        self.assertEqual(evaluation_provenance_reasons(metrics), [])
        self.assertTrue(evaluations_comparable(metrics, metrics))

    def test_evaluation_body_rejects_non_finite_numbers(self):
        with self.assertRaises(ValueError):
            StrategyEvaluationBody(
                forward_days=30,
                trades=20,
                profit_factor=float("nan"),
                net_return_pct=1,
                max_drawdown_pct=2,
            )


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

    def test_evaluation_endpoint_ignores_claimed_streak_without_provenance(self):
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
        pools = self.client.get("/api/v1/strategy-pools")
        self.assertEqual(pools.status_code, 200, pools.text)
        response = self.client.patch(
            "/api/v1/strategy-pools/XAUUSDT/candidates/okx-xau-actionzone-v1/evaluation",
            headers=self.headers,
            json={
                "forward_days": 30,
                "trades": 30,
                "profit_factor": 1.4,
                "net_return_pct": 8,
                "max_drawdown_pct": 8,
                "winning_evaluations": 3,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["eligible"])
        self.assertEqual(body["evaluation"]["winning_evaluations"], 0)
        self.assertTrue(any("run id" in reason for reason in body["reasons"]))

    def test_strategy_pool_save_rejects_stale_revision(self):
        user, tenant = self.store.bootstrap_owner("revision@example.com", "revision-owner")
        pool = new_pool("BTCUSDT", "okx-swap", _preset())
        saved = self.store.save_strategy_pool(tenant["id"], user["id"], pool)
        self.assertEqual(saved["revision"], 1)
        with self.assertRaises(ControlPlaneStore.StrategyPoolConflict):
            self.store.save_strategy_pool(tenant["id"], user["id"], pool)
