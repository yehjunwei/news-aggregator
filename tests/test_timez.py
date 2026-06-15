from datetime import datetime, timezone

from news_aggregator.core.timez import format_taipei, parse_epoch, to_taipei, to_utc


def test_naive_treated_as_utc():
    dt = datetime(2026, 6, 15, 0, 0, 0)
    assert to_utc(dt).tzinfo == timezone.utc


def test_to_taipei_offset_plus_8():
    dt = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
    tp = to_taipei(dt)
    assert tp.hour == 8
    assert tp.utcoffset().total_seconds() == 8 * 3600


def test_format_taipei():
    dt = datetime(2026, 6, 14, 22, 30, tzinfo=timezone.utc)
    assert format_taipei(dt) == "2026-06-15 06:30"


def test_parse_epoch():
    dt = parse_epoch(0)
    assert dt.year == 1970 and dt.tzinfo == timezone.utc
