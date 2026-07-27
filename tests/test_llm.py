import json
import logging

import pytest

from news_aggregator.enrich.llm import GeminiProvider, _extract_json


class FakeResponse:
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


def test_extract_json_tolerates_control_chars_in_strings():
    # 模型偶爾在字串內輸出未跳脫的原始換行
    assert _extract_json('{"a": "line1\nline2"}') == {"a": "line1\nline2"}


def test_extract_json_strips_fence_and_surrounding_text():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('前言 {"a": 1} 後記') == {"a": 1}


def test_extract_json_failure_logs_tail(caplog):
    truncated = '{"results": [{"id": 1, "title_zh": "被截斷'
    with caplog.at_level(logging.WARNING):
        with pytest.raises(json.JSONDecodeError):
            _extract_json(truncated)
    assert "tail=" in caplog.text and "被截斷" in caplog.text
