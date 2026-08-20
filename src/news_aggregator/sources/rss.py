"""一般 RSS/Atom adapter（feedparser 解析，支援 ETag / Last-Modified 條件式請求）。

config 範例：{"url": "https://simonwillison.net/atom/everything/", "limit": 40}
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

import feedparser
from bs4 import BeautifulSoup

from ..core.paywall import article_paywalled, is_metered, is_paywalled
from .base import FetchResult, RawItem, SourceState, conditional_headers

logger = logging.getLogger(__name__)


def _entry_published(entry) -> datetime | None:
    tm = entry.get("published_parsed") or entry.get("updated_parsed")
    if not tm:
        return None
    return datetime.fromtimestamp(calendar.timegm(tm), tz=timezone.utc)


def _entry_text(entry) -> str | None:
    """取內文：summary 與 content 取較長者。

    有些 feed（例如微信公眾號鏡像）summary 只放十幾個字的導言，全文在 content。
    先前寫死優先 summary，等於只餵給 LLM 一句話，摘要品質直接報廢。
    """
    summary = entry.get("summary") or ""
    content = ""
    if entry.get("content"):
        content = entry["content"][0].get("value", "") or ""
    raw = max(summary, content, key=len)
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return text[:2000] or None


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _source_link(entry) -> str | None:
    """從 description/summary HTML 抽第一個站外 <a href>（如 Techmeme 帶 gift token 的原文連結）。"""
    raw = entry.get("summary") or ""
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    if not raw:
        return None
    own = _host(entry.get("link") or "")
    for a in BeautifulSoup(raw, "html.parser").find_all("a", href=True):
        host = _host(a["href"])
        if host and host != own:
            return a["href"]
    return None


async def _paywall_blocked(client, url: str, publisher: str | None) -> bool:
    """全站付費（URL 或 Google News <source> 出版商）直接擋；metered 域名看文章頁標記。"""
    if is_paywalled(url) or is_paywalled(publisher):
        return True
    return is_metered(url) and await article_paywalled(client, url)


def _to_item(state: SourceState, entry, url: str, external_id: str) -> RawItem:
    return RawItem(
        source_name=state.name,
        external_id=external_id,
        url=url,
        title=entry.get("title", ""),
        author=entry.get("author"),
        published_at=_entry_published(entry),
        metrics={"score": None, "comments": None, "views": None, "stars": None},
        content=_entry_text(entry),
    )


class RSSAdapter:
    source_type = "rss"

    async def fetch(self, client, state: SourceState) -> FetchResult:
        url = (state.config or {}).get("url")
        if not url:
            return FetchResult(items=[])
        limit = int((state.config or {}).get("limit", 40))
        extract = bool((state.config or {}).get("extract_source_link"))
        # 深度來源用:內文太短的條目直接不收——「點進去資訊量太少」是主人最大的痛點
        min_chars = int((state.config or {}).get("min_chars", 0))

        resp = await client.get(url, headers=conditional_headers(state))
        if resp.status_code == 304:
            return FetchResult(
                not_modified=True, etag=state.etag, last_modified=state.last_modified
            )

        parsed = feedparser.parse(resp.content)
        items: list[RawItem] = []
        blocked = 0
        too_short = 0
        for entry in parsed.entries[:limit]:
            link = entry.get("link") or ""
            external_id = str(entry.get("id") or link)
            if not link:
                continue
            item_url = (_source_link(entry) if extract else None) or link
            # Google News 條目的 link 是跳轉網址，真正出版商在 <source url=...>
            publisher = (entry.get("source") or {}).get("href")
            if await _paywall_blocked(client, item_url, publisher):
                blocked += 1
                continue
            raw = _to_item(state, entry, item_url, external_id)
            if min_chars and len(raw.content or "") < min_chars:
                too_short += 1
                continue
            items.append(raw)
        if blocked:
            logger.info("來源 %s 擋掉 %d 則付費牆條目", state.name, blocked)
        if too_short:
            logger.info("來源 %s 擋掉 %d 則過短條目（<%d 字）", state.name, too_short, min_chars)

        return FetchResult(
            items=items,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )
