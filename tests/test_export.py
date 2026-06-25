import json
from datetime import datetime, timezone

from news_aggregator import pipeline


def test_write_json_keeps_original_url_and_prunes(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "JSON_DIR", tmp_path)
    run_dt = datetime(2026, 6, 21, 22, 0, tzinfo=timezone.utc)  # 台北 2026-06-22
    old = tmp_path / "2024-01-01-morning.json"  # 一年多前
    old.write_text("{}")

    views = [{
        "id": 1, "title": "T", "url": "https://example.com/full/path",
        "metrics": {},
    }]
    path = pipeline._write_json_file(views, run_dt, "晨間精選", "morning")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "2026-06-22-morning.json"
    assert data["slug"] == "morning" and data["count"] == 1
    # 保留原始 url，不輸出縮短網址
    assert data["items"][0]["url"] == "https://example.com/full/path"
    # 一年前的舊檔被清掉
    assert not old.exists()
