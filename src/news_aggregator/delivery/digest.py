"""每日摘要渲染：Telegram（HTML 區塊）與檔案（Markdown）。

每則包含：標題（HTML 為超連結）、來源標注、熱度指標、摘要、why_relevant、網址。
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import parse_qsl, urlsplit

from ..core.timez import now_utc, to_taipei

# 已知付費牆域名（顯示用標記，與 dedup 的 URL 正規化無關）
_PAYWALLED = frozenset({
    "bloomberg.com", "theinformation.com", "wsj.com",
    "ft.com", "nytimes.com", "economist.com",
})

# item view 為 dict，欄位：
#   title, title_zh, summary, why_relevant, personal_relevance_score,
#   source_name, url, metrics{score,comments,stars}


def _metric_str(metrics: dict) -> str:
    parts = []
    if metrics.get("score"):
        parts.append(f"🔺{metrics['score']}")
    if metrics.get("comments"):
        parts.append(f"💬{metrics['comments']}")
    if metrics.get("stars"):
        parts.append(f"⭐{metrics['stars']}")
    return " ".join(parts)


def _title(item: dict) -> str:
    return item.get("title_zh") or item.get("title") or "(無標題)"


def _url(item: dict) -> str:
    return item.get("url") or ""


def _paywalled(url: str) -> bool:
    """連結是否指向已知付費牆域名（gift link 帶 accessToken 者豁免）。"""
    if not url:
        return False
    parts = urlsplit(url)
    if any(k == "accessToken" for k, _ in parse_qsl(parts.query)):
        return False
    host = parts.netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _PAYWALLED)


def _meta_suffix(item: dict) -> str:
    bits = [item.get("source_name", "")]
    ms = _metric_str(item.get("metrics", {}))
    if ms:
        bits.append(ms)
    rel = item.get("personal_relevance_score")
    if rel is not None:
        bits.append(f"相關度 {rel}")
    return " · ".join(b for b in bits if b)


def item_keyboard(item_id: int) -> dict:
    """每則底下的 👍/👎 inline 按鈕；callback_data 直接帶 item id，回收時免對應表。"""
    return {
        "inline_keyboard": [[
            {"text": "👍 想看更多", "callback_data": f"fb:{item_id}:up"},
            {"text": "👎 不感興趣", "callback_data": f"fb:{item_id}:down"},
        ]]
    }


def render_item_html(item: dict) -> str:
    url = _url(item)
    title = escape(_title(item))
    mark = "💰 " if _paywalled(url) else ""
    head = f'{mark}<b><a href="{escape(url, quote=True)}">{title}</a></b>' if url else f"<b>{title}</b>"
    lines = [head, f"<i>{escape(_meta_suffix(item))}</i>"]
    if item.get("summary"):
        lines.append(escape(item["summary"]))
    if item.get("why_relevant"):
        lines.append(f"👉 {escape(item['why_relevant'])}")
    return "\n".join(lines)


def render_item_md(item: dict) -> str:
    url = _url(item)
    mark = "💰 " if _paywalled(url) else ""
    lines = [f"### {mark}[{_title(item)}]({url})", f"*{_meta_suffix(item)}*"]
    if item.get("summary"):
        lines.append(item["summary"])
    if item.get("why_relevant"):
        lines.append(f"👉 {item['why_relevant']}")
    lines.append(f"🔗 {_url(item)}")
    return "\n".join(lines)


def _header(run_dt: datetime, count: int, title: str, *, html: bool) -> str:
    date_str = to_taipei(run_dt).strftime("%Y-%m-%d")
    if html:
        return f"📰 <b>{title}</b> — {date_str}（共 {count} 則）"
    return f"# 📰 {title} — {date_str}（共 {count} 則）"


def render_telegram(
    items: list[dict], run_dt: datetime | None = None, title: str = "每日新聞精選"
) -> tuple[str, list[str]]:
    run_dt = run_dt or now_utc()
    header = _header(run_dt, len(items), title, html=True)
    blocks = [render_item_html(it) for it in items]
    return header, blocks


def render_markdown(
    items: list[dict], run_dt: datetime | None = None, title: str = "每日新聞精選"
) -> str:
    run_dt = run_dt or now_utc()
    header = _header(run_dt, len(items), title, html=False)
    blocks = [render_item_md(it) for it in items]
    return header + "\n\n" + "\n\n---\n\n".join(blocks) + "\n"
