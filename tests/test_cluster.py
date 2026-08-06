"""同事件去重：分組清理 + 推送前過濾。用假 provider，不打網路。"""

import asyncio
from datetime import timedelta

from news_aggregator.config import Settings
from news_aggregator.core.timez import now_utc
from news_aggregator.db.models import Item, Source
from news_aggregator.enrich.cluster import MAX_CANDIDATES, group_same_event
from news_aggregator.pipeline import drop_duplicate_events


class _FakeProvider:
    """回固定分組；groups=None 代表呼叫時拋例外。"""

    model = "fake"

    def __init__(self, groups):
        self.groups = groups
        self.usage = {"prompt": 0, "completion": 0, "total": 0}
        self.calls = 0
        self.last_user = ""

    async def complete_json(self, system, user, schema=None):
        self.calls += 1
        self.last_user = user
        if self.groups is None:
            raise RuntimeError("boom")
        return {"groups": [{"ids": g} for g in self.groups]}


def _settings():
    return Settings(candidate_max_age_days=3, credentials_file="/nonexistent")


def _seed(session, specs):
    """specs: [(title, score, delivered)] -> 依序建立 Item，回傳 list。"""
    src = Source(name="s", type="rss", config={})
    session.add(src)
    session.commit()
    items = []
    for title, score, delivered in specs:
        it = Item(
            source_id=src.id, external_id=title, url=f"https://e.com/{title}",
            canonical_url=f"https://e.com/{title}", title=title,
            content_hash=title, final_score=score, delivered=delivered,
            delivered_at=now_utc() - timedelta(hours=1) if delivered else None,
        )
        session.add(it)
        items.append(it)
    session.commit()
    return items


def _undelivered(items):
    """模擬 _select_candidates 的輸出：未推送、分數降序。"""
    return sorted(
        [i for i in items if not i.delivered],
        key=lambda i: i.final_score, reverse=True,
    )


# --------------------------------------------------------------------------- #
# group_same_event
# --------------------------------------------------------------------------- #
def test_drops_unknown_ids_and_singleton_groups():
    p = _FakeProvider([[1, 999], [2, 3], [4]])
    entries = [{"id": i, "title": f"t{i}"} for i in (1, 2, 3, 4)]
    # [1, 999] 只剩 1 個有效 id → 丟；[4] 單元素 → 丟
    assert asyncio.run(group_same_event(p, entries)) == [[2, 3]]


def test_id_only_kept_in_first_group():
    p = _FakeProvider([[1, 2], [2, 3]])
    entries = [{"id": i, "title": f"t{i}"} for i in (1, 2, 3)]
    # 2 已被第一組用掉 → 第二組只剩 [3] 不成組
    assert asyncio.run(group_same_event(p, entries)) == [[1, 2]]


def test_empty_entries_does_not_call_provider():
    p = _FakeProvider([[1, 2]])
    assert asyncio.run(group_same_event(p, [])) == []
    assert p.calls == 0


def test_provider_failure_returns_empty():
    p = _FakeProvider(None)
    entries = [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]
    assert asyncio.run(group_same_event(p, entries)) == []


# --------------------------------------------------------------------------- #
# drop_duplicate_events
# --------------------------------------------------------------------------- #
def test_keeps_highest_score_in_group(session):
    items = _seed(session, [("a", 0.9, False), ("b", 0.5, False), ("c", 0.3, False)])
    a, b, c = items
    p = _FakeProvider([[a.id, b.id]])
    out = asyncio.run(drop_duplicate_events(session, _settings(), p, _undelivered(items)))
    assert [i.id for i in out] == [a.id, c.id]  # b 被丟，順序仍為分數降序


def test_group_with_delivered_item_drops_all_candidates(session):
    items = _seed(session, [("old", 0.9, True), ("new", 0.8, False), ("other", 0.7, False)])
    old, new, other = items
    p = _FakeProvider([[old.id, new.id]])
    out = asyncio.run(drop_duplicate_events(session, _settings(), p, _undelivered(items)))
    assert [i.id for i in out] == [other.id]  # 早上推過的事件，晚上不再推


def test_no_groups_keeps_everything(session):
    items = _seed(session, [("a", 0.9, False), ("b", 0.5, False)])
    p = _FakeProvider([])
    out = asyncio.run(drop_duplicate_events(session, _settings(), p, _undelivered(items)))
    assert len(out) == 2


def test_provider_failure_keeps_everything(session):
    items = _seed(session, [("a", 0.9, False), ("b", 0.5, False)])
    p = _FakeProvider(None)
    out = asyncio.run(drop_duplicate_events(session, _settings(), p, _undelivered(items)))
    assert len(out) == 2


def test_candidates_beyond_cap_are_preserved(session):
    n = MAX_CANDIDATES + 5
    items = _seed(session, [(f"i{i:03d}", 1.0 - i * 0.001, False) for i in range(n)])
    cands = _undelivered(items)
    p = _FakeProvider([[cands[0].id, cands[1].id]])
    out = asyncio.run(drop_duplicate_events(session, _settings(), p, cands))
    assert len(out) == n - 1  # 只丟掉那一則重複
    assert [i.id for i in out[-5:]] == [i.id for i in cands[-5:]]  # 尾端 5 則原封不動


def test_uses_title_zh_when_present(session):
    items = _seed(session, [("a", 0.9, False), ("b", 0.5, False)])
    items[0].title_zh = "湖人擊敗勇士"
    session.commit()
    p = _FakeProvider([])
    asyncio.run(drop_duplicate_events(session, _settings(), p, _undelivered(items)))
    assert "湖人擊敗勇士" in p.last_user  # 送進 prompt 的是中文標題
    assert "- id=" in p.last_user
