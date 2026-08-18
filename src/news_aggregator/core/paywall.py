"""付費牆判斷：全站付費域名直接擋；metered 域名要 GET 文章頁看 schema.org 標記。

原則：判斷不了一律放行（寧可漏擋，不誤殺免費文章）。
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlsplit

logger = logging.getLogger(__name__)

# 全站付費：命中即擋（gift link 帶 accessToken 者豁免）
PAYWALLED_DOMAINS = frozenset({
    "bloomberg.com", "theinformation.com", "wsj.com",
    "ft.com", "nytimes.com", "economist.com", "barrons.com",
})

# 部分文章付費：需抓文章頁確認
METERED_DOMAINS = frozenset({"theverge.com"})

# 有付費牆又要被 Google 索引的頁面，依規範須在 JSON-LD 標 "isAccessibleForFree": false
_NOT_FREE = re.compile(r'"isAccessibleForFree"\s*:\s*"?false"?', re.IGNORECASE)


def _host_matches(url: str | None, domains: frozenset[str]) -> bool:
    if not url:
        return False
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def is_paywalled(url: str | None) -> bool:
    """URL 指向全站付費域名則 True；帶 accessToken query（gift link）豁免。"""
    if not _host_matches(url, PAYWALLED_DOMAINS):
        return False
    return not any(k == "accessToken" for k, _ in parse_qsl(urlsplit(url).query))


def is_metered(url: str | None) -> bool:
    return _host_matches(url, METERED_DOMAINS)


async def article_paywalled(client, url: str) -> bool:
    """GET 文章頁看付費標記；抓不到頁面或無標記 → False（放行）。"""
    try:
        resp = await client.get(url)
        return bool(_NOT_FREE.search(resp.text))
    except Exception as exc:  # noqa: BLE001 - 檢查失敗一律放行
        logger.info("paywall 檢查失敗，放行 %s：%s", url, exc)
        return False
