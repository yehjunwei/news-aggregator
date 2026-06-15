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
