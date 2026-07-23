import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import yaml
from fastapi.testclient import TestClient

from xauby.saas.app import create_app
from xauby.saas.security import totp_code
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor


class SaaSControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        project = Path(__file__).resolve().parents[1]
        self.settings = SaaSSettings(
            project_root=project,
            data_root=root / "data",
            tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime",
            database_path=root / "control.db",
            public_base_url="http://testserver",
            session_secret="test-session-secret-that-is-long-enough",
            max_active_engines=3,
            systemctl_bin="mock",
            cookie_secure=False,
            dev_login_enabled=True,
            live_activation_enabled=True,
        )
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        self.store.bootstrap_owner("owner@example.com", "owner-itsara")
        self.supervisor = TenantSupervisor(self.settings)
        self.app = create_app(self.settings, store=self.store, supervisor=self.supervisor)
        self.client = TestClient(self.app)
        response = self.client.post("/auth/dev-login", params={"email": "owner@example.com"})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json()["csrf_token"]
        self.headers = {"X-CSRF-Token": self.csrf}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_owner_has_admin_and_personal_tenant(self):
        response = self.client.get("/api/v1/me")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["role"], "platform_admin")
        self.assertEqual(payload["tenant"]["slug"], "owner-itsara")
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/admin/tenants").status_code, 200)

    def test_profile_appearance_updates_name_and_private_avatar(self):
        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"profile-image"
        ).decode("ascii")
        updated = self.client.patch(
            "/api/v1/profile/appearance",
            headers=self.headers,
            json={
                "display_name": "Itsara Pilot",
                "avatar_data_url": f"data:image/png;base64,{png}",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        me = self.client.get("/api/v1/me").json()
        self.assertEqual(me["display_name"], "Itsara Pilot")
        self.assertTrue(me["avatar_url"].startswith("/api/v1/profile/avatar?v="))
        avatar = self.client.get(me["avatar_url"])
        self.assertEqual(avatar.status_code, 200)
        self.assertEqual(avatar.headers["content-type"], "image/png")

    def test_catalog_exposes_backtest_evidence_without_inventing_missing_scores(self):
        presets = {
            item["id"]: item for item in self.client.get("/api/v1/catalog").json()["presets"]
        }
        xau = presets["okx-xau-actionzone-v1"]["backtest"]
        self.assertEqual(xau["score_label"], "PF 1.7")
        self.assertEqual(xau["duration"], "2.6 years · full cycle")
        self.assertEqual(presets["okx-xau-actionzone-v1"]["allowed_sides"], ["long", "short"])
        self.assertTrue(presets["okx-xau-actionzone-v1"]["cdc_pure_certified"])
        self.assertFalse(presets["okx-xau-actionzone-v1"]["stop_loss_required"])
        self.assertEqual(presets["binance-btc-supertrend-v1"]["backtest"]["status"], "insufficient")
        okx_btc = presets["okx-btc-supertrend-v1"]
        self.assertEqual(okx_btc["primary_timeframe"], "4h")
        self.assertEqual(okx_btc["allowed_sides"], ["long", "short"])
        self.assertEqual(okx_btc["backtest"]["score_label"], "+9.8% OOS")
        self.assertEqual(okx_btc["allocation_pct"], 30.0)
        self.assertEqual(self.client.get("/api/v1/catalog").json()["risk"]["max_daily_loss_pct"], {
            "default": 6.0,
            "min": 1.0,
            "max": 6.0,
        })

    def test_compiled_profile_exposes_pair_allocations_and_guardrails(self):
        saved = self.client.put(
            "/api/v1/profile",
            headers=self.headers,
            json={
                "preset_ids": [
                    "okx-xau-actionzone-v1",
                    "okx-btc-supertrend-v1",
                ],
                "active_preset_id": "okx-xau-actionzone-v1",
                "risk": {},
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        compiled = self.client.get("/api/v1/profile").json()["compiled"]
        self.assertEqual(compiled["max_daily_loss_pct"], 6.0)
        self.assertEqual(compiled["max_daily_trades"], 3)
        self.assertTrue(compiled["drawdown_guard_enabled"])
        self.assertEqual(compiled["max_drawdown_pct"], 25.0)
        self.assertEqual(
            compiled["pair_allocations"], {"XAUUSDT": 65.0, "BTCUSDT": 30.0}
        )
        self.assertEqual(
            compiled["pair_position_caps"], {"XAUUSDT": 25.0, "BTCUSDT": 25.0}
        )

    def test_runtime_snapshot_is_tenant_scoped_and_fail_closed(self):
        response = self.client.get("/api/v1/runtime/snapshot")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "tenant_engine")
        self.assertTrue(payload["stale"])
        self.assertNotIn("path", payload)

        runtime = self.supervisor.runtime_dir("owner-itsara") / "logs"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "xauby_bot_state.json").write_text(
            json.dumps({"focus_symbol": "XAUUSDT", "position_side": "FLAT"}),
            encoding="utf-8",
        )
        fresh = self.client.get("/api/v1/runtime/snapshot")
        self.assertEqual(fresh.status_code, 200)
        self.assertTrue(fresh.json()["ok"])
        self.assertEqual(fresh.json()["state"]["focus_symbol"], "XAUUSDT")

    def test_runtime_price_is_tenant_scoped_and_lightweight(self):
        runtime = self.supervisor.runtime_dir("owner-itsara") / "logs"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "xauby_bot_state.json").write_text(
            json.dumps({
                "focus_symbol": "XAUUSDT",
                "by_symbol": {
                    "XAUUSDT": {
                        "current_price": 3991.25,
                        "bid": 3991.2,
                        "ask": 3991.3,
                        "timestamp": "2026-07-17T00:00:00",
                    }
                },
            }),
            encoding="utf-8",
        )

        response = self.client.get(
            "/api/v1/runtime/price", params={"symbol": "XAUUSDT"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["symbol"], "XAUUSDT")
        self.assertEqual(payload["price"], 3991.25)
        self.assertNotIn("state", payload)
        self.assertNotIn("currency", payload)

    def test_runtime_queries_validate_public_inputs(self):
        bad_symbol = self.client.get(
            "/api/v1/runtime/candles", params={"symbol": "../secret", "timeframe": "4h"}
        )
        self.assertEqual(bad_symbol.status_code, 422)
        bad_timeframe = self.client.get(
            "/api/v1/runtime/candles", params={"symbol": "XAUUSDT", "timeframe": "99h"}
        )
        self.assertEqual(bad_timeframe.status_code, 422)
        bad_price_symbol = self.client.get(
            "/api/v1/runtime/price", params={"symbol": "../secret"}
        )
        self.assertEqual(bad_price_symbol.status_code, 422)

    def test_backend_root_exposes_service_metadata_only(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "xauby-control")

    def test_write_endpoint_requires_csrf(self):
        response = self.client.post("/api/v1/bot/start")
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/v1/bot/start", headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def test_curated_config_is_bounded_and_revisioned(self):
        updated = self.client.patch(
            "/api/v1/bot/config", headers=self.headers,
            json={"risk_pct": 0.01, "max_position_per_trade_pct": 10,
                  "max_daily_loss_pct": 3},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], 1)
        high_allocation = self.client.patch(
            "/api/v1/bot/config", headers=self.headers,
            json={"max_position_per_trade_pct": 95},
        )
        self.assertEqual(high_allocation.status_code, 200, high_allocation.text)
        self.assertEqual(high_allocation.json()["config"]["max_position_per_trade_pct"], 95.0)
        denied = self.client.patch(
            "/api/v1/bot/config", headers=self.headers, json={"risk_pct": 0.5}
        )
        self.assertEqual(denied.status_code, 422)

    def test_manual_trading_is_not_part_of_the_pilot(self):
        preview = self.client.post(
            "/api/v1/orders/preview", headers=self.headers,
            json={"symbol": "XAUUSDT", "intent": "OPEN_LONG"},
        )
        self.assertEqual(preview.status_code, 404)
        self.assertFalse(self.client.get("/api/v1/catalog").json()["features"]["manual_trading"])

    def test_fourth_active_tenant_is_queued(self):
        self.store.update_tenant(
            self.store.tenant_for_user(self.store.session(self.client.cookies.get("xauby_saas_session"))["id"])["id"],
            status="running",
        )
        for index in range(2):
            user, _ = self.store.upsert_google_user(f"u{index}@example.com", f"sub-{index}")
            tenant, _ = self.store.ensure_tenant(user["id"], f"user-{index}")
            self.store.update_tenant(tenant["id"], status="running")
        user, _ = self.store.upsert_google_user("queued@example.com", "sub-queued")
        tenant, _ = self.store.ensure_tenant(user["id"], "queued-user")
        self.assertEqual(self.store.active_count(), 3)
        self.assertEqual(tenant["status"], "queued")

    def test_live_approval_requires_tested_connection(self):
        me = self.client.get("/api/v1/me").json()
        tenant_id = me["tenant"]["id"]
        self.store.request_live(tenant_id, me["id"])
        response = self.client.post(
            f"/api/v1/admin/tenants/{tenant_id}/approve-live", headers=self.headers
        )
        self.assertEqual(response.status_code, 409)

    def test_self_service_live_materializes_credentials_only_while_running(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        connected = self.client.post(
            "/api/v1/exchange/connect", headers=self.headers,
            json={"target_id": "okx-swap", "api_key": "test-key-1234",
                  "api_secret": "test-secret-value", "passphrase": "test-passphrase",
                  "withdraw_disabled_attested": True},
        )
        self.assertEqual(connected.status_code, 200, connected.text)
        with self.store.connection() as conn:
            envelope = conn.execute(
                "SELECT credential_blob FROM exchange_connections WHERE tenant_id=?",
                (tenant["id"],),
            ).fetchone()[0]
        self.assertNotIn("test-secret-value", envelope)
        profile = self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={"preset_ids": ["okx-xau-actionzone-v1"],
                  "active_preset_id": "okx-xau-actionzone-v1", "risk": {}},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        connection = self.store.exchange_connection(tenant["id"])
        self.store.set_exchange_connection(
            tenant["id"], "okx", connection["key_last4"], target_id="okx-swap",
            status="tested", capabilities={"swap": True},
        )
        self.client.post("/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"})
        response = self.client.post(
            "/api/v1/live/activate", headers=self.headers,
            json={"trade_pin": "12345678", "risk_acknowledged": True},
        )
        self.assertEqual(response.status_code, 200)
        config_dir = self.supervisor.config_dir(tenant["slug"])
        config = yaml.safe_load((config_dir / "bot_config.yaml").read_text(encoding="utf-8"))
        whitelist = json.loads((config_dir / "coin_whitelist.json").read_text(encoding="utf-8"))
        self.assertFalse(config["simulate_only"])
        self.assertTrue(all(asset["mode"] == "live" for asset in whitelist["assets"]))
        self.assertFalse((config_dir / "secrets.env").exists())
        ephemeral = self.supervisor.credential_path(tenant["slug"])
        self.assertIn("test-secret-value", ephemeral.read_text(encoding="utf-8"))
        stopped = self.client.post("/api/v1/live/deactivate", headers=self.headers)
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(ephemeral.exists())

    def test_saving_the_same_live_profile_is_idempotent(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        payload = {
            "preset_ids": ["okx-xau-actionzone-v1"],
            "active_preset_id": "okx-xau-actionzone-v1",
            "risk": {},
        }
        first = self.client.put("/api/v1/profile", headers=self.headers, json=payload)
        self.assertEqual(first.status_code, 200, first.text)

        self.supervisor.set_live_mode(tenant["slug"], "okx-swap")
        self.store.update_tenant(tenant["id"], status="running", live_status="active")

        with patch.object(self.supervisor, "stop") as stop, \
                patch.object(self.supervisor, "apply_profile", wraps=self.supervisor.apply_profile) as apply:
            saved = self.client.put("/api/v1/profile", headers=self.headers, json=payload)

        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["mode"], "live")
        self.assertFalse(saved.json()["live_reapproval_required"])
        self.assertFalse(saved.json()["profile_changed"])
        stop.assert_not_called()
        apply.assert_not_called()

        config = yaml.safe_load(
            (self.supervisor.config_dir(tenant["slug"]) / "bot_config.yaml").read_text(encoding="utf-8")
        )
        self.assertFalse(config["simulate_only"])

    def test_live_certified_pair_is_hot_added_without_stopping_existing_pair(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        xau_payload = {
            "preset_ids": ["okx-xau-actionzone-v1"],
            "active_preset_id": "okx-xau-actionzone-v1",
            "risk": {},
        }
        first = self.client.put("/api/v1/profile", headers=self.headers, json=xau_payload)
        self.assertEqual(first.status_code, 200, first.text)
        # Profiles saved before the multi-pair release did not carry the XAU
        # allocation metadata and can contain an older certified preset
        # snapshot. Adding BTC must still take the safe current-catalog path.
        legacy_profile = self.store.trading_profile(tenant["id"])
        legacy_profile["presets"][0].pop("allocation_pct", None)
        legacy_profile["presets"][0]["allowed_sides"] = ["long"]
        legacy_profile["presets"][0]["execution_profile"]["fresh_zone_window"] = 1
        self.store.save_trading_profile(tenant["id"], me["id"], legacy_profile)
        self.supervisor.set_live_mode(tenant["slug"], "okx-swap")
        self.store.update_tenant(tenant["id"], status="running", live_status="active")

        expanded_payload = {
            "preset_ids": ["okx-xau-actionzone-v1", "okx-btc-supertrend-v1"],
            "active_preset_id": "okx-xau-actionzone-v1",
            "risk": {},
        }
        with patch.object(self.supervisor, "stop") as stop, \
                patch.object(self.supervisor, "restart") as restart:
            expanded = self.client.put(
                "/api/v1/profile", headers=self.headers, json=expanded_payload
            )

        self.assertEqual(expanded.status_code, 200, expanded.text)
        self.assertEqual(expanded.json()["mode"], "live")
        self.assertTrue(expanded.json()["live_preserved"])
        self.assertFalse(expanded.json()["live_reapproval_required"])
        self.assertEqual(expanded.json()["hot_reload_eta_seconds"], 30)
        stop.assert_not_called()
        restart.assert_not_called()

        refreshed_tenant = self.store.tenant_by_slug(tenant["slug"])
        self.assertEqual(refreshed_tenant["live_status"], "active")
        self.assertEqual(refreshed_tenant["status"], "running")
        config_dir = self.supervisor.config_dir(tenant["slug"])
        config = yaml.safe_load((config_dir / "bot_config.yaml").read_text(encoding="utf-8"))
        whitelist = json.loads((config_dir / "coin_whitelist.json").read_text(encoding="utf-8"))
        self.assertFalse(config["simulate_only"])
        self.assertEqual(config["trading"]["max_open_positions"], 2)
        self.assertEqual({item["symbol"] for item in whitelist["assets"]}, {"XAU", "BTC"})
        self.assertTrue(all(item["mode"] == "live" for item in whitelist["assets"]))

    def test_removing_a_live_pair_still_requires_reapproval(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        two_pairs = {
            "preset_ids": ["okx-xau-actionzone-v1", "okx-btc-supertrend-v1"],
            "active_preset_id": "okx-xau-actionzone-v1",
            "risk": {},
        }
        first = self.client.put("/api/v1/profile", headers=self.headers, json=two_pairs)
        self.assertEqual(first.status_code, 200, first.text)
        self.supervisor.set_live_mode(tenant["slug"], "okx-swap")
        self.store.update_tenant(tenant["id"], status="running", live_status="active")

        removed = self.client.put(
            "/api/v1/profile",
            headers=self.headers,
            json={
                "preset_ids": ["okx-xau-actionzone-v1"],
                "active_preset_id": "okx-xau-actionzone-v1",
                "risk": {},
            },
        )

        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["mode"], "simulation")
        self.assertFalse(removed.json()["live_preserved"])
        self.assertTrue(removed.json()["live_reapproval_required"])
        refreshed_tenant = self.store.tenant_by_slug(tenant["slug"])
        self.assertEqual(refreshed_tenant["live_status"], "not_requested")
        self.assertEqual(refreshed_tenant["status"], "stopped")

    def test_manual_order_preview_uses_saved_profile(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        profile = self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={"preset_ids": ["okx-xau-actionzone-v1"],
                  "active_preset_id": "okx-xau-actionzone-v1",
                  "risk": {"max_position_per_trade_pct": 95}},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.store.update_tenant(tenant["id"], status="running")

        runtime = self.supervisor.runtime_dir(tenant["slug"]) / "logs"
        runtime.mkdir(parents=True, exist_ok=True)
        state_path = runtime / "xauby_bot_state.json"
        state_path.write_text(
            json.dumps({"focus_symbol": "XAUUSDT", "current_price": 4000.0,
                        "total_equity_usdt": 10000.0,
                        "position": {"state": "idle"}}),
            encoding="utf-8",
        )

        object.__setattr__(self.settings, "manual_trading_enabled", True)
        with patch.object(self.supervisor, "status", return_value="active"):
            preview = self.client.post(
                "/api/v1/orders/preview", headers=self.headers,
                json={"symbol": "XAUUSDT", "intent": "OPEN_LONG"},
            )

        self.assertEqual(preview.status_code, 200, preview.text)
        payload = preview.json()["preview"]
        self.assertEqual(payload["mode"], "simulation")
        self.assertEqual(payload["management_mode"], "strategy_handoff")
        self.assertEqual(payload["sizing_mode"], "cdc_pure")
        self.assertAlmostEqual(payload["allocation_pct"], 65.0)
        self.assertAlmostEqual(payload["estimated_notional"], 6500.0)
        self.assertAlmostEqual(payload["estimated_quantity"], 1.625)

    def test_manual_order_preview_keeps_multi_pair_position_sizes_distinct(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        profile = self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={
                "preset_ids": [
                    "okx-xau-actionzone-v1",
                    "okx-btc-supertrend-v1",
                ],
                "active_preset_id": "okx-xau-actionzone-v1",
                "risk": {},
            },
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.store.update_tenant(tenant["id"], status="running")

        runtime = self.supervisor.runtime_dir(tenant["slug"]) / "logs"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "xauby_bot_state.json").write_text(
            json.dumps({
                "focus_symbol": "XAUUSDT",
                "by_symbol": {
                    "XAUUSDT": {
                        "current_price": 4000.0,
                        "total_equity_usdt": 10000.0,
                        "position": {"state": "idle"},
                    },
                    "BTCUSDT": {
                        "current_price": 50000.0,
                        "total_equity_usdt": 10000.0,
                        "position": {"state": "idle"},
                    },
                },
            }),
            encoding="utf-8",
        )

        object.__setattr__(self.settings, "manual_trading_enabled", True)
        with patch.object(self.supervisor, "status", return_value="active"):
            xau_long = self.client.post(
                "/api/v1/orders/preview", headers=self.headers,
                json={"symbol": "XAUUSDT", "intent": "OPEN_LONG"},
            )
            xau_short = self.client.post(
                "/api/v1/orders/preview", headers=self.headers,
                json={"symbol": "XAUUSDT", "intent": "OPEN_SHORT"},
            )
            btc_long = self.client.post(
                "/api/v1/orders/preview", headers=self.headers,
                json={"symbol": "BTCUSDT", "intent": "OPEN_LONG"},
            )

        for response in (xau_long, xau_short, btc_long):
            self.assertEqual(response.status_code, 200, response.text)
        for response in (xau_long, xau_short):
            payload = response.json()["preview"]
            self.assertAlmostEqual(payload["allocation_pct"], 65.0)
            self.assertAlmostEqual(payload["estimated_notional"], 6500.0)
        btc_payload = btc_long.json()["preview"]
        self.assertAlmostEqual(btc_payload["allocation_pct"], 25.0)
        self.assertAlmostEqual(btc_payload["estimated_notional"], 2500.0)
        # No indicators in this fixture's snapshot -> ATR unavailable -> the
        # preview fell back to the fixed-percent heuristic (and, here, that
        # heuristic's notional happened to exceed the allocation cap, so the
        # cap — not the fallback distance — produced the 2500.0 above).
        self.assertEqual(btc_payload["sizing_basis"], "fixed_pct")

    def test_manual_order_preview_sizes_from_live_atr(self):
        # Audit F-3: with a live ATR present, the preview must size off
        # ATR * the preset's own sl_atr_mult (matching how the certified
        # strategy itself stops), not the old synthetic mark*2% distance.
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        profile = self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={"preset_ids": ["okx-btc-supertrend-v1"],
                  "active_preset_id": "okx-btc-supertrend-v1",
                  "risk": {}},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.store.update_tenant(tenant["id"], status="running")

        runtime = self.supervisor.runtime_dir(tenant["slug"]) / "logs"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "xauby_bot_state.json").write_text(
            json.dumps({
                "focus_symbol": "BTCUSDT",
                "by_symbol": {
                    "BTCUSDT": {
                        "current_price": 50000.0,
                        "total_equity_usdt": 10000.0,
                        "position": {"state": "idle"},
                        # supertrend_ema200 names its ATR indicator "atr".
                        "indicators": {"atr": 1500.0},
                    },
                },
            }),
            encoding="utf-8",
        )

        object.__setattr__(self.settings, "manual_trading_enabled", True)
        with patch.object(self.supervisor, "status", return_value="active"):
            preview = self.client.post(
                "/api/v1/orders/preview", headers=self.headers,
                json={"symbol": "BTCUSDT", "intent": "OPEN_LONG"},
            )

        self.assertEqual(preview.status_code, 200, preview.text)
        payload = preview.json()["preview"]
        self.assertEqual(payload["sizing_mode"], "risk_based")
        self.assertEqual(payload["sizing_basis"], "atr")
        # risk_amount 10000*0.02=200; stop_distance = atr 1500 * sl_atr_mult
        # 3.0 = 4500; risk_sized = (200/4500)*50000 = 2222.22 — comfortably
        # under the 25% allocation cap (2500), so this value is driven by the
        # ATR math, not clamped by the cap.
        self.assertAlmostEqual(payload["estimated_notional"], 2222.222222, places=3)
        self.assertLess(payload["estimated_notional"], 2500.0)

    def test_live_manual_side_is_independent_of_actionzone_colour(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        profile = self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={"preset_ids": ["okx-xau-actionzone-v1"],
                  "active_preset_id": "okx-xau-actionzone-v1", "risk": {}},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.store.update_tenant(
            tenant["id"], status="running", live_status="active"
        )

        runtime = self.supervisor.runtime_dir(tenant["slug"]) / "logs"
        runtime.mkdir(parents=True, exist_ok=True)
        state_path = runtime / "xauby_bot_state.json"

        def preview(zone, intent):
            state_path.write_text(
                json.dumps({
                    "focus_symbol": "XAUUSDT",
                    "current_price": 4000.0,
                    "total_equity_usdt": 10000.0,
                    "position": {"state": "idle"},
                    "indicators": {"cdc_zone_4h": zone},
                }),
                encoding="utf-8",
            )
            return self.client.post(
                "/api/v1/orders/preview", headers=self.headers,
                json={"symbol": "XAUUSDT", "intent": intent},
            )

        object.__setattr__(self.settings, "manual_trading_enabled", True)
        with patch.object(self.supervisor, "status", return_value="active"):
            long_in_red = preview("RED", "OPEN_LONG")
            short_in_green = preview("GREEN", "OPEN_SHORT")

        self.assertEqual(long_in_red.status_code, 200, long_in_red.text)
        self.assertEqual(long_in_red.json()["preview"]["side"], "LONG")
        self.assertEqual(short_in_green.status_code, 200, short_in_green.text)
        self.assertEqual(short_in_green.json()["preview"]["side"], "SHORT")
        self.assertEqual(short_in_green.json()["preview"]["mode"], "live")

    def _create_password_user(self, email: str, password: str) -> dict:
        user, _ = self.store.create_password_user(email, password)
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE users SET email_verified=1,account_status='active' WHERE id=?",
                (user["id"],),
            )
        return self.store.user_by_id(user["id"])

    def test_login_is_rate_limited_after_repeated_failures(self):
        self._create_password_user("bruteforce@example.com", "Sup3rSecurePw!")
        payload = {"email": "bruteforce@example.com", "password": "WrongPassword1"}
        statuses = [
            self.client.post("/auth/login", json=payload).status_code for _ in range(9)
        ]
        self.assertEqual(statuses[0], 401)
        self.assertIn(429, statuses)
        locked = self.client.post(
            "/auth/login",
            json={"email": "bruteforce@example.com", "password": "Sup3rSecurePw!"},
        )
        self.assertEqual(locked.status_code, 429)
        self.assertIn("retry-after", {key.lower() for key in locked.headers})

    def test_password_login_cookie_is_immediately_usable_and_logout_revokes_it(self):
        self._create_password_user("first-login@example.com", "Sup3rSecurePw!")
        browser = TestClient(self.app)
        try:
            login = browser.post(
                "/auth/login",
                json={
                    "email": "first-login@example.com",
                    "password": "Sup3rSecurePw!",
                    "totp_code": "",
                },
            )
            self.assertEqual(login.status_code, 200, login.text)
            cookie = login.headers.get("set-cookie", "").lower()
            self.assertIn("xauby_saas_session=", cookie)
            self.assertIn("httponly", cookie)
            self.assertIn("samesite=lax", cookie)
            self.assertIn("path=/", cookie)

            me = browser.get("/api/v1/me")
            self.assertEqual(me.status_code, 200, me.text)
            self.assertEqual(me.json()["email"], "first-login@example.com")

            logout = browser.post(
                "/auth/logout",
                headers={"X-CSRF-Token": me.json()["csrf_token"]},
            )
            self.assertEqual(logout.status_code, 200, logout.text)
            self.assertEqual(browser.get("/api/v1/me").status_code, 401)
        finally:
            browser.close()

    def test_admin_read_policy_rejects_non_admin_without_requiring_csrf_for_owner(self):
        self._create_password_user("pilot-reader@example.com", "Sup3rSecurePw!")
        pilot = TestClient(self.app)
        try:
            login = pilot.post(
                "/auth/login",
                json={"email": "pilot-reader@example.com", "password": "Sup3rSecurePw!"},
            )
            self.assertEqual(login.status_code, 200, login.text)
            self.assertEqual(pilot.get("/api/v1/admin/users").status_code, 403)
            self.assertEqual(pilot.get("/api/v1/admin/tenants").status_code, 403)
            self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/admin/tenants").status_code, 200)
        finally:
            pilot.close()

    def test_recovery_code_replaces_totp_and_is_single_use(self):
        user = self._create_password_user("mfa-user@example.com", "Sup3rSecurePw!")
        self.store.set_totp_secret(user["id"], "JBSWY3DPEHPK3PXP")
        self.store.enable_totp(user["id"], ["AAAA111111", "BBBB222222"])
        without_code = self.client.post(
            "/auth/login",
            json={"email": "mfa-user@example.com", "password": "Sup3rSecurePw!"},
        )
        self.assertEqual(without_code.status_code, 403)
        with_recovery = self.client.post(
            "/auth/login",
            json={"email": "mfa-user@example.com", "password": "Sup3rSecurePw!",
                  "totp_code": "AAAA111111"},
        )
        self.assertEqual(with_recovery.status_code, 200, with_recovery.text)
        reused = self.client.post(
            "/auth/login",
            json={"email": "mfa-user@example.com", "password": "Sup3rSecurePw!",
                  "totp_code": "AAAA111111"},
        )
        self.assertEqual(reused.status_code, 403)

    def test_totp_reenroll_requires_current_code(self):
        setup = self.client.post("/auth/totp/setup", headers=self.headers)
        self.assertEqual(setup.status_code, 200)
        secret = setup.json()["secret"]
        enabled = self.client.post(
            "/auth/totp/enable", headers=self.headers,
            json={"code": totp_code(secret)},
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        denied = self.client.post("/auth/totp/setup", headers=self.headers)
        self.assertEqual(denied.status_code, 403)
        denied_wrong = self.client.post(
            "/auth/totp/setup", headers=self.headers, json={"current_code": "000000"}
        )
        self.assertEqual(denied_wrong.status_code, 403)
        allowed = self.client.post(
            "/auth/totp/setup", headers=self.headers,
            json={"current_code": totp_code(secret)},
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        stored = self.store.user_by_id(self.client.get("/api/v1/me").json()["id"])
        self.assertTrue(stored["totp_enabled"])
        self.assertEqual(stored["totp_secret"], secret)
        self.assertEqual(stored["pending_totp_secret"], allowed.json()["secret"])

    def test_replacing_trade_pin_requires_current_pin(self):
        first = self.client.post(
            "/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"}
        )
        self.assertEqual(first.status_code, 200)
        denied = self.client.post(
            "/api/v1/trade-pin", headers=self.headers, json={"pin": "87654321"}
        )
        self.assertEqual(denied.status_code, 403)
        replaced = self.client.post(
            "/api/v1/trade-pin", headers=self.headers,
            json={"pin": "87654321", "current_pin": "12345678"},
        )
        self.assertEqual(replaced.status_code, 200)

    def test_legacy_six_digit_current_pin_can_be_rotated_to_stronger_pin(self):
        user = self.client.get("/api/v1/me").json()
        first = self.client.post(
            "/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"}
        )
        self.assertEqual(first.status_code, 200, first.text)
        with patch.object(self.store, "check_trade_pin", return_value=(True, "")) as check:
            replaced = self.client.post(
                "/api/v1/trade-pin", headers=self.headers,
                json={"pin": "87654321", "current_pin": "123456"},
            )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        check.assert_called_once_with(user["id"], "123456")

    def test_legacy_alphanumeric_current_pin_can_be_rotated(self):
        user = self.client.get("/api/v1/me").json()
        first = self.client.post(
            "/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"}
        )
        self.assertEqual(first.status_code, 200, first.text)
        with patch.object(self.store, "check_trade_pin", return_value=(True, "")) as check:
            replaced = self.client.post(
                "/api/v1/trade-pin", headers=self.headers,
                json={"pin": "87654321", "current_pin": "legacy@PinA"},
            )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        check.assert_called_once_with(user["id"], "legacy@PinA")

    def test_forgot_trade_pin_reset_requires_step_up_and_sets_new_pin(self):
        user = self.client.get("/api/v1/me").json()
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?,totp_enabled=1,totp_secret=? WHERE id=?",
                ("configured-password-hash", "configured-totp-secret", user["id"]),
            )
        denied = self.client.post(
            "/api/v1/trade-pin/reset", headers=self.headers,
            json={"new_pin": "87654321", "current_password": "wrong", "totp_code": "000000"},
        )
        self.assertEqual(denied.status_code, 403)
        with patch("xauby.saas.app.verify_password", return_value=True), \
                patch("xauby.saas.app.verify_totp", return_value=True):
            reset = self.client.post(
                "/api/v1/trade-pin/reset", headers=self.headers,
                json={"new_pin": "87654321", "current_password": "current", "totp_code": "123456"},
            )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertTrue(self.store.check_trade_pin(user["id"], "87654321")[0])


class GoogleOAuthAccountGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        project = Path(__file__).resolve().parents[1]
        self.settings = SaaSSettings(
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
            google_client_id="client-id-123",
            google_client_secret="client-secret-456",
        )
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        self.store.bootstrap_owner("owner@example.com", "owner-itsara")
        self.app = create_app(
            self.settings, store=self.store, supervisor=TenantSupervisor(self.settings)
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def _callback(self, email: str, sub: str) -> int:
        start = self.client.get("/auth/google/start", follow_redirects=False)
        self.assertIn(start.status_code, {302, 307})
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        claims = {
            "aud": "client-id-123", "iss": "accounts.google.com",
            "email_verified": "true", "email": email, "sub": sub,
        }
        with patch("xauby.saas.app.requests.post") as post, \
                patch("xauby.saas.app.requests.get") as get:
            post.return_value.json.return_value = {"id_token": "id-token"}
            post.return_value.raise_for_status.return_value = None
            get.return_value.json.return_value = claims
            get.return_value.raise_for_status.return_value = None
            response = self.client.get(
                "/auth/google/callback",
                params={"code": "auth-code", "state": state},
                follow_redirects=False,
            )
        return response.status_code

    def test_suspended_account_cannot_sign_in_via_google(self):
        user, _ = self.store.upsert_google_user("banned@example.com", "sub-banned")
        self.store.set_account_status(user["id"], "suspended", user["id"])
        self.assertEqual(self._callback("banned@example.com", "sub-banned"), 403)

    def test_active_account_still_signs_in_via_google(self):
        user, _ = self.store.upsert_google_user("ok@example.com", "sub-ok")
        self.store.set_account_status(user["id"], "active", user["id"])
        status = self._callback("ok@example.com", "sub-ok")
        self.assertIn(status, {302, 307})


if __name__ == "__main__":
    unittest.main()
