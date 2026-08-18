# 付費牆過濾：Google News 出版商辨識 + schema.org 標記

- Google News RSS 條目的 `<link>` 是 `news.google.com/rss/articles/...` 跳轉網址，**無法**本地解回原文 URL（新版編碼要打 Google 內部 batchexecute API）。但每條都帶 `<source url="https://www.barrons.com">`，feedparser 映射為 `entry.source.href`——用它判斷出版商，零額外請求。
- 有付費牆又要被 Google 索引的頁面，依 Google 規範須在 JSON-LD 標 `"isAccessibleForFree": false`。實測 The Verge：metered/付費文（analysis、news、features）標 `false`，免費導購文完全不標——可精準逐篇判斷，不必封全站。
- 兩層設計（`core/paywall.py`）：`PAYWALLED_DOMAINS` 全站付費直接擋（fetch 階段，不進 DB 不花 LLM token）；`METERED_DOMAINS`（theverge.com）逐篇 GET 看標記。判斷不了一律放行。
- `accessToken` query（Bloomberg gift link，見 2026-08-12-techmeme-gift-links.md）豁免不擋。
- `pipeline._select_candidates` 有第二道 `is_paywalled` 防線，涵蓋 HN 等直連付費站的來源；但 DB 既有的 Google News 舊條目出版商資訊已丟失，擋不到，靠 3 天時效窗自然過期。
- `premium-tech-business` 來源（純 theinformation/bloomberg）已 `enabled: false`——過濾後必為空，留著白打 HTTP。
