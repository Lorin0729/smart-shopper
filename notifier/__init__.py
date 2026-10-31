"""降价/补货通知：控制台 + 可选 Webhook（Server酱 / 钉钉 / 企业微信机器人）。"""
from __future__ import annotations

import requests


class Notifier:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.serverchan_key = cfg.get("serverchan_key", "")
        self.webhook_url = cfg.get("webhook_url", "")

    def send(self, title: str, lines: list[str]) -> None:
        body = "\n".join(lines)
        print(f"\n=== {title} ===")
        print(body)
        if self.serverchan_key:
            self._post_form(f"https://sctapi.ftqq.com/{self.serverchan_key}.send",
                            {"title": title, "desp": body})
        elif self.webhook_url:
            self._post_json(self.webhook_url, {"title": title, "text": body, "markdown": body})

    @staticmethod
    def _post_form(url: str, data: dict) -> None:
        try:
            requests.post(url, data=data, timeout=10)
        except requests.RequestException:
            pass

    @staticmethod
    def _post_json(url: str, payload: dict) -> None:
        try:
            requests.post(url, json=payload, timeout=10)
        except requests.RequestException:
            pass
