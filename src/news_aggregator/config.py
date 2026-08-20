"""集中設定：Pydantic Settings 讀 .env / 環境變數，並可從 openclaw credentials 補齊金鑰。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # news-aggregator/
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
# 全域 LLM model／單價單一來源（openclaw 層，跨專案共用）
MODELS_FILE = Path("~/.openclaw/config/models.json").expanduser()

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
    gemini_model: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    llm_summary_sentences: int = 3
    llm_batch_size: int = 8

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Sources ---
    github_token: str = ""
    sources_file: Path = CONFIG_DIR / "sources.json"
    profile_file: Path = CONFIG_DIR / "profile.json"

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
    credentials_file: Path = Path("~/.openclaw/credentials/api_keys.json")
    # 全域 model 單一來源；gemini_model / openai_model 留空時由它補，.env 指定則優先
    models_file: Path = MODELS_FILE

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DATA_DIR / 'news.db'}"


def _merge_credentials(settings: Settings) -> Settings:
    """以 credentials/api_keys.json 補齊環境變數未提供的金鑰。"""
    path = Path(settings.credentials_file).expanduser()
    if not path.exists():
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


# Settings 欄位 -> config/models.json 的 key
# news digest 是高頻批次摘要，屬 quick 級工作，故兩邊都取 quick
_MODEL_MAP = {"gemini_model": "gemini_quick", "openai_model": "openai_quick"}


def _merge_models(settings: Settings) -> Settings:
    """空著的 model 欄位改用全域 models.json。

    與 _merge_credentials 不同，這裡讀不到就**拋錯不放行**：靜默沿用預設 model
    正是先前「以為改了其實沒改」的來源。要脫離全域設定就在 .env 明確指定。
    """
    missing = [f for f in _MODEL_MAP if not getattr(settings, f)]
    if not missing:
        return settings
    path = Path(settings.models_file).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"讀不到全域 model 設定 {path}（{exc}）；"
            "請確認檔案存在，或在 .env 明確指定 GEMINI_MODEL / OPENAI_MODEL"
        ) from None
    updates = {}
    for field_name in missing:
        model = (data.get(_MODEL_MAP[field_name]) or {}).get("model")
        if not model:
            raise RuntimeError(f"{path} 缺少 {_MODEL_MAP[field_name]}.model")
        updates[field_name] = str(model)
    return settings.model_copy(update=updates)


def get_settings(*, refresh: bool = False) -> Settings:
    global _settings
    if _settings is None or refresh:
        _settings = _merge_models(_merge_credentials(Settings()))
    return _settings
