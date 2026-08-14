"""Topic discovery: tier-1 RSS feeds -> a gated, scored Topic Brief (ticket #10).

Pipeline stages are pure functions (no network) so the CLI seam is testable offline
(spec #9): parse -> gate -> score -> select -> build Topic Brief. The only network touch
is the injectable default fetcher, replaced by local files in tests.
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

_ATOM = {"a": "http://www.w3.org/2005/Atom"}

# Tier-1 feeds, verified live in docs/research/topic-discovery.md. Registry order is
# the deterministic tie-break when two candidates score equally.
FEEDS: dict[str, dict[str, str]] = {
    "yahoo-finance-news": {
        "url": "https://finance.yahoo.com/news/rssindex",
        "publisher": "Yahoo Finance",
    },
    "cnbc-business-news": {
        "url": "https://www.cnbc.com/id/10001147/device/rss/rss.html",
        "publisher": "CNBC",
    },
    "marketwatch-top-stories": {
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "publisher": "MarketWatch",
    },
}


@dataclass(frozen=True)
class FeedItem:
    title: str
    url: str
    publisher: str
    published: datetime | None
    categories: tuple[str, ...]
    feed: str

    @property
    def searchable_text(self) -> str:
        """Lowercased title/url/categories: the single haystack for gate and rubric."""
        return f"{self.title} {self.url} {' '.join(self.categories)}".lower()


@dataclass(frozen=True)
class Score:
    currency: int = 0
    relatability: int = 0
    explainability: int = 0
    total: int = 0


# JSON payload shapes; values are heterogeneous (int index, str title, list key_points).
Segment = dict[str, object]
TopicBrief = dict[str, object]


def _prefix_match(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}[a-z0-9]*", text) is not None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    found = el.find(name)
    if found is not None:
        return found
    return el.find(f"a:{name}", _ATOM)


def _text(el: ET.Element, name: str) -> str:
    ch = _child(el, name)
    if ch is None or not ch.text:
        return ""
    return ch.text.strip()


def _link(el: ET.Element) -> str:
    ch = _child(el, "link")
    if ch is not None:
        href = ch.get("href") or ch.get("url")
        if href:
            return href.strip()
    return (_text(el, "link") or _text(el, "url")).strip()


def _parse_date(txt: str) -> datetime | None:
    try:
        return parsedate_to_datetime(txt)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _published(el: ET.Element) -> datetime | None:
    for name in ("pubDate", "published", "updated"):
        ch = _child(el, name)
        if ch is not None and ch.text:
            dt = _parse_date(ch.text.strip())
            if dt is not None:
                return dt
    return None


def _source(el: ET.Element) -> str:
    src = _child(el, "source")
    if src is None:
        return ""
    if src.text and src.text.strip():
        return src.text.strip()
    title = _child(src, "title")
    if title is not None and title.text:
        return title.text.strip()
    return ""


def _categories(el: ET.Element) -> tuple[str, ...]:
    cats: list[str] = []
    for c in (*el.findall("category"), *el.findall("a:category", _ATOM)):
        term = c.get("term") or c.get("label") or (c.text or "").strip()
        if term:
            cats.append(term)
    return tuple(cats)


# Function words pruned from the user's niche/thesis before gating.
_STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "at", "with", "from", "by", "too", "how", "why", "what", "is", "was", "are", "were", "be", "it", "its", "as", "so", "no", "not", "far", "while", "when", "than", "that", "this", "has", "have", "had", "will", "would", "can", "could", "should", "do", "does", "did", "into", "over", "under", "very", "much", "more", "most"]
)


def _terms(text: str) -> frozenset[str]:
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return frozenset(t for t in tokens if t not in _STOPWORDS and len(t) >= 2)


def matches_terms(item: FeedItem, terms: frozenset[str]) -> bool:
    """True if any term is a prefix of a word in the item's title/url/categories."""
    if not terms:
        return True
    hay = item.searchable_text
    return any(_prefix_match(hay, term) for term in terms)


def gate_items(
    items: list[FeedItem], *, niche: str | None = None, thesis: str | None = None
) -> list[FeedItem]:
    """Keep items whose title/url/categories match the user's niche or thesis.

    The hard gate (research §1 pick rule): off-topic candidates never reach the Topic
    Brief, even if they score well. With no usable terms the gate passes everything.
    """
    terms = _terms(niche) if niche else _terms(thesis) if thesis else frozenset()
    if not terms:
        return list(items)
    return [i for i in items if matches_terms(i, terms)]


# Retail-audience resonance and "bloomable" (visually drawable) concept proxies for the
# 0-3 scoring rubric (research §1). Heuristic pre-filters; the script stage does the
# real reasoning. Keep them as prefix-words so rate matches "rates" but "a" never does.
RELATABLE_TERMS = frozenset(
    ["fed", "reserve", "rate", "mortgage", "inflation", "recession", "housing", "rent", "gas", "oil", "stock", "bitcoin", "crypto", "nvidia", "apple", "tesla", "bank", "credit", "debt", "job", "wage", "savings", "retirement", "interest", "economy", "price", "earnings", "ai", "chip", "tariff", "deficit", "unemployment", "household", "pension", "stimulus", "layoff"]
)

EXPLAINABLE_TERMS = frozenset(
    ["what", "why", "how", "mean", "meaning", "behind", "explained", "explain", "rally", "crash", "boom", "bust", "surge", "tumble", "hike", "cut", "comeback", "win", "lose", "deal", "merger", "cost", "expensive", "cheap", "record", "race", "shift", "shake", "turnaround", "paying", "shape", "reshapes", "drives", "threatens", "squeeze"]
)


def _bucket(count: int) -> int:
    return min(3, count)


def _distinct_matches(hay: str, terms: frozenset[str]) -> int:
    return sum(1 for t in terms if _prefix_match(hay, t))


def score_item(item: FeedItem, *, now: datetime) -> Score:
    """Rubric score (currency, relatability, explainability), 0-3 each (research §1)."""
    hay = item.searchable_text
    if item.published is None:
        currency = 0
    else:
        hours = (now - item.published).total_seconds() / 3600.0
        currency = 3 if hours <= 24 else 2 if hours <= 48 else 1 if hours <= 72 else 0
    relatability = _bucket(_distinct_matches(hay, RELATABLE_TERMS))
    explainability = _bucket(_distinct_matches(hay, EXPLAINABLE_TERMS))
    total = currency + relatability + explainability
    return Score(
        currency=currency, relatability=relatability, explainability=explainability, total=total
    )


def select_topic(scored: list[tuple[FeedItem, Score]]) -> tuple[FeedItem, Score] | None:
    """Best topic by (total desc, most-recent desc, feed-order asc). None when empty."""
    if not scored:
        return None

    def rank(pair: tuple[FeedItem, Score]) -> tuple[int, float, int]:
        item, sc = pair
        epoch = item.published.timestamp() if item.published else float("-inf")
        feed_index = list(FEEDS).index(item.feed) if item.feed in FEEDS else len(FEEDS)
        return (sc.total, epoch, -feed_index)

    return max(scored, key=rank)


# Ordered narrative skeleton (research §3 Problem -> Mechanism -> Players -> Climax ->
# Outlook). Fixed at 5 segments, inside the 4-6 band. Hooks are seed templates the script
# stage polishes; key_points carry the actual, source-traceable content.
NARRATIVE_ROLES: tuple[dict[str, str], ...] = (
    {"title": "The Context", "hook": "Every story has a before."},
    {"title": "The Mechanism", "hook": "Here's how the pieces actually connect."},
    {"title": "Why It Matters", "hook": "This is where the headline turns into real money."},
    {"title": "Who It Hits", "hook": "The impact lands unevenly."},
    {"title": "Where It Goes Next", "hook": "Watch one signal to see where this is heading."},
)


def _title_phrases(title: str) -> list[str]:
    phrases = [p.strip() for p in re.split(r"[:—–,;]", title) if p.strip()]
    return phrases or [title]


def _title_keywords(title: str) -> list[str]:
    return list(
        dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 3 and w not in _STOPWORDS)
    )


def build_segments(topic: FeedItem) -> list[Segment]:
    """Deterministic 4-6 segment outline; every beat traces to the topic's title."""
    phrases = _title_phrases(topic.title)
    keywords = _title_keywords(topic.title)
    segments: list[Segment] = []
    for position, role in enumerate(NARRATIVE_ROLES, start=1):
        seed = phrases[(position - 1) % len(phrases)]
        seen = {seed.lower()}
        key_points = [seed]
        cursor = position - 1
        while len(key_points) < 3 and keywords:
            kw = keywords[cursor % len(keywords)]
            if kw.lower() not in seen:
                key_points.append(kw)
                seen.add(kw.lower())
            cursor += 1
        segments.append(
            {"index": position, "title": role["title"], "hook": role["hook"], "key_points": key_points}
        )
    return segments


def _to_zulu(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_topic_brief(
    topic: FeedItem,
    segments: list[Segment],
    *,
    niche: str | None,
    thesis: str | None,
    now: datetime,
) -> TopicBrief:
    """Assemble the Topic Brief (research §4). `now` must be timezone-aware."""
    return {
        "topic_brief": {
            "id": f"tb-{now:%Y-%m-%d-%H%M}",
            "generated_at": _to_zulu(now),
            "status": "ready",
            "input": {"niche": niche or None, "thesis": thesis or None},
            "title": topic.title,
            "segments": segments,
            "sources": [
                {
                    "title": topic.title,
                    "url": topic.url,
                    "publisher": topic.publisher,
                    "published": _to_zulu(topic.published) if topic.published else None,
                    "feed": topic.feed,
                }
            ],
        }
    }


# Words that flip a single free-text CLI arg from a niche into a thesis (research §1:
# niche ~ "subject area", thesis ~ "a claim about the subject").
_THESIS_MARKERS = frozenset(
    ["how", "why", "should", "because", "too", "enough", "is", "was", "are", "were", "will", "would", "did", "does", "hiked", "cut", "raised", "rising", "falling", "caused", "drives", "shapes", "threatens", "slows", "moves", "due"]
)


def classify_input(text: str) -> tuple[str | None, str | None]:
    """Return (niche, thesis); exactly one populated. Heuristic marker test."""
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    if tokens & _THESIS_MARKERS:
        return None, text.strip()
    return text.strip(), None


Fetcher = Callable[[str], str]


def urlopen_fetcher(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "vibe/0.1 (explainer pipeline)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return bytes(resp.read()).decode("utf-8", errors="replace")


def fetch_feeds(fetcher: Fetcher, *, feeds: dict[str, dict[str, str]] | None = None) -> list[FeedItem]:
    """Pull every tier-1 feed; a failed feed (network or malformed) is skipped."""
    feeds = feeds or FEEDS
    items: list[FeedItem] = []
    for name, meta in feeds.items():
        try:
            xml_text = fetcher(meta["url"])
            items.extend(parse_rss(xml_text, name, meta.get("publisher")))
        except Exception:  # noqa: BLE001, S112 - skip a failed feed, never abort the poll
            continue
    return items


def read_feeds_dir(directory: Path) -> list[FeedItem]:
    """Load local RSS/XML files (offline discovery, test seam). Feed = filename stem."""
    items: list[FeedItem] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".rss", ".xml"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        feed_name = path.stem
        default_pub = FEEDS.get(feed_name, {}).get("publisher", feed_name)
        items.extend(parse_rss(text, feed_name, default_pub))
    return items


def choose_topic(
    items: list[FeedItem], *, niche: str | None = None, thesis: str | None = None, now: datetime
) -> tuple[FeedItem, Score] | None:
    """The pipeline: gate (hard) -> score (rubric) -> pick the best survivor."""
    gated = gate_items(items, niche=niche, thesis=thesis)
    scored = [(i, score_item(i, now=now)) for i in gated]
    return select_topic(scored)


def build_topic_brief_from_items(
    items: list[FeedItem],
    *,
    niche: str | None = None,
    thesis: str | None = None,
    now: datetime,
) -> TopicBrief | None:
    """Best on-topic item -> a Topic Brief, or None when nothing passes the gate."""
    picked = choose_topic(items, niche=niche, thesis=thesis, now=now)
    if picked is None:
        return None
    topic, _ = picked
    return build_topic_brief(topic, build_segments(topic), niche=niche, thesis=thesis, now=now)


def parse_rss(xml: str, feed_name: str, publisher: str | None = None) -> list[FeedItem]:
    """Parse RSS 2.0 or Atom into `FeedItem`s. Pure; drops items missing title/url."""
    default_pub = publisher if publisher is not None else FEEDS.get(feed_name, {}).get("publisher", "")
    root = ET.fromstring(xml)
    entries = [*root.findall(".//item"), *root.findall(".//a:entry", _ATOM)]
    items: list[FeedItem] = []
    for el in entries:
        title = _text(el, "title")
        url = _link(el)
        if not title or not url:
            continue
        items.append(
            FeedItem(
                title=title,
                url=url,
                publisher=_source(el) or default_pub,
                published=_published(el),
                categories=_categories(el),
                feed=feed_name,
            )
        )
    return items