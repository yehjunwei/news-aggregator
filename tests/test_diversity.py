from dataclasses import dataclass

from news_aggregator.pipeline import select_diverse


@dataclass
class _It:
    category: str
    final_score: float


def test_cap_limits_dominant_category():
    # 類別充足（兩類各 >=4）時，高分類別被上限壓到 4，多元類別才擠得進來
    items = [_It("ai_agents", 1.0 - i * 0.01) for i in range(10)]
    items += [_It("automotive", 0.5 - i * 0.01) for i in range(6)]
    out = select_diverse(items, top_n=8, max_per_category=4)
    cats = [i.category for i in out]
    assert cats.count("ai_agents") == 4
    assert cats.count("automotive") == 4
    assert len(out) == 8


def test_fills_with_overflow_when_not_enough_diversity():
    items = [_It("ai_agents", 1.0 - i * 0.01) for i in range(10)]
    out = select_diverse(items, top_n=6, max_per_category=4)
    # 只有一個類別 → 上限 4 填不滿，用 overflow 補到 6
    assert len(out) == 6
    assert all(i.category == "ai_agents" for i in out)


def test_no_cap_when_zero():
    items = [_It("ai_agents", 1.0 - i * 0.01) for i in range(10)]
    out = select_diverse(items, top_n=5, max_per_category=0)
    assert len(out) == 5


def test_preserves_score_order_within_selection():
    items = [_It("a", 0.9), _It("a", 0.8), _It("b", 0.7), _It("a", 0.6)]
    out = select_diverse(items, top_n=4, max_per_category=2)
    assert [i.final_score for i in out] == [0.9, 0.8, 0.7, 0.6]


def test_min_per_category_guarantees_low_score_topics():
    # 高分類別 8 則、低分冷門類別 3 則；保底 2 則確保冷門類別不被擠掉
    items = [_It("hot", 1.0 - i * 0.01) for i in range(8)]
    items += [_It("cold", 0.001 - i * 0.0001) for i in range(3)]
    out = select_diverse(items, top_n=6, max_per_category=4, min_per_category=2)
    cats = [i.category for i in out]
    assert cats.count("cold") >= 2  # 即使分數極低也保底 2 則
    assert cats.count("hot") == 4    # 其餘名額按分數補、受上限 4 限制
    assert len(out) == 6
    # 仍依分數由高到低排序（冷門落在後段）
    assert out == sorted(out, key=lambda i: i.final_score, reverse=True)


def test_min_capped_by_top_n():
    # 主題數 × 保底 超過 top_n 時，不超發、以分數高的類別優先保底
    items = [_It(c, s) for c, s in [("a", 0.9), ("a", 0.8), ("b", 0.7), ("b", 0.6), ("c", 0.5)]]
    out = select_diverse(items, top_n=4, max_per_category=4, min_per_category=2)
    assert len(out) == 4
