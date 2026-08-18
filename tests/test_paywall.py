from types import SimpleNamespace

from news_aggregator.core.paywall import article_paywalled, is_metered, is_paywalled


def test_is_paywalled_domains_and_subdomains():
    assert is_paywalled("https://www.barrons.com/articles/tesla-stock") is True
    assert is_paywalled("https://a.wsj.com/x") is True
    assert is_paywalled("https://example.com/x") is False


def test_is_paywalled_gift_link_exempt():
    assert is_paywalled("https://www.bloomberg.com/news/x?accessToken=gift123") is False


def test_is_paywalled_lookalike_and_garbage_pass():
    assert is_paywalled("https://evilbloomberg.com/x") is False
    assert is_paywalled("https://bloomberg.com.evil.io/x") is False
    assert is_paywalled(None) is False
    assert is_paywalled("") is False
    assert is_paywalled("http://[::") is False  # 畸形 URL 不 raise、放行


def test_is_metered():
    assert is_metered("https://www.theverge.com/tech/979231/apple") is True
    assert is_metered("https://example.com/x") is False


class _FakeClient:
    def __init__(self, text="", exc=None):
        self._text, self._exc = text, exc

    async def get(self, url, **kw):
        if self._exc:
            raise self._exc
        return SimpleNamespace(text=self._text)


async def test_article_paywalled_marker_variants():
    paid = '<script type="application/ld+json">{"isAccessibleForFree": false}</script>'
    assert await article_paywalled(_FakeClient(paid), "https://x.com/a") is True
    assert await article_paywalled(_FakeClient('{"isAccessibleForFree":false}'), "https://x.com/a") is True
    assert await article_paywalled(_FakeClient('{"isAccessibleForFree": "False"}'), "https://x.com/a") is True


async def test_article_paywalled_free_or_error_passes():
    assert await article_paywalled(_FakeClient("<html>free article</html>"), "https://x.com/a") is False
    assert await article_paywalled(_FakeClient('{"isAccessibleForFree": true}'), "https://x.com/a") is False
    assert await article_paywalled(_FakeClient(exc=RuntimeError("boom")), "https://x.com/a") is False
