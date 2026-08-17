import json
import logging

import pytest

from news_aggregator.enrich.llm import GeminiProvider, _extract_json


class FakeResponse:
    status_code = 200
    text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}


class FakeClient:
    def __init__(self):
        self.url = None
        self.kwargs = None

    async def post(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return FakeResponse()


async def test_gemini_key_in_header_not_url():
    client = FakeClient()
    provider = GeminiProvider("SECRET-KEY", client=client)
    await provider.complete_json("sys", "user")
    assert client.kwargs["headers"]["x-goog-api-key"] == "SECRET-KEY"
    assert "SECRET-KEY" not in client.url
    assert "params" not in client.kwargs


async def test_gemini_response_schema_in_payload():
    client = FakeClient()
    provider = GeminiProvider("k", client=client)
    await provider.complete_json("sys", "user")
    assert "responseSchema" not in client.kwargs["json"]["generationConfig"]
    schema = {"type": "OBJECT"}
    await provider.complete_json("sys", "user", schema=schema)
    assert client.kwargs["json"]["generationConfig"]["responseSchema"] is schema


async def test_gemini_geo_400_retries_then_succeeds(monkeypatch):
    # Google 偶發把出口 IP 誤判為不支援地區：前兩次 geo-400、第三次成功 → 應重試後成功
    from news_aggregator.enrich import llm

    class Geo400Response(FakeResponse):
        status_code = 400
        text = '{"error": {"message": "User location is not supported for the API use."}}'

    class FlakyClient(FakeClient):
        calls = 0

        async def post(self, url, **kwargs):
            type(self).calls += 1
            return Geo400Response() if self.calls <= 2 else await super().post(url, **kwargs)

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    provider = GeminiProvider("k", client=FlakyClient())
    assert await provider.complete_json("sys", "user") == {}
    assert FlakyClient.calls == 3
    assert sleeps == [2, 4]


def test_extract_json_tolerates_control_chars_in_strings():
    # 模型偶爾在字串內輸出未跳脫的原始換行
    assert _extract_json('{"a": "line1\nline2"}') == {"a": "line1\nline2"}


def test_extract_json_strips_fence_and_surrounding_text():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('前言 {"a": 1} 後記') == {"a": 1}


def test_extract_json_failure_logs_ctx_and_dumps_full_text(caplog, monkeypatch, tmp_path):
    from news_aggregator.enrich import llm

    monkeypatch.setattr(llm, "DATA_DIR", tmp_path)
    monkeypatch.setattr(llm, "BAD_OUTPUT_FILE", tmp_path / "llm_bad_output.txt")
    truncated = '{"results": [{"id": 1, "title_zh": "被截斷'
    with caplog.at_level(logging.WARNING):
        with pytest.raises(json.JSONDecodeError):
            _extract_json(truncated)
    assert "ctx=" in caplog.text and "被截斷" in caplog.text
    assert (tmp_path / "llm_bad_output.txt").read_text(encoding="utf-8") == truncated
