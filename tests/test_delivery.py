from datetime import datetime, timezone

from news_aggregator.delivery.digest import item_keyboard, render_markdown, render_telegram

RUN_DT = datetime(2026, 6, 14, 22, 30, tzinfo=timezone.utc)  # 台北 06-15 06:30


def _item(**kw):
    base = {
        "title": "Original Title",
        "title_zh": "中文標題",
        "summary": "這是摘要。",
        "why_relevant": "與你相關。",
        "personal_relevance_score": 90,
        "source_name": "hackernews-top",
        "url": "https://example.com/very/long/url",
        "short_url": "https://is.gd/abc",
        "metrics": {"score": 120, "comments": 30},
    }
    base.update(kw)
    return base


def test_render_markdown_includes_short_url_and_source():
    md = render_markdown([_item()], RUN_DT)
    assert "2026-06-15" in md
    assert "https://is.gd/abc" in md
    assert "hackernews-top" in md
    assert "中文標題" in md


def test_render_telegram_escapes_and_uses_short_url():
    header, blocks = render_telegram([_item(title_zh="A < B & C")], RUN_DT)
    assert "每日新聞精選" in header
    assert "A &lt; B &amp; C" in blocks[0]
    assert "https://is.gd/abc" in blocks[0]


def test_render_falls_back_to_original_url_without_short():
    md = render_markdown([_item(short_url=None)], RUN_DT)
    assert "https://example.com/very/long/url" in md


def test_item_keyboard_carries_item_id():
    kb = item_keyboard(42)
    buttons = kb["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["fb:42:up", "fb:42:down"]
