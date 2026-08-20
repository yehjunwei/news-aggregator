"""推送 profile：依時段挑不同類別。

- morning（晨間）：偏專業 / 資訊密度高的類別。
- evening（晚間）：偏輕鬆閱讀的類別。
- all：不分時段，推全部類別（保留作手動或備用）。

categories=None 代表不限類別；top_n=None 代表沿用 settings.top_n。
調整時段內容只要改下面的 categories 即可。
"""

from __future__ import annotations

PROFILES: dict[str, dict] = {
    "morning": {
        "label": "晨間精選 · 專業資訊",
        "categories": [
            "ai_agents",
            "ai_coding",
            "dev_tools",
            "github_project",
            "hackernews",
            "ai_paper",
            "ai_application",
            "automotive",
            "holdings",
        ],
        "top_n": 20,
        "min_per_category": 0,
    },
    "deep": {
        "label": "每日精選 · 5 則",
        # 2026-08-20：早晚兩份停用後，這是唯一的每日推送，所以不限類別——
        # 限類別會把 dev_tools／github_project／hackernews／new_app 這些高權重興趣整個排除掉。
        # 品質改由「KOL 來源優先 + 非 KOL 門檻 78」把關，不靠類別白名單。
        "categories": None,
        "top_n": 5,
        "min_per_category": 0,
        "max_per_category": 2,   # 5 則裡同一類最多 2 則,避免整天都是 AI
        # 非 KOL 的位子門檻拉到 78(全域是 50):寧可只推 3 則,也不要用普通新聞湊滿 5 則
        "min_personal_score": 78,
        # 深度長文的評分永遠贏不了當天的短新聞(時效半衰期 24h),所以這裡靠來源保證品質:
        # 這些來源的文章一律排在候選最前面,不足 top_n 才用分數補。
        "prefer_sources": [
            "kol-addy-osmani",
            "kol-martin-fowler",
            "kol-charity-majors",
            "rss-simonwillison",
            "crypto-bitmex-blog",
            "wechat-jiqizhixin",   # 微信公眾號「机器之心」,經 wechat2rss 鏡像
        ],
    },
    "evening": {
        "label": "晚間精選 · 輕鬆閱讀",
        "categories": [
            "nba",
            "world_cup",
            "yt_interview",
            "tech_feature",
            "book_review",
            "tv_streaming",
            "card_collecting",
            "new_app",
            "other",
        ],
        "top_n": 20,
        "min_per_category": 0,
    },
    "all": {
        "label": "每日新聞精選",
        "categories": None,
        "top_n": None,
    },
}


def get_profile(name: str) -> dict:
    return PROFILES.get(name, PROFILES["all"])
