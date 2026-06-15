from news_aggregator.core.dedup import (
    canonical_url,
    content_hash,
    normalize_title,
    title_similarity,
)


def test_canonical_strips_tracking_and_fragment():
    a = canonical_url("https://www.Example.com/Post/?utm_source=tw&id=3#section")
    b = canonical_url("http://example.com/Post?id=3")
    assert a == b


def test_canonical_trailing_slash_and_host_lowercase():
    assert canonical_url("https://Example.com/a/b/") == canonical_url("https://example.com/a/b")


def test_normalize_title_removes_punctuation_and_case():
    assert normalize_title("Hello, World!  Foo") == "hello world foo"


def test_content_hash_same_for_equivalent_urls():
    h1 = content_hash("Cool Tool", "https://example.com/x?utm_medium=rss")
    h2 = content_hash("cool   tool", "https://www.example.com/x")
    assert h1 == h2


def test_content_hash_differs_for_different_titles():
    assert content_hash("A", "https://e.com/x") != content_hash("B", "https://e.com/x")


def test_title_similarity_high_for_reordered_tokens():
    assert title_similarity("OpenAI releases new model", "new model releases OpenAI") >= 88


def test_title_similarity_low_for_unrelated():
    assert title_similarity("NBA finals recap", "Rust async runtime guide") < 50
