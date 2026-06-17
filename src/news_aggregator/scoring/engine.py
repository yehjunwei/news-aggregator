"""評分引擎：final = w_interest × (relevance/100) × (1 + velocity_boost) × recency_decay。

權重為兩級（高 / 中），其餘類別給較低 fallback 權重。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ..core.timez import now_utc, to_utc

HIGH = 1.0
MID = 0.6
FALLBACK = 0.4

CATEGORY_WEIGHTS = {
    # 高權重
    "ai_agents": HIGH,
    "ai_coding": HIGH,
    "dev_tools": HIGH,
    "github_project": HIGH,
    "hackernews": HIGH,
    "yt_interview": HIGH,
    "new_app": HIGH,
    "product_hunt": HIGH,
    # 中權重
    "nba": MID,
    "card_collecting": MID,
    "automotive": MID,
    "holdings": MID,
    "ai_application": MID,
    "ai_paper": MID,
    "tech_feature": MID,
    "world_cup": MID,
    "book_review": MID,
    "tv_streaming": MID,
    # 其他
    "other": FALLBACK,
}


def interest_weight(category: str | None) -> float:
    if not category:
        return FALLBACK
    return CATEGORY_WEIGHTS.get(category, FALLBACK)


def recency_decay(
    published_at: datetime | None,
    half_life_hours: float = 24.0,
    ref: datetime | None = None,
) -> float:
    """指數衰減，半衰期 half_life_hours；未知發布時間給 0.5。"""
    if published_at is None:
        return 0.5
    ref = ref or now_utc()
    # DB（SQLite）讀回的 datetime 可能是 naive，統一視為 UTC
    age_hours = max((ref - to_utc(published_at)).total_seconds() / 3600.0, 0.0)
    return 0.5 ** (age_hours / half_life_hours)


def velocity_boost(metric_points: list[tuple[datetime, int | None]], cap: float = 1.0) -> float:
    """由 (時間, 數值) 時序算熱度增速，log 壓縮並封頂。"""
    points = [(t, v) for t, v in metric_points if v is not None]
    if len(points) < 2:
        return 0.0
    points.sort(key=lambda p: p[0])
    (t0, v0), (t1, v1) = points[0], points[-1]
    dt_hours = max((t1 - t0).total_seconds() / 3600.0, 1e-6)
    rate = (v1 - v0) / dt_hours
    if rate <= 0:
        return 0.0
    return min(math.log1p(rate) / 5.0, cap)


@dataclass
class ScoreInput:
    category: str | None = None
    personal_relevance_score: int | None = None
    published_at: datetime | None = None
    metric_points: list[tuple[datetime, int | None]] = field(default_factory=list)


def compute_score(
    inp: ScoreInput,
    *,
    half_life_hours: float = 24.0,
    velocity_cap: float = 1.0,
    ref: datetime | None = None,
) -> float:
    weight = interest_weight(inp.category)
    relevance = (
        inp.personal_relevance_score if inp.personal_relevance_score is not None else 50
    ) / 100.0
    velocity = velocity_boost(inp.metric_points, velocity_cap)
    recency = recency_decay(inp.published_at, half_life_hours, ref)
    return weight * relevance * (1.0 + velocity) * recency
