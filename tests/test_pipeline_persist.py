from datetime import timedelta

from sqlalchemy import select

from news_aggregator.config import Settings
from news_aggregator.core.timez import now_utc
from news_aggregator.db.models import Item, ItemMetric, Source
from news_aggregator.pipeline import (
    load_enabled_sources,
    persist_results,
    score_undelivered,
    seed_sources,
    top_undelivered,
)
from news_aggregator.sources.base import FetchResult, RawItem


def _settings():
    return Settings(dedup_lookback_days=7, dedup_title_threshold=88, credentials_file="/nonexistent")


def _raw(source_name, ext, title, url, score=100):
    return RawItem(
        source_name=source_name,
        external_id=ext,
        url=url,
        title=title,
        published_at=now_utc() - timedelta(hours=1),
        metrics={"score": score, "comments": 1, "views": None, "stars": None},
    )


def _entry(name, enabled=None):
    e = {"name": name, "type": "rss", "config": {}}
    if enabled is not None:
        e["enabled"] = enabled
    return e


def test_seed_disables_absent_sources(session):
    seed_sources(session, [_entry("a"), _entry("b")])
    assert {s.name for s in load_enabled_sources(session)} == {"a", "b"}
    # 再 seed 只含 a → b 被停用、a 仍啟用
    seed_sources(session, [_entry("a")])
    assert {s.name for s in load_enabled_sources(session)} == {"a"}


def test_seed_entry_enabled_false_not_treated_as_orphan(session):
    seed_sources(session, [_entry("a"), _entry("c", enabled=False)])
    # 再 seed 同樣兩筆：c 維持停用、a 維持啟用，c 不因不在 enabled 而被重複處理
    seed_sources(session, [_entry("a"), _entry("c", enabled=False)])
    states = {s.name: s.enabled for s in session.scalars(select(Source)).all()}
    assert states["a"] is True and states["c"] is False


def test_persist_creates_new_and_dedups_repeat(session):
    src = Source(name="hn", type="hackernews", config={})
    session.add(src)
    session.commit()

    fr = FetchResult(items=[_raw("hn", "1", "Cool AI tool", "https://e.com/a")])
    new_count = persist_results(session, [(src, fr)], _settings())
    assert new_count == 1
    assert session.query(Item).count() == 1
    assert session.query(ItemMetric).count() == 1

    # 第二次同 external_id：不新增 item，但追加一筆 metric
    fr2 = FetchResult(items=[_raw("hn", "1", "Cool AI tool", "https://e.com/a", score=150)])
    new_count2 = persist_results(session, [(src, fr2)], _settings())
    assert new_count2 == 0
    assert session.query(Item).count() == 1
    assert session.query(ItemMetric).count() == 2


def test_persist_dedups_by_canonical_url_across_sources(session):
    s1 = Source(name="hn", type="hackernews", config={})
    s2 = Source(name="rss", type="rss", config={})
    session.add_all([s1, s2])
    session.commit()

    persist_results(session, [(s1, FetchResult(items=[_raw("hn", "1", "Story", "https://e.com/x")]))], _settings())
    # 不同來源、不同 external_id，但 canonical URL 相同（utm 差異）→ 視為重複
    persist_results(
        session,
        [(s2, FetchResult(items=[_raw("rss", "z", "Story diff title", "https://www.e.com/x?utm_source=rss")]))],
        _settings(),
    )
    assert session.query(Item).count() == 1


def test_score_and_rank(session):
    src = Source(name="hn", type="hackernews", config={})
    session.add(src)
    session.commit()
    persist_results(
        session,
        [(src, FetchResult(items=[
            _raw("hn", "1", "AI agents framework", "https://e.com/a"),
            _raw("hn", "2", "Random thing", "https://e.com/b"),
        ]))],
        _settings(),
    )
    # 指定 category 讓權重不同
    items = session.query(Item).order_by(Item.external_id).all()
    items[0].category = "ai_agents"
    items[0].personal_relevance_score = 90
    items[1].category = "other"
    items[1].personal_relevance_score = 40
    session.commit()

    score_undelivered(session, _settings())
    top = top_undelivered(session, 10)
    assert top[0].external_id == "1"
    assert all(i.final_score > 0 for i in top)
