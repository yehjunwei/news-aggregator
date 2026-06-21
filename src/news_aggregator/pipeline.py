"""主流程：seed sources → fetch → persist+dedup → enrich → score → rank → deliver。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path

from rapidfuzz import fuzz, process
from sqlalchemy import select

from .config import DATA_DIR, Settings, get_settings
from .core.dedup import canonical_url, content_hash, normalize_title
from .core.http import HttpClient
from .core.shorten import shorten_url
from .core.timez import now_utc, to_taipei, to_utc
from .db.models import Digest, Item, ItemMetric, Source
from .db.session import init_db, make_engine, make_session_factory
from .delivery.digest import item_keyboard, render_markdown, render_telegram
from .delivery.telegram import TelegramClient
from .enrich.classify import classify_items
from .enrich.llm import build_provider, estimate_cost
from .feedback import feedback_examples, poll_feedback
from .profiles import get_profile
from .scoring.engine import ScoreInput, compute_score
from .sources.base import SourceState
from .sources.registry import build_registry

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #
def seed_sources(session, sources_file: Path) -> None:
    """以 config/sources.json upsert 到 DB（保留既有 etag/last_modified）。"""
    if not sources_file or not Path(sources_file).exists():
        logger.warning("找不到 sources 設定檔：%s", sources_file)
        return
    entries = json.loads(Path(sources_file).read_text(encoding="utf-8"))
    for entry in entries:
        existing = session.scalars(
            select(Source).where(Source.name == entry["name"])
        ).first()
        if existing:
            existing.type = entry["type"]
            existing.config = entry.get("config", {})
            if "enabled" in entry:
                existing.enabled = entry["enabled"]
        else:
            session.add(
                Source(
                    name=entry["name"],
                    type=entry["type"],
                    config=entry.get("config", {}),
                    enabled=entry.get("enabled", True),
                )
            )
    session.commit()


def load_enabled_sources(session) -> list[Source]:
    return list(session.scalars(select(Source).where(Source.enabled.is_(True))).all())


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
async def fetch_all(http: HttpClient, registry, sources: list[Source]):
    """並發抓取所有來源；單一來源失敗不影響其他來源。"""

    async def _fetch(source: Source):
        adapter = registry.get(source.type)
        if adapter is None:
            logger.warning("來源 %s 無對應 adapter type=%s", source.name, source.type)
            return source, None
        state = SourceState(
            name=source.name,
            etag=source.etag,
            last_modified=source.last_modified,
            config=source.config or {},
        )
        try:
            result = await adapter.fetch(http, state)
            return source, result
        except Exception as exc:  # noqa: BLE001 - 隔離單一來源失敗
            # ponytail: 單一來源失敗已隔離（常見：Google News RSS 503），降到 INFO 不推 Telegram；-v 看 traceback
            logger.info("來源 %s 抓取失敗：%s", source.name, exc)
            return source, None

    return await asyncio.gather(*[_fetch(s) for s in sources])


# --------------------------------------------------------------------------- #
# persist + dedup
# --------------------------------------------------------------------------- #
def _add_metric(session, item: Item, raw) -> None:
    m = raw.metrics or {}
    session.add(
        ItemMetric(
            item_id=item.id,
            score=m.get("score"),
            comments=m.get("comments"),
            views=m.get("views"),
            stars=m.get("stars"),
        )
    )


def _find_existing(session, source, raw, canon, chash, idx, threshold):
    """三層去重：①(source, external_id) ②canonical/hash ③標題相似度。回傳既有 Item 或 None。"""
    existing = session.scalars(
        select(Item).where(Item.source_id == source.id, Item.external_id == raw.external_id)
    ).first()
    if existing is None:
        existing = idx["hash"].get(chash) or idx["canon"].get(canon)
    if existing is None and idx["title"]:
        match = process.extractOne(
            normalize_title(raw.title), idx["title"],
            scorer=fuzz.token_sort_ratio, score_cutoff=threshold,
        )
        if match:
            existing = idx["id"].get(match[2])
    return existing


def _persist_source(session, source, result, idx, settings) -> int:
    """處理單一來源的抓取結果，回傳新增則數，並就地更新去重索引與來源狀態。"""
    new_count = 0
    for raw in result.items:
        canon, chash = canonical_url(raw.url), content_hash(raw.title, raw.url)
        existing = _find_existing(session, source, raw, canon, chash, idx, settings.dedup_title_threshold)
        if existing is not None:
            _add_metric(session, existing, raw)
            continue
        item = Item(
            source_id=source.id, external_id=raw.external_id, canonical_url=canon,
            url=raw.url, title=raw.title, author=raw.author,
            content_hash=chash, published_at=raw.published_at,
        )
        session.add(item)
        session.flush()
        _add_metric(session, item, raw)
        new_count += 1
        idx["hash"][chash] = idx["canon"][canon] = idx["id"][item.id] = item
        idx["title"][item.id] = normalize_title(item.title)
    source.etag = result.etag or source.etag
    source.last_modified = result.last_modified or source.last_modified
    source.last_fetched_at = now_utc()
    return new_count


def persist_results(session, results, settings: Settings) -> int:
    cutoff = now_utc() - timedelta(days=settings.dedup_lookback_days)
    recent = list(session.scalars(select(Item).where(Item.first_seen_at >= cutoff)).all())
    idx = {
        "hash": {i.content_hash: i for i in recent},
        "canon": {i.canonical_url: i for i in recent},
        "id": {i.id: i for i in recent},
        "title": {i.id: normalize_title(i.title) for i in recent},
    }
    new_count = 0
    for source, result in results:
        if result is None:
            continue
        if result.not_modified:
            source.last_fetched_at = now_utc()
            continue
        new_count += _persist_source(session, source, result, idx, settings)
    session.commit()
    return new_count


# --------------------------------------------------------------------------- #
# enrich
# --------------------------------------------------------------------------- #
def _apply_enrichments(pending, enrichments: dict[int, dict]) -> int:
    """把 LLM 結果寫回 Item，回傳成功套用筆數。"""
    applied = 0
    for it in pending:
        data = enrichments.get(it.id)
        if not data:
            continue
        it.category = data["category"]
        it.title_zh = data["title_zh"]
        it.summary = data["summary"]
        it.why_relevant = data["why_relevant"]
        it.personal_relevance_score = data["personal_relevance_score"]
        it.enriched = True
        applied += 1
    return applied


async def enrich_pending(session, settings: Settings, http: HttpClient) -> dict:
    empty = {"applied": 0, "usage": {"prompt": 0, "completion": 0, "total": 0}, "model": "", "cost": None}
    pending = list(
        session.scalars(
            select(Item).where(Item.delivered.is_(False), Item.enriched.is_(False))
        ).all()
    )
    if not pending:
        return empty

    inputs = [
        {"id": it.id, "title": it.title, "url": it.url,
         "source": it.source.name if it.source else ""}
        for it in pending
    ]
    provider = build_provider(settings, client=http.raw)
    enrichments = await classify_items(
        provider, inputs,
        summary_sentences=settings.llm_summary_sentences,
        batch_size=settings.llm_batch_size,
        examples=feedback_examples(session),
    )
    applied = _apply_enrichments(pending, enrichments)
    session.commit()

    usage = dict(getattr(provider, "usage", {"prompt": 0, "completion": 0, "total": 0}))
    model = getattr(provider, "model", "")
    return {"applied": applied, "usage": usage, "model": model, "cost": estimate_cost(model, usage)}


# --------------------------------------------------------------------------- #
# score + rank
# --------------------------------------------------------------------------- #
def score_undelivered(session, settings: Settings) -> None:
    items = list(session.scalars(select(Item).where(Item.delivered.is_(False))).all())
    ref = now_utc()
    for it in items:
        points = [
            (m.captured_at, m.score if m.score is not None else m.stars)
            for m in sorted(it.metrics, key=lambda x: x.captured_at)
        ]
        it.final_score = compute_score(
            ScoreInput(
                category=it.category,
                personal_relevance_score=it.personal_relevance_score,
                published_at=it.published_at,
                metric_points=points,
            ),
            half_life_hours=settings.recency_half_life_hours,
            velocity_cap=settings.velocity_cap,
            ref=ref,
        )
    session.commit()


def top_undelivered(session, n: int) -> list[Item]:
    return list(
        session.scalars(
            select(Item)
            .where(Item.delivered.is_(False), Item.final_score > 0)
            .order_by(Item.final_score.desc())
            .limit(n)
        ).all()
    )


def select_diverse(
    items: list[Item],
    top_n: int,
    max_per_category: int,
    min_per_category: int = 0,
) -> list[Item]:
    """從（已按分數排序的）候選挑 top_n：①每類別保底 min_per_category ②按分數補、受
    max_per_category 上限 ③仍不足則放寬上限補滿。最後依分數由高到低輸出。"""
    cap = max_per_category if max_per_category > 0 else top_n
    selected: list[Item] = []
    seen: set[int] = set()
    counts: dict[str, int] = {}

    def take(item: Item) -> None:
        selected.append(item)
        seen.add(id(item))
        counts[item.category or "other"] = counts.get(item.category or "other", 0) + 1

    # ① 保底：每類別至少 min_per_category
    per_cat: dict[str, int] = {}
    for item in items:
        if len(selected) >= top_n:
            break
        cat = item.category or "other"
        if min_per_category and per_cat.get(cat, 0) < min_per_category:
            take(item)
            per_cat[cat] = per_cat.get(cat, 0) + 1

    # ② 受上限補名額；③ 放寬上限補滿
    for relax in (False, True):
        for item in items:
            if len(selected) >= top_n:
                break
            if id(item) in seen:
                continue
            if relax or counts.get(item.category or "other", 0) < cap:
                take(item)

    selected.sort(key=lambda it: it.final_score or 0.0, reverse=True)
    return selected[:top_n]


def _item_view(item: Item) -> dict:
    latest = max(item.metrics, key=lambda m: m.captured_at) if item.metrics else None
    metrics = (
        {"score": latest.score, "comments": latest.comments, "stars": latest.stars}
        if latest
        else {}
    )
    return {
        "id": item.id,
        "title": item.title,
        "title_zh": item.title_zh,
        "summary": item.summary,
        "why_relevant": item.why_relevant,
        "category": item.category,
        "personal_relevance_score": item.personal_relevance_score,
        "source_name": item.source.name if item.source else "",
        "url": item.url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "metrics": metrics,
    }


# --------------------------------------------------------------------------- #
# deliver
# --------------------------------------------------------------------------- #
def _write_markdown_file(markdown: str, run_dt, slug: str = "") -> Path:
    out_dir = DATA_DIR / "digests"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = to_taipei(run_dt).strftime("%Y-%m-%d")
    name = f"{date_str}-{slug}.md" if slug else f"{date_str}.md"
    path = out_dir / name
    path.write_text(markdown, encoding="utf-8")
    return path


JSON_DIR = DATA_DIR / "digests-json"


def _write_json_file(views: list[dict], run_dt, title: str, slug: str = "") -> Path:
    """輸出供官網讀取的 JSON：保留原始 url（非縮短）。同時清掉一年前的舊檔。"""
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    date_str = to_taipei(run_dt).strftime("%Y-%m-%d")
    items = [{k: v for k, v in view.items() if k != "short_url"} for view in views]
    payload = {
        "date": date_str,
        "slug": slug or "all",
        "title": title,
        "generated_at": run_dt.isoformat(),
        "count": len(items),
        "items": items,
    }
    name = f"{date_str}-{slug}.json" if slug else f"{date_str}.json"
    path = JSON_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_old_json(run_dt)
    return path


def _prune_old_json(run_dt, keep_days: int = 365) -> None:
    cutoff = (to_taipei(run_dt) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    for f in JSON_DIR.glob("*.json"):
        if f.name[:10] < cutoff:  # 檔名以 YYYY-MM-DD 開頭，字串比較即日期比較
            f.unlink(missing_ok=True)


def _format_footer(usage: dict | None, cost_usd: float | None, model: str) -> str:
    total = (usage or {}).get("total", 0)
    if not total:
        return "📊 本次執行未使用 LLM（0 tokens）"
    prompt = usage.get("prompt", 0)
    completion = usage.get("completion", 0)
    if cost_usd is not None:
        cost_str = f"≈ US${cost_usd:.4f}"
    else:
        cost_str = "（此模型無價目表，無法估價）"
    return (
        f"📊 本次摘要 LLM 用量：{total:,} tokens"
        f"（輸入 {prompt:,} / 輸出 {completion:,}）{cost_str} · {model}"
    )


async def deliver(
    session, settings: Settings, http: HttpClient, items: list[Item], *,
    dry_run: bool, title: str = "每日新聞精選", slug: str = "",
    usage: dict | None = None, cost_usd: float | None = None, model: str = "",
):
    run_dt = now_utc()
    views = [_item_view(it) for it in items]
    for view in views:
        view["short_url"] = await shorten_url(http, view["url"])

    footer = _format_footer(usage, cost_usd, model)

    markdown = render_markdown(views, run_dt, title).rstrip() + "\n\n---\n\n" + footer + "\n"
    md_path = _write_markdown_file(markdown, run_dt, slug)
    _write_json_file(views, run_dt, title, slug)

    digest = Digest(
        run_date=to_taipei(run_dt).strftime("%Y-%m-%d"),
        item_count=len(items),
        markdown_path=str(md_path),
    )

    if dry_run:
        logger.info("[dry-run] 不推送；%d 則，digest=%s", len(items), md_path)
        session.add(digest)
        session.commit()
        return md_path

    if await _push_telegram(settings, http, views, footer, run_dt, title):
        digest.sent_at = run_dt
    for it in items:
        it.delivered = True
        it.delivered_at = run_dt
    session.add(digest)
    session.commit()
    return md_path


async def _push_telegram(settings, http, views, footer, run_dt, title) -> bool:
    """每則各一條訊息 + 👍/👎 按鈕。回傳是否實際送出。"""
    if not (settings.telegram_bot_token and settings.telegram_chat_id and views):
        logger.warning("缺 Telegram 設定或無內容，略過推送")
        return False
    header, blocks = render_telegram(views, run_dt, title)
    tg = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id, client=http.raw)
    await tg.send(header)
    for view, block in zip(views, blocks):
        await tg.send(block, reply_markup=item_keyboard(view["id"]))
    await tg.send(footer)
    return True


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
def _select_candidates(session, settings: Settings, prof: dict) -> list[Item]:
    """候選過濾：時效窗 + profile 類別 + 個人相關度硬門檻（None 放行不誤殺）。"""
    candidates = top_undelivered(session, 1000)
    fresh_cutoff = now_utc() - timedelta(days=settings.candidate_max_age_days)
    candidates = [c for c in candidates if c.first_seen_at and to_utc(c.first_seen_at) >= fresh_cutoff]
    allowed = prof["categories"]
    if allowed is not None:
        allowed_set = set(allowed)
        candidates = [c for c in candidates if (c.category or "other") in allowed_set]
    return [
        c for c in candidates
        if c.personal_relevance_score is None
        or c.personal_relevance_score >= settings.min_personal_score
    ]


def _persist_stage(session_factory, results, settings: Settings) -> int:
    with session_factory() as session:
        sources = load_enabled_sources(session)  # 重新繫結到本 session
        by_id = {s.id: s for s in sources}
        rebound = [(by_id.get(s.id, s), r) for s, r in results]
        return persist_results(session, rebound, settings)


async def _deliver_stage(session_factory, settings, http, profile, enrich_info, dry_run) -> int:
    with session_factory() as session:
        score_undelivered(session, settings)
        prof = get_profile(profile)
        top = select_diverse(
            _select_candidates(session, settings, prof),
            prof["top_n"] or settings.top_n, settings.max_per_category,
            min_per_category=prof.get("min_per_category", 0),
        )
        if not top:
            logger.info("沒有可推送的項目（profile=%s）", profile)
            return 0
        await deliver(
            session, settings, http, top, dry_run=dry_run, title=prof["label"],
            slug=(profile if profile != "all" else ""),
            usage=enrich_info["usage"], cost_usd=enrich_info["cost"], model=enrich_info["model"],
        )
        return len(top)


async def run(
    settings: Settings | None = None,
    *,
    dry_run: bool = False,
    fetch_only: bool = False,
    profile: str = "all",
) -> dict:
    settings = settings or get_settings()
    engine = make_engine(settings.resolved_database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    registry = build_registry(settings.github_token)
    summary = {"new_items": 0, "enriched": 0, "delivered": 0, "tokens": 0, "cost_usd": None, "feedback": 0}

    with session_factory() as session:
        seed_sources(session, settings.sources_file)
        sources = load_enabled_sources(session)

    async with HttpClient(
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
        rate_limit_per_host=settings.rate_limit_per_host,
    ) as http:
        with session_factory() as session:
            summary["feedback"] = await poll_feedback(session, settings, http)
        results = await fetch_all(http, registry, sources)
        summary["new_items"] = _persist_stage(session_factory, results, settings)
        if fetch_only:
            logger.info("fetch-only 完成：新增 %d 則", summary["new_items"])
            return summary
        with session_factory() as session:
            enrich_info = await enrich_pending(session, settings, http)
        summary["enriched"] = enrich_info["applied"]
        summary["tokens"] = enrich_info["usage"]["total"]
        summary["cost_usd"] = enrich_info["cost"]
        summary["delivered"] = await _deliver_stage(
            session_factory, settings, http, profile, enrich_info, dry_run
        )

    logger.info("完成：%s", summary)
    return summary
