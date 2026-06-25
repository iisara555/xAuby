"""Cross-instance live account lock (#3, approach A)."""
from __future__ import annotations

import os
import tempfile
import unittest

from xauby.engine.base import BaseEngine
from xauby.runtime.paths import account_lock_dir, account_lock_path

try:
    import fcntl  # noqa: F401
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


def _stub_engine(*, simulate_only, live_allowed, fp, config_path="bot_config.yaml"):
    """A BaseEngine shell with only the attributes the lock methods touch."""
    eng = BaseEngine.__new__(BaseEngine)
    eng.simulate_only = simulate_only
    eng.live_trading_allowed = live_allowed
    eng._account_fp = fp
    eng._account_lock_fp = None
    eng.config_path = config_path
    return eng


class TestFingerprint(unittest.TestCase):
    def test_deterministic_and_short(self):
        a = BaseEngine._account_fingerprint("my-api-key")
        b = BaseEngine._account_fingerprint("my-api-key")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_distinct_keys_differ(self):
        self.assertNotEqual(
            BaseEngine._account_fingerprint("key-a"),
            BaseEngine._account_fingerprint("key-b"),
        )

    def test_empty_key_is_empty(self):
        self.assertEqual(BaseEngine._account_fingerprint(""), "")
        self.assertEqual(BaseEngine._account_fingerprint(None), "")

    def test_secret_not_in_fingerprint(self):
        self.assertNotIn("secret", BaseEngine._account_fingerprint("secret-key"))


class TestLockPaths(unittest.TestCase):
    def test_env_override(self):
        old = os.environ.get("XAUBY_ACCOUNT_LOCK_DIR")
        os.environ["XAUBY_ACCOUNT_LOCK_DIR"] = "/tmp/xa_locks"
        try:
            self.assertEqual(account_lock_dir(), "/tmp/xa_locks")
            self.assertEqual(account_lock_path("abc"), os.path.join("/tmp/xa_locks", "account_abc.lock"))
        finally:
            if old is None:
                os.environ.pop("XAUBY_ACCOUNT_LOCK_DIR", None)
            else:
                os.environ["XAUBY_ACCOUNT_LOCK_DIR"] = old


class TestSkipConditions(unittest.TestCase):
    def test_sim_mode_never_locks(self):
        eng = _stub_engine(simulate_only=True, live_allowed=True, fp="abc")
        eng._acquire_account_lock()
        self.assertIsNone(eng._account_lock_fp)

    def test_live_not_allowed_never_locks(self):
        eng = _stub_engine(simulate_only=False, live_allowed=False, fp="abc")
        eng._acquire_account_lock()
        self.assertIsNone(eng._account_lock_fp)

    def test_no_fingerprint_skips(self):
        eng = _stub_engine(simulate_only=False, live_allowed=True, fp="")
        eng._acquire_account_lock()
        self.assertIsNone(eng._account_lock_fp)


@unittest.skipUnless(HAS_FCNTL, "flock contention requires fcntl (POSIX)")
class TestLockContention(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old = os.environ.get("XAUBY_ACCOUNT_LOCK_DIR")
        os.environ["XAUBY_ACCOUNT_LOCK_DIR"] = self._tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XAUBY_ACCOUNT_LOCK_DIR", None)
        else:
            os.environ["XAUBY_ACCOUNT_LOCK_DIR"] = self._old

    def test_second_live_instance_on_same_account_refused(self):
        a = _stub_engine(simulate_only=False, live_allowed=True, fp="acct1")
        a._acquire_account_lock()
        self.assertIsNotNone(a._account_lock_fp)

        b = _stub_engine(simulate_only=False, live_allowed=True, fp="acct1")
        with self.assertRaises(RuntimeError):
            b._acquire_account_lock()

        # Different account fingerprint is allowed concurrently.
        c = _stub_engine(simulate_only=False, live_allowed=True, fp="acct2")
        c._acquire_account_lock()
        self.assertIsNotNone(c._account_lock_fp)

        # Releasing the first lets a new instance take acct1.
        a._release_account_lock()
        d = _stub_engine(simulate_only=False, live_allowed=True, fp="acct1")
        d._acquire_account_lock()
        self.assertIsNotNone(d._account_lock_fp)
        d._release_account_lock()
        c._release_account_lock()


if __name__ == "__main__":
    unittest.main()
