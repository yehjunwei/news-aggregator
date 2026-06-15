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
        "top_n": 10,
    },
    "evening": {
        "label": "晚間精選 · 輕鬆閱讀",
        "categories": [
            "nba",
            "card_collecting",
            "new_app",
            "product_hunt",
            "yt_interview",
            "other",
        ],
        "top_n": 10,
    },
    "all": {
        "label": "每日新聞精選",
        "categories": None,
        "top_n": None,
    },
}


def get_profile(name: str) -> dict:
    return PROFILES.get(name, PROFILES["all"])
