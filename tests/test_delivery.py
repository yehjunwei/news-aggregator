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
        "metrics": {"score": 120, "comments": 30},
    }
    base.update(kw)
    return base


def test_render_markdown_includes_url_and_source():
    md = render_markdown([_item()], RUN_DT)
    assert "2026-06-15" in md
    assert "https://example.com/very/long/url" in md
    assert "hackernews-top" in md
    assert "中文標題" in md


def test_render_telegram_title_is_hyperlink_and_escapes():
    header, blocks = render_telegram([_item(title_zh="A < B & C")], RUN_DT)
    assert "每日新聞精選" in header
    # 標題為超連結，連結指向原始網址，標題內容正確跳脫
    assert '<a href="https://example.com/very/long/url">A &lt; B &amp; C</a>' in blocks[0]


def test_paywall_marker():
    # 裸 Bloomberg 連結：HTML 與 MD 標題前都標 💰；子網域也算
    _, blocks = render_telegram([_item(url="https://www.bloomberg.com/news/x")], RUN_DT)
    assert blocks[0].startswith("💰 <b>")
    md = render_markdown([_item(url="https://www.wsj.com/articles/y")], RUN_DT)
    assert "### 💰 [中文標題]" in md
    # 名單外域名不標
    _, blocks = render_telegram([_item()], RUN_DT)
    assert "💰" not in blocks[0]


def test_paywall_gift_link_and_lookalike_exempt():
    # gift link（accessToken）豁免
    _, blocks = render_telegram(
        [_item(url="https://www.bloomberg.com/news/x?accessToken=gift123")], RUN_DT
    )
    assert "💰" not in blocks[0]
    # 後綴仿冒域名不誤標
    _, blocks = render_telegram([_item(url="https://bloomberg.com.evil.io/x")], RUN_DT)
    assert "💰" not in blocks[0]


def test_item_keyboard_carries_item_id():
    kb = item_keyboard(42)
    buttons = kb["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["fb:42:up", "fb:42:down"]
