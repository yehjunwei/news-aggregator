"""Hacker News（Firebase API）adapter。

config 範例：{"story_type": "top", "limit": 50, "min_score": 0}
story_type: top | new | best
"""

from __future__ import annotations

import asyncio

from ..core.timez import parse_epoch
from .base import FetchResult, RawItem, SourceState

BASE = "https://hacker-news.firebaseio.com/v0"


def _story_to_raw(detail, source_name: str, min_score: int) -> RawItem | None:
    if not detail or detail.get("type") != "story" or detail.get("dead") or detail.get("deleted"):
        return None
    score = detail.get("score") or 0
    if score < min_score:
        return None
    hn_url = f"https://news.ycombinator.com/item?id={detail['id']}"
    return RawItem(
        source_name=source_name,
        external_id=str(detail["id"]),
        url=detail.get("url") or hn_url,
        title=detail.get("title", ""),
        author=detail.get("by"),
        published_at=parse_epoch(detail["time"]) if detail.get("time") else None,
        metrics={"score": score, "comments": detail.get("descendants") or 0, "views": None, "stars": None},
    )


class HackerNewsAdapter:
    source_type = "hackernews"

    async def fetch(self, client, state: SourceState) -> FetchResult:
        cfg = state.config or {}
        limit = int(cfg.get("limit", 50))
        min_score = int(cfg.get("min_score", 0))
        resp = await client.get(f"{BASE}/{cfg.get('story_type', 'top')}stories.json")
        ids = (resp.json() or [])[:limit]

        async def _fetch_item(item_id):
            r = await client.get(f"{BASE}/item/{item_id}.json")
            return r.json()

        details = await asyncio.gather(*[_fetch_item(i) for i in ids], return_exceptions=True)
        items = [
            raw for d in details if not isinstance(d, Exception)
            if (raw := _story_to_raw(d, state.name, min_score)) is not None
        ]
        return FetchResult(items=items)
