#!/usr/bin/env python3
"""Preflight the transactional email path. Roadmap P2.5.

Before P2.5 there was exactly one way to find out whether SMTP worked: invite a
real person and see whether `delivery` came back `manual`. That is a poor way to
learn it, because the person on the other end is a design partner waiting for an
email, and the failure looks identical to "the invite was sent and they ignored
it".

    python -m scripts.check_email                     # config + connect + login
    python -m scripts.check_email --to you@example.com  # also send a real message

`--to` is the only check that proves delivery end to end. Authentication
succeeding says the credentials are right; it does not say the provider will
accept your From address or that the message will clear a spam filter. Send one
to yourself and look at it before inviting anyone.

Exit codes: 0 = usable, 1 = not. Nothing here writes to the database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.saas.mailer import (  # noqa: E402
    MailDeliveryFailed,
    Mailer,
    MailNotConfigured,
)
from xauby.saas.settings import SaaSSettings  # noqa: E402


def run(mailer: Mailer, recipient: str = "") -> tuple[int, dict[str, object]]:
    try:
        report: dict[str, object] = dict(mailer.verify())
    except MailNotConfigured as exc:
        return 1, {"ok": False, "stage": "configuration", "reason": str(exc)}
    except MailDeliveryFailed as exc:
        return 1, {"ok": False, "stage": "connect", "reason": str(exc)}

    report["ok"] = True
    report["stage"] = "authenticated"
    if not recipient:
        report["note"] = ("credentials accepted; delivery not proven. "
                          "Re-run with --to <address> before inviting anyone.")
        return 0, report

    subject = "xAuby email preflight"
    body = (
        "This is a preflight message from scripts/check_email.py.\n\n"
        f"Sent at {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}.\n"
        "If it reached your inbox rather than spam, invites will too.\n"
    )
    try:
        mailer.send(recipient, subject, body)
    except (MailNotConfigured, MailDeliveryFailed) as exc:
        return 1, {"ok": False, "stage": "send", "reason": str(exc),
                   "sender": report.get("sender")}
    report["stage"] = "sent"
    report["sent_to"] = recipient
    report["note"] = "check the inbox AND the spam folder before inviting anyone"
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that transactional email is configured and working.")
    parser.add_argument("--to", default="",
                        help="send a real test message to this address")
    args = parser.parse_args(argv)

    code, report = run(Mailer(SaaSSettings.from_env()), args.to)
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
