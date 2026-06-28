import json

from news_aggregator.preferences import (
    Preferences,
    Ticker,
    _gnews_url,
    expand_sources,
    load_preferences,
    watchlist_block,
)

_FULL = {
    "people": [
        {"name": "Elon Musk", "x_handle": "elonmusk"},
        {"name": "No Handle"},
    ],
    "tickers": [{"symbol": "TSLA", "name": "Tesla"}],
    "topics": ["AI", "電動車"],
    "platforms": {"x": True, "hackernews": True, "github": True, "reddit": True, "trending": True},
}


def _write(tmp_path, data):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _names(entries):
    return {e["name"] for e in entries}


def test_load_full(tmp_path):
    prefs = load_preferences(_write(tmp_path, _FULL))
    assert [p.name for p in prefs.people] == ["Elon Musk", "No Handle"]
    assert prefs.tickers[0].symbol == "TSLA"
    assert prefs.topics == ["AI", "電動車"]
    assert prefs.on("reddit") is True


def test_load_missing_file_returns_empty(tmp_path):
    prefs = load_preferences(tmp_path / "nope.json")
    assert prefs.people == [] and prefs.topics == []
    assert prefs.on("x") is True  # 缺項預設啟用


def test_load_bad_json_returns_empty(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_preferences(p).people == []


def test_expand_full_produces_all_source_kinds(tmp_path):
    prefs = load_preferences(_write(tmp_path, _FULL))
    names = _names(expand_sources(prefs))
    assert "x-elonmusk" in names          # 有 handle 才展開
    assert "tickers-news" in names
    assert "topic-AI" in names and "reddit-AI" in names
    assert "topic-電動車" in names
    assert {"hn-trending", "gh-trending", "reddit-popular"} <= names


def test_person_without_handle_not_fetched_but_in_watchlist(tmp_path):
    prefs = load_preferences(_write(tmp_path, _FULL))
    assert "x-No Handle" not in _names(expand_sources(prefs))
    assert "No Handle" in watchlist_block(prefs)


def test_platform_x_off_drops_x_sources():
    prefs = Preferences(
        people=[],
        topics=[],
        platforms={"x": False, "trending": False},
    )
    assert _names(expand_sources(prefs)) == set()


def test_reddit_off_drops_reddit_sources():
    prefs = Preferences(topics=["AI"], platforms={"reddit": False, "trending": False})
    names = _names(expand_sources(prefs))
    assert "topic-AI" in names
    assert "reddit-AI" not in names and "reddit-popular" not in names


def test_trending_off_drops_all_trending():
    prefs = Preferences(platforms={"trending": False})
    assert _names(expand_sources(prefs)) == set()


def test_trending_respects_platform_gate():
    prefs = Preferences(platforms={"trending": True, "hackernews": False})
    names = _names(expand_sources(prefs))
    assert "hn-trending" not in names
    assert {"gh-trending", "reddit-popular"} <= names


def test_ticker_source_merges_into_one_query():
    prefs = Preferences(
        tickers=[Ticker("TSLA", "Tesla"), Ticker("AAPL", "Apple")],
        platforms={"trending": False},
    )
    entries = expand_sources(prefs)
    assert len(entries) == 1
    url = entries[0]["config"]["url"]
    assert "TSLA" in url and "Tesla" in url and "AAPL" in url


def test_gnews_url_switches_language():
    assert "hl=en-US" in _gnews_url("AI")
    assert "hl=zh-TW" in _gnews_url("電動車")


def test_watchlist_block_empty_when_no_prefs():
    assert watchlist_block(Preferences()) == ""


def test_watchlist_block_lists_people_tickers_topics(tmp_path):
    block = watchlist_block(load_preferences(_write(tmp_path, _FULL)))
    assert "Elon Musk" in block and "TSLA" in block and "AI" in block
