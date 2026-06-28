# NA-02 seed 時自動停用孤兒來源

- **Task ID**：NA-02
- **Complexity**：L2（改變 source persistence 行為）
- **狀態**：DESIGN（等待 `/design-gate:approve-design NA-02`）

---

## 1. Problem

`seed_sources` 只對當次 entries 做 upsert，**不會處理「已從定義移除」的來源**。這些來源在 DB 仍 `enabled=True`、持續被 `load_enabled_sources` 抓取。NA-01 上線後即出現 7 個孤兒（`hackernews-top`、`github-new-trending`、`x-shams`、`x-nba`…），已手動一次性停用，但成因未根治。

## 2. Goal

讓「`sources.json`（依平台開關過濾）+ `profile.json` 展開」成為抓取來源的**唯一真實來源**：seed 後，DB 中不在當次 entries 內的來源一律 `enabled=False`。使用者移除偏好 / 關平台 → 下次 run 自動停抓。

## 3. Non-goal

- 不刪除 DB 來源 row（只翻 `enabled` 旗標，保留歷史 items / metrics 關聯）。
- 不改 `build_source_entries` 的組成邏輯（平台過濾 + 展開維持不變）。
- 不處理已抓進來的舊 items（既有 candidate 時效窗 `candidate_max_age_days` 自然淘汰）。

## 4. Existing behavior

- `seed_sources(session, entries)`：逐筆 upsert（存在則更新 type/config/enabled；否則新增），最後 commit。
- `load_enabled_sources(session)`：抓 `enabled=True` 的來源。
- entries 由 `build_source_entries` 產生，已含當次所有應啟用 + 明確 `enabled:false` 的來源名稱。

## 5. Reuse candidates

- 直接在 `seed_sources` 收尾加停用邏輯，沿用其已持有的 entries。
- 既有 `select(Source)` 查詢 pattern。

## 6. Proposed design

在 `seed_sources` upsert 迴圈後、commit 前，加一段：

```
keep = {e["name"] for e in entries}
for src in session.scalars(select(Source).where(Source.enabled.is_(True))).all():
    if src.name not in keep:
        src.enabled = False
```

- `keep` 為當次 entries 全部名稱（含 entry 自帶 `enabled:false` 者——這些已在 upsert 階段被設為停用，不會被誤判為孤兒）。
- 僅翻 `enabled` 旗標；不動其他欄位、不刪 row。
- `seed_sources` 仍為單一責任（「讓 DB sources 與當次 entries 對齊」），加完約 +5 行有效邏輯，總長仍 <40 行。

## 7. Data flow / Error flow

- data flow：`build_source_entries` → `seed_sources`（upsert + disable absent）→ `load_enabled_sources`（只剩當次定義內的來源）。
- error flow：無新增 I/O 或外部呼叫；單一 transaction 內完成，沿用既有 commit。

## 8. 預計修改的 files

- 改 `src/news_aggregator/pipeline.py`（`seed_sources` 收尾加 disable absent）
- 改 `tests/test_pipeline_persist.py`（新增 regression test）

## 9. Test strategy

- **normal**：兩個來源都在 entries → 都 `enabled=True`。
- **regression（核心）**：先 seed 含 A、B → 再 seed 只含 A → B 變 `enabled=False`、A 仍 `enabled=True`。
- **boundary**：entries 含 `{enabled:false}` 的 C → C 停用且不被當孤兒重複處理；空 entries → 既有所有 enabled 來源全部停用。
- 全測試（`uv run pytest`）通過。

## 10. 最小可行方案

只在 `seed_sources` 加 disable-absent 一段 + 一個 regression test。不重構、不加設定旗標。

## 11. Risk / open question

1. **語意變更**：任何不在當次 entries 的 DB 來源都會被停用。由於 DB 一律由 `build_source_entries` seed、無其他寫入來源，符合「檔案為單一真實來源」的預期。
2. 若未來想保留「手動於 DB 啟用、不寫進檔案」的來源，本設計會與之衝突——目前無此需求，列為已知取捨。

---

## Approval

設計到此停止，未改 production code。請 review 後執行：

```
/design-gate:approve-design NA-02
```
