"""去重工具：canonical URL 正規化、content hash、標題相似度。"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

# 追蹤參數：開頭前綴與完整鍵名
_TRACKING_PREFIXES = ("utm_", "ref_")
_TRACKING_KEYS = {"ref", "fbclid", "gclid", "spm", "source", "cmpid", "mc_cid", "mc_eid"}

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def canonical_url(url: str) -> str:
    """正規化 URL：小寫 scheme/host、去 www、去追蹤參數與 fragment、去結尾斜線。"""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    # http/https 視為同一來源（避免同篇文章因協定不同被當成兩則）
    if scheme in ("http", "https"):
        scheme = "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_title(title: str) -> str:
    t = (title or "").lower().strip()
    t = _PUNCT_RE.sub("", t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


def content_hash(title: str, url: str) -> str:
    base = f"{normalize_title(title)}|{canonical_url(url)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def title_similarity(a: str, b: str) -> float:
    """0~100 的 token_sort_ratio 相似度。"""
    return fuzz.token_sort_ratio(normalize_title(a), normalize_title(b))
