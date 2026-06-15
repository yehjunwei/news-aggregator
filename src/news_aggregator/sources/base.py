"""SourceAdapter 統一介面與資料結構。新增來源 = 新增 adapter + 在 registry 註冊。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class RawItem:
    source_name: str
    external_id: str
    url: str
    title: str
    author: str | None = None
    published_at: datetime | None = None  # UTC
    metrics: dict[str, int | None] = field(default_factory=dict)  # score/comments/views/stars
    content: str | None = None


@dataclass
class SourceState:
    """單一來源的抓取狀態（由 DB sources 列載入）。"""

    name: str
    etag: str | None = None
    last_modified: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    items: list[RawItem] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


class SourceAdapter(Protocol):
    source_type: str

    async def fetch(self, client, state: SourceState) -> FetchResult: ...


def conditional_headers(state: SourceState) -> dict[str, str]:
    """依 ETag / Last-Modified 組出條件式請求 header。"""
    headers: dict[str, str] = {}
    if state.etag:
        headers["If-None-Match"] = state.etag
    if state.last_modified:
        headers["If-Modified-Since"] = state.last_modified
    return headers
