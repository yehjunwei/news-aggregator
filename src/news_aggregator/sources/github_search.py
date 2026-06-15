"""GitHub Search（repositories）adapter。

config 範例：
{"query": "created:>2026-06-01 stars:>50", "sort": "stars", "order": "desc", "limit": 30}
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..core.timez import now_utc, to_utc
from .base import FetchResult, RawItem, SourceState

API = "https://api.github.com/search/repositories"


def _build_query(cfg: dict) -> str:
    """支援 created_within_days / min_stars 動態組查詢，並串接自訂 query。"""
    parts: list[str] = []
    within = cfg.get("created_within_days")
    if within:
        since = (now_utc() - timedelta(days=int(within))).strftime("%Y-%m-%d")
        parts.append(f"created:>={since}")
    min_stars = cfg.get("min_stars")
    if min_stars is not None:
        parts.append(f"stars:>={int(min_stars)}")
    if cfg.get("query"):
        parts.append(cfg["query"])
    return " ".join(parts) if parts else "stars:>100"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


class GitHubSearchAdapter:
    source_type = "github_search"

    def __init__(self, token: str = ""):
        self.token = token

    async def fetch(self, client, state: SourceState) -> FetchResult:
        cfg = state.config or {}
        query = _build_query(cfg)
        sort = cfg.get("sort", "stars")
        order = cfg.get("order", "desc")
        limit = int(cfg.get("limit", 30))

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        resp = await client.get(
            API,
            headers=headers,
            params={"q": query, "sort": sort, "order": order, "per_page": min(limit, 100)},
        )
        data = resp.json() or {}

        items: list[RawItem] = []
        for repo in (data.get("items") or [])[:limit]:
            description = repo.get("description") or ""
            title = f"{repo['full_name']}: {description}".strip().rstrip(":").strip()
            items.append(
                RawItem(
                    source_name=state.name,
                    external_id=str(repo["id"]),
                    url=repo["html_url"],
                    title=title,
                    author=(repo.get("owner") or {}).get("login"),
                    published_at=_parse_iso(repo.get("created_at")),
                    metrics={
                        "score": repo.get("stargazers_count"),
                        "comments": repo.get("open_issues_count"),
                        "views": repo.get("watchers_count"),
                        "stars": repo.get("stargazers_count"),
                    },
                    content=description or None,
                )
            )
        return FetchResult(items=items)
