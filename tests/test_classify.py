import json
import logging

import httpx

from news_aggregator.enrich.classify import (
    RESPONSE_SCHEMA,
    _build_user,
    _content_thin,
    _examples_block,
    classify_items,
)


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.schema = None

    async def complete_json(self, system, user, schema=None):
        self.calls += 1
        self.schema = schema
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
    assert provider.schema is RESPONSE_SCHEMA  # 約束解碼 schema 有傳給 provider


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


async def test_thin_content_summary_forced_none():
    # 無 content：模型硬寫的摘要也要被丟掉
    provider = FakeProvider(
        {"results": [{"id": 1, "category": "ai_agents", "title_zh": "中文",
                      "summary": "幻想出來的摘要", "personal_relevance_score": 80}]}
    )
    out = await classify_items(provider, [{"id": 1, "title": "t", "url": "u", "source": "x"}])
    assert out[1]["summary"] is None
    assert out[1]["title_zh"] == "中文"  # 其他欄位不受影響


async def test_rich_content_summary_kept():
    content = "標題本文" + "真實摘錄" * 40  # 遠超過標題+80 字元
    provider = FakeProvider(
        {"results": [{"id": 1, "category": "ai_agents",
                      "summary": "有依據的摘要", "personal_relevance_score": 80}]}
    )
    out = await classify_items(
        provider, [{"id": 1, "title": "標題本文", "url": "u", "source": "x", "content": content}]
    )
    assert out[1]["summary"] == "有依據的摘要"


def test_build_user_marks_thin_entries():
    thin = {"id": 1, "title": "只有標題", "source": "gnews"}
    rich = {"id": 2, "title": "T", "source": "tm", "content": "T " + "摘錄內容" * 30}
    user = _build_user([thin, rich], summary_sentences=3)
    assert "內容不足，summary 留空" in user
    assert "（內容：T 摘錄內容" in user


def test_content_thin_boundary():
    title = "標題"
    assert _content_thin({"title": title, "content": title + "x" * 79})
    assert not _content_thin({"title": title, "content": title + "x" * 80})
    assert _content_thin({"title": title})          # 無 content
    assert _content_thin({"title": title, "content": None})


def test_examples_block_empty_when_no_feedback():
    assert _examples_block(None) == ""
    assert _examples_block({"liked": [], "disliked": []}) == ""


def test_examples_block_lists_liked_and_disliked():
    out = _examples_block({"liked": ["Mobileye Robotaxi"], "disliked": ["CSS 實驗"]})
    assert "Mobileye Robotaxi" in out and "CSS 實驗" in out
    assert "👍" in out and "👎" in out


async def test_classify_empty_inputs():
    assert await classify_items(FakeProvider({}), []) == {}


async def test_classify_failure_log_omits_url_and_key(caplog, monkeypatch):
    monkeypatch.setattr("news_aggregator.enrich.classify._RETRY_DELAY", 0)

    class FailingProvider:
        def __init__(self):
            self.calls = 0

        async def complete_json(self, system, user, schema=None):
            self.calls += 1
            req = httpx.Request("POST", "https://x/gen?key=SECRET-KEY")
            raise httpx.HTTPStatusError(
                "Server error '503' for url 'https://x/gen?key=SECRET-KEY'",
                request=req,
                response=httpx.Response(503, request=req),
            )

    provider = FailingProvider()
    with caplog.at_level(logging.WARNING):
        out = await classify_items(
            provider, [{"id": 1, "title": "t", "url": "u", "source": "x"}]
        )
    assert out == {}
    assert provider.calls == 2  # 失敗後重試一次
    assert "SECRET-KEY" not in caplog.text
    assert "503" in caplog.text


async def test_classify_retry_succeeds_on_second_attempt(monkeypatch):
    monkeypatch.setattr("news_aggregator.enrich.classify._RETRY_DELAY", 0)

    class FlakyProvider:
        def __init__(self):
            self.calls = 0

        async def complete_json(self, system, user, schema=None):
            self.calls += 1
            if self.calls == 1:
                raise json.JSONDecodeError("bad", "", 0)
            return {"results": [{"id": 1, "category": "ai_agents",
                                 "personal_relevance_score": 80}]}

    provider = FlakyProvider()
    out = await classify_items(provider, [{"id": 1, "title": "t", "url": "u", "source": "x"}])
    assert provider.calls == 2
    assert out[1]["personal_relevance_score"] == 80


async def test_classify_batches():
    provider = FakeProvider({"results": []})
    inputs = [{"id": i, "title": "t", "url": "u", "source": "s"} for i in range(20)]
    await classify_items(provider, inputs, batch_size=8)
    assert provider.calls == 3  # 8 + 8 + 4
