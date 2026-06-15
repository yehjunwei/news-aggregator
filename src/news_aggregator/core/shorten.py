"""URL 縮短：用 TinyURL 免金鑰 API，失敗時 fallback 原網址。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TINYURL_ENDPOINT = "https://tinyurl.com/api-create.php"


async def shorten_url(client, url: str) -> str:
    """以 TinyURL 縮短網址；任何失敗都回傳原網址（best-effort）。

    client 需具備 async ``get(url, *, params=...)`` 介面（HttpClient 或 httpx.AsyncClient）。
    """
    if not url:
        return url
    try:
        resp = await client.get(TINYURL_ENDPOINT, params={"url": url})
        text = (resp.text or "").strip()
        if resp.status_code == 200 and text.startswith("http") and "error" not in text.lower():
            return text
        logger.warning("URL 縮短回應異常 (%s)：%s", resp.status_code, text[:120])
    except Exception as exc:  # noqa: BLE001 - 縮短為非關鍵路徑
        logger.warning("URL 縮短失敗 %s：%s", url, exc)
    return url
