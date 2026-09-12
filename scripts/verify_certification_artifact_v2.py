#!/usr/bin/env python3
"""Verify an Institutional Certification Framework v2 artifact bundle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.backtest.certification_runner import (  # noqa: E402
    Phase1CertificationError,
    verify_certification_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", help="directory containing certificate.json")
    parser.add_argument(
        "--expected-sha256",
        required=True,
        help="external SHA-256 anchor published by the CI run",
    )
    args = parser.parse_args()
    try:
        artifact = verify_certification_artifact(
            args.bundle,
            expected_sha256=args.expected_sha256,
        )
    except Phase1CertificationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "verdict": artifact["verdict"],
                "protocol_id": artifact["protocol"]["protocol_id"],
                "git_commit": artifact["ci_provenance"]["git_commit"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
