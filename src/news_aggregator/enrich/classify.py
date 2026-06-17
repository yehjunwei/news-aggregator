"""用 LLM 對新聞做：分類、繁中標題、N 句摘要、why_relevant、personal_relevance_score。

批次呼叫以節省成本；對缺漏 / 失敗的批次採容錯（略過，不中斷）。
"""

from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

# 類別清單（與 scoring 權重表對應）
CATEGORIES = [
    "ai_agents",      # AI agents
    "ai_coding",      # AI 寫程式 / coding 工具
    "dev_tools",      # 最新軟體工具
    "github_project", # GitHub 新專案
    "hackernews",     # HN 熱門討論
    "yt_interview",   # 技術 YouTube 訪談
    "new_app",        # 新 App / 新服務
    "product_hunt",   # Product Hunt
    "nba",            # NBA
    "card_collecting",# 球員卡 / Topps / Panini / PSA
    "automotive",     # 車用座艙 / 智慧座艙 / 車載 HMI
    "holdings",       # 手上持股相關
    "ai_application", # AI 應用一般新聞
    "ai_paper",       # AI 論文（小幅 benchmark）
    "tech_feature",   # 科技深度長文 / 產業觀點分析
    "world_cup",      # 世界盃足球賽
    "book_review",    # 新書書評 / 讀後心得 / 暢銷書
    "tv_streaming",   # Netflix / Apple TV 等串流新劇評價
    "other",
]

SYSTEM = (
    "你是個人新聞策展助理。針對每則新聞輸出繁體中文結構化資訊。"
    "只輸出 JSON，不要額外文字。"
)

_INTEREST = (
    "使用者興趣權重：\n"
    "高（personal_relevance_score 偏高）：AI agents、AI coding、最新軟體工具、"
    "GitHub 新專案、Hacker News 熱門、技術 YouTube 訪談、新 App / 新服務、Product Hunt。\n"
    "中：NBA、籃球球員卡（Topps/Panini/PSA）、車用座艙 / 智慧座艙 / 車載 HMI、"
    "手上持股相關、AI 應用一般新聞、僅小幅 benchmark 改善的 AI 論文、"
    "科技深度長文 / 產業觀點分析（tech_feature，如 Stratechery、Ars Technica 深度報導、創投/技術 YouTube 訪談的觀點內容）、"
    "世界盃足球賽（world_cup，賽事/賽程/球員/分組）、"
    "新書書評與讀後心得（book_review，新書/暢銷書/閱讀心得）、"
    "串流新劇評價（tv_streaming，Netflix / Apple TV / Disney+ 等新上架影集劇集的評價與推薦）。\n"
    "其他主題給較低分。"
)


def _build_user(batch: list[dict], summary_sentences: int) -> str:
    lines = [
        _INTEREST,
        "",
        f"請為以下每則新聞輸出 JSON：{{\"results\": [{{"
        f"\"id\": <int>, \"category\": <下列其一>, \"title_zh\": <繁中標題>, "
        f"\"summary\": <{summary_sentences} 句繁中摘要>, "
        f"\"why_relevant\": <一句話說明為何值得使用者看>, "
        f"\"personal_relevance_score\": <0-100 整數>}}]}}",
        f"category 僅能是：{', '.join(CATEGORIES)}",
        "",
        "新聞清單：",
    ]
    for entry in batch:
        ctx = f"（內容：{entry['content'][:300]}）" if entry.get("content") else ""
        lines.append(
            f"- id={entry['id']} 來源={entry.get('source', '')} 標題={entry['title']} {ctx}"
        )
    return "\n".join(lines)


def _coerce_result(raw: dict) -> dict | None:
    if "id" not in raw:
        return None
    try:
        item_id = int(raw["id"])
    except (TypeError, ValueError):
        return None
    category = raw.get("category")
    if category not in CATEGORIES:
        category = "other"
    score = raw.get("personal_relevance_score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = None
    return {
        "id": item_id,
        "category": category,
        "title_zh": (raw.get("title_zh") or "").strip() or None,
        "summary": (raw.get("summary") or "").strip() or None,
        "why_relevant": (raw.get("why_relevant") or "").strip() or None,
        "personal_relevance_score": score,
    }


async def classify_items(
    provider,
    inputs: list[dict],
    *,
    summary_sentences: int = 3,
    batch_size: int = 8,
    concurrency: int = 4,
) -> dict[int, dict]:
    """inputs: [{"id", "title", "url", "source", "content"?}] -> {id: enrichment}。"""
    if not inputs:
        return {}

    batches = [inputs[i : i + batch_size] for i in range(0, len(inputs), batch_size)]
    results: dict[int, dict] = {}
    sem = asyncio.Semaphore(concurrency)

    async def _run(batch):
        async with sem:
            try:
                out = await provider.complete_json(SYSTEM, _build_user(batch, summary_sentences))
            except (json.JSONDecodeError, KeyError, Exception) as exc:  # noqa: BLE001
                logger.warning("LLM 批次失敗（%d 則）：%s", len(batch), exc)
                return
            for raw in out.get("results", []) or []:
                coerced = _coerce_result(raw)
                if coerced:
                    results[coerced["id"]] = coerced

    await asyncio.gather(*[_run(b) for b in batches])
    return results
