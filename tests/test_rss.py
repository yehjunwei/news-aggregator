import httpx
import respx

from news_aggregator.core.http import HttpClient
from news_aggregator.sources.base import SourceState
from news_aggregator.sources.rss import RSSAdapter

FEED_URL = "https://example.com/feed.xml"

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>Test Feed</title>
<item>
  <title>First Post</title>
  <link>https://example.com/first</link>
  <guid>https://example.com/first</guid>
  <author>bob@example.com</author>
  <pubDate>Sun, 14 Jun 2026 22:30:00 +0000</pubDate>
  <description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description>
</item>
<item>
  <title>Second Post</title>
  <link>https://example.com/second</link>
  <guid>https://example.com/second</guid>
</item>
</channel></rss>
"""


@respx.mock
async def test_rss_parses_entries_and_etag():
    respx.get(FEED_URL).mock(
        return_value=httpx.Response(200, text=ATOM, headers={"ETag": "abc123"})
    )
    adapter = RSSAdapter()
    state = SourceState(name="feed", config={"url": FEED_URL, "limit": 10})
    async with HttpClient(rate_limit_per_host=0) as http:
        result = await adapter.fetch(http, state)

    assert result.etag == "abc123"
    assert len(result.items) == 2
    first = result.items[0]
    assert first.title == "First Post"
    assert first.url == "https://example.com/first"
    assert first.published_at is not None
    assert "Hello world" in (first.content or "")


# 仿 Techmeme：description 內含相對連結、permalink（同站）、原文 gift link（站外）
TECHMEME = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>Techmeme</title>
<item>
  <title>Big Story (Bloomberg)</title>
  <link>https://www.example.com/260811/p42</link>
  <guid>https://www.example.com/260811/p42</guid>
  <description>&lt;a href="/img/logo.png"&gt;img&lt;/a&gt;
    &lt;a href="https://example.com/260811/p42" title="permalink"&gt;pml&lt;/a&gt;
    &lt;b&gt;&lt;a href="https://www.bloomberg.com/news/big-story?accessToken=gift123"&gt;Big Story&lt;/a&gt;&lt;/b&gt;
    &amp;mdash; excerpt text here</description>
</item>
<item>
  <title>Self Only</title>
  <link>https://www.example.com/260811/p43</link>
  <guid>https://www.example.com/260811/p43</guid>
  <description>&lt;a href="https://example.com/260811/p43"&gt;pml&lt;/a&gt; no outbound link</description>
</item>
</channel></rss>
"""


@respx.mock
async def test_rss_extract_source_link():
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=TECHMEME))
    adapter = RSSAdapter()
    state = SourceState(name="feed", config={"url": FEED_URL, "extract_source_link": True})
    async with HttpClient(rate_limit_per_host=0) as http:
        result = await adapter.fetch(http, state)

    first, second = result.items
    # 站外原文連結（含 gift token 完整 query）；external_id 仍為 permalink
    assert first.url == "https://www.bloomberg.com/news/big-story?accessToken=gift123"
    assert first.external_id == "https://www.example.com/260811/p42"
    assert "excerpt text here" in (first.content or "")
    # 找不到站外連結（www 差異視為同站）→ 退回 entry link
    assert second.url == "https://www.example.com/260811/p43"


@respx.mock
async def test_rss_extract_disabled_keeps_entry_link():
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=TECHMEME))
    adapter = RSSAdapter()
    state = SourceState(name="feed", config={"url": FEED_URL})
    async with HttpClient(rate_limit_per_host=0) as http:
        result = await adapter.fetch(http, state)

    assert result.items[0].url == "https://www.example.com/260811/p42"


@respx.mock
async def test_rss_handles_304_not_modified():
    respx.get(FEED_URL).mock(return_value=httpx.Response(304))
    adapter = RSSAdapter()
    state = SourceState(name="feed", etag="abc123", config={"url": FEED_URL})
    async with HttpClient(rate_limit_per_host=0) as http:
        result = await adapter.fetch(http, state)

    assert result.not_modified is True
    assert result.items == []
    assert result.etag == "abc123"
