"""使用者回饋迴圈：收 Telegram 👍/👎 按鈕點擊，寫回 items.feedback，並把最近的
讚/倒讚標題彙整成範例餵回排序 prompt（few-shot，不訓練模型、不用 embedding）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from .config import DATA_DIR, Settings
from .core.http import HttpClient
from .db.models import Item
from .delivery.telegram import TelegramClient

logger = logging.getLogger(__name__)

_OFFSET_FILE = DATA_DIR / "tg_offset.txt"


def _read_offset() -> int | None:
    try:
        return int(_OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_offset(n: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(str(n))


def _parse(data: str) -> tuple[int, int] | None:
    """'fb:<item_id>:up|down' -> (item_id, +1|-1)；格式不符回 None。"""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "fb":
        return None
    try:
        item_id = int(parts[1])
    except ValueError:
        return None
    signal = {"up": 1, "down": -1}.get(parts[2])
    return (item_id, signal) if signal is not None else None


def record_feedback(session, data: str) -> str:
    """記錄單筆回饋（給 CLI 用，callback 被 gateway 轉成文字時的補救路徑）。

    格式不符 raise ValueError、查無 item raise LookupError；成功回傳單行結果訊息。
    """
    parsed = _parse(data)
    if parsed is None:
        raise ValueError(f"格式不符（需 fb:<item_id>:up|down）：{data!r}")
    item_id, signal = parsed
    item = session.get(Item, item_id)
    if item is None:
        raise LookupError(f"查無新聞 item {item_id}")
    item.feedback = signal
    session.commit()
    emoji = "👍" if signal > 0 else "👎"
    return f"已記錄 {emoji}：{item.title_zh or item.title}"


def _apply_update(session, tg_updates: list[dict]) -> tuple[int, int, list[tuple[str, int]]]:
    """套用回饋到 DB，回傳 (套用筆數, 最大 update_id, 待回應的 [(callback_id, signal)])。"""
    applied = 0
    max_id = -1
    acks: list[tuple[str, int]] = []
    for u in tg_updates:
        max_id = max(max_id, u.get("update_id", max_id))
        cq = u.get("callback_query")
        if not cq:
            continue
        parsed = _parse(cq.get("data", ""))
        if parsed is None:
            continue
        item_id, signal = parsed
        item = session.get(Item, item_id)
        if item is not None:
            item.feedback = signal
            applied += 1
        acks.append((cq.get("id", ""), signal))
    return applied, max_id, acks


async def poll_feedback(session, settings: Settings, http: HttpClient) -> int:
    """拉 getUpdates，把按鈕點擊寫進 items.feedback。回傳套用筆數。"""
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return 0
    tg = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id, client=http.raw)
    offset = _read_offset()
    try:
        updates = await tg.get_updates(offset)
    except Exception as exc:  # noqa: BLE001 - 回饋輪詢失敗不影響主流程
        logger.info("getUpdates 失敗：%s", exc)
        return 0

    applied, max_id, acks = _apply_update(session, updates)
    for cb_id, signal in acks:
        await tg.answer_callback(cb_id, "已記錄 👍" if signal > 0 else "已記錄 👎")
    if updates:
        _write_offset(max(max_id + 1, (offset or 0)))
    session.commit()
    if applied:
        logger.info("套用 %d 筆回饋", applied)
    return applied


def feedback_examples(session, limit: int = 12) -> dict:
    """撈最近被讚/倒讚的標題，當排序 prompt 的 few-shot 範例。"""
    def _titles(signal: int) -> list[str]:
        rows = session.scalars(
            select(Item)
            .where(Item.feedback == signal)
            .order_by(Item.delivered_at.desc().nullslast())
            .limit(limit)
        ).all()
        return [(it.title_zh or it.title) for it in rows if (it.title_zh or it.title)]

    return {"liked": _titles(1), "disliked": _titles(-1)}
