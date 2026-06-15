"""時間工具：一律以 UTC 儲存，顯示時轉 Asia/Taipei。"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
TAIPEI = ZoneInfo("Asia/Taipei")


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    """naive datetime 視為 UTC；aware datetime 轉為 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_taipei(dt: datetime) -> datetime:
    return to_utc(dt).astimezone(TAIPEI)


def format_taipei(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return to_taipei(dt).strftime(fmt)


def parse_epoch(ts: int | float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)
