"""集中設定：Pydantic Settings 讀 .env / 環境變數，並可從 openclaw credentials 補齊金鑰。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # news-aggregator/
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"

# credentials/api_keys.json -> Settings 欄位
_CREDENTIAL_MAP = {
    "gemini_api_key": "gemini_api_key",
    "openai_api_key": "openai_api_key",
    "telegram_bot_token": "telegram_bot_token",
    "telegram_chat_id": "telegram_chat_id",
    "github_token": "github_token",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    llm_provider: str = "gemini"  # gemini | openai
    llm_enabled: bool = True
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    llm_summary_sentences: int = 3
    llm_batch_size: int = 8

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Sources ---
    github_token: str = ""
    sources_file: Path = CONFIG_DIR / "sources.json"

    # --- pipeline / scoring ---
    top_n: int = 20  # 每日推送的「上限」；低於門檻時不會湊滿
    min_personal_score: int = 50  # 個人相關度（已折算 niche 懲罰）硬門檻，低於此不推送
    candidate_max_age_days: int = 3  # 候選池只看近 N 天抓進來的項目，避免未推送的舊 backlog 永久佔位
    max_per_category: int = 4  # 每類別在每日推送的最多則數（0=不限）
    dedup_title_threshold: int = 88
    dedup_lookback_days: int = 7
    recency_half_life_hours: float = 24.0
    velocity_cap: float = 1.0

    # --- http ---
    http_timeout: float = 20.0
    http_max_retries: int = 3
    rate_limit_per_host: float = 5.0  # 每秒請求上限

    # --- db / 顯示 ---
    database_url: str = ""
    timezone: str = "Asia/Taipei"

    # openclaw 金鑰檔（不存在時略過）
    credentials_file: Path = Path("/home/tony/.openclaw/credentials/api_keys.json")

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DATA_DIR / 'news.db'}"


def _merge_credentials(settings: Settings) -> Settings:
    """以 credentials/api_keys.json 補齊環境變數未提供的金鑰。"""
    path = settings.credentials_file
    if not path or not Path(path).exists():
        return settings
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return settings
    updates = {}
    for field_name, cred_key in _CREDENTIAL_MAP.items():
        if not getattr(settings, field_name) and data.get(cred_key):
            updates[field_name] = str(data[cred_key])
    return settings.model_copy(update=updates) if updates else settings


_settings: Settings | None = None


def get_settings(*, refresh: bool = False) -> Settings:
    global _settings
    if _settings is None or refresh:
        _settings = _merge_credentials(Settings())
    return _settings
