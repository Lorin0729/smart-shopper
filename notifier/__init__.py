"""降价/补货通知：控制台 + 多渠道推送。

支持的渠道（config -> notify，可同时启用多个）：
- serverchan_key      Server酱（推送到微信）：https://sct.ftqq.com/
- dingtalk_webhook    钉钉群机器人（安全设置建议用"自定义关键词"，如包含"价格"）
- wecom_webhook       企业微信群机器人
- telegram_*          Telegram Bot（bot_token + chat_id）
- webhook_url         通用 JSON Webhook（POST {"title","text","markdown"}）
"""
from __future__ import annotations

import requests

TIMEOUT = 10


class Notifier:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.serverchan_key = cfg.get("serverchan_key", "")
        self.dingtalk_webhook = cfg.get("dingtalk_webhook", "")
        self.wecom_webhook = cfg.get("wecom_webhook", "")
        self.telegram_token = cfg.get("telegram_bot_token", "")
        self.telegram_chat = cfg.get("telegram_chat_id", "")
        self.webhook_url = cfg.get("webhook_url", "")

    def send(self, title: str, lines: list[str]) -> None:
        body = "\n".join(lines)
        print(f"\n=== {title} ===")
        print(body)
        if not body.strip():
            return
        if self.serverchan_key:
            _form(f"https://sctapi.ftqq.com/{self.serverchan_key}.send",
                  {"title": title, "desp": body})
        if self.dingtalk_webhook:
            _json(self.dingtalk_webhook, {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": f"## {title}\n\n" + "\n\n".join(lines)},
            })
        if self.wecom_webhook:
            _json(self.wecom_webhook, {
                "msgtype": "markdown",
                "markdown": {"content": f"**{title}**\n" + "\n".join(lines)},
            })
        if self.telegram_token and self.telegram_chat:
            _json(f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                  {"chat_id": self.telegram_chat, "text": f"{title}\n{body}"})
        if self.webhook_url:
            _json(self.webhook_url, {"title": title, "text": body, "markdown": body})


def _form(url: str, data: dict) -> None:
    try:
        requests.post(url, data=data, timeout=TIMEOUT)
    except requests.RequestException:
        pass


def _json(url: str, payload: dict) -> None:
    try:
        requests.post(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException:
        pass
