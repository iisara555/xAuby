"""Tests for instance-scoped runtime paths (item 4 foundation)."""

import os
import unittest
from unittest import mock

from xauby.runtime.paths import config_file, config_root, env_file, runtime_path, runtime_root


class TestRuntimePaths(unittest.TestCase):
    def test_default_root_is_core(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAUBY_HOME", None)
            os.environ.pop("XAUBY_INSTANCE_ID", None)
            self.assertEqual(runtime_root(), "core")
            self.assertEqual(runtime_path("xauby.db"), os.path.join("core", "xauby.db"))

    def test_xauby_home_relocates_root(self):
        with mock.patch.dict(os.environ, {"XAUBY_HOME": "/data/xauby"}, clear=False):
            os.environ.pop("XAUBY_INSTANCE_ID", None)
            self.assertEqual(runtime_root(), "/data/xauby")
            self.assertEqual(runtime_path("logs"), os.path.join("/data/xauby", "logs"))

    def test_instance_id_namespaces_root(self):
        with mock.patch.dict(
            os.environ, {"XAUBY_HOME": "/data", "XAUBY_INSTANCE_ID": "tenantA"}, clear=False
        ):
            self.assertEqual(runtime_root(), os.path.join("/data", "tenantA"))

    def test_instance_id_with_default_home(self):
        with mock.patch.dict(os.environ, {"XAUBY_INSTANCE_ID": "acct42"}, clear=False):
            os.environ.pop("XAUBY_HOME", None)
            self.assertEqual(runtime_root(), os.path.join("core", "acct42"))

    def test_instance_id_rejects_path_traversal(self):
        with mock.patch.dict(os.environ, {"XAUBY_HOME": "/data", "XAUBY_INSTANCE_ID": "../acct"}, clear=False):
            with self.assertRaises(ValueError):
                runtime_root()


class TestNamedArtifactsAreInstanceScoped(unittest.TestCase):
    def test_all_named_paths_under_instance_root(self):
        from xauby.runtime import paths

        with mock.patch.dict(
            os.environ, {"XAUBY_HOME": "/data", "XAUBY_INSTANCE_ID": "t1"}, clear=False
        ):
            root = os.path.join("/data", "t1")
            self.assertEqual(paths.db_path(), os.path.join(root, "xauby.db"))
            self.assertEqual(paths.logs_dir(), os.path.join(root, "logs"))
            self.assertEqual(paths.events_dir(), os.path.join(root, "logs", "events"))
            self.assertEqual(
                paths.bot_state_path(), os.path.join(root, "logs", "xauby_bot_state.json")
            )
            self.assertEqual(
                paths.dashboard_focus_path(), os.path.join(root, "dashboard_focus.json")
            )
            self.assertEqual(
                paths.sentiment_guard_state_path(),
                os.path.join(root, "sentiment_guard_state.json"),
            )
            self.assertEqual(
                paths.sim_balance_path("bot_config"),
                os.path.join(root, "simulated_balance_bot_config.json"),
            )
            self.assertEqual(
                paths.log_path("telegram_failures.log"),
                os.path.join(root, "logs", "telegram_failures.log"),
            )

    def test_default_named_paths_unchanged(self):
        from xauby.runtime import paths

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAUBY_HOME", None)
            os.environ.pop("XAUBY_INSTANCE_ID", None)
            self.assertEqual(paths.bot_state_path(), os.path.join("core", "logs", "xauby_bot_state.json"))
            self.assertEqual(paths.dashboard_focus_path(), os.path.join("core", "dashboard_focus.json"))


class TestResolveDbPathHonorsRoot(unittest.TestCase):
    def test_explicit_env_wins(self):
        from xauby.database.db import resolve_db_path

        with mock.patch.dict(os.environ, {"SQLITE_DB_PATH": "/tmp/custom.db"}, clear=False):
            self.assertEqual(resolve_db_path(), "/tmp/custom.db")

    def test_instance_scopes_db(self):
        from xauby.database.db import resolve_db_path

        with mock.patch.dict(
            os.environ, {"XAUBY_HOME": "/data", "XAUBY_INSTANCE_ID": "t1"}, clear=False
        ):
            os.environ.pop("SQLITE_DB_PATH", None)
            self.assertEqual(resolve_db_path(), os.path.join("/data", "t1", "xauby.db"))


class TestConfigRootIsInstanceScoped(unittest.TestCase):
    def test_default_config_root_is_cwd(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAUBY_CONFIG_DIR", None)
            self.assertEqual(config_root(), os.getcwd())
            # behaviour-preserving: bare names resolve under cwd
            self.assertEqual(config_file("bot_config.yaml"), os.path.join(os.getcwd(), "bot_config.yaml"))
            self.assertEqual(env_file(), os.path.join(os.getcwd(), ".env"))

    def test_config_dir_overrides_root(self):
        with mock.patch.dict(os.environ, {"XAUBY_CONFIG_DIR": "/etc/xauby/acct1"}, clear=False):
            self.assertEqual(config_root(), "/etc/xauby/acct1")
            self.assertEqual(config_file("coin_whitelist.json"), os.path.join("/etc/xauby/acct1", "coin_whitelist.json"))
            self.assertEqual(env_file(), os.path.join("/etc/xauby/acct1", ".env"))

    def test_config_and_data_roots_are_independent(self):
        # A full instance sets both; they need not point to the same place.
        with mock.patch.dict(
            os.environ,
            {"XAUBY_CONFIG_DIR": "instances/a", "XAUBY_HOME": "/data", "XAUBY_INSTANCE_ID": "a"},
            clear=False,
        ):
            self.assertEqual(config_root(), "instances/a")
            self.assertEqual(runtime_root(), os.path.join("/data", "a"))


if __name__ == "__main__":
    unittest.main()
