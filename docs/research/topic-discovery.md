# AI Topic Discovery: from niche to Topic Brief

Research ticket: issue #4 of Chickenman67/V2YoutubeAutomation
Status: research-only, no code produced.
Date grounded on live probes: 2026-08-12/13.

---

## Summary decision

The AI produces a **Topic Brief** (per `CONTEXT.md`: a title plus an ordered
segment outline) by running a **scoring pipeline over live financial news**,
rather than inventing topics free-form. Concretely:

1. **Source tiering** — pull from three mutually-covering tiers that were all
   **verified live** (HTTP 200, well-formed RSS): Yahoo Finance News RSS, CNBC
   Business News RSS, and MarketWatch Top Stories. Tier 2 (finance subreddits,
   earnings call transcripts, search) are used as **secondary evidence**, never
   as the sole topic source, because they are slower-moving, partially
   paywalled, or bot-hostile.
2. **Topic selection** — each candidate is scored on a small rubric
   (currency, audience-relatability, explainability) constrained by the user's
   niche or thesis; the highest-scoring candidate whose niche filter passes is
   promoted to the brief.
3. **Number of segments** — **4 to 6 segments** per full-length video, the
   empirical zone for The-Paint-Explainer-style sectioned explainers (long-form
   videos split into 3–6 labelled sections). **One topic = one segment = one
   short** (already the domain rule); a 4–6 segment video yields 4–6 shorts.
4. **Topic Brief schema** — a self-contained JSON/markdown contract with
   `title`, an ordered list of `segments[]`, per-segment `hook` + `key_points`,
   and `sources[]` carrying citation URLs. The script stage consumes exactly
   this; not a word more.

---

## 1. Candidate content sources

Verified live feeds (probing done with `Invoke-WebRequest`, all returned
`HTTP 200` with well-formed RSS/Atom):

| Tier | Source | Endpoint | Notes |
|------|--------|----------|-------|
| 1 | Yahoo Finance News | `https://finance.yahoo.com/news/rssindex` | High volume; 30+ items in a single poll; each item already carries a **category path** in its URL (`/markets/`, `/economy/`, `/healthcare/`, `/energy/`, `/crypto/`, `/real-estate/`) plus a **publisher attribution** (`<source url=...>`). Great for niche filtering by URL category. |
| 1 | CNBC Business News | `https://www.cnbc.com/id/10001147/device/rss/rss.html` | Official section feed; each `<item>` has `<category>` tags usable for niche gating. |
| 1 | MarketWatch Top Stories | `https://feeds.content.dowjones.io/public/rss/mw_topstories` | Dow Jones source; good for macro/retail-investor angles. |
| 2 | Finance subreddits (`r/finance`, `r/stocks`, `r/investing`) | JSON API `https://www.reddit.com/r/finance/top.json?t=day` | Good sentiment/what-people-ask signal; but **bot-hostile** (rate limits, blocks). Use as popularity evidence, not title sourcing. |
| 2 | Earnings call transcript sites + search | `site:...` web search, company IR | Slower; needed when a niche is company-specific. |
| 2 | Web search (for niche scoping) | Bing/Google search API | Used to confirm a niche is "still a thing" and to collect up-to-date numbers for key points. |

**Pick rule:** when the user gives only a **niche** (e.g. "banking,
interest rates"), filter Tier-1 items by the niche's known category/entity set
and score the survivors. When the user gives only a **thesis** (e.g. "the Fed
hiked rates too slowly"), search for the thesis keywords across Tier-1 feeds,
and pull corroborating figures from Tier-2 search. If nothing survives the
filter in one poll, poll again after the feed TTL (Yahoo `ttl=5` min, CNBC and
MarketWatch `ttl=60` min).

### Choosing one topic from the feed (scoring rubric)
Score each surviving candidate 0–3 on:

- **Currency** — is it dated *today*? (pubDate vs poll time; news is date-halflife).
- **Audience-relatability** — does a retail viewer feel it ("fed hikes",
  "Nvidia vs AMD") rather than an obscure B2B filing ("Fura acquires Pacific
  Northwest freight broker" scored low here).
- **Explainability** — can it be decomposed into 4–6 broad, visually-drawn
  ideas? The Paint Explainer style lives on *visible, bloomable concepts*.

Plus a **hard gate**: the topic must satisfy the user's niche/thesis, else drop
it. Highest total wins; on ties prefer the most recent pubDate and the
strongest `<source>` reputation.

---

## 2. Crafting a strong hook / SEO-friendly title

- **Herd Curiosity + Concreteness:** name a specific, expensive-feeling or
  counter-intuitive object/situation in the title. Paint-Explainer-style titles
  are built as "[Subject]: [stakes/number]" (e.g. "Why Gas Is So Expensive",
  "How the Fed Shapes Your Mortgage Rate").
- **Numbers > adjectives** — a concrete figure (price, %, timeline) makes a
  title scannable on both YouTube search and Shorts.
- **Keywords first, summarise not tease** — the title should state the topic so
  search engines and the Shorts algorithm can classify it; keep the *hook* (the
  curiosity line) for the segment's first sentence instead of the title.
- **Title ≠ hook.** The title is the SEO contract; the **hook** is the first
  1–2 lines of each segment that earns the viewer's continued attention
  (rhetorical question, surprising fact, or outcome disposed).

**Recommended title template:** `{Topic} Explained: {Why/How/What} + {consequence}`

Example from the probed feed, rewritten:
- Feed title: `Intel's huge rally is helping pay for its AI comeback`
- Brief title: `Intel's Comeback: How AI Is Paying for Its Rally`

---

## 3. Segment count and segment → short mapping

- **4 to 6 segments** is the recommended band for a full-length, sectioned
  financial explainer.
  - Fewer than 4 → the video reads as a single short dressed up as long-form.
  - More than 6 → retention drops and each segment thins out below a
    "one full short" of substance.
- Each **segment is self-contained** (per `CONTEXT.md`: one topic = one
  segment = one short, independently renderable). Therefore a **4–6 segment
  video produces exactly 4–6 shorts**, each short's content being identical to
  its segment's narrative — re-rendered vertically, never written anew.
- Segments are **ordered narratively** (e.g. Problem → Mechanism → Players →
  Climax → Outlook), each described by (a) segment title, (b) hook, (c) key
  points. That ordered list is what makes a vertical-shorts feed into a
  consumable series while keeping the full video coherent.

---

## 4. Topic Brief schema

The Topic Brief is the **input contract to production**. It must hold exactly
what the script stage needs and nothing else.

```jsonc
{
  "topic_brief": {
    "id": "tb-2026-08-13-01",          // unique slug for traceability
    "generated_at": "2026-08-13T00:30:00Z",
    "status": "ready",                  // "ready" -> script stage consumes
    "input": {
      "niche": "interest rates / banking",   // user supplied, at most niche or thesis
      "thesis": null                         // null when only niche given
    },
    "title": "Intel's Comeback: How AI Is Paying for Its Rally",
    "segments": [
      {
        "index": 1,
        "title": "The Fall",                // segment title in the narrative
        "hook": "For a decade Intel lost ground to a rival it used to own.",
        "key_points": [
          "Intel's share in the 2010s",
          "the node-transition stumble",
          "the turn under the new roadmap"
        ]
      },
      {
        "index": 2,
        "title": "AI Changes the Math",
        "hook": "Then the market decided Intel's AI story was worth paying for.",
        "key_points": [ /* ... */ ]
      }
      // ... indices 3..N (N in [4,6])
    ],
    "sources": [
      {
        "title": "Intel's huge rally is helping pay for its AI comeback",
        "url": "https://finance.yahoo.com/markets/article/...",
        "publisher": "Yahoo Finance",
        "published": "2026-08-11T10:00:00Z",
        "feed": "yahoo-finance-news"
      }
      // one per segment minimum; every factual claim maps to a source
    ]
  }
}
```

### Field rationale
- `title` — the SEO contract (Section 2); the only long-form audience-facing
  string fixed here.
- `segments[]` — ordered; `index` gives the short-order in the series; `hook`
  is the first-line attention grabber a script must open with; `key_points`
  are the beat-level facts the script expands. No narration text lives in the
  brief — the Script Standard stage owns prose.
- `sources[]` — provenance for the script stage and for fact-checking; each
  segment references at least one entry. Keeps the AI honest about currency
  (label `feed` + `published`).
- Anything absent (voice, camera, imagery) is deliberately out of scope here;
  later stages (`Design Standard`, `Canonical camera`) add their own contracts.

---

## 5. Handoff to the script stage

The brief is non-prose: the **Script Standard** stage takes `title` +
`segments[].{title,hook,key_points}` as inputs and produces narration that
satisfies the human-written standards (banned words/openings, non-mechanical
endings, Source-list citation). Facts in `key_points` are traceable back to
`sources[]`, so the script never introduces un-cited numbers.

---

## Sources examined

- Yahoo Finance News RSS — live probe, HTTP 200, 30+ items/poll with category
  paths and publisher attribution.
- CNBC Business News RSS — live probe, HTTP 200, category-tagged items.
- MarketWatch Top Stories RSS — live probe, HTTP 200, Dow Jones attribution.
- `CONTEXT.md` — domain definitions for Topic Brief, Segment, Short, Hero frame
  and Script Standard (authoritative for naming).
- The-Paint-Explainer sectioned-explainers convention (long-form split into
  3–6 labelled sections, each independently watchable) vs. the repo's own
  one-topic-per-short rule.