"""Transactional email. Roadmap P2.5.

Invites fall back to a manually copied link when this is not configured, which
is fine for the owner and not fine for a design partner who is waiting for an
email that will never arrive. Four things were wrong with the original beyond
"no credentials set":

* **Port 465 could not work.** `smtplib.SMTP` + `starttls()` speaks explicit
  TLS; 465 is implicit TLS and needs `SMTP_SSL`. An operator picking a 465
  provider got a hang and then an opaque failure.
* **The reason was thrown away.** Every failure became the same string,
  "transactional email delivery is unavailable", and that string is what landed
  in the `invite_delivery_failed` audit row — so the one record of the problem
  said nothing about it.
* **No `Date` header.** `smtplib.send_message` does not add one (it only
  handles `Resent-Date`), and `EmailMessage` does not either. `Date` is
  mandatory under RFC 5322 and its absence is a strong spam signal — exactly
  the failure that looks like "sent successfully, never arrived".
* **`From` fell back to the SMTP username**, which for several providers is not
  an address at all (SendGrid uses the literal `apikey`), producing an invalid
  header.

There is no retry queue and deliberately no background worker: at two design
partners, a failed invite falls back to the link the admin already has on
screen, and adding a queue would be infrastructure serving a volume that does
not exist.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from xauby.saas.settings import SaaSSettings

#: Implicit-TLS port. 587 is STARTTLS, 25 is plaintext submission.
IMPLICIT_TLS_PORT = 465


class MailNotConfigured(RuntimeError):
    """No SMTP host, or no credentials. Distinct from a delivery failure."""


class MailDeliveryFailed(RuntimeError):
    """The server was reachable and refused, or the connection broke."""


class Mailer:
    def __init__(self, settings: SaaSSettings):
        self.settings = settings
        self.outbox: list[dict[str, str]] = []

    # --- configuration ------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.settings.smtp_host and self.settings.smtp_username
                    and self.settings.smtp_password)

    def sender(self) -> str:
        """The From address, or "" when it cannot be determined.

        Falling back to the SMTP username is only valid when that username is
        itself an address; several providers use an opaque token instead.
        """
        explicit = (self.settings.smtp_from or "").strip()
        if explicit:
            return explicit
        username = (self.settings.smtp_username or "").strip()
        return username if "@" in parseaddr(username)[1] else ""

    def _redact(self, text: str) -> str:
        """Keep a server's own words, drop anything of ours it echoed back."""
        for secret in (self.settings.smtp_password, self.settings.smtp_username):
            if secret and len(secret) > 3:
                text = text.replace(secret, "***")
        return text

    def _connect(self) -> smtplib.SMTP:
        context = ssl.create_default_context()
        host, port = self.settings.smtp_host, int(self.settings.smtp_port)
        if port == IMPLICIT_TLS_PORT or self.settings.smtp_use_ssl:
            return smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        smtp = smtplib.SMTP(host, port, timeout=15)
        try:
            smtp.starttls(context=context)
        except Exception:
            smtp.close()
            raise
        return smtp

    # --- operations ---------------------------------------------------------

    def verify(self) -> dict[str, object]:
        """Connect and authenticate without sending. For the preflight check.

        The point is that "email works" stops being an assumption an operator
        discovers is false when a design partner says the invite never came.
        """
        if not self.settings.smtp_host:
            raise MailNotConfigured("XAUBY_SMTP_HOST is not set")
        if not self.configured:
            raise MailNotConfigured(
                "XAUBY_SMTP_USERNAME and XAUBY_SMTP_PASSWORD are required")
        if not self.sender():
            raise MailNotConfigured(
                "XAUBY_SMTP_FROM is required: the SMTP username is not an email address")
        try:
            with self._connect() as smtp:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                return {
                    "host": self.settings.smtp_host,
                    "port": int(self.settings.smtp_port),
                    "implicit_tls": int(self.settings.smtp_port) == IMPLICIT_TLS_PORT
                    or self.settings.smtp_use_ssl,
                    "sender": self.sender(),
                }
        except (OSError, smtplib.SMTPException) as exc:
            raise MailDeliveryFailed(
                f"{type(exc).__name__}: {self._redact(str(exc))}") from exc

    def send(self, recipient: str, subject: str, text: str) -> None:
        item = {"to": recipient, "subject": subject, "text": text}
        if not self.settings.smtp_host:
            if self.settings.dev_login_enabled:
                self.outbox.append(item)
                return
            raise MailNotConfigured("transactional email is not configured")
        if not self.settings.smtp_username or not self.settings.smtp_password:
            raise MailNotConfigured("transactional email credentials are not configured")
        sender = self.sender()
        if not sender:
            raise MailNotConfigured(
                "XAUBY_SMTP_FROM is required: the SMTP username is not an email address")

        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        # Neither EmailMessage nor smtplib.send_message supplies these. Date is
        # mandatory (RFC 5322) and a missing one reads as spam.
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or None)
        message.set_content(text)
        try:
            with self._connect() as smtp:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            # The reason travels with the error: it is what the audit row and
            # the admin's screen show, and a generic string there left the one
            # record of the failure saying nothing.
            raise MailDeliveryFailed(
                f"{type(exc).__name__}: {self._redact(str(exc))}") from exc
