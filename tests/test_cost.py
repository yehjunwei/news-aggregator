from news_aggregator.enrich.llm import estimate_cost
from news_aggregator.pipeline import _format_footer


def test_estimate_cost_flash_lite():
    usage = {"prompt": 1_000_000, "completion": 1_000_000, "total": 2_000_000}
    # gemini-flash-lite: (0.10, 0.40) -> 0.10 + 0.40
    assert abs(estimate_cost("gemini-flash-lite-latest", usage) - 0.50) < 1e-9


def test_estimate_cost_longest_match_wins():
    usage = {"prompt": 1_000_000, "completion": 0, "total": 1_000_000}
    # 'gemini-flash-lite' (0.10) 應優先於 'gemini-flash' (0.30)
    assert abs(estimate_cost("gemini-flash-lite-latest", usage) - 0.10) < 1e-9


def test_estimate_cost_unknown_model_returns_none():
    assert estimate_cost("some-unknown-model", {"prompt": 100, "completion": 100}) is None


def test_format_footer_with_usage():
    footer = _format_footer(
        {"prompt": 12000, "completion": 3000, "total": 15000}, 0.0021, "gemini-flash-lite-latest"
    )
    assert "15,000 tokens" in footer
    assert "US$0.0021" in footer
    assert "gemini-flash-lite-latest" in footer


def test_format_footer_no_llm():
    assert "未使用 LLM" in _format_footer({"total": 0}, None, "")


def test_format_footer_unknown_price():
    footer = _format_footer({"prompt": 10, "completion": 10, "total": 20}, None, "mystery")
    assert "無法估價" in footer
