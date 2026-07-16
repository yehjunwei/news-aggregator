"""用 LLM 對新聞做：分類、繁中標題、N 句摘要、why_relevant、personal_relevance_score。

批次呼叫以節省成本；對缺漏 / 失敗的批次採容錯（略過，不中斷）。
"""

from __future__ import annotations

import asyncio
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

# 折算技術 niche 懲罰的係數：net = relevance - NICHE_PENALTY * technical_nicheness（0-100 後裁切）
NICHE_PENALTY = 0.5

SYSTEM = (
    "你是高度個人化的新聞排序器。你的任務不是判斷文章是否與使用者的廣泛興趣「有關」，"
    "而是預測使用者是否真的會點開並花至少 2 分鐘閱讀。"
    "使用者是資深軟體工程主管，但這不代表他對所有程式設計、GitHub、Linux、Rust、前端或 "
    "Hacker News 專案感興趣。只輸出 JSON，不要額外文字，內容用繁體中文。"
)

_INTEREST = (
    "使用者最關心（personal_relevance_score 應偏高）：\n"
    "1. AI 產業的重要產品、公司策略與競爭態勢（OpenAI、Anthropic、Google、Meta、NVIDIA…）\n"
    "2. AI 對人類工作、隱私、社會、教育與日常生活的影響\n"
    "3. Tesla、FSD、自動駕駛、Robotaxi、ADAS、智慧座艙（Mobileye、Waymo、Qualcomm、NVIDIA DRIVE）\n"
    "4. 科技領袖的重要觀點（Jensen Huang、Elon Musk、Sam Altman、Karpathy）\n"
    "5. 真實世界的產品使用體驗、商業落地與產業變化\n"
    "6. 突然爆紅的新產品 / 新服務 / 網路趨勢\n"
    "7. 對投資、職涯或生活決策有潛在影響的內容\n"
    "8. 條件式：GitHub / AI coding / HN / 軟體技術——只有在真的爆紅、能直接改變 workflow、"
    "有產業意義或重大爭議時才給高分；NBA / 球員卡只在重大交易、球星、爭議、市場異常時給高分。\n"
    "\n"
    "使用者通常不感興趣（technical_nicheness 應偏高、personal_relevance_score 應偏低）：\n"
    "1. 純技術炫技或玩具型專案、Show HN 小作品\n"
    "2. CSS、favicon、個人作品集、復古介面等前端展示\n"
    "3. 冷門 CLI、小型新程式語言、一般 library 或 systemd / Linux kernel 版本更新\n"
    "4. 只對少數底層開發者有用的 niche 工具（如 Rust unsafe 檢查、無線電硬體小專案）\n"
    "5. 缺乏產品、商業或產業意義的 benchmark\n"
    "6. 只因為是 Show HN 或 GitHub trending 就被推薦、沒有新觀點的普通新聞\n"
    "\n"
    "評分原則：不要因為文章包含 AI、Agent、GitHub、Linux、Rust、JavaScript 等關鍵字就給高分。"
    "優先考慮：使用者是否真的會點開、是否影響產業/產品/真實世界、是否含重要人物或商業策略、"
    "是否有爭議或意外發展、是否與使用者正在使用或考慮的產品直接相關。\n"
    "technical_nicheness：純底層 / niche 技術細節給高分（80-100），有明確產業或產品意義給低分（0-30）。"
)


def _examples_block(examples: dict | None) -> str:
    """把使用者最近讚/倒讚的標題當 few-shot 範例，讓模型歸納偏好。"""
    if not examples:
        return ""
    liked = examples.get("liked") or []
    disliked = examples.get("disliked") or []
    if not liked and not disliked:
        return ""
    parts = ["\n使用者過去的實際回饋（請據此校準分數）："]
    if liked:
        parts.append("👍 想看更多：\n" + "\n".join(f"- {t}" for t in liked))
    if disliked:
        parts.append("👎 不感興趣：\n" + "\n".join(f"- {t}" for t in disliked))
    return "\n".join(parts)


def _build_user(
    batch: list[dict],
    summary_sentences: int,
    examples: dict | None = None,
    watchlist: str | None = None,
) -> str:
    lines = [
        _INTEREST,
        watchlist or "",
        _examples_block(examples),
        "",
        f"請為以下每則新聞輸出 JSON：{{\"results\": [{{"
        f"\"id\": <int>, \"category\": <下列其一>, \"title_zh\": <繁中標題>, "
        f"\"summary\": <{summary_sentences} 句繁中摘要>, "
        f"\"why_relevant\": <一句話，指出新聞與使用者工作/產品/投資/生活的「具體」關係；"
        f"禁止使用「符合使用者對某領域的興趣」這類泛化句型>, "
        f"\"personal_relevance_score\": <0-100 整數，預測點開並閱讀的機率>, "
        f"\"technical_nicheness\": <0-100 整數，越底層 / niche 越高>}}]}}",
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
    # 折算 niche 懲罰：底層/niche 內容即使關鍵字相符也壓低分數
    try:
        niche = max(0, min(100, int(raw.get("technical_nicheness"))))
    except (TypeError, ValueError):
        niche = 0
    if score is not None:
        score = max(0, min(100, round(score - NICHE_PENALTY * niche)))
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
    examples: dict | None = None,
    watchlist: str | None = None,
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
                out = await provider.complete_json(
                    SYSTEM, _build_user(batch, summary_sentences, examples, watchlist)
                )
            except Exception as exc:  # noqa: BLE001
                # 只印例外類名與 HTTP 狀態碼，不印含 URL 的完整訊息
                status = getattr(getattr(exc, "response", None), "status_code", "")
                logger.warning(
                    "LLM 批次失敗（%d 則）：%s %s", len(batch), type(exc).__name__, status
                )
                return
            for raw in out.get("results", []) or []:
                coerced = _coerce_result(raw)
                if coerced:
                    results[coerced["id"]] = coerced

    await asyncio.gather(*[_run(b) for b in batches])
    return results
