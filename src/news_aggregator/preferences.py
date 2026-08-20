"""使用者偏好層：宣告式 profile.json -> 抓取來源展開 + LLM relevance watchlist。

profile.json 結構：
  people:    [{name, x_handle?}]
  tickers:   [{symbol, name}]
  topics:    [str]
  platforms: {x, hackernews, github, reddit, trending}  # 缺項預設 true

展開規則見 docs/designs/NA-01-user-defined-preferences.md。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

_NITTER = "https://nitter.net/{handle}/rss"
_REDDIT_SEARCH = "https://www.reddit.com/search.rss?q={q}&sort=hot&t=week"
_REDDIT_POPULAR = "https://www.reddit.com/r/popular/.rss"


@dataclass
class Person:
    name: str
    x_handle: str | None = None


@dataclass
class Ticker:
    symbol: str
    name: str


@dataclass
class Preferences:
    people: list[Person] = field(default_factory=list)
    tickers: list[Ticker] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    platforms: dict[str, bool] = field(default_factory=dict)

    def on(self, platform: str) -> bool:
        """平台是否啟用；缺項預設 true。"""
        return self.platforms.get(platform, True)


HOLDINGS_FILE = Path("/home/tony/.openclaw/shared/data/stocks.json")


def _holdings_tickers() -> list[Ticker]:
    """持股清單以 shared/data/stocks.json 為準。

    profile.json 手寫的 tickers 會過期——2026-08 就發現 AAPL 早就不持有，卻還在
    製造蘋果新聞（tickers-news 用它組 Google News 查詢）又替它加分（watchlist_block
    叫 LLM 提到就加分），一則蘋果新聞因此拿到 85 分擠進每日 5 則。

    ponytail: ETF 靠名稱含「ETF」判斷跳過——VOO/QQQM/009816 在新聞裡搜不到有意義的
    東西,只會製造雜訊。哪天有不叫 ETF 的 ETF 再改成看代號清單。
    """
    try:
        data = json.loads(HOLDINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("讀取持股清單失敗：%s", exc)
        return []
    out: dict[str, Ticker] = {}
    for market in ("us", "tw"):
        for h in data.get(market, []):
            name = h.get("name") or h.get("ticker", "")
            if "ETF" in name.upper():
                continue
            symbol = (h.get("ticker") or "").split(".")[0]   # 2330.TW -> 2330
            if symbol:
                out.setdefault(symbol, Ticker(symbol, name))
    return list(out.values())


def load_preferences(path) -> Preferences:
    """讀 profile.json -> Preferences；不存在 / 壞 JSON 回傳空偏好（不中斷流程）。

    tickers 一律由 stocks.json 推導,profile.json 的 tickers 只當「額外想追但沒持有」的補充。
    """
    if not path or not Path(path).exists():
        return Preferences(tickers=_holdings_tickers())
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("讀取 profile 失敗：%s", exc)
        return Preferences(tickers=_holdings_tickers())
    people = [Person(p["name"], p.get("x_handle")) for p in data.get("people", []) if p.get("name")]
    tickers = {t.symbol: t for t in _holdings_tickers()}
    for t in data.get("tickers", []):          # profile.json 只補「想追但沒持有」的
        if t.get("symbol"):
            tickers.setdefault(t["symbol"], Ticker(t["symbol"], t.get("name") or t["symbol"]))
    tickers = list(tickers.values())
    topics = [t for t in data.get("topics", []) if t]
    return Preferences(people, tickers, topics, data.get("platforms") or {})


def _has_cjk(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def _gnews_url(query: str) -> str:
    """Google News 搜尋 RSS；含 CJK 用 zh-TW，否則 en-US。"""
    hl, gl, lang = ("zh-TW", "TW", "zh-Hant") if _has_cjk(query) else ("en-US", "US", "en")
    return (
        f"https://news.google.com/rss/search?q={quote(query)}"
        f"&hl={hl}&gl={gl}&ceid={gl}:{lang}"
    )


def _x_sources(people: list[Person]) -> list[dict]:
    """有 x_handle 的名人 -> nitter RSS 來源。"""
    return [
        {"name": f"x-{p.x_handle}", "type": "rss",
         "config": {"url": _NITTER.format(handle=p.x_handle), "limit": 10}}
        for p in people if p.x_handle
    ]


def _ticker_source(tickers: list[Ticker]) -> list[dict]:
    """所有 ticker 合併成單一 Google News OR 查詢來源。"""
    if not tickers:
        return []
    terms: list[str] = []
    for t in tickers:
        terms.append(t.symbol)
        if t.name and t.name != t.symbol:
            terms.append(t.name)
    query = "(" + " OR ".join(terms) + ")"
    return [{"name": "tickers-news", "type": "rss",
             "config": {"url": _gnews_url(query), "limit": 15}}]


def _topic_sources(topics: list[str], reddit: bool) -> list[dict]:
    """每 topic -> 一個 Google News 來源；reddit 開啟時另加 Reddit 搜尋來源。"""
    out: list[dict] = []
    for t in topics:
        out.append({"name": f"topic-{t}", "type": "rss",
                    "config": {"url": _gnews_url(t), "limit": 10}})
        if reddit:
            out.append({"name": f"reddit-{t}", "type": "rss",
                        "config": {"url": _REDDIT_SEARCH.format(q=quote(t)), "limit": 10}})
    return out


def _trending_sources(prefs: Preferences) -> list[dict]:
    """trending 開啟時，對每個啟用平台產生「目前熱門」來源（X 無公開 trending，略過）。"""
    if not prefs.on("trending"):
        return []
    out: list[dict] = []
    if prefs.on("hackernews"):
        out.append({"name": "hn-trending", "type": "hackernews",
                    "config": {"story_type": "top", "limit": 20, "min_score": 50}})
    if prefs.on("github"):
        out.append({"name": "gh-trending", "type": "github_search",
                    "config": {"created_within_days": 30, "min_stars": 1500, "sort": "stars", "limit": 10}})
    if prefs.on("reddit"):
        out.append({"name": "reddit-popular", "type": "rss",
                    "config": {"url": _REDDIT_POPULAR, "limit": 15}})
    return out


def expand_sources(prefs: Preferences) -> list[dict]:
    """偏好 -> 抓取來源 entries（與 sources.json 同結構）。"""
    entries: list[dict] = []
    if prefs.on("x"):
        entries += _x_sources(prefs.people)
    entries += _ticker_source(prefs.tickers)
    entries += _topic_sources(prefs.topics, prefs.on("reddit"))
    entries += _trending_sources(prefs)
    return entries


def watchlist_block(prefs: Preferences) -> str:
    """組 LLM relevance prompt 用的追蹤對象區塊；皆空回 ''。"""
    parts: list[str] = []
    if prefs.people:
        parts.append("人物：" + "、".join(p.name for p in prefs.people))
    if prefs.tickers:
        parts.append("公司/股票：" + "、".join(f"{t.symbol}（{t.name}）" for t in prefs.tickers))
    if prefs.topics:
        parts.append("主題：" + "、".join(prefs.topics))
    if not parts:
        return ""
    body = "\n".join(f"- {p}" for p in parts)
    return "\n使用者明確追蹤的對象（新聞提到以下任一者，請提高 personal_relevance_score）：\n" + body
