# Techmeme RSS description 內含原文連結（部分為 Bloomberg gift link）

- Techmeme feed 的 `<link>` 是 techmeme.com permalink，但 description HTML 的第一個站外 `<a href>` 是原文 URL。
- Bloomberg 連結有時帶 `accessToken=...`（JWT，source=SubscriberGiftedArticle）可免費讀全文；實測 5 條 Bloomberg 中 1 條有 token，並非全部。
- 專案的 `canonical_url()` 只剝追蹤參數（utm_/ref 等白名單），`accessToken` 會保留——改連結前先確認 dedup 不會吃掉關鍵 query。
- 實作：`rss.py` 的 `extract_source_link` config 開關 + `_source_link()`；`external_id` 維持 permalink 以免舊條目重複推送。
