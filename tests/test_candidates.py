"""_select_candidates 的迴歸測試：prefer_sources 的 0 分文章必須進得了候選。

2026-09-08 的回歸：top_undelivered 的 `final_score > 0` 把相關度被評 0 分的 KOL
長文全砍在上游，_select_candidates 裡的豁免因此從來沒生效過。
"""

from __future__ import annotations

from datetime import datetime, timezone

from news_aggregator.config import Settings
from news_aggregator.db.models import Item, Source
from news_aggregator.pipeline import _select_candidates

PROF = {"categories": ["ai_agents"], "min_personal_score": 78,
        "prefer_sources": ["kol-martin-fowler"]}


def _item(source, eid, title, **kw):
    return Item(source_id=source.id, external_id=eid, url=f"https://x/{eid}",
                canonical_url=f"https://x/{eid}", content_hash=eid, title=title,
                first_seen_at=datetime.now(timezone.utc), enriched=True, delivered=False, **kw)


def _seed(session):
    kol = Source(name="kol-martin-fowler", type="rss", enabled=True)
    news = Source(name="tickers-news", type="rss", enabled=True)
    session.add_all([kol, news])
    session.flush()
    session.add_all([
        # KOL 長文：相關度 0 → final_score 0，正是被誤殺的那種
        _item(kol, "k1", "Fowler on X", category="other",
              personal_relevance_score=0, final_score=0.0),
        # 一般新聞、同樣 0 分 → 必須被擋
        _item(news, "n1", "junk", category="other",
              personal_relevance_score=0, final_score=0.0),
        # 一般新聞、高分 → 照常進候選
        _item(news, "n2", "big news", category="ai_agents",
              personal_relevance_score=90, final_score=0.5),
    ])
    session.commit()


def test_zero_score_kol_survives_but_zero_score_news_does_not(session):
    _seed(session)
    titles = {c.title for c in _select_candidates(session, Settings(), PROF)}
    assert titles == {"Fowler on X", "big news"}


def test_delivered_kol_is_not_recycled(session):
    _seed(session)
    item = session.query(Item).filter_by(external_id="k1").one()
    item.delivered = True
    session.commit()
    titles = {c.title for c in _select_candidates(session, Settings(), PROF)}
    assert titles == {"big news"}


def test_prefer_min_score_blocks_kol_filler_but_keeps_real_posts(session):
    _seed(session)
    kol = session.query(Source).filter_by(name="kol-martin-fowler").one()
    session.add(_item(kol, "k2", "Fowler long read", category="other",
                      personal_relevance_score=50, final_score=0.2))
    session.commit()
    prof = {**PROF, "prefer_min_score": 30}
    titles = {c.title for c in _select_candidates(session, Settings(), prof)}
    # 0 分的 "Fowler on X" 被 30 門檻擋掉；50 分的長文照放；一般高分新聞不受影響
    assert titles == {"Fowler long read", "big news"}


def test_career_profile_keeps_only_scored_career_items(session):
    src = Source(name="tickers-news", type="rss", enabled=True)
    session.add(src)
    session.flush()
    session.add_all([
        _item(src, "c1", "in-car agent", category="automotive", personal_relevance_score=40,
              career_score=80, final_score=0.4),
        _item(src, "c2", "ev sales", category="automotive", personal_relevance_score=90,
              career_score=50, final_score=0.9),
        _item(src, "c3", "old item", category="ai_agents", personal_relevance_score=90,
              career_score=None, final_score=0.9),  # 改版前 enrich 的舊資料
    ])
    session.commit()
    career = {"categories": None, "min_personal_score": 0, "min_career_score": 70}
    assert {c.title for c in _select_candidates(session, Settings(), career)} == {"in-car agent"}
    # 沒設 min_career_score 的 profile 完全不受 career_score 影響
    deep = {"categories": None, "min_personal_score": 0}
    assert {c.title for c in _select_candidates(session, Settings(), deep)} == {
        "in-car agent", "ev sales", "old item"}
