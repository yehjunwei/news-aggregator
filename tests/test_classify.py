import logging

import httpx

from news_aggregator.enrich.classify import _examples_block, classify_items


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def complete_json(self, system, user):
        self.calls += 1
        return self.payload


async def test_classify_maps_results_by_id():
    provider = FakeProvider(
        {
            "results": [
                {
                    "id": 5,
                    "category": "ai_agents",
                    "title_zh": "中文",
                    "summary": "摘要",
                    "why_relevant": "原因",
                    "personal_relevance_score": 88,
                }
            ]
        }
    )
    inputs = [{"id": 5, "title": "t", "url": "u", "source": "hn"}]
    out = await classify_items(provider, inputs)
    assert out[5]["category"] == "ai_agents"
    assert out[5]["personal_relevance_score"] == 88


async def test_classify_coerces_bad_category_and_score():
    provider = FakeProvider(
        {"results": [{"id": 1, "category": "garbage", "personal_relevance_score": 999}]}
    )
    out = await classify_items(provider, [{"id": 1, "title": "t", "url": "u", "source": "x"}])
    assert out[1]["category"] == "other"
    assert out[1]["personal_relevance_score"] == 100


async def test_classify_niche_penalty_lowers_score():
    # relevance 80, niche 80 -> 80 - 0.5*80 = 40
    provider = FakeProvider(
        {"results": [{"id": 1, "category": "dev_tools",
                      "personal_relevance_score": 80, "technical_nicheness": 80}]}
    )
    out = await classify_items(provider, [{"id": 1, "title": "t", "url": "u", "source": "x"}])
    assert out[1]["personal_relevance_score"] == 40


async def test_classify_no_niche_keeps_score():
    # 無 technical_nicheness -> 不懲罰
    provider = FakeProvider(
        {"results": [{"id": 1, "category": "ai_agents", "personal_relevance_score": 90}]}
    )
    out = await classify_items(provider, [{"id": 1, "title": "t", "url": "u", "source": "x"}])
    assert out[1]["personal_relevance_score"] == 90


def test_examples_block_empty_when_no_feedback():
    assert _examples_block(None) == ""
    assert _examples_block({"liked": [], "disliked": []}) == ""


def test_examples_block_lists_liked_and_disliked():
    out = _examples_block({"liked": ["Mobileye Robotaxi"], "disliked": ["CSS 實驗"]})
    assert "Mobileye Robotaxi" in out and "CSS 實驗" in out
    assert "👍" in out and "👎" in out


async def test_classify_empty_inputs():
    assert await classify_items(FakeProvider({}), []) == {}


async def test_classify_failure_log_omits_url_and_key(caplog):
    class FailingProvider:
        async def complete_json(self, system, user):
            req = httpx.Request("POST", "https://x/gen?key=SECRET-KEY")
            raise httpx.HTTPStatusError(
                "Server error '503' for url 'https://x/gen?key=SECRET-KEY'",
                request=req,
                response=httpx.Response(503, request=req),
            )

    with caplog.at_level(logging.WARNING):
        out = await classify_items(
            FailingProvider(), [{"id": 1, "title": "t", "url": "u", "source": "x"}]
        )
    assert out == {}
    assert "SECRET-KEY" not in caplog.text
    assert "503" in caplog.text


async def test_classify_batches():
    provider = FakeProvider({"results": []})
    inputs = [{"id": i, "title": "t", "url": "u", "source": "s"} for i in range(20)]
    await classify_items(provider, inputs, batch_size=8)
    assert provider.calls == 3  # 8 + 8 + 4
