"""用 LLM 把「同一事件、不同連結」的新聞分組，供推送前去重。

字串相似度抓不到同事件的不同寫法（NBA、球卡尤其明顯），所以這裡只走 LLM。
失敗時一律回空分組放行——寧可重複推送，也不誤殺。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 60  # 進 prompt 的候選上限
MAX_DELIVERED = 40   # 進 prompt 的近期已推送上限

# Gemini responseSchema：只回 id，輸出短、不易壞
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "groups": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
                "required": ["ids"],
            },
        }
    },
    "required": ["groups"],
}

SYSTEM = (
    "你是新聞去重助手。判斷哪些新聞在報導「同一個具體事件」，"
    "即使標題寫法、語言或來源不同。只輸出 JSON，不要額外文字。"
)


def _build_user(entries: list[dict]) -> str:
    lines = [
        "請找出報導「同一個具體事件」的新聞，把它們的 id 分成一組。",
        "同一事件指：同一場比賽、同一筆交易、同一次發表或發售、同一份公告或財報、同一起爭議。",
        "只是同一個主題、同一支球隊、同一家公司、同一個球員，但講的是不同事件——不算同一組。",
        '判斷不確定時不要分組。沒有重複就回 {"groups": []}。',
        "每組至少 2 個 id，同一個 id 只能出現在一組。",
        "",
        "新聞清單：",
    ]
    lines += [f"- id={e['id']} {e['title']}" for e in entries]
    return "\n".join(lines)


def _clean_groups(raw_groups, valid_ids: set[int]) -> list[list[int]]:
    """濾掉非法 / 不存在的 id，一個 id 只留在第一組，丟掉不足 2 個的組。"""
    groups: list[list[int]] = []
    used: set[int] = set()
    for group in raw_groups or []:
        ids: list[int] = []
        for raw in (group or {}).get("ids") or []:
            try:
                item_id = int(raw)
            except (TypeError, ValueError):
                continue
            if item_id in valid_ids and item_id not in used:
                ids.append(item_id)
                used.add(item_id)
        if len(ids) >= 2:
            groups.append(ids)
        else:
            used.difference_update(ids)  # 組不成立，釋放 id 讓後面的組還能用
    return groups


async def group_same_event(provider, entries: list[dict]) -> list[list[int]]:
    """entries: [{"id": int, "title": str}] -> 同事件的 id 分組（只回 len>=2 的組）。"""
    if not entries:
        return []
    try:
        out = await provider.complete_json(
            SYSTEM, _build_user(entries), schema=RESPONSE_SCHEMA
        )
    except Exception as exc:  # noqa: BLE001 - 去重失敗放行，不阻斷推送
        logger.warning("同事件分組失敗（%d 則）：%s", len(entries), type(exc).__name__)
        return []
    return _clean_groups((out or {}).get("groups"), {e["id"] for e in entries})
