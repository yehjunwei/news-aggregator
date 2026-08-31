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


def _geo_error() -> "httpx.HTTPStatusError":
    import httpx

    req = httpx.Request("POST", "http://test")
    resp = httpx.Response(
        400, request=req,
        text='{"error": {"message": "User location is not supported for the API use."}}',
    )
    return httpx.HTTPStatusError("400", request=req, response=resp)


class StubProvider:
    model = "stub"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0
        self.usage = {"prompt": 1, "completion": 2, "total": 3}

    async def complete_json(self, system, user, schema=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


async def test_fallback_uses_backup_on_geo_400(caplog):
    from news_aggregator.enrich.llm import FallbackProvider

    primary = StubProvider(error=_geo_error())
    backup = StubProvider(result={"ok": True})
    fb = FallbackProvider(primary, backup)
    with caplog.at_level(logging.WARNING, logger="news_aggregator.enrich.llm"):
        assert await fb.complete_json("s", "u") == {"ok": True}
    assert primary.calls == 1 and backup.calls == 1
    assert fb.usage == {"prompt": 2, "completion": 4, "total": 6}  # 兩邊合併
    # 備援成功屬正常運作：stderr 必須安靜（cron announce 會把 stderr 當錯誤）
    assert not caplog.records


def _http_error(code: int, text: str = "oops") -> "httpx.HTTPStatusError":
    import httpx

    req = httpx.Request("POST", "http://test")
    resp = httpx.Response(code, request=req, text=text)
    return httpx.HTTPStatusError(str(code), request=req, response=resp)


@pytest.mark.parametrize("exc_factory, expected", [
    (lambda: _http_error(401), True),           # 金鑰失效
    (lambda: _http_error(403), True),           # 權限/地區封鎖
    (lambda: _http_error(429), True),           # 限流
    (lambda: _http_error(500), True),           # provider 自己壞了
    (lambda: _http_error(503), True),
    (_geo_error, True),                          # Gemini geo-400
    (lambda: _http_error(400), False),           # 一般 400 = prompt 問題，切了只是雙倍付費
    (lambda: _http_error(404), False),
    (lambda: _http_error(422), False),
    (lambda: __import__("httpx").ConnectTimeout("timeout"), True),
    (lambda: RuntimeError("boom"), False),
])
def test_should_failover(exc_factory, expected):
    from news_aggregator.enrich.llm import _should_failover

    assert _should_failover(exc_factory()) is expected


async def test_fallback_uses_backup_on_provider_unavailable():
    """401（key 失效）也要切備援——Gemini key 掛掉那次就是這個情境。"""
    from news_aggregator.enrich.llm import FallbackProvider

    primary = StubProvider(error=_http_error(401))
    backup = StubProvider(result={"ok": True})
    assert await FallbackProvider(primary, backup).complete_json("s", "u") == {"ok": True}
    assert backup.calls == 1


async def test_fallback_propagates_plain_400():
    import httpx

    from news_aggregator.enrich.llm import FallbackProvider

    primary = StubProvider(error=_http_error(400))
    backup = StubProvider(result={})
    with pytest.raises(httpx.HTTPStatusError):
        await FallbackProvider(primary, backup).complete_json("s", "u")
    assert backup.calls == 0


async def test_fallback_model_and_cost_reflect_backup():
    from news_aggregator.enrich.llm import FallbackProvider, estimate_cost

    primary = StubProvider(error=_geo_error())
    primary.model = "gemini-flash-lite-latest"
    backup = StubProvider(result={})
    backup.model = "gpt-5.4-mini"
    backup.usage = {"prompt": 0, "completion": 0, "total": 0}
    fb = FallbackProvider(primary, backup)
    assert fb.model == "gemini-flash-lite-latest"  # 備援未用 → 只顯示主用
    await fb.complete_json("s", "u")
    backup.usage = {"prompt": 100, "completion": 10, "total": 110}
    assert fb.model == "gemini-flash-lite-latest＋備援 gpt-5.4-mini"
    assert fb.cost == (
        estimate_cost("gemini-flash-lite-latest", primary.usage)
        + estimate_cost("gpt-5.4-mini", backup.usage)
    )


async def test_fallback_raises_backup_error_when_both_fail(caplog):
    from news_aggregator.enrich.llm import FallbackProvider

    primary = StubProvider(error=_geo_error())
    backup = StubProvider(error=RuntimeError("backup dead"))
    with caplog.at_level(logging.WARNING, logger="news_aggregator.enrich.llm"):
        with pytest.raises(RuntimeError, match="backup dead"):
            await FallbackProvider(primary, backup).complete_json("s", "u")
    # 雙敗才印、且恰一行
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "備援" in warnings[0].getMessage()


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


class _S:
    """build_provider 用的最小 settings stub。"""

    llm_enabled = True
    gemini_model = "gemini-x"
    openai_model = "gpt-x"

    def __init__(self, provider, fallback, gemini_key="", openai_key=""):
        self.llm_provider = provider
        self.llm_fallback_provider = fallback
        self.gemini_api_key = gemini_key
        self.openai_api_key = openai_key


def test_build_provider_wraps_primary_and_fallback():
    from news_aggregator.enrich.llm import (
        FallbackProvider, GeminiProvider, OpenAIProvider, build_provider,
    )

    p = build_provider(_S("openai", "gemini", gemini_key="g", openai_key="o"))
    assert isinstance(p, FallbackProvider)
    assert isinstance(p.primary, OpenAIProvider) and isinstance(p.backup, GeminiProvider)

    # 對調 providers 區塊就整個反過來，不需改程式
    p = build_provider(_S("gemini", "openai", gemini_key="g", openai_key="o"))
    assert isinstance(p.primary, GeminiProvider) and isinstance(p.backup, OpenAIProvider)


def test_build_provider_single_key_no_wrapper():
    from news_aggregator.enrich.llm import GeminiProvider, NullProvider, build_provider

    # 主 provider 沒金鑰時直接用備援，不該退成 NullProvider
    assert isinstance(build_provider(_S("openai", "gemini", gemini_key="g")), GeminiProvider)
    assert isinstance(build_provider(_S("openai", "gemini")), NullProvider)
