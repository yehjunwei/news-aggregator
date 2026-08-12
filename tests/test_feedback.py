from news_aggregator.db.models import Item, Source
import pytest

from news_aggregator.feedback import _apply_update, _parse, feedback_examples, record_feedback


def test_parse_valid_and_invalid():
    assert _parse("fb:42:up") == (42, 1)
    assert _parse("fb:7:down") == (7, -1)
    assert _parse("fb:7:sideways") is None
    assert _parse("nope") is None
    assert _parse("fb:x:up") is None
    assert _parse("") is None


def _item(session, ext, title, fb=None):
    src = session.query(Source).first()
    if src is None:
        src = Source(name="s", type="rss", config={})
        session.add(src)
        session.commit()
    it = Item(
        source_id=src.id, external_id=ext, canonical_url=f"https://e/{ext}",
        url=f"https://e/{ext}", title=title, content_hash=ext, feedback=fb,
    )
    session.add(it)
    session.commit()
    return it


def test_apply_update_sets_feedback_and_collects_acks(session):
    it = _item(session, "1", "Some story")
    updates = [
        {"update_id": 10, "callback_query": {"id": "cb1", "data": f"fb:{it.id}:up"}},
        {"update_id": 11, "callback_query": {"id": "cb2", "data": "garbage"}},
        {"update_id": 12, "callback_query": {"id": "cb3", "data": "fb:99999:down"}},  # 不存在的 item
    ]
    applied, max_id, acks = _apply_update(session, updates)
    session.commit()
    assert applied == 1
    assert max_id == 12
    assert it.feedback == 1
    # 有效格式都要 ack（即使 item 不存在也回應，避免使用者以為沒收到）
    assert ("cb1", 1) in acks and ("cb3", -1) in acks
    assert all(a[0] != "cb2" for a in acks)  # 格式錯誤的不 ack


def test_record_feedback_up_and_down(session):
    it = _item(session, "10", "Story X")
    assert "👍" in record_feedback(session, f"fb:{it.id}:up")
    assert it.feedback == 1
    assert "👎" in record_feedback(session, f"fb:{it.id}:down")
    assert it.feedback == -1


def test_record_feedback_rejects_bad_input(session):
    with pytest.raises(ValueError):
        record_feedback(session, "garbage")
    with pytest.raises(LookupError):
        record_feedback(session, "fb:99999:up")


def test_feedback_examples_splits_liked_disliked(session):
    _item(session, "1", "Liked A", fb=1)
    _item(session, "2", "Disliked B", fb=-1)
    _item(session, "3", "No feedback", fb=None)
    ex = feedback_examples(session)
    assert ex["liked"] == ["Liked A"]
    assert ex["disliked"] == ["Disliked B"]
