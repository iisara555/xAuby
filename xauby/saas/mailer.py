from __future__ import annotations

import smtplib
from email.message import EmailMessage

from xauby.saas.settings import SaaSSettings


class Mailer:
    def __init__(self, settings: SaaSSettings):
        self.settings = settings
        self.outbox: list[dict[str, str]] = []

    def send(self, recipient: str, subject: str, text: str) -> None:
        item = {"to": recipient, "subject": subject, "text": text}
        if not self.settings.smtp_host:
            if self.settings.dev_login_enabled:
                self.outbox.append(item)
                return
            raise RuntimeError("transactional email is not configured")
        message = EmailMessage()
        message["From"] = self.settings.smtp_from or self.settings.smtp_username
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(message)
