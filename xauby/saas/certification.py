"""Certification records: the catalog's verdict is read, never written by hand.

Roadmap P1.4, and recommendation 4 of `docs/audit_system_2026-07-21.md`.

The July 2026 incident was not a bad measurement. It was a *label*: a preset in
`catalog.py` advertised "PF 2.00 / MDD 8.3%" from a report that had measured a
different configuration, and nothing in the code could tell. Every field was a
Python literal an editor could type, so "certified" cost exactly as much as any
other string.

This module removes that possibility rather than policing it:

* A verdict exists only as a **record** under ``xauby/saas/certificates/``,
  emitted by ``scripts/certify_preset.py`` from an actual protocol run. There is
  no code path that produces a passing verdict from anything else.
* Every record carries a :func:`config_fingerprint` of the configuration that
  was measured. If the preset's configuration later changes, the fingerprint
  stops matching and the certificate **stops applying** — the preset silently
  reverts to unassessed rather than keeping a verdict it no longer earns. That
  is precisely the July failure, made detectable.
* Approval is still an operator's to give, but a preset approved for live
  *without* a passing verdict must say so out loud: :func:`apply_certification`
  refuses to build the catalog unless such a preset carries an
  ``operator_override`` naming who decided, when, and why.

The three axes stay separate — evidence, verdict, approval — because they
genuinely disagree. XAU has good evidence, a failed verdict, and operator
approval to run anyway; all three are true at once.
"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from typing import Any, Mapping

CERTIFICATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certificates")

#: Record format. Bump when the schema changes in a way a reader must notice;
#: an unknown version is refused rather than half-understood.
RECORD_VERSION = 1

CERTIFICATION_STATUSES = ("certified", "failed", "not_assessed")

#: Fields that define *what was measured*. Change any of them and an existing
#: certificate no longer describes this preset. `live_certified` is deliberately
#: absent: approval is not part of the measurement.
FINGERPRINTED_FIELDS = (
    "strategy",
    "symbol",
    "market_type",
    "primary_timeframe",
    "confirm_timeframe",
    "allowed_sides",
    "max_leverage",
    "execution_profile",
)

_UNASSESSED_NOTE = "Not yet assessed against backtest.acceptance."

_PENDING_EVIDENCE = {
    "status": "pending",
    "score_label": "Pending",
    "period": "—",
    "duration": "—",
    "win_rate_pct": None,
    "max_drawdown_pct": None,
    "trades": None,
    "source": "No certification run published for this configuration",
}


class CertificationError(RuntimeError):
    """The catalog cannot be built as written."""


def config_fingerprint(preset: Mapping[str, Any]) -> str:
    """Stable digest of the configuration a certificate would be measuring.

    Canonical JSON so key order and whitespace cannot change the answer. Only
    :data:`FINGERPRINTED_FIELDS` participate, so editing a label or an
    allocation does not invalidate a certificate, while changing a strategy
    parameter does.
    """
    payload = {key: preset.get(key) for key in FINGERPRINTED_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _record_path(preset_id: str) -> str:
    return os.path.join(CERTIFICATE_DIR, f"{preset_id}.json")


def load_record(preset_id: str) -> dict[str, Any] | None:
    """Read one certificate record, or ``None`` when none has been issued."""
    path = _record_path(preset_id)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        record = json.load(fh)
    if not isinstance(record, dict):
        raise CertificationError(f"{path}: certificate must be a JSON object")
    version = record.get("record_version")
    if version != RECORD_VERSION:
        # A newer record read by older code would be interpreted with the wrong
        # rules, and "interpreted with the wrong rules" is how this went wrong
        # the first time.
        raise CertificationError(
            f"{path}: record_version {version!r}, this build understands "
            f"{RECORD_VERSION}"
        )
    if record.get("verdict") not in CERTIFICATION_STATUSES:
        raise CertificationError(f"{path}: verdict must be one of {CERTIFICATION_STATUSES}")
    if record.get("verdict") != "not_assessed" and not record.get("config_fingerprint"):
        raise CertificationError(f"{path}: a verdict without a fingerprint cannot be checked")
    protocol = record.get("protocol") or {}
    if str(protocol.get("version") or "1") == "2":
        source = record.get("data_source")
        if not isinstance(source, Mapping):
            raise CertificationError(f"{path}: protocol v2 requires structured data_source")
        missing = [key for key in ("venue", "symbol", "timeframe", "native") if key not in source]
        if missing:
            raise CertificationError(f"{path}: protocol v2 data_source missing {missing}")
        if record.get("verdict") == "certified" and not bool(source.get("native")):
            raise CertificationError(f"{path}: proxy evidence cannot certify a venue preset")
        if record.get("verdict") == "certified" and not bool((record.get("gate") or {}).get("native_history_sufficient")):
            raise CertificationError(f"{path}: protocol v2 certification requires sufficient native history")
        if record.get("verdict") == "certified" and not bool((record.get("gate") or {}).get("fold_gate_passed")):
            raise CertificationError(f"{path}: protocol v2 certification requires the 4/5 profitable-fold gate")
    if str(protocol.get("version") or "1") == "3":
        source = record.get("data_source")
        if not isinstance(source, Mapping):
            raise CertificationError(f"{path}: protocol v3 requires structured data_source")
        missing = [key for key in ("venue", "symbol", "timeframe", "native") if key not in source]
        if missing:
            raise CertificationError(f"{path}: protocol v3 data_source missing {missing}")
        if not str(protocol.get("manifest_sha256") or ""):
            raise CertificationError(f"{path}: protocol v3 requires a locked manifest hash")
        gate = record.get("gate") or {}
        checks = gate.get("checks") or {}
        if record.get("verdict") == "certified" and not bool(source.get("native")):
            raise CertificationError(f"{path}: protocol v3 proxy evidence cannot certify")
        if record.get("verdict") == "certified" and (
            not bool(gate.get("passed"))
            or not isinstance(checks, Mapping)
            or not checks
            or not all(value is True for value in checks.values())
        ):
            raise CertificationError(
                f"{path}: protocol v3 certification requires every locked gate"
            )
    return record


def load_records() -> dict[str, dict[str, Any]]:
    """Every issued certificate, keyed by preset id."""
    if not os.path.isdir(CERTIFICATE_DIR):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(os.listdir(CERTIFICATE_DIR)):
        if not name.endswith(".json"):
            continue
        preset_id = name[: -len(".json")]
        record = load_record(preset_id)
        if record is None:
            continue
        if record.get("preset_id") != preset_id:
            raise CertificationError(
                f"{name}: preset_id {record.get('preset_id')!r} does not match the filename"
            )
        out[preset_id] = record
    return out


def certification_for(preset: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve verdict + evidence for one preset from its certificate, if any.

    Returns the three derived fields the catalog publishes. A preset with no
    record, or whose configuration has moved on from the one measured, comes
    back ``not_assessed`` — never "still certified because it used to be".
    """
    preset_id = str(preset.get("id") or "")
    record = load_record(preset_id)
    if record is None:
        return {
            "certification_status": "not_assessed",
            "certification_note": _UNASSESSED_NOTE,
            "backtest": deepcopy(_PENDING_EVIDENCE),
        }

    current = config_fingerprint(preset)
    measured = str(record.get("config_fingerprint") or "")
    if measured and measured != current:
        # The exact July 2026 failure, caught instead of published.
        return {
            "certification_status": "not_assessed",
            "certification_note": (
                "The certificate on file measured a different configuration "
                f"(fingerprint {measured}, this preset is {current}). It does "
                "not describe what this preset now ships, so it no longer "
                "applies. Re-run scripts/certify_preset.py."
            ),
            "backtest": {
                **deepcopy(_PENDING_EVIDENCE),
                "source": (
                    "Superseded: the published run measured a different "
                    f"configuration ({record.get('document') or 'see certificates/'})"
                ),
            },
        }

    return {
        "certification_status": record["verdict"],
        "certification_note": str(record.get("note") or _UNASSESSED_NOTE),
        "backtest": deepcopy(record.get("evidence") or _PENDING_EVIDENCE),
    }


def _require_override(preset: Mapping[str, Any], verdict: str) -> None:
    """Approval without a passing verdict must name a person and a reason.

    Not a veto — the operator may still run it, and XAU does. The requirement is
    that the decision is written down next to the thing it approves, rather than
    inferred later from a `True` nobody remembers setting.
    """
    override = preset.get("operator_override")
    missing = [key for key in ("decided_by", "decided_at", "reason")
               if not str((override or {}).get(key) or "").strip()]
    if not isinstance(override, Mapping) or missing:
        raise CertificationError(
            f"preset {preset.get('id')!r} is approved for live (live_certified) "
            f"but its verdict is {verdict!r}. That is allowed, and sometimes "
            "right, but it must be stated: add operator_override with "
            f"decided_by / decided_at / reason (missing: {missing or 'all'})."
        )


def apply_certification(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return the published preset: hand-authored spec + derived certification.

    Raises :class:`CertificationError` if the spec declares a derived field
    itself. Allowing both would put the hand-typed value back in reach, which is
    the whole thing this exists to prevent.
    """
    declared = [key for key in ("certification_status", "certification_note", "backtest")
                if key in spec]
    if declared:
        raise CertificationError(
            f"preset {spec.get('id')!r} declares {declared} directly. These are "
            "derived from xauby/saas/certificates/<preset_id>.json — issue a "
            "certificate with scripts/certify_preset.py instead."
        )
    preset = deepcopy(dict(spec))
    resolved = certification_for(preset)
    if (
        preset.get("live_certified")
        and resolved["certification_status"] == "not_assessed"
    ):
        # An override can accept a measured failure (XAU), but it cannot turn
        # absence of venue evidence into approval.  Keeping unmeasured presets
        # live-selectable violated the Phase-1 release criterion even though
        # the UI honestly labelled them pending.
        raise CertificationError(
            f"preset {preset.get('id')!r} is approved for live but has no "
            "certificate for this configuration. Set live_certified=false "
            "until scripts/certify_preset.py issues a venue-backed record."
        )
    if preset.get("live_certified") and resolved["certification_status"] != "certified":
        _require_override(preset, resolved["certification_status"])
    preset.update(resolved)
    return preset
