"""來源 registry：type -> adapter 實例。pipeline 只依賴本表與 DB 啟用的 sources。"""

from __future__ import annotations

from .base import SourceAdapter
from .github_search import GitHubSearchAdapter
from .hackernews import HackerNewsAdapter
from .rss import RSSAdapter


def build_registry(github_token: str = "") -> dict[str, SourceAdapter]:
    adapters: list[SourceAdapter] = [
        HackerNewsAdapter(),
        GitHubSearchAdapter(token=github_token),
        RSSAdapter(),
    ]
    return {adapter.source_type: adapter for adapter in adapters}
