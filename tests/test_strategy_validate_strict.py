"""P1.2 — fatal-vs-advisory config validation + strict load mode.

config_errors() = the 'must' (invalid-value) subset of validate_config(); strict
load raises ConfigError on it so a fatally misconfigured strategy cannot trade,
while advisory 'should'/'usually'/research notices never block loading.
"""
import unittest

from xauby.runtime.architecture_config import strategy_validate_strict
from xauby.runtime.config_error import ConfigError
from xauby.strategies.registry import load_strategy


class TestConfigErrorsClassification(unittest.TestCase):
    def test_fatal_must_warning_is_a_config_error(self):
        # cdc: rsi_min >= rsi_max -> "must be less than" -> fatal
        s = load_strategy("cdc_action_zone", {"rsi_min": 80.0, "rsi_max": 70.0})
        self.assertTrue(any("must" in w.lower() for w in s.validate_config()))
        self.assertTrue(s.config_errors())

    def test_advisory_should_warning_is_not_a_config_error(self):
        # donchian: entry_len < exit_len -> "should usually" -> advisory only
        s = load_strategy("donchian_trend", {"entry_len": 10, "exit_len": 24})
        self.assertTrue(s.validate_config())          # warns
        self.assertEqual(s.config_errors(), [])        # but not fatal

    def test_research_notice_is_not_a_config_error(self):
        s = load_strategy("rsi2_short")
        self.assertTrue(any("RESEARCH" in w for w in s.validate_config()))
        self.assertEqual(s.config_errors(), [])

    def test_valid_config_has_no_errors(self):
        s = load_strategy("cdc_action_zone")
        self.assertEqual(s.validate_config(), [])
        self.assertEqual(s.config_errors(), [])


class TestStrictLoad(unittest.TestCase):
    def test_strict_raises_on_fatal_config(self):
        with self.assertRaises(ConfigError):
            load_strategy("cdc_action_zone", {"rsi_min": 80.0, "rsi_max": 70.0}, strict=True)

    def test_non_strict_loads_despite_fatal_config(self):
        # backward-compatible default: warn and run
        s = load_strategy("cdc_action_zone", {"rsi_min": 80.0, "rsi_max": 70.0}, strict=False)
        self.assertIsNotNone(s)

    def test_strict_allows_advisory_only_config(self):
        s = load_strategy("donchian_trend", {"entry_len": 10, "exit_len": 24}, strict=True)
        self.assertIsNotNone(s)

    def test_strict_allows_research_plugin(self):
        s = load_strategy("rsi2_short", strict=True)
        self.assertIsNotNone(s)

    def test_strict_allows_valid_config(self):
        self.assertIsNotNone(load_strategy("cdc_action_zone", strict=True))


class TestStrictAccessor(unittest.TestCase):
    def test_accessor_reads_flag(self):
        self.assertFalse(strategy_validate_strict({}))
        self.assertFalse(strategy_validate_strict({"architecture": {"strategy_validate_strict": False}}))
        self.assertTrue(strategy_validate_strict({"architecture": {"strategy_validate_strict": True}}))


if __name__ == "__main__":
    unittest.main()
