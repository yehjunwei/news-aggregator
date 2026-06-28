# NA-01 使用者可定義偏好層（people / tickers / topics / sources）

- **Task ID**：NA-01
- **Complexity**：L3（新增設定層、改 data flow、影響 source persistence seed）
- **狀態**：DESIGN（scope 擴張後**重新等待** `/design-gate:approve-design NA-01`）
- **變更紀錄**：v2 新增 `topics`、`reddit`、`trending`；v3 重定義 `trending`＝由 expander 對每個開啟平台自動產生熱門來源（不靠 sources.json）。原 v1 已 approve，scope 擴張故退回 DESIGN 重審。

---

## 1. Problem

目前 service 是單一使用者、偏好硬編碼：

- **來源**寫死在 `config/sources.json`（含一批手寫的 `x-elonmusk`、`x-shams`… nitter RSS）。
- **關注名人 / 公司**散落兩處：sources.json 的 Google News 查詢字串，以及 `enrich/classify.py` 的 `_INTEREST` prompt 文字。

新增 / 調整關注對象要同時改多個檔案、改 prompt，沒有單一可定義入口。

## 2. Goal

讓使用者用**一份宣告式設定檔**定義：

1. **關注名人**（含 X handle）—— 自動展開成 X/nitter 抓取來源，**且**注入 LLM 相關度 prompt。
2. **關注股票 / 公司**（ticker + 名稱）—— 自動展開成 Google News 抓取來源，**且**注入相關度 prompt。
3. **關注主題 topics**（如 AI、電動車、AI Engineering Management）—— 每個 topic 自動展開成 Google News 抓取來源（reddit 開啟時另加 Reddit 搜尋來源），**且**注入相關度 prompt。
4. **平台開關**（X / HackerNews / Github / Reddit）—— 控制是否納入該平台的來源。
5. **Trending 開關** —— 開啟時，對每個已啟用的平台額外抓取「目前熱門新聞/討論」（與 topics 無關的熱門列表），來源由程式自動產生、不需在 sources.json 指明。

## 3. Non-goal（本次明確不做）

- 多使用者（DB user 表、per-user 推送路由）——確認為單人。
- 重寫 `_INTEREST` 那段質性指引（只在其後**追加**動態 watchlist 區塊，不改原文）。
- 重構 `CATEGORIES` / `CATEGORY_WEIGHTS` 分類體系。
- 新增 CLI 子指令 / UI 來編輯偏好（宣告式檔案手動編輯即可）。
- 把 sources.json 的主題型 feed（NBA、球員卡、各家 RSS）搬進新設定檔——它們仍留在 sources.json。
- delivery / Telegram 輸出格式變更。

## 4. Existing behavior（已確認事實）

- `pipeline.seed_sources(session, sources_file)` 讀 `sources.json` upsert 進 DB `sources` 表（保留 etag）。
- `pipeline.run()` 流程：seed → fetch → persist+dedup → enrich → score → rank → deliver。
- `enrich.classify.classify_items(provider, inputs, *, examples=...)` 已支援把使用者回饋當 few-shot；prompt 由 `_build_user` 組出，`_INTEREST` 為硬編碼指引。
- RSS adapter 只需 `config.url`（+ `limit`）。X 來源即 `https://nitter.net/<handle>/rss`；公司新聞即 `https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en`。
- 既有 `profiles.py` 是「時段推送 profile」，與本功能無關——**新模組不可叫 profile，改名 `preferences`** 以免混淆。

## 5. Reuse candidates

- `sources.json` 既有 entry 結構 `{name, type, config, enabled?}`——展開產生的來源沿用同一結構。
- 既有 nitter / Google News RSS URL 模板。
- `classify_items` 既有 `examples` 注入機制——以相同 pattern 新增 `watchlist` 參數。
- `config.Settings`——新增 `profile_file` 路徑欄位。

## 6. Proposed design

### 6.1 設定檔 `config/profile.json`（宣告式，git 追蹤）

```json
{
  "people": [
    { "name": "Elon Musk", "x_handle": "elonmusk" },
    { "name": "Shams Charania", "x_handle": "ShamsCharania" }
  ],
  "tickers": [
    { "symbol": "TSLA", "name": "Tesla" },
    { "symbol": "AAPL", "name": "Apple" },
    { "symbol": "META", "name": "Meta" },
    { "symbol": "NVDA", "name": "NVIDIA" },
    { "symbol": "TSM",  "name": "TSMC" }
  ],
  "topics": ["AI", "電動車", "AI Engineering Management"],
  "platforms": {
    "x": true, "hackernews": true, "github": true,
    "reddit": true, "trending": true
  }
}
```

- `x_handle` 省略 → 該名人只進相關度、不展開抓取來源。
- `platforms` 缺項 → 預設 `true`。

### 6.2 新模組 `src/news_aggregator/preferences.py`

| function | 責任 | 預估行數 |
|---|---|---|
| `Person` / `Ticker` / `Preferences`（dataclass，`Preferences` 含 `topics: list[str]`、`platforms: dict`） | 結構 | — |
| `load_preferences(path) -> Preferences` | 讀 JSON → dataclass；檔案不存在回傳空偏好（不爆） | ~20 |
| `_gnews_url(query) -> str` | 組 Google News 搜尋 RSS URL；含 CJK → `zh-TW/TW`，否則 `en-US/US` | ~6 |
| `_x_sources(people) -> list[dict]` | 有 handle 的名人 → `{name:"x-<handle>", type:"rss", config:{url, limit:10}}` | ~10 |
| `_ticker_source(tickers) -> list[dict]` | 合併成單一 Google News OR 查詢 → 一個 rss entry（`name:"tickers-news"`） | ~12 |
| `_topic_sources(topics, platforms) -> list[dict]` | 每 topic → 一個 `topic-<t>` Google News rss；`platforms.reddit` 時另加 `reddit-<t>` Reddit 搜尋 rss | ~18 |
| `_trending_sources(platforms) -> list[dict]` | `platforms.trending` 時，對每個開啟平台產生熱門來源：`hackernews`→`hn-trending`(HN top)、`github`→`gh-trending`(近 30 天高星)、`reddit`→`reddit-popular`(r/popular)。X 無公開 trending 故略過 | ~16 |
| `expand_sources(prefs) -> list[dict]` | 串接：x(若 on)+ticker+topics+trending | ~14 |
| `watchlist_block(prefs) -> str` | 組「追蹤人物 / 公司 / 主題」中文區塊；皆空回 `""` | ~16 |

> - ticker：**單一合併 OR 查詢**（`(TSLA OR Tesla OR AAPL OR Apple …)`），省抓取/去重。
> - topic：**每 topic 一個來源**（topic 是語意片語，分開查較準；CJK 主題自動切到 `zh-TW`）。
> - reddit 來源由 topics 自動轉 `https://www.reddit.com/search.rss?q=<topic>&sort=hot&t=week`。
> - **trending = 跨平台熱門開關**：開啟時對每個已啟用平台各產生一個「目前熱門」來源——HN top（`type:hackernews, story_type:top`）、GitHub 近期高星（`type:github_search, created_within_days:30, sort:stars`）、Reddit r/popular（rss）。皆由程式自動產生，sources.json 不需列。X 無公開 trending API/feed，故 trending 不含 X（見 §12）。

### 6.3 接線（pipeline.py）

- 新增 `build_source_entries(settings, prefs) -> list[dict]`（~18 行）：
  1. 讀 `sources.json` entries；
  2. 依 `platforms` 過濾靜態 entries（`platforms.hackernews=false` → 丟 `type==hackernews`；`platforms.github=false` → 丟 `type==github_search`）；
  3. append `expand_sources(prefs)`。
- `seed_sources` 改為接收 **entries list**（純 upsert，不再自己讀檔）；`run()` 先 `load_preferences` 再 `build_source_entries` 傳入。
- `enrich_pending` 把 `watchlist_block(prefs)` 透過 `classify_items(..., watchlist=...)` 往下傳。

### 6.4 相關度注入（enrich/classify.py）

- `classify_items` 與 `_build_user` 新增 `watchlist: str | None = None` 參數。
- `_build_user` 在 `_INTEREST` 之後、few-shot 之前插入 watchlist 區塊（人物 / 公司 / 主題；原 `_INTEREST` 文字不動）。

### 6.5 清理 sources.json

移除兩類已被取代的手寫來源：

1. **被 `profile.people` 取代的 X 來源**：`x-elonmusk`、`x-karpathy`、`x-sama`、`x-shams`、`x-nba`、`x-anthropic`、`x-openai`。handle 轉入 `profile.json`（`x-nba` 機構帳號無真實姓名，建議捨棄——NBA 已有 `nba-google-news`/`nba-espn`）。
2. **被 `trending` 取代的熱門列表**：`hackernews-top`、`github-new-trending`（改由 `_trending_sources` 自動產生）。

保留的 curated 來源：`hackernews-best`（不同排序的精選）、`github-ai-agents`（特定 topic 查詢）、其餘各主題 RSS feed。

## 7. Data flow

```
run()
 ├─ prefs = load_preferences(settings.profile_file)
 ├─ entries = build_source_entries(settings, prefs)   # 靜態(過濾) + 展開
 ├─ seed_sources(session, entries)                    # upsert DB
 ├─ fetch_all → persist_results                       # 不變
 ├─ enrich_pending(..., prefs)                         # watchlist 注入 prompt
 └─ score → rank → deliver                            # 不變
```

## 8. Error flow

- `profile.json` 不存在 / 壞 JSON → `load_preferences` 回傳空 `Preferences`（`people=[]`, `tickers=[]`, `topics=[]`, `platforms` 全 true），流程照舊不中斷（與既有 `seed_sources` 找不到檔案只 warning 的容錯一致）。
- 名人無 `x_handle` → 略過抓取展開，仍進 watchlist。
- 無 ticker → 不產生 `tickers-news` 來源；無 topic → 不產生 topic/reddit 來源。
- `platforms.reddit=false` → topics 不展開 Reddit 來源、trending 不含 `reddit-popular`（topics 仍有 Google News 來源、仍進 watchlist）。
- `platforms.trending=false` → 不產生任何熱門來源。各平台熱門來源同時受該平台開關閘門（如 `hackernews=false` 則 trending 不含 HN top）。

## 9. 預計修改的 files

- **新增** `config/profile.json`
- **新增** `src/news_aggregator/preferences.py`
- **新增** `tests/test_preferences.py`
- 改 `src/news_aggregator/config.py`（加 `profile_file`）
- 改 `src/news_aggregator/pipeline.py`（`build_source_entries`、`seed_sources` 簽名、`enrich_pending` 傳 prefs）
- 改 `src/news_aggregator/enrich/classify.py`（`watchlist` 參數）
- 改 `config/sources.json`（移除手寫 x-* entries）
- 改 `tests/test_pipeline_persist.py` / `tests/test_classify.py`（配合簽名）

## 10. Test strategy

- **normal**：`load_preferences` 解析完整檔（含 topics）；`expand_sources` 產出正確 X + ticker + topic(×N) + reddit-search + trending(hn/gh/reddit-popular) 來源；`watchlist_block` 內容含人名、ticker、topic。
- **boundary**：無 handle 名人不展開抓取但進 watchlist；無 ticker / 無 topic 不產對應來源；`platforms.x=false` 不展開 X；`platforms.reddit=false` 不展開 reddit-search/reddit-popular；`platforms.trending=false` 完全不產熱門來源；`trending=true` 但 `hackernews=false` → 熱門不含 HN top；CJK topic → `_gnews_url` 切 `zh-TW`；空偏好 `watchlist_block` 回 `""`。
- **failure**：檔案不存在 / 壞 JSON → 空偏好、不丟例外。
- **regression**：`build_source_entries` 在預設 prefs 下，輸出含原 sources.json 的主題 feed（過濾不誤殺）；既有 `test_pipeline_persist`、`test_classify` 綠燈。
- 全測試（`uv run pytest`）通過。

## 11. 最小可行方案

只做 §6 接線：偏好檔 + `preferences.py` + 三處接線 + sources.json 清理。不碰分類、不碰 delivery、不加 CLI。

## 12. Risk / open question

1. **DB 殘留**：sources.json 移除的 x-* 在 DB 仍 enabled。`x-elonmusk` 等因 name 相同會被新展開 upsert 覆蓋；但若某帳號未轉入 profile（如棄用 `x-sama`），該 DB row 會殘留持續抓取。→ **已知限制**，NA-01 不自動 disable 孤兒來源（可後續 task）。
2. **ticker 合併查詢雜訊**：OR 查詢可能帶進弱相關新聞，但後段 relevance 硬門檻會過濾。可接受。
3. **X 無 trending**：trending 對每個開啟平台產生熱門來源，但 X（nitter）無公開 trending API/feed，故 trending 不含 X——X 的熱門僅能透過 `profile.people` 追蹤特定帳號取得。已知限制。
4. **Reddit 抓取穩定度**：`reddit.com/search.rss` 可能被 rate-limit / 擋 UA；單一來源失敗已被 `fetch_all` 隔離（只 INFO log），不影響整體流程。
5. **topic 來源數量**：每 topic 產 1（無 reddit）或 2（有 reddit）個來源；3 個 topic = 最多 6 個來源，量級可接受。

---

## Approval

設計到此停止，未改任何 production code。請 human review 後執行：

```
/design-gate:approve-design NA-01
```
