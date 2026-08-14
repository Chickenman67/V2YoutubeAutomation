"""Discovery unit seam (spec #9/#10): parsing, gating, scoring, brief building.

Pure functions in `vibe.discover`; no network in tests. Feed fixtures under
`tests/fixtures/` are read as local files.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from vibe import discover

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_rss_items_with_sources():
    items = discover.parse_rss(_fixture("yahoo-finance-news.rss"), "yahoo-finance-news")
    assert len(items) == 3
    first = items[0]
    assert first.title == "Fed keeps rates high and mortgage costs keep climbing"
    assert first.url == "https://finance.yahoo.com/economy/article/fed-rates-mortgage-costs"
    assert first.publisher == "Yahoo Finance"
    assert first.feed == "yahoo-finance-news"
    assert first.published is not None
    assert "Economy" in first.categories
    assert "interest rates" in first.categories


def test_gate_keeps_on_topic_and_rejects_off_topic():
    items = []
    for name in ("yahoo-finance-news", "cnbc-business-news"):
        items += discover.parse_rss(_fixture(f"{name}.rss"), name)

    kept = discover.gate_items(items, niche="mortgage rates", thesis=None)
    titles = {i.title for i in kept}
    assert "Fed keeps rates high and mortgage costs keep climbing" in titles
    assert "What the Fed's steady rates mean for your mortgage" in titles
    assert "Nvidia's AI chip rally reshapes the semiconductor race" not in titles
    assert "Grimaldi acquires Pacific Northwest freight broker in logistics deal" not in titles
    assert all(i.feed in ("yahoo-finance-news", "cnbc-business-news") for i in kept)


def test_gate_uses_thesis_keywords():
    items = discover.parse_rss(_fixture("yahoo-finance-news.rss"), "yahoo-finance-news")
    kept = discover.gate_items(items, niche=None, thesis="the Fed hiked rates far too slowly")
    assert kept
    assert all("fed" in i.title.lower() or "rate" in i.title.lower() for i in kept)


def test_score_item_currency_by_recency():
    from datetime import datetime

    items = discover.parse_rss(_fixture("cnbc-business-news.rss"), "cnbc-business-news")
    item = next(i for i in items if "mortgage" in i.title)
    now = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)  # item published 11:00Z same day
    score = discover.score_item(item, now=now)
    assert score.currency == 3  # ~5h old
    assert score.relatability == 3  # fed / rates / mortgage all retail-resonant, capped
    assert score.explainability == 2  # "what" + "mean" verbs, capped at topic words


def test_score_item_discounts_obscure_b2b_and_stale():
    from datetime import datetime

    items = discover.parse_rss(_fixture("yahoo-finance-news.rss"), "yahoo-finance-news")
    b2b = next(i for i in items if "Grimaldi" in i.title)
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)  # published 2026-08-11: ~52h stale
    score = discover.score_item(b2b, now=now)
    assert score.relatability == 0
    assert score.currency == 1


def test_select_topic_prefers_highest_total_then_most_recent():
    from datetime import datetime

    score = lambda total: discover.Score(
        currency=1, relatability=1, explainability=1, total=total
    )
    older = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
    mk = lambda title, pub, total: (
        discover.FeedItem(title, f"https://x/{title}", "Pub", pub, (), "yahoo-finance-news"),
        score(total),
    )

    # total wins over a more recent but lower-scoring rival
    lower_total_recent = mk("lower total", newer, 4)
    higher_total_older = mk("higher total", older, 6)
    picked, best = discover.select_topic([lower_total_recent, higher_total_older])
    assert picked.title == "higher total"
    assert best.total == 6

    # same total -> most recent wins
    a = mk("tie old", older, 5)
    b = mk("tie new", newer, 5)
    picked, _ = discover.select_topic([a, b])
    assert picked.title == "tie new"


def test_select_topic_empty_returns_none():
    assert discover.select_topic([]) is None


def test_select_topic_breaks_ties_by_feed_registry_order():
    from datetime import UTC, datetime

    tie = discover.Score(currency=1, relatability=1, explainability=1, total=3)
    published = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
    later_feed = discover.FeedItem(
        "cnbc rate story", "https://cnbc/x", "CNBC", published, (), "cnbc-business-news"
    )
    earlier_feed = discover.FeedItem(
        "yahoo rate story", "https://yahoo/x", "Yahoo Finance", published, (), "yahoo-finance-news"
    )
    picked, _ = discover.select_topic([(later_feed, tie), (earlier_feed, tie)])
    assert picked.feed == "yahoo-finance-news"  # earlier registry entry = stronger source reputation


def _fed_topic():
    return discover.parse_rss(_fixture("yahoo-finance-news.rss"), "yahoo-finance-news")[0]


def test_build_segments_yields_4_to_6_ordered_self_contained_segments():
    topic = _fed_topic()
    segments = discover.build_segments(topic)
    assert 4 <= len(segments) <= 6
    assert [s["index"] for s in segments] == list(range(1, len(segments) + 1))
    for seg in segments:
        assert seg["title"].strip()
        assert seg["hook"].strip()
        assert seg["key_points"] and all(isinstance(kp, str) and kp for kp in seg["key_points"])


def test_build_segments_seed_key_points_from_the_topic_title():
    import re

    topic = _fed_topic()
    segments = discover.build_segments(topic)
    seeds = " ".join(" ".join(s["key_points"]) for s in segments).lower()
    token = next(w for w in re.split(r"[^a-z0-9]+", topic.title.lower()) if w)
    assert token in seeds  # the beats trace back to the source title (provenance)


def test_build_brief_follows_topic_brief_schema():
    from datetime import UTC, datetime

    topic = _fed_topic()
    now = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)
    segments = discover.build_segments(topic)
    brief = discover.build_topic_brief(topic, segments, niche="mortgage rates", thesis=None, now=now)
    assert set(brief) == {"topic_brief"}
    tb = brief["topic_brief"]
    assert tb["id"].startswith("tb-")
    assert tb["generated_at"].endswith("Z")
    assert tb["status"] == "ready"
    assert tb["input"]["niche"] == "mortgage rates"
    assert tb["input"]["thesis"] is None
    assert tb["title"] == topic.title
    assert len(tb["segments"]) == len(segments)

    src = tb["sources"][0]
    assert src["url"] == topic.url
    assert src["publisher"] == topic.publisher
    assert src["feed"] == topic.feed
    assert src["published"].endswith("Z")