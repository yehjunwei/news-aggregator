# news-aggregator — 個人化新聞聚合服務

每天從多個來源抓取新聞，**去重 → 熱度追蹤 → LLM 分類/繁中摘要/相關度評分 → 多元化排序 → Telegram 推送**個人化每日精選。模組化設計，新增來源不需動主流程。

- 執行環境：Python 3.12 + [uv](https://docs.astral.sh/uv/)
- 排程：openclaw gateway cron，每天台北 **06:30（晨間·專業資訊）** 與 **20:00（晚間·輕鬆閱讀）** 各推 20 則（每主題保底 2 則）
- 位置：`/home/tony/.openclaw/workspace/scripts/news-aggregator/`

---

## 目錄

1. [目前做法與流程](#目前做法與流程)
2. [資料模型](#資料模型)
3. [安裝與使用](#安裝與使用)
4. [可調參數（.env）](#可調參數env)
5. [來源管理](#來源管理)
6. [評分與多元性機制](#評分與多元性機制)
7. [排程](#排程)
8. [測試](#測試)
9. [已知限制](#已知限制)
10. [將來可優化方向](#將來可優化方向)

---

## 目前做法與流程

主流程在 `pipeline.py` 的 `run()`，分為六個階段（單一來源失敗會被隔離，不影響其他來源）：

```
seed sources → fetch → persist+dedup → enrich(LLM) → score → rank+多元化 → deliver
```

| 階段 | 模組 | 做什麼 |
|------|------|--------|
| **seed** | `pipeline.seed_sources` | 把 `config/sources.json` upsert 進 DB `sources`（保留既有 etag/last_modified） |
| **fetch** | `sources/*` + `core/http` | 各來源**並發**抓取，帶 ETag/Last-Modified 條件式請求；retry + per-host rate limit |
| **persist + dedup** | `pipeline.persist_results` + `core/dedup` | 三層去重：①`(source, external_id)` ②canonical URL / content hash ③標題相似度（rapidfuzz）。新項目寫 `items`；既有項目只追加一筆 `item_metrics`（熱度時序） |
| **enrich** | `enrich/llm` + `enrich/classify` | 只對「未推送且未加值」項目，**批次**呼叫 LLM 回傳 JSON：category、繁中標題、N 句摘要、why_relevant、相關度 0–100。失敗容錯（略過不中斷） |
| **score** | `scoring/engine` | `final = w_interest × (relevance/100) × (1+velocity) × recency_decay`，寫回 `items.final_score` |
| **rank** | `pipeline.select_diverse` | 取候選池後**先每類別保底 `min_per_category`、再按分數補名額（受每類別上限約束）**，兼顧冷門主題曝光與避免單一類別洗版 |
| **deliver** | `delivery/*` + `core/shorten` | 縮短網址（TinyURL）→ 渲染 → Telegram 分段推送 + 寫每日 Markdown digest 檔，標記 `delivered` |

時間一律以 **UTC** 儲存，顯示時轉 **Asia/Taipei**（`core/timez`）。

---

## 資料模型

SQLite（預設 `data/news.db`），SQLAlchemy 2.0，Alembic 管理 migration。

- **sources** — 來源設定與抓取狀態（`etag`/`last_modified`/`last_fetched_at`）
- **items** — 新聞主檔（標題、繁中標題、摘要、category、相關度、`final_score`、`delivered`…）
- **item_metrics** — 每次抓取的熱度快照（score/comments/views/stars + `captured_at`），**用於計算熱度增速 velocity**
- **digests** — 每日推送記錄（日期、則數、`markdown_path`、`sent_at`）

---

## 安裝與使用

```bash
cd /home/tony/.openclaw/workspace/scripts/news-aggregator
uv sync --extra dev
cp .env.example .env          # 金鑰可留空，會自動從 openclaw credentials 補齊
```

**金鑰優先序**：環境變數 / `.env` ＞ `credentials/api_keys.json`（`CREDENTIALS_FILE`）。
預設從 `/home/tony/.openclaw/credentials/api_keys.json` 補齊
`GEMINI_API_KEY` / `OPENAI_API_KEY` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `GITHUB_TOKEN`。

```bash
uv run python -m news_aggregator.cli run        # 完整流程並推送 Telegram
uv run python -m news_aggregator.cli dry-run    # 完整流程但不推送（仍寫 digest 檔）
uv run python -m news_aggregator.cli fetch       # 只抓取 + 去重（不 enrich / 不推送，省 LLM 成本）
uv run python -m news_aggregator.cli init-db     # 僅建表
```

Migration：

```bash
uv run alembic upgrade head                      # 套用
uv run alembic revision --autogenerate -m "..."  # 改 models 後產生新版本
```

---

## 可調參數（.env）

> 所有參數都有預設值，留空即用預設。對應 `config.py` 的 `Settings`。

### LLM（分類 / 摘要 / 評分）
| 參數 | 預設 | 說明 |
|------|------|------|
| `LLM_PROVIDER` | `gemini` | `gemini` 或 `openai` |
| `LLM_ENABLED` | `true` | 設 `false` 完全跳過 LLM（仍會以原標題、相關度預設 50 推送） |
| `GEMINI_API_KEY` | — | 留空則自動從 credentials 補 |
| `GEMINI_MODEL` | `gemini-flash-lite-latest` | ⚠️ `gemini-2.0-flash` 已停用會 404；可用 `gemini-flash-latest` / `gemini-2.5-flash` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | 切到 OpenAI 時用 |
| `LLM_SUMMARY_SENTENCES` | `3` | **摘要句數**（你要的「N 句」） |
| `LLM_BATCH_SIZE` | `8` | 每次 LLM 呼叫處理幾則（越大越省呼叫數，但單次 prompt 越長） |

### Telegram
| 參數 | 預設 | 說明 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | 留空則自動從 credentials 補 |

### 來源
| 參數 | 預設 | 說明 |
|------|------|------|
| `GITHUB_TOKEN` | — | 選用，提高 GitHub Search rate limit（10→30 req/min） |
| `SOURCES_FILE` | `config/sources.json` | 來源清單檔路徑 |

### Pipeline / 排序 / 評分
| 參數 | 預設 | 說明 |
|------|------|------|
| `TOP_N` | `20` | 每日推送則數 |
| `MAX_PER_CATEGORY` | `4` | **每類別最多幾則**（提升多元性，避免被 AI 洗版；`0`=不限） |
| `DEDUP_TITLE_THRESHOLD` | `88` | 標題相似度去重門檻（0–100，越高越保守、越不會誤判成重複） |
| `DEDUP_LOOKBACK_DAYS` | `7` | 去重比對回看天數 |
| `RECENCY_HALF_LIFE_HOURS` | `24` | 時效衰減半衰期（越小越偏好新鮮新聞） |
| `VELOCITY_CAP` | `1.0` | 熱度增速加成上限 |

### HTTP
| 參數 | 預設 | 說明 |
|------|------|------|
| `HTTP_TIMEOUT` | `20` | 單請求逾時（秒） |
| `HTTP_MAX_RETRIES` | `3` | 重試次數（指數退避） |
| `RATE_LIMIT_PER_HOST` | `5` | 每個 host 每秒請求上限 |

### DB / 顯示
| 參數 | 預設 | 說明 |
|------|------|------|
| `DATABASE_URL` | `sqlite:///data/news.db` | 可換 Postgres 等 |
| `TIMEZONE` | `Asia/Taipei` | 顯示時區（儲存恆為 UTC） |
| `CREDENTIALS_FILE` | openclaw 金鑰檔 | 補金鑰用 |

> **調整興趣權重 / 類別**：在 `scoring/engine.py` 的 `CATEGORY_WEIGHTS`，
> 與 `enrich/classify.py` 的 `CATEGORIES`、`_INTEREST` 描述（同步改才會一致）。

---

## 來源管理

來源清單在 **`config/sources.json`**，每次執行會 upsert 進 DB。新增一筆即多一個來源：

```json
{ "name": "唯一名稱", "type": "rss", "config": { "url": "feed 網址", "limit": 30 } }
```

各 adapter 的 `config`：

| type | config 欄位 |
|------|------------|
| `hackernews` | `story_type`(top/new/best), `limit`, `min_score` |
| `github_search` | `created_within_days`, `min_stars`, `query`, `sort`, `order`, `limit` |
| `rss` | `url`, `limit` |

共用欄位：

- **`limit`** — 每次抓取從該來源最多取幾則（取 feed/列表**最前面**的 N 則，越前面通常越新/越熱）。預設：`rss` 40、`hackernews` 50、`github_search` 30。調小省 token、調大增覆蓋。
- **`min_score`**（僅 `hackernews`）— 過濾門檻：HN 分數低於此值的故事直接捨棄，不進後續流程。預設 0（不過濾）。`github_search` 的類比欄位是 `min_stars`（star 數下限）。

**RSS 是最萬用的擴充方式** —— 任何主題都能用 Google News 中文搜尋餵入，不必寫新 adapter：

```
https://news.google.com/rss/search?q=<URL編碼的查詢>&hl=zh-TW&gl=TW&ceid=TW:zh-Hant
```

目前已內建來源：HN(top/best)、GitHub(新專案/AI agents)、Simon Willison、HN frontpage、
球員卡(reddit + Google News)、車用座艙(Google News)、新服務(Google News)、The Verge、TechCrunch、
NBA(Google News + ESPN)、技術/創投 YouTube 訪談(Lex Fridman / Y Combinator / a16z 頻道 RSS)、科技深度長文(Ars Technica / Stratechery)、
世界盃足球(Google News)、新書書評(Google News)、串流新劇評價 Netflix/Apple TV(Google News)。
（Product Hunt feed 多為隨機 SaaS，已 `enabled:false` 停用以提升晚間品質。）

### 新增一個全新「類型」的來源
1. 在 `sources/` 新增 adapter，實作 `source_type` 與 `async def fetch(client, state) -> FetchResult`。
2. 在 `sources/registry.py` 的 `build_registry()` 註冊。
3. 在 `config/sources.json` 加設定。

主流程只依賴 registry 與 DB 啟用的 sources，**無需改 pipeline**。

---

## 評分與多元性機制

**評分**（`scoring/engine.py`）：
```
final = w_interest × (relevance / 100) × (1 + velocity_boost) × recency_decay
```
- `w_interest`：類別權重 — **高(1.0)**：AI agents、AI coding、dev tools、GitHub 新專案、HN、YouTube 技術訪談、新 App/服務、Product Hunt；**中(0.6)**：NBA、球員卡、車用座艙、持股、AI 應用、小幅 benchmark 論文、科技深度長文(tech_feature)、世界盃足球(world_cup)、新書書評(book_review)、串流新劇評價(tv_streaming)；其他 0.4。
- `relevance`：LLM 給的個人相關度 0–100。
- `velocity_boost`：由 `item_metrics` 時序算熱度增速（log 壓縮、封頂）。
- `recency_decay`：依發布時間指數衰減。

**多元性**（`pipeline.select_diverse`）：取較大候選池（按分數排序）後分三步挑選：
1. **保底**：每個有貨的類別先保證至少 `min_per_category` 則（profile 設定，晨/晚皆為 2），即使分數偏低也納入——這是讓世界盃、新書書評、新劇評價等冷門或慢節奏主題每天都看得到的關鍵。
2. **補名額**：剩餘名額按分數補，**每類別最多 `MAX_PER_CATEGORY` 則**，避免被 AI/HN 洗版。
3. **補滿**：若仍不足則放寬上限按分數補滿 `TOP_N`。
最後依分數由高到低排序輸出（保底的低分項目落在後段）。

---

## 排程

由 openclaw gateway 管理，分**兩個時段**各推 20 則（每主題保底 2 則）：

| 時段 | 時間（台北） | Job ID | 指令 | 內容 |
|------|------|--------|------|------|
| 晨間 | 06:30 | `7a2662cd-3711-4a8e-a601-ecb350d25aaa` | `cli run --profile morning` | 專業 / 資訊類 |
| 晚間 | 20:00 | `3609730a-2cbd-4218-9eac-82505dae2911` | `cli run --profile evening` | 輕鬆閱讀類 |

- 時段的類別分組定義在 `src/news_aggregator/profiles.py`（改 `categories` / `top_n` 即可調整）。
- 已推送的項目標記 `delivered`，**晚間不會重複早上推過的**。
- payload(`command`)：`uv run python -m news_aggregator.cli run --profile <p>`（cwd 為本專案）；script 自行推送 digest，cron announce 為完成回報（best-effort）。

```bash
# 手動觸發 / 查看
openclaw cron run 7a2662cd-3711-4a8e-a601-ecb350d25aaa     # 晨間
openclaw cron run 3609730a-2cbd-4218-9eac-82505dae2911     # 晚間

# 本機手動測試（不推送）
uv run python -m news_aggregator.cli dry-run --profile morning
uv run python -m news_aggregator.cli dry-run --profile evening
```

---

## 測試

```bash
uv run pytest -q
```

涵蓋：去重正規化/相似度、評分各分支、時間轉換、HN/RSS adapter（respx mock）、
persist+dedup、classify 容錯、多元性挑選、Telegram 分段、digest 渲染。

---

## 已知限制

- **摘要基於標題 + 來源描述**（未抓全文）。對 AI 新專案/HN/PH 已足夠；長文章的深度摘要為後續工作。
- **縮網址 best-effort**：TinyURL 失敗時顯示原網址（is.gd 已不可用，故改用 TinyURL）。
- **意見領袖 / X 貼文**未納入（無穩定免費 RSS）；目前以 YouTube RSS / Google News 替代。
- **GitHub Search 未帶 token 時** rate limit 較低（10 req/min），多個 GitHub 來源可能偶有空結果。
- SQLite 讀回為 naive datetime，程式已統一視為 UTC 處理。

---

## 將來可優化方向

**內容品質**
- 抓原文全文（trafilatura / readability）做真正的長文摘要，而非只看標題。
- 加入「為什麼這則對你重要」的個人化理由更精準（餵入近期已讀/已點記錄）。
- 重複事件聚合：同一事件多來源時合併成一則，列出多個出處。

**來源擴充**
- 補齊 spec 規劃但本期未做的 adapter：GitHub Trending、Product Hunt GraphQL（取代 RSS）、Hugging Face Daily Papers、Reddit API、RSSHub。（YouTube channel RSS 已用於技術/創投訪談來源。）
- 意見領袖：YouTube 頻道訂閱清單、Nitter/X 替代來源。

**排序 / 個人化**
- 從「點開/略過」回饋學習權重（簡單線性模型或 bandit），讓相關度評分自我校準。
- 類別權重、`MAX_PER_CATEGORY` 改成可隨星期/心情調整（例如週末多 NBA/球員卡）。
- 熱度增速改用更穩健的估計（多點回歸而非首尾兩點）。

**工程**
- LLM 結果快取（同一 item 不重複呼叫）、失敗重試與成本上報。
- 來源健康度監控（連續失敗自動停用 + Telegram 告警）。
- 推送格式優化：分區塊（AI / 球員卡 / 車用…）標題、可點的 inline 連結、圖片預覽。
- 將 SQLite 換 Postgres 以支援更長歷史與分析；digest 改可在 web 瀏覽。
