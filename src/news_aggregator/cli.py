"""命令列進入點。

用法：
  python -m news_aggregator.cli run        # 完整流程並推送
  python -m news_aggregator.cli dry-run    # 完整流程但不推送（仍寫 digest 檔）
  python -m news_aggregator.cli fetch       # 只抓取 + 去重，不 enrich/推送
  python -m news_aggregator.cli init-db     # 僅建表
  python -m news_aggregator.cli feedback fb:<item_id>:up|down   # 記錄單筆 👍/👎 回饋
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from . import pipeline
from .config import get_settings
from .db.session import init_db, make_engine


def _setup_logging(verbose: bool) -> None:
    # 預設只顯示 WARNING 以上，避免 cron announce 把整包 INFO log 推到 Telegram。
    # -v 時才開 DEBUG（含 httpx 連線細節）。
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        # 非 verbose：成功時 stderr 應安靜，cron announce 只會看到 stdout 的單行結果。
        # 真正的問題（來源失敗、缺設定）以 WARNING/ERROR 仍會顯示。
        for noisy in ("httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _summary_line(result: dict) -> str:
    line = (
        f"✅ 新聞精選完成：新增 {result['new_items']} 則、"
        f"摘要 {result['enriched']} 則、推送 {result['delivered']} 則"
    )
    if result.get("feedback"):
        line += f"、回饋 {result['feedback']} 筆"
    if result.get("tokens"):
        line += f"、{result['tokens']:,} tokens"
        if result.get("cost_usd") is not None:
            line += f" ≈ US${result['cost_usd']:.4f}"
    return line


def main() -> None:
    parser = argparse.ArgumentParser(prog="news-aggregator")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "dry-run", "fetch", "init-db", "feedback"],
    )
    parser.add_argument(
        "data",
        nargs="?",
        help="feedback 專用：按鈕 callback data，如 fb:123:up",
    )
    parser.add_argument(
        "--profile",
        default="all",
        choices=["all", "morning", "evening"],
        help="推送時段 profile（morning=專業資訊 / evening=輕鬆閱讀）",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    settings = get_settings()

    if args.command == "init-db":
        init_db(make_engine(settings.resolved_database_url))
        print("DB 已初始化：", settings.resolved_database_url)
        return

    if args.command == "feedback":
        # gateway 會把按鈕 callback 當文字訊息轉給 agent，由 agent 呼叫這裡補記錄
        from .db.session import make_session_factory
        from .feedback import record_feedback

        with make_session_factory(make_engine(settings.resolved_database_url))() as session:
            try:
                print(record_feedback(session, args.data or ""))
            except (ValueError, LookupError) as exc:
                raise SystemExit(f"❌ {exc}")
        return

    result = asyncio.run(
        pipeline.run(
            settings,
            dry_run=(args.command == "dry-run"),
            fetch_only=(args.command == "fetch"),
            profile=args.profile,
        )
    )
    print(_summary_line(result))


if __name__ == "__main__":
    main()
