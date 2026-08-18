"""LLM provider 抽象。預設 Gemini，可切 OpenAI；無金鑰時用 NullProvider。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Protocol

import httpx

from news_aggregator.config import DATA_DIR

logger = logging.getLogger(__name__)

# 解析失敗的完整模型輸出存這裡（保留最後一次），供診斷壞 JSON 的實際內容
BAD_OUTPUT_FILE = DATA_DIR / "llm_bad_output.txt"


class LLMProvider(Protocol):
    model: str
    usage: dict
    async def complete_json(
        self, system: str, user: str, schema: dict | None = None
    ) -> dict: ...


# 約略單價（USD / 1M tokens）：(input, output)。可依官方價目調整。
PRICING: dict[str, tuple[float, float]] = {
    "gemini-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-flash": (0.30, 2.50),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.0),
}


# Google geo-400（誤判地區）單一請求的最大嘗試次數
_GEO_RETRIES = 4


def _empty_usage() -> dict:
    return {"prompt": 0, "completion": 0, "total": 0}


def _match_rate(model: str) -> tuple[float, float] | None:
    """以最長相符的 key 取得單價。"""
    for key in sorted(PRICING, key=len, reverse=True):
        if key in model:
            return PRICING[key]
    return None


def estimate_cost(model: str, usage: dict) -> float | None:
    """依 usage 與 model 估算 USD；無價目表時回 None。"""
    rate = _match_rate(model or "")
    if rate is None:
        return None
    in_rate, out_rate = rate
    return usage.get("prompt", 0) / 1e6 * in_rate + usage.get("completion", 0) / 1e6 * out_rate


def _extract_json(text: str) -> dict:
    text = text.strip()
    # 容錯：去掉可能的 ```json fence
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        # strict=False：容忍字串內未跳脫的控制字元（模型偶爾在中文摘要輸出原始換行）
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    try:
        if not (0 <= start < end):
            raise json.JSONDecodeError("no JSON object found", text, 0)
        return json.loads(text[start : end + 1], strict=False)
    except json.JSONDecodeError as exc:
        # 留下診斷線索：錯誤位置前後文 + 全文存檔；模型輸出不含金鑰
        ctx = text[max(0, exc.pos - 80) : exc.pos + 80]
        logger.warning(
            "LLM 輸出非合法 JSON：%s；ctx=%r；全文已存 %s",
            exc, ctx, _dump_bad_output(text) or "（寫檔失敗）",
        )
        raise


def _dump_bad_output(text: str):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BAD_OUTPUT_FILE.write_text(text, encoding="utf-8")
        return BAD_OUTPUT_FILE
    except OSError:
        return None


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemini-flash-lite-latest", client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.model = model
        self._client = client
        self.usage = _empty_usage()

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        gen_config: dict = {"temperature": 0.2, "responseMimeType": "application/json"}
        if schema:
            # 約束解碼：從結構上保證輸出為合法 JSON 且符合 schema
            gen_config["responseSchema"] = schema
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_config,
        }
        client = self._client or httpx.AsyncClient(timeout=60)
        try:
            # key 走 header，避免出現在 URL / 例外訊息 / log
            # Google 偶發把本機 IP 誤判為不支援地區（400 FAILED_PRECONDITION），
            # 逐請求隨機發生、多數重試即過 → 只對這個錯誤重試，其他錯誤原樣拋出
            for attempt in range(1, _GEO_RETRIES + 1):
                resp = await client.post(url, headers={"x-goog-api-key": self.api_key}, json=payload)
                if (
                    resp.status_code == 400
                    and attempt < _GEO_RETRIES
                    and "User location is not supported" in resp.text
                ):
                    # debug 級：正常運作的重試不進 stderr（cron announce 會把 stderr 當錯誤回報）
                    logger.debug("Gemini geo-400（第 %d 次），%ds 後重試", attempt, 2 * attempt)
                    await asyncio.sleep(2 * attempt)
                    continue
                break
            resp.raise_for_status()
            data = resp.json()
            meta = data.get("usageMetadata") or {}
            self.usage["prompt"] += meta.get("promptTokenCount", 0) or 0
            self.usage["completion"] += meta.get("candidatesTokenCount", 0) or 0
            self.usage["total"] += meta.get("totalTokenCount", 0) or 0
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return _extract_json(text)
        finally:
            if self._client is None:
                await client.aclose()


class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.model = model
        self._client = client
        self.usage = _empty_usage()

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        # schema 僅 Gemini 使用；OpenAI 維持 json_object 模式
        url = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        client = self._client or httpx.AsyncClient(timeout=60)
        try:
            resp = await client.post(
                url, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            u = data.get("usage") or {}
            self.usage["prompt"] += u.get("prompt_tokens", 0) or 0
            self.usage["completion"] += u.get("completion_tokens", 0) or 0
            self.usage["total"] += u.get("total_tokens", 0) or 0
            return _extract_json(data["choices"][0]["message"]["content"])
        finally:
            if self._client is None:
                await client.aclose()


class FallbackProvider:
    """主 provider 因 Gemini geo-400（重試耗盡）失敗時，改用備援 provider 完成該次呼叫。

    每次呼叫都先試主 provider，Google 恢復後自動回歸；其他錯誤原樣拋出。
    """

    def __init__(self, primary: LLMProvider, backup: LLMProvider):
        self.primary = primary
        self.backup = backup

    @property
    def model(self) -> str:
        # 備援有實際用量時如實標示，digest footer 才能反映現狀
        if self.backup.usage.get("total"):
            return f"{self.primary.model}＋備援 {self.backup.model}"
        return self.primary.model

    @property
    def usage(self) -> dict:
        merged = _empty_usage()
        for u in (self.primary.usage, self.backup.usage):
            for k in merged:
                merged[k] += u.get(k, 0)
        return merged

    @property
    def cost(self) -> float | None:
        """兩邊各按各的單價估算後加總；皆無價目表時回 None。"""
        parts = [
            estimate_cost(self.primary.model, self.primary.usage),
            estimate_cost(self.backup.model, self.backup.usage),
        ]
        known = [p for p in parts if p is not None]
        return sum(known) if known else None

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        try:
            return await self.primary.complete_json(system, user, schema=schema)
        except httpx.HTTPStatusError as exc:
            resp = exc.response
            if resp.status_code != 400 or "User location is not supported" not in resp.text:
                raise
            # 切備援屬正常運作，不進 stderr；footer 會揭露備援用量
            logger.debug("Gemini geo-400 重試耗盡，本次改用備援 %s", self.backup.model)
            try:
                return await self.backup.complete_json(system, user, schema=schema)
            except Exception:
                logger.warning("Gemini 被擋（geo-400），備援 %s 也失敗", self.backup.model)
                raise


class NullProvider:
    """無金鑰 / 停用 LLM 時的 no-op provider。"""

    model = ""

    def __init__(self):
        self.usage = _empty_usage()

    async def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        return {}


def build_provider(settings, client: httpx.AsyncClient | None = None) -> LLMProvider:
    if not settings.llm_enabled:
        return NullProvider()
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(settings.openai_api_key, settings.openai_model, client)
    if settings.gemini_api_key:
        gemini = GeminiProvider(settings.gemini_api_key, settings.gemini_model, client)
        if settings.openai_api_key:
            # Gemini geo-400（Google 誤判出口 IP 地區）連續失敗窗口的自動備援
            return FallbackProvider(
                gemini, OpenAIProvider(settings.openai_api_key, settings.openai_model, client)
            )
        return gemini
    logger.warning("找不到 LLM 金鑰，改用 NullProvider（不做分類/摘要）")
    return NullProvider()
