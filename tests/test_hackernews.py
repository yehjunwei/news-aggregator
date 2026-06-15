import httpx
import respx

from news_aggregator.core.http import HttpClient
from news_aggregator.sources.base import SourceState
from news_aggregator.sources.hackernews import BASE, HackerNewsAdapter


@respx.mock
async def test_hackernews_parses_stories_and_filters():
    respx.get(f"{BASE}/topstories.json").mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )
    respx.get(f"{BASE}/item/1.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1,
                "type": "story",
                "title": "Great AI tool",
                "url": "https://example.com/ai",
                "by": "alice",
                "score": 120,
                "descendants": 45,
                "time": 1_700_000_000,
            },
        )
    )
    # 低分被 min_score 過濾
    respx.get(f"{BASE}/item/2.json").mock(
        return_value=httpx.Response(
            200, json={"id": 2, "type": "story", "title": "low", "score": 5, "time": 1}
        )
    )
    # 非 story 被過濾
    respx.get(f"{BASE}/item/3.json").mock(
        return_value=httpx.Response(200, json={"id": 3, "type": "job", "title": "job"})
    )

    adapter = HackerNewsAdapter()
    state = SourceState(name="hn", config={"story_type": "top", "limit": 3, "min_score": 30})
    async with HttpClient(rate_limit_per_host=0) as http:
        result = await adapter.fetch(http, state)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.external_id == "1"
    assert item.url == "https://example.com/ai"
    assert item.metrics["score"] == 120
    assert item.metrics["comments"] == 45
    assert item.source_name == "hn"


@respx.mock
async def test_hackernews_story_without_url_uses_hn_permalink():
    respx.get(f"{BASE}/topstories.json").mock(return_value=httpx.Response(200, json=[10]))
    respx.get(f"{BASE}/item/10.json").mock(
        return_value=httpx.Response(
            200,
            json={"id": 10, "type": "story", "title": "Ask HN", "score": 200, "time": 1},
        )
    )
    adapter = HackerNewsAdapter()
    async with HttpClient(rate_limit_per_host=0) as http:
        result = await adapter.fetch(http, SourceState(name="hn", config={"limit": 1}))
    assert result.items[0].url == "https://news.ycombinator.com/item?id=10"
