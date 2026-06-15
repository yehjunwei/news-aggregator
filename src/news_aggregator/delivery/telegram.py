"""Telegram 推送：HTML parse_mode（較好跳脫），超長自動以區塊邊界分段。"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

TG_LIMIT = 4096


def split_messages(blocks: list[str], *, limit: int = TG_LIMIT, header: str = "") -> list[str]:
    """把多個區塊合併成數則訊息，盡量在區塊邊界切，單一區塊過長才硬切。"""
    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            messages.append(current)
            current = ""
        if len(block) <= limit:
            current = block
        else:
            for i in range(0, len(block), limit):
                messages.append(block[i : i + limit])
            current = ""
    if current:
        messages.append(current)
    return messages


class TelegramClient:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self.chat_id = chat_id
        self._client = client

    async def send(self, text: str, parse_mode: str = "HTML") -> dict:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            resp = await client.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": False,
                },
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if self._client is None:
                await client.aclose()

    async def send_all(self, messages: list[str]) -> None:
        for message in messages:
            await self.send(message)
