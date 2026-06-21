"""Telegram 推送：HTML parse_mode（較好跳脫），超長自動以區塊邊界分段。"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

class TelegramClient:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self.chat_id = chat_id
        self._client = client

    async def send(
        self, text: str, parse_mode: str = "HTML", reply_markup: dict | None = None
    ) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        finally:
            if self._client is None:
                await client.aclose()

    async def send_all(self, messages: list[str]) -> None:
        for message in messages:
            await self.send(message)

    async def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """拉取 callback_query 更新（按鈕點擊）。offset=上次最大 update_id+1，用來 ack。"""
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params: dict = {"timeout": timeout, "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            resp = await client.post(url, json=params)
            resp.raise_for_status()
            return resp.json().get("result", []) or []
        finally:
            if self._client is None:
                await client.aclose()

    async def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        """回應按鈕點擊，讓 Telegram 顯示已收到的小提示。"""
        url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            await client.post(url, json={"callback_query_id": callback_query_id, "text": text})
        finally:
            if self._client is None:
                await client.aclose()
