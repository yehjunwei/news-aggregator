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
from .core.timez import now_utc, to_taipei
from .db.models import Digest, Item, ItemMetric, Source
from .db.session import init_db, make_engine, make_session_factory
from .delivery.digest import render_markdown, render_telegram
from .delivery.telegram import TelegramClient, split_messages
from .enrich.classify import classify_items
from .enrich.llm import build_provider, estimate_cost
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
            logger.exception("來源 %s 抓取失敗：%s", source.name, exc)
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


def persist_results(session, results, settings: Settings) -> int:
    cutoff = now_utc() - timedelta(days=settings.dedup_lookback_days)
    recent = list(session.scalars(select(Item).where(Item.first_seen_at >= cutoff)).all())
    by_hash = {i.content_hash: i for i in recent}
    by_canon = {i.canonical_url: i for i in recent}
    id_map = {i.id: i for i in recent}
    title_index = {i.id: normalize_title(i.title) for i in recent}

    new_count = 0
    for source, result in results:
        if result is None:
            continue
        if result.not_modified:
            source.last_fetched_at = now_utc()
            continue

        for raw in result.items:
            canon = canonical_url(raw.url)
            chash = content_hash(raw.title, raw.url)

            existing = session.scalars(
                select(Item).where(
                    Item.source_id == source.id, Item.external_id == raw.external_id
                )
            ).first()
            if existing is None:
                existing = by_hash.get(chash) or by_canon.get(canon)
            if existing is None and title_index:
                match = process.extractOne(
                    normalize_title(raw.title),
                    title_index,
                    scorer=fuzz.token_sort_ratio,
                    score_cutoff=settings.dedup_title_threshold,
                )
                if match:
                    existing = id_map.get(match[2])

            if existing is not None:
                _add_metric(session, existing, raw)
                continue

            item = Item(
                source_id=source.id,
                external_id=raw.external_id,
                canonical_url=canon,
                url=raw.url,
                title=raw.title,
                author=raw.author,
                content_hash=chash,
                published_at=raw.published_at,
            )
            session.add(item)
            session.flush()
            _add_metric(session, item, raw)
            new_count += 1

            by_hash[chash] = item
            by_canon[canon] = item
            id_map[item.id] = item
            title_index[item.id] = normalize_title(item.title)

        source.etag = result.etag or source.etag
        source.last_modified = result.last_modified or source.last_modified
        source.last_fetched_at = now_utc()

    session.commit()
    return new_count


# --------------------------------------------------------------------------- #
# enrich
# --------------------------------------------------------------------------- #
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
        {
            "id": it.id,
            "title": it.title,
            "url": it.url,
            "source": it.source.name if it.source else "",
        }
        for it in pending
    ]
    provider = build_provider(settings, client=http.raw)
    enrichments = await classify_items(
        provider,
        inputs,
        summary_sentences=settings.llm_summary_sentences,
        batch_size=settings.llm_batch_size,
    )

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


def select_diverse(items: list[Item], top_n: int, max_per_category: int) -> list[Item]:
    """從（已按分數排序的）候選中挑 top_n，每類別最多 max_per_category 則。

    若受上限限制仍填不滿 top_n，再用 overflow（仍按分數）補齊。
    """
    if max_per_category <= 0:
        return items[:top_n]
    selected: list[Item] = []
    overflow: list[Item] = []
    counts: dict[str, int] = {}
    for item in items:
        cat = item.category or "other"
        if counts.get(cat, 0) < max_per_category:
            selected.append(item)
            counts[cat] = counts.get(cat, 0) + 1
            if len(selected) >= top_n:
                return selected
        else:
            overflow.append(item)
    for item in overflow:
        if len(selected) >= top_n:
            break
        selected.append(item)
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
        "personal_relevance_score": item.personal_relevance_score,
        "source_name": item.source.name if item.source else "",
        "url": item.url,
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
    session,
    settings: Settings,
    http: HttpClient,
    items: list[Item],
    *,
    dry_run: bool,
    title: str = "每日新聞精選",
    slug: str = "",
    usage: dict | None = None,
    cost_usd: float | None = None,
    model: str = "",
):
    run_dt = now_utc()
    views = [_item_view(it) for it in items]
    for view in views:
        view["short_url"] = await shorten_url(http, view["url"])

    footer = _format_footer(usage, cost_usd, model)

    markdown = render_markdown(views, run_dt, title).rstrip() + "\n\n---\n\n" + footer + "\n"
    md_path = _write_markdown_file(markdown, run_dt, slug)

    header, blocks = render_telegram(views, run_dt, title)
    messages = split_messages(blocks + [footer], header=header)

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

    if settings.telegram_bot_token and settings.telegram_chat_id and messages:
        tg = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id, client=http.raw)
        await tg.send_all(messages)
        digest.sent_at = run_dt
    else:
        logger.warning("缺 Telegram 設定或無內容，略過推送")

    for it in items:
        it.delivered = True
        it.delivered_at = run_dt
    session.add(digest)
    session.commit()
    return md_path


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
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
    summary = {"new_items": 0, "enriched": 0, "delivered": 0, "tokens": 0, "cost_usd": None}
    enrich_info = {"applied": 0, "usage": {"prompt": 0, "completion": 0, "total": 0}, "model": "", "cost": None}

    with session_factory() as session:
        seed_sources(session, settings.sources_file)
        sources = load_enabled_sources(session)

    async with HttpClient(
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
        rate_limit_per_host=settings.rate_limit_per_host,
    ) as http:
        results = await fetch_all(http, registry, sources)

        with session_factory() as session:
            # 重新繫結 source 物件到本 session
            sources = load_enabled_sources(session)
            by_id = {s.id: s for s in sources}
            rebound = [(by_id.get(s.id, s), r) for s, r in results]
            summary["new_items"] = persist_results(session, rebound, settings)

        if fetch_only:
            logger.info("fetch-only 完成：新增 %d 則", summary["new_items"])
            return summary

        with session_factory() as session:
            enrich_info = await enrich_pending(session, settings, http)
            summary["enriched"] = enrich_info["applied"]
            summary["tokens"] = enrich_info["usage"]["total"]
            summary["cost_usd"] = enrich_info["cost"]

        with session_factory() as session:
            score_undelivered(session, settings)
            prof = get_profile(profile)
            top_n = prof["top_n"] or settings.top_n
            allowed = prof["categories"]
            # 取大候選池（個人規模一天數百則，全撈即可），再依 profile 過濾類別
            candidates = top_undelivered(session, 1000)
            if allowed is not None:
                allowed_set = set(allowed)
                candidates = [c for c in candidates if (c.category or "other") in allowed_set]
            top = select_diverse(candidates, top_n, settings.max_per_category)
            summary["delivered"] = len(top)
            if top:
                slug = profile if profile != "all" else ""
                await deliver(
                    session, settings, http, top,
                    dry_run=dry_run, title=prof["label"], slug=slug,
                    usage=enrich_info["usage"], cost_usd=enrich_info["cost"],
                    model=enrich_info["model"],
                )
            else:
                logger.info("沒有可推送的項目（profile=%s）", profile)

    logger.info("完成：%s", summary)
    return summary
