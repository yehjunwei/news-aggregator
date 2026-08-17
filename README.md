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
5. [個人化偏好（profile.json）](#個人化偏好profilejson)
6. [來源管理](#來源管理)
7. [評分與多元性機制](#評分與多元性機制)
8. [排程](#排程)
9. [測試](#測試)
10. [TODO](#todo)
11. [已知限制](#已知限制)
12. [將來可優化方向](#將來可優化方向)

---

## 目前做法與流程

主流程在 `pipeline.py` 的 `run()`（單一來源失敗會被隔離，不影響其他來源）：

```
poll 回饋 → seed sources → fetch → persist+dedup → enrich(LLM＋niche折算) → score → 硬門檻過濾 → rank+多元化 → deliver(Telegram 按鈕 + JSON)
```

| 階段 | 模組 | 做什麼 |
|------|------|--------|
| **poll 回饋** | `feedback.poll_feedback` | 跑批次前先拉 Telegram `getUpdates`，把上次的 👍/👎 按鈕寫進 `items.feedback`（offset 存 `data/tg_offset.txt`） |
| **seed** | `pipeline.build_source_entries` + `seed_sources` | 合併「`config/sources.json`（依 profile 平台開關過濾）＋ `config/profile.json` 展開的個人化來源」upsert 進 DB `sources`（保留既有 etag/last_modified），並**自動停用不在當次定義內的孤兒來源** |
| **fetch** | `sources/*` + `core/http` | 各來源**並發**抓取，帶 ETag/Last-Modified 條件式請求；retry + per-host rate limit |
| **persist + dedup** | `pipeline.persist_results` + `core/dedup` | 三層去重：①`(source, external_id)` ②canonical URL / content hash ③標題相似度（rapidfuzz）。新項目寫 `items`；既有項目只追加一筆 `item_metrics`（熱度時序） |
| **enrich** | `enrich/llm` + `enrich/classify` | 只對「未推送且未加值」項目，**批次**呼叫 LLM 回傳 JSON：category、繁中標題、N 句摘要、why_relevant、**預測點開機率的相關度 0–100**、`technical_nicheness`。當場折算 `relevance −= 0.5 × nicheness`（壓低 niche/玩具型內容），存回 `personal_relevance_score`。並把最近 👍/👎 的標題當 few-shot 範例餵回 prompt。失敗容錯（略過不中斷）。Gemini 偶發 geo-400（Google 誤判出口 IP 地區，`User location is not supported`）在 `enrich/llm.py` 內對單一請求最多重試 4 次（間隔 2·attempt 秒）再拋出 |
| **score** | `scoring/engine` | `final = w_interest × (relevance/100) × (1+velocity) × recency_decay`，寫回 `items.final_score` |
| **硬門檻過濾** | `pipeline.run` | 排序前先丟掉 ①超過 `CANDIDATE_MAX_AGE_DAYS` 天才抓進來的舊 backlog ②`relevance < MIN_PERSONAL_SCORE` 的低分項目。則數因此隨「真正想看的量」浮動，不湊滿 `TOP_N` |
| **rank** | `pipeline.select_diverse` | 過門檻的候選**先每類別保底 `min_per_category`、再按分數補名額（受每類別上限約束）**，兼顧冷門主題曝光與避免單一類別洗版 |
| **deliver** | `delivery/*` | 渲染（Telegram HTML 標題為超連結，指向原始網址）→ **每則各一條 Telegram 訊息、底下掛 👍/👎 inline 按鈕** → 寫 Markdown digest（`data/digests/`）與 **JSON（`data/digests-json/`，供官網讀取，保留原始 url、留一年）**，標記 `delivered` |

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
uv run python -m news_aggregator.cli feedback "fb:<item_id>:up|down"  # 記錄單筆 👍/👎 回饋
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
| `SOURCES_FILE` | `config/sources.json` | 靜態主題來源清單檔路徑 |
| `PROFILE_FILE` | `config/profile.json` | 個人化偏好檔路徑（people/tickers/topics/platforms，見[個人化偏好](#個人化偏好profilejson)） |

### Pipeline / 排序 / 評分
| 參數 | 預設 | 說明 |
|------|------|------|
| `TOP_N` | `20` | 每日推送則數**上限**；過門檻的不足 20 則時不會湊滿 |
| `MIN_PERSONAL_SCORE` | `50` | **個人相關度硬門檻**（已折算 niche 懲罰的 0–100 分）。低於此分直接淘汰，不推送也不湊數。調高＝更嚴格、每日更少則 |
| `CANDIDATE_MAX_AGE_DAYS` | `3` | 候選池只看近 N 天抓進來的項目，未推送的舊 backlog 自動淘汰、不再永久佔位 |
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

## 個人化偏好（profile.json）

`config/profile.json` 是「你關注什麼」的單一宣告式入口。每次執行會把它**展開成抓取來源**，同時把關注對象**注入 LLM 相關度 prompt**（提到就加分）。實作於 `preferences.py`。

| 欄位 | 展開的抓取來源 | 注入相關度 |
|------|----------------|------------|
| `people`（`name` + 選填 `x_handle`） | 有 handle → `x-<handle>` Nitter feed | ✅ 人名 |
| `tickers`（`symbol` + `name`） | 全部合併成單一 `tickers-news` Google News 查詢 | ✅ symbol＋公司名 |
| `topics`（字串清單） | 每個 → `topic-<t>` Google News（含 CJK 自動切 `zh-TW`）；`reddit` 開啟時加 `reddit-<t>` 搜尋 | ✅ |
| `platforms`（`x`/`hackernews`/`github`/`reddit`/`trending`，缺項預設 `true`） | 過濾靜態 `sources.json` 來源＋閘門展開 | — |
| `trending`（platforms 內開關） | 對每個啟用平台抓「目前熱門」：`hn-trending`(HN top)、`gh-trending`(近 30 天高星)、`reddit-popular`(r/popular)。X 無公開 trending 故略過 | — |

```json
{
  "people": [{ "name": "Elon Musk", "x_handle": "elonmusk" }],
  "tickers": [{ "symbol": "TSLA", "name": "Tesla" }],
  "topics": ["AI", "電動車", "AI Engineering Management"],
  "platforms": { "x": true, "hackernews": true, "github": true, "reddit": true, "trending": true }
}
```

`pipeline.build_source_entries` 會把「`sources.json`（依 `platforms` 過濾）＋ profile 展開」合併後 seed。

> **單一真實來源**：seed 時自動停用 DB 中**不在當次定義內**的來源——所以從 `profile.json` / `sources.json` 移除項目、或關掉某平台，下次 run 就會停抓，不留孤兒。

---

## 來源管理

主題型靜態來源在 **`config/sources.json`**；個人關注對象（名人/股票/主題/熱門）走 [`profile.json`](#個人化偏好profilejson)。兩者每次執行都會 upsert 進 DB。在 `sources.json` 新增一筆即多一個主題來源：

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

目前已內建來源（依定位分組）：

- **AI 公司 / 產業決策**：OpenAI Newsroom、Anthropic（Google News `site:`）、Google DeepMind、NVIDIA（`site:blogs.nvidia.com` + 關鍵字）。
- **高品質分析**：Platformer、One Useful Thing（Ethan Mollick）、Import AI（Jack Clark）、Stratechery、Ars Technica、Simon Willison。
- **自駕 / Robotaxi / 車用**：Mobileye、Waymo、NHTSA 安全調查（皆 Google News `site:`）、車用座艙(Google News)。
- **產業事實層**：Reuters、Axios、The Information / Bloomberg（皆 Google News `site:` + AI/auto 關鍵字）、Techmeme。
- **機器人**：IEEE Spectrum Robotics。
- **人物**：people-ai-leaders（黃仁勳/Hassabis/Amodei/Shashua/Karpathy/Jim Fan 的 Google News 人名查詢）。**個別 X 帳號（Elon Musk / Karpathy / Sam Altman / Shams…）改由 [`profile.json`](#個人化偏好profilejson) 的 `people` 定義、自動展開 Nitter feed。**
- **候選池（只當召回，靠評分篩選）**：HN best、GitHub AI agents、The Verge、TechCrunch、新服務(Google News)。（HN top、GitHub 近期高星已改由 profile 的 `trending` 自動產生。）
- **生活 / 休閒**：NBA(Google News + ESPN)、球員卡(reddit + Google News)、技術/創投 YouTube(Lex Fridman / Y Combinator / a16z)、世界盃足球、新書書評、串流新劇(Netflix/Apple TV)。

> 已停用（`enabled:false`）：`rss-hn-frontpage`（與 HN top 重複）、`producthunt-feed`（多為隨機 SaaS）。手寫的 `x-*` Nitter 來源與 `hackernews-top`/`github-new-trending` 已移除，分別改由 profile 的 `people` 與 `trending` 產生。
> 非 RSS 的官方站（Anthropic/Waymo/Mobileye/NHTSA/NVIDIA）一律用 Google News `site:X.com (關鍵字)` 查詢餵入，**不需另寫 webpage adapter**；個別關鍵字直接寫在查詢字串裡。

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
- `relevance`：LLM 預測的「點開閱讀機率」0–100，**已在 enrich 階段折算 niche 懲罰**（`net = relevance − 0.5 × technical_nicheness`，見 `classify.py`）。底層/玩具型/前端展示等 niche 內容即使關鍵字相符也會被壓低。
- `velocity_boost`：由 `item_metrics` 時序算熱度增速（log 壓縮、封頂）。
- `recency_decay`：依發布時間指數衰減。

**硬門檻**（`pipeline.run`）：排序前先淘汰 ①`first_seen` 超過 `CANDIDATE_MAX_AGE_DAYS` 天的舊 backlog（避免未推送項目永久佔位）②`relevance < MIN_PERSONAL_SCORE` 的低分候選。這讓每日則數隨「真正想看的量」浮動，不為了湊 `TOP_N` 把 niche 內容塞進來。

**多元性**（`pipeline.select_diverse`）：過門檻的候選（按分數排序）後分三步挑選：
1. **保底**：每個有貨的類別先保證至少 `min_per_category` 則（profile 設定，目前晨/晚皆為 `0`＝不強制保底；要讓冷門主題每天露出可調回 1–2）。
2. **補名額**：名額按分數補，**每類別最多 `MAX_PER_CATEGORY` 則**，避免被 AI/HN 洗版。
3. **補滿**：若仍不足則放寬上限按分數補滿 `TOP_N`（此時補進的都是已過門檻的項目）。
最後依分數由高到低排序輸出。

---

## 回饋迴圈（讓命中率自己變高）

每則推送底下有 **👍 想看更多 / 👎 不感興趣** 兩顆按鈕（Telegram inline keyboard，私聊收 reaction 不可靠，故用 callback）。

- 點按 → 下次批次開跑時 `feedback.poll_feedback` 以 `getUpdates` 收回，寫進 `items.feedback`（+1/−1）。`callback_data` 直接帶 item id，免對應表；offset 存 `data/tg_offset.txt` 避免重複處理。
- **實務上 openclaw gateway 佔用同一個 bot token 的 long-poll**，callback 會被 gateway 搶走並以純文字 `fb:<id>:up|down` 轉給 Telegram agent。SOUL.md 已規定 agent 收到此格式時執行 `cli feedback "<原文>"` 補記錄（主要路徑）；`poll_feedback` 保留當 gateway 停用時的備援。
- enrich 時，`feedback.feedback_examples` 撈最近讚/倒讚的標題，當 **few-shot 範例**塞進排序 prompt，讓 LLM 自己歸納偏好。**不訓練模型、不用 embedding、不用權重表**——回饋愈多，命中率愈高。
- 設計取捨：來源/類別只負責召回，**per-item 相關度是唯一閘門**，不另設來源或類別配額。

## 官網 JSON 輸出

每次推送同時把內容寫成 JSON 供官網讀取：

- 路徑：`data/digests-json/<日期>[-morning|evening].json`
- **保留原始 url**（非縮短網址）；每則含 title/title_zh/summary/why_relevant/category/相關度/source/published_at/metrics。
- 自動清掉**一年前**的舊檔（`CANDIDATE_*` 無關，固定 365 天，見 `_prune_old_json`）。

---

## 排程

由 openclaw gateway 管理，分**兩個時段**各推最多 20 則（過門檻才推，不湊數）：

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

涵蓋：去重正規化/相似度、評分各分支（含 niche 懲罰）、時間轉換、HN/RSS adapter（respx mock）、
persist+dedup、classify 容錯與 few-shot 範例、多元性挑選、digest 渲染與 👍/👎 按鈕、
回饋解析/套用（`test_feedback`）、官網 JSON 輸出與一年保留（`test_export`）。

> **開發約定**：改動後務必 `uv run pytest -q` 全綠才算完成；單一 function 不超過 40 行（過長就抽 helper）；用不到的 function/檔案直接刪。

---

## TODO

近期（已排序，由小到大）：

- [ ] **Reddit 429**：`profile.json` 的 `topics`/`trending` 會展開 Reddit 搜尋與 `r/popular`，但 `reddit.com/*.rss` 常被 rate-limit（HTTP 429）。需加 User-Agent / 退避，或改用 RSSHub 等代理。單一來源失敗已被隔離、不影響整體。
- [ ] **X(Nitter) 穩定度**：`people` 展開的 `x-<handle>` 依賴 `nitter.net`，公開實例常掛。考慮可設定的 Nitter 實例或替代來源。
- [ ] **`_INTEREST` 可定義化**：目前 `enrich/classify.py` 的質性興趣描述仍硬編碼；可考慮搬進 `profile.json` 讓使用者完整定義（profile 已負責 people/tickers/topics 的具體清單）。
- [ ] **來源健康度監控**：連續失敗自動停用 + Telegram 告警。

✅ 已完成：個人化偏好層 `profile.json`（NA-01）、seed 自動停用孤兒來源（NA-02）。

---

## 已知限制

- **摘要基於標題 + 來源描述**（未抓全文）。對 AI 新專案/HN/PH 已足夠；長文章的深度摘要為後續工作。
- **X 貼文**經 `profile.json` 的 `people` 以 Nitter 納入（best-effort，實例不穩，見 TODO）；X 平台層級的「熱門 trending」無公開來源，故 `trending` 不含 X。
- **Reddit 來源易被 429**（見 TODO）。
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
- ✅ 已做：👍/👎 回饋當 few-shot 範例餵回 prompt（見「回饋迴圈」）。進一步可用線性模型 / bandit 學權重做更精準的自我校準。
- 類別權重、`MAX_PER_CATEGORY` 改成可隨星期/心情調整（例如週末多 NBA/球員卡）。
- 熱度增速改用更穩健的估計（多點回歸而非首尾兩點）。

**工程**
- LLM 結果快取（同一 item 不重複呼叫）、失敗重試與成本上報。
- 來源健康度監控（連續失敗自動停用 + Telegram 告警）。
- 推送格式優化：分區塊（AI / 球員卡 / 車用…）標題、可點的 inline 連結、圖片預覽。
- 將 SQLite 換 Postgres 以支援更長歷史與分析；digest 改可在 web 瀏覽。
