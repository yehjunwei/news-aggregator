from datetime import timedelta

from news_aggregator.core.timez import now_utc
from news_aggregator.scoring.engine import (
    ScoreInput,
    compute_score,
    interest_weight,
    recency_decay,
    velocity_boost,
)


def test_interest_weight_tiers():
    assert interest_weight("ai_agents") == 1.0
    assert interest_weight("nba") == 0.6
    assert interest_weight("unknown") == 0.4
    assert interest_weight(None) == 0.4


def test_recency_decay_half_life():
    ref = now_utc()
    published = ref - timedelta(hours=24)
    assert abs(recency_decay(published, 24.0, ref) - 0.5) < 1e-6


def test_recency_decay_unknown():
    assert recency_decay(None) == 0.5


def test_velocity_boost_increases_with_growth():
    ref = now_utc()
    pts = [(ref - timedelta(hours=2), 10), (ref, 100)]
    assert velocity_boost(pts) > 0


def test_velocity_boost_zero_for_no_growth():
    ref = now_utc()
    pts = [(ref - timedelta(hours=2), 100), (ref, 100)]
    assert velocity_boost(pts) == 0.0


def test_velocity_boost_single_point():
    assert velocity_boost([(now_utc(), 5)]) == 0.0


def test_compute_score_high_beats_mid_all_else_equal():
    ref = now_utc()
    published = ref - timedelta(hours=1)
    high = compute_score(
        ScoreInput(category="ai_agents", personal_relevance_score=80, published_at=published),
        ref=ref,
    )
    mid = compute_score(
        ScoreInput(category="nba", personal_relevance_score=80, published_at=published),
        ref=ref,
    )
    assert high > mid > 0


def test_compute_score_default_relevance_when_missing():
    ref = now_utc()
    score = compute_score(
        ScoreInput(category="other", personal_relevance_score=None, published_at=ref),
        ref=ref,
    )
    # weight 0.4 * 0.5 * 1 * 1.0 (age 0)
    assert abs(score - 0.2) < 1e-6
