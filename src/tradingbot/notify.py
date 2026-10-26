"""Notifications: Telegram, ntfy (phone push) and e-mail, all optional, standard library only.

Secrets never go in config/bot.toml: the Telegram bot token comes from
TELEGRAM_BOT_TOKEN and the SMTP password from SMTP_PASSWORD. A channel that
fails is reported back but never stops the bot.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage

from .config import NotifySettings


class Notifier:
    def __init__(self, settings: NotifySettings, env: dict | None = None):
        self.s = settings
        self.env = os.environ if env is None else env

    def channels(self) -> list[str]:
        out = []
        if self.s.telegram_chat_id and self.env.get("TELEGRAM_BOT_TOKEN"):
            out.append("telegram")
        if self.s.ntfy_topic:
            out.append("ntfy")
        if self.s.email_to and self.s.smtp_host:
            out.append("email")
        return out

    def wants(self, kind: str) -> bool:
        return kind in self.s.events and bool(self.channels())

    def send(self, title: str, detail: str = "") -> list[str]:
        """Send to every configured channel. Returns one error line per channel that failed."""
        errors = []
        for ch in self.channels():
            try:
                getattr(self, "_" + ch)(title, detail)
            except Exception as exc:  # a dead channel must not stop trading decisions
                errors.append("%s: %s" % (ch, exc))
        return errors

    def _post(self, url: str, body: bytes, ctype: str) -> None:
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()

    def _telegram(self, title: str, detail: str) -> None:
        token = self.env["TELEGRAM_BOT_TOKEN"]
        text = "%s\n%s" % (title, detail) if detail else title
        body = json.dumps({"chat_id": self.s.telegram_chat_id, "text": text}).encode("utf-8")
        self._post("https://api.telegram.org/bot%s/sendMessage" % token, body, "application/json")

    def _ntfy(self, title: str, detail: str) -> None:
        # JSON publishing keeps non-ASCII titles (Lithuanian letters) out of HTTP headers.
        body = json.dumps(
            {"topic": self.s.ntfy_topic, "title": title, "message": detail or title, "tags": ["chart"]}
        )
        self._post(self.s.ntfy_server.rstrip("/"), body.encode("utf-8"), "application/json")

    def _email(self, title: str, detail: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = "Trading bot: %s" % title
        msg["From"] = self.s.smtp_user or self.s.email_to
        msg["To"] = self.s.email_to
        msg.set_content(detail or title)
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if self.s.smtp_user:
                smtp.login(self.s.smtp_user, self.env.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
