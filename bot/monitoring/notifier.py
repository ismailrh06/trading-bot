"""Notifications Telegram et Discord.

Un échec de notification ne doit jamais interrompre le trading :
tout est enveloppé dans try/except, l'erreur est loguée et la vie continue.
"""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

TIMEOUT_S = 10


class Notifier:
    def __init__(self, monitoring_cfg: dict | None = None):
        cfg = monitoring_cfg or {}
        self.telegram_enabled = bool(cfg.get("telegram", {}).get("enabled"))
        self.discord_enabled = bool(cfg.get("discord", {}).get("enabled"))
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")

        if self.telegram_enabled and not (self.telegram_token and self.telegram_chat_id):
            log.warning("Telegram activé mais TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID absents de .env")
            self.telegram_enabled = False
        if self.discord_enabled and not self.discord_webhook:
            log.warning("Discord activé mais DISCORD_WEBHOOK_URL absent de .env")
            self.discord_enabled = False

    def send(self, text: str) -> None:
        log.info("NOTIFICATION : %s", text.replace("\n", " | "))
        if self.telegram_enabled:
            self._telegram(text)
        if self.discord_enabled:
            self._discord(text)

    def alert(self, title: str, text: str) -> None:
        self.send(f"🚨 ALERTE — {title}\n{text}")

    def _telegram(self, text: str) -> None:
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": text},
                timeout=TIMEOUT_S,
            ).raise_for_status()
        except Exception as exc:
            log.error("Envoi Telegram échoué : %s", exc)

    def _discord(self, text: str) -> None:
        try:
            requests.post(
                self.discord_webhook, json={"content": text[:2000]}, timeout=TIMEOUT_S
            ).raise_for_status()
        except Exception as exc:
            log.error("Envoi Discord échoué : %s", exc)
