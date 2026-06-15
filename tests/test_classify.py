from news_aggregator.enrich.classify import classify_items


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


async def test_classify_empty_inputs():
    assert await classify_items(FakeProvider({}), []) == {}


async def test_classify_batches():
    provider = FakeProvider({"results": []})
    inputs = [{"id": i, "title": "t", "url": "u", "source": "s"} for i in range(20)]
    await classify_items(provider, inputs, batch_size=8)
    assert provider.calls == 3  # 8 + 8 + 4
