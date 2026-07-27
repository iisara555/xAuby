"""A verdict must be earned by a run, not typed into a file.

The July 2026 incident was a label, not a measurement: a preset advertised
"PF 2.00 / MDD 8.3%" from a report that had measured a different configuration,
and nothing in the code could tell. These tests hold the two properties that
make that unrepeatable — a verdict comes only from a certificate record, and a
record stops applying the moment the configuration it measured changes.
"""
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.saas import certification  # noqa: E402
from xauby.saas.certification import (  # noqa: E402
    CERTIFICATION_STATUSES,
    CertificationError,
    RECORD_VERSION,
    apply_certification,
    config_fingerprint,
    load_records,
)
from xauby.saas.preset_specs import PRESET_SPECS  # noqa: E402


def _spec(preset_id):
    for spec in PRESET_SPECS:
        if spec["id"] == preset_id:
            return deepcopy(spec)
    raise AssertionError(f"no spec {preset_id}")


def _record(preset_id, **overrides):
    record = {
        "record_version": RECORD_VERSION,
        "preset_id": preset_id,
        "config_fingerprint": config_fingerprint(_spec(preset_id)),
        "measured_at": "2026-07-27",
        "verdict": "certified",
        "note": "Clears the gate by +9.99pp.",
        "document": "docs/research/example.md",
        "evidence": {"status": "validated", "score_label": "PF 1.50",
                     "period": "x", "duration": "y", "win_rate_pct": 40.0,
                     "max_drawdown_pct": 9.0, "trades": 100, "source": "test"},
    }
    record.update(overrides)
    return record


class _WithCertificateDir(unittest.TestCase):
    """Point the loader at a scratch directory instead of the shipped records."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        patcher = mock.patch.object(certification, "CERTIFICATE_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, record):
        path = os.path.join(self.dir, f"{record['preset_id']}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return path


class TestVerdictCannotBeTyped(_WithCertificateDir):
    def test_a_spec_declaring_a_verdict_is_rejected(self):
        spec = _spec("okx-btc-supertrend-v1")
        spec["certification_status"] = "certified"
        with self.assertRaises(CertificationError) as ctx:
            apply_certification(spec)
        self.assertIn("certification_status", str(ctx.exception))

    def test_a_spec_declaring_a_backtest_block_is_rejected(self):
        # The precise shape of the July 2026 mistake: metrics pasted in from a
        # report, with no way to check what they measured.
        spec = _spec("binance-eth-supertrend-v1")
        spec["backtest"] = {"status": "validated", "score_label": "PF 2.00"}
        with self.assertRaises(CertificationError) as ctx:
            apply_certification(spec)
        self.assertIn("backtest", str(ctx.exception))

    def test_no_record_means_unassessed_not_certified(self):
        spec = _spec("binance-eth-supertrend-v1")
        resolved = apply_certification(spec)
        self.assertEqual(resolved["certification_status"], "not_assessed")
        self.assertEqual(resolved["backtest"]["status"], "pending")


class TestFingerprintInvalidation(_WithCertificateDir):
    def test_a_matching_record_applies(self):
        self.write(_record("okx-btc-supertrend-v1"))
        resolved = apply_certification(_spec("okx-btc-supertrend-v1"))
        self.assertEqual(resolved["certification_status"], "certified")
        self.assertEqual(resolved["backtest"]["score_label"], "PF 1.50")

    def test_changing_a_strategy_parameter_revokes_the_certificate(self):
        # This is the whole point. A certificate describes a configuration; edit
        # the configuration and it describes something you are no longer running.
        self.write(_record("okx-btc-supertrend-v1"))
        spec = _spec("okx-btc-supertrend-v1")
        spec["live_certified"] = False          # approval tested separately
        spec["execution_profile"]["sl_atr_mult"] = 1.5
        resolved = apply_certification(spec)
        self.assertEqual(resolved["certification_status"], "not_assessed")
        self.assertIn("different configuration", resolved["certification_note"])
        self.assertEqual(resolved["backtest"]["status"], "pending")

    def test_changing_the_traded_sides_revokes_the_certificate(self):
        self.write(_record("okx-btc-supertrend-v1"))
        spec = _spec("okx-btc-supertrend-v1")
        spec["live_certified"] = False
        spec["allowed_sides"] = ["long"]
        self.assertEqual(
            apply_certification(spec)["certification_status"], "not_assessed")

    def test_editing_a_live_approved_preset_breaks_the_build(self):
        # The strongest form of the property, and worth stating on its own: you
        # cannot quietly retune a configuration that is approved for live and
        # keep shipping it. Losing the certificate costs the approval too, until
        # someone either re-certifies or signs the override.
        self.write(_record("okx-btc-supertrend-v1"))
        spec = _spec("okx-btc-supertrend-v1")
        self.assertTrue(spec["live_certified"])
        spec["execution_profile"]["supertrend_mult"] = 2.0
        with self.assertRaises(CertificationError) as ctx:
            apply_certification(spec)
        self.assertIn("operator_override", str(ctx.exception))

    def test_cosmetic_edits_do_not_revoke_it(self):
        # Renaming a preset or changing an allocation is not a new experiment;
        # invalidating on those would train everyone to re-issue thoughtlessly.
        self.write(_record("okx-btc-supertrend-v1"))
        spec = _spec("okx-btc-supertrend-v1")
        spec["label"] = "Something else"
        spec["allocation_pct"] = 12.5
        spec["strategy_traits"] = ["different copy"]
        self.assertEqual(
            apply_certification(spec)["certification_status"], "certified")

    def test_fingerprint_is_order_independent(self):
        spec = _spec("okx-btc-supertrend-v1")
        shuffled = dict(reversed(list(spec.items())))
        shuffled["execution_profile"] = dict(
            reversed(list(spec["execution_profile"].items())))
        self.assertEqual(config_fingerprint(spec), config_fingerprint(shuffled))


class TestApprovalMustBeStated(_WithCertificateDir):
    def test_live_without_a_passing_verdict_needs_an_override(self):
        spec = _spec("binance-eth-supertrend-v1")
        spec["live_certified"] = True
        spec.pop("operator_override", None)
        with self.assertRaises(CertificationError) as ctx:
            apply_certification(spec)
        self.assertIn("operator_override", str(ctx.exception))

    def test_a_half_filled_override_is_not_enough(self):
        spec = _spec("binance-eth-supertrend-v1")
        spec["live_certified"] = True
        spec["operator_override"] = {"decided_by": "owner", "decided_at": "",
                                     "reason": ""}
        with self.assertRaises(CertificationError):
            apply_certification(spec)

    def test_a_complete_override_is_accepted(self):
        spec = _spec("binance-eth-supertrend-v1")
        spec["live_certified"] = True
        spec["operator_override"] = {"decided_by": "owner",
                                     "decided_at": "2026-07-27",
                                     "reason": "pilot tenant, sim-sized"}
        self.assertEqual(
            apply_certification(spec)["certification_status"], "not_assessed")

    def test_a_certified_preset_needs_no_override(self):
        self.write(_record("okx-btc-supertrend-v1"))
        spec = _spec("okx-btc-supertrend-v1")
        spec.pop("operator_override", None)
        self.assertEqual(
            apply_certification(spec)["certification_status"], "certified")


class TestRecordIntegrity(_WithCertificateDir):
    def test_an_unknown_record_version_is_refused(self):
        self.write(_record("okx-btc-supertrend-v1", record_version=99))
        with self.assertRaises(CertificationError):
            apply_certification(_spec("okx-btc-supertrend-v1"))

    def test_an_unknown_verdict_is_refused(self):
        self.write(_record("okx-btc-supertrend-v1", verdict="probably fine"))
        with self.assertRaises(CertificationError):
            apply_certification(_spec("okx-btc-supertrend-v1"))

    def test_a_verdict_without_a_fingerprint_is_refused(self):
        # Otherwise a record could claim a pass while being uncheckable against
        # any configuration at all.
        self.write(_record("okx-btc-supertrend-v1", config_fingerprint=""))
        with self.assertRaises(CertificationError):
            apply_certification(_spec("okx-btc-supertrend-v1"))

    def test_filename_and_preset_id_must_agree(self):
        record = _record("okx-btc-supertrend-v1")
        record["preset_id"] = "something-else"
        with open(os.path.join(self.dir, "okx-btc-supertrend-v1.json"), "w") as fh:
            json.dump(record, fh)
        with self.assertRaises(CertificationError):
            load_records()


class TestShippedRecords(unittest.TestCase):
    """The certificates actually in the repo, checked against the live specs."""

    def test_every_shipped_record_matches_its_preset(self):
        specs = {spec["id"]: spec for spec in PRESET_SPECS}
        for preset_id, record in load_records().items():
            with self.subTest(preset=preset_id):
                self.assertIn(preset_id, specs,
                              "a certificate for a preset that no longer exists")
                self.assertEqual(
                    record["config_fingerprint"], config_fingerprint(specs[preset_id]),
                    "the shipped certificate measured a different configuration "
                    "than the preset now ships — re-run scripts/certify_preset.py",
                )

    def test_every_record_carries_its_gate_result(self):
        for preset_id, record in load_records().items():
            with self.subTest(preset=preset_id):
                gate = record.get("gate") or {}
                self.assertIn("passed", gate)
                self.assertEqual(record["verdict"] == "certified", bool(gate["passed"]),
                                 "the verdict must be the gate's own answer")
                self.assertIn(record["verdict"], CERTIFICATION_STATUSES)

    def test_no_spec_declares_a_derived_field(self):
        for spec in PRESET_SPECS:
            with self.subTest(preset=spec["id"]):
                for key in ("certification_status", "certification_note", "backtest"):
                    self.assertNotIn(key, spec)

    def test_the_catalog_builds(self):
        # Imports the real module: a bad record or a missing override is a
        # startup failure, not a runtime surprise.
        from xauby.saas.catalog import PRESETS

        self.assertEqual(len(PRESETS), len(PRESET_SPECS))


if __name__ == "__main__":
    unittest.main()
