import unittest

from xauby.runtime.config_error import ConfigError


class TestConfigError(unittest.TestCase):
    def test_is_exception(self):
        err = ConfigError("bad config")
        self.assertIsInstance(err, Exception)
        self.assertEqual(str(err), "bad config")
