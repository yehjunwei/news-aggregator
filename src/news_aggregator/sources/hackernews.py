"""Hacker News（Firebase API）adapter。

config 範例：{"story_type": "top", "limit": 50, "min_score": 0}
story_type: top | new | best
"""

from __future__ import annotations

import asyncio

from ..core.timez import parse_epoch
from .base import FetchResult, RawItem, SourceState

BASE = "https://hacker-news.firebaseio.com/v0"


class HackerNewsAdapter:
    source_type = "hackernews"

    async def fetch(self, client, state: SourceState) -> FetchResult:
        cfg = state.config or {}
        story_type = cfg.get("story_type", "top")
        limit = int(cfg.get("limit", 50))
        min_score = int(cfg.get("min_score", 0))

        resp = await client.get(f"{BASE}/{story_type}stories.json")
        ids = (resp.json() or [])[:limit]

        async def _fetch_item(item_id):
            r = await client.get(f"{BASE}/item/{item_id}.json")
            return r.json()

        details = await asyncio.gather(
            *[_fetch_item(i) for i in ids], return_exceptions=True
        )

        items: list[RawItem] = []
        for detail in details:
            if isinstance(detail, Exception) or not detail:
                continue
            if detail.get("type") != "story" or detail.get("dead") or detail.get("deleted"):
                continue
            score = detail.get("score") or 0
            if score < min_score:
                continue
            hn_url = f"https://news.ycombinator.com/item?id={detail['id']}"
            items.append(
                RawItem(
                    source_name=state.name,
                    external_id=str(detail["id"]),
                    url=detail.get("url") or hn_url,
                    title=detail.get("title", ""),
                    author=detail.get("by"),
                    published_at=parse_epoch(detail["time"]) if detail.get("time") else None,
                    metrics={
                        "score": score,
                        "comments": detail.get("descendants") or 0,
                        "views": None,
                        "stars": None,
                    },
                )
            )
        return FetchResult(items=items)
