# Script Standard: Human-sounding narration with emphasis authoring

**Status:** Decided spec, adopted by this effort. Source: wayfinder ticket #6 (HITL, resolved).
**Input contract:** the Topic Brief (`docs/research/topic-discovery.md` §4) — `title` + `segments[].{title, hook, key_points}` + `sources[]`. No narration text lives in the brief; this standard owns prose.
**Provenance of the checklist:** `AI-Detection-Techniques.md` (raw techniques list, user-owned; register reproduced inline below so the repo is self-contained). `CONTEXT.md` glossary defines **Script Standard**.

---

## 1. Script artifact

- **One narration script per segment**, independently renderable (per `CONTEXT.md`). A per-video index lists the ordered segment scripts.
- The atomic unit is the **line**: one line = one spoken sentence or clause = one caption = one camera cut (design-standard: "cut on the beat… scene cuts per clause"). No prose paragraphs.
- **Inline markup only** — emphasis and pause markers live in the line text; no separate metadata fields.

### Line markup

| Marker | Meaning | Consumed by |
|---|---|---|
| `**word**` | **Keyword emphasis** — on-screen partial bold + accent colour; **max one per line**. | Renderer (visual); → SSML `<emphasis>` at #7 |
| `##figure##` | **Money/figure emphasis** — the spoken figure must appear on-screen the same second, tabular + accent-coloured, one-beat pause after. | Renderer (visual); → SSML `<break>` at #7 |
| `~` | **Beat pause** — short pause: camera hold + TTS break. | Camera timing + TTS |
| `**gold**` | "Wow" figure — gold highlight. | Renderer (visual) |

The `**` emphasis markers are the **single allowed exception** to the no-asterisks rule (§3.5). Markers are structural and consumed downstream — never spoken.

## 2. Segment narrative skeleton

Fixed cadence per segment:

1. **Hook** — the Topic Brief's `hook`, lightly polished, ≤2 lines, always opens the segment.
2. **Thesis** — 1 line stating the segment's claim.
3. **Beats** — one per `key_point`, 2–4 beats per segment.
4. **Payoff** — 1 closing line with consequence/stakes. Must **not** restate the hook and must not be a mechanical conclusion (§3.6).

### Word budget

- **Segment narration: 200–280 words** (≈ 80–115 s at ~150 wpm) → a ~1.5–2 min segment = short.
- **Line length:** most lines 6–14 words, deliberately mixing very short with longer (§3.4).
- End-of-video recap card is **silent/visual** (Two Cents pattern); it is not narration.

## 3. Register rules (adapted from `AI-Detection-Techniques.md`)

Register is informal spoken English: contractions mandatory, plain connectors, concrete claims.

### 3.1 Banned words (hard fail)

delve, realm, pivotal, nuanced, tapestry, beacon, navigate, unravel, embark, testament, moreover, furthermore, additionally, thus, thereby, utilize, leverage, overarching, multifaceted, cornerstone, streamline, seamless, robust, cutting-edge, game-changer, best-in-class, actionable insights, foster a culture of, drive results.

Occasional "moreover"/"furthermore" is tolerable only if not clustered; prefer plain connectors: also, but, and, so, yet, though.

### 3.2 Banned openings (hard fail)

"In today's fast-paced world…", "It is no secret that…", "As we all know…", "In recent years…", "This article/essay will discuss…", "Throughout history…", "In conclusion" / "To summarize" (anywhere, not just the start), "Whether you're a… or a…", "Let's dive in.", "In the ever-evolving landscape of…".

### 3.3 Triadic abstract lists (hard fail)

No list of three abstract qualities ("efficiency, collaboration, and growth"). Break into a pair or make it concrete.

### 3.4 Vague/abstract claims (hard fail)

No generic phrases ("improve efficiency", "positive impact", "streamline processes"). Every claim names a specific number, name, date, place, or example — this is the same rule as figure discipline (§4).

### 3.5 Punctuation & markdown artifacts (hard fail)

- No em dashes, colons, semicolons, or ellipses in script text — use `~` for pauses; split or use periods/commas otherwise.
- No markdown artifacts: no stray `*` (other than the `**` emphasis markers), no double spaces, no bullet formatting imposed on prose.

### 3.6 Mechanical conclusions (hard fail)

No ending that restates the opening or uses "In conclusion"/"To summarize". The payoff line is a plain final sentence.

### 3.7 Soft style (LLM critique pass)

- **Sentence-length mixing:** mix very short sentences with longer ones (strongest statistical AI-text signature); every sentence complete and grammatically standard, no fragments.
- **Opener variety:** not perfect variety — a few consecutive sentences may share an opening structure (perfect variety reads mechanical).
- **≥1 And/But/So sentence start** per segment — split a compound sentence at the conjunction to add one.

### 3.8 Spoken-video additions (hard fail)

- No mid-script self-reference ("as we said earlier", "stay tuned").
- Call-to-action is **video-end only**: a single spoken CTA line, optional, decided per video, never per segment.

## 4. Figure discipline

- Every number spoken must trace to a `key_point` or an entry in `sources[]` — no invented or un-cited figures, ever.
- Every number must carry `##figure##` and therefore appear on-screen the same second it is spoken (design-standard §8).
- Source footline shown on-screen for every statistic (24 px, "Source: …", design-standard §8).

## 5. The gate

Three layers, run on the LLM's own draft before production. **Never auto-ship a failing draft.**

1. **Deterministic checks (hard fail)** — regex/count-based:
   - banned words (§3.1) and banned openings (§3.2);
   - punctuation/artifact scan (§3.5) and mechanical-conclusion scan (§3.6);
   - spelled-out contraction misses: `cannot`, `will not`, `it is`, `do not` → `can't`, `won't`, `it's`, `don't` (§3 register);
   - numbers without `##figure##` (§4);
   - word budget within 200–280 words per segment (§2);
   - every number traceable to `key_points`/`sources[]` (§4).
2. **LLM critique pass (soft fail → regenerate)** — the LLM reviews its own draft against §3.3, §3.4, §3.7, and tone/rhythm, and emits fixable feedback.
3. **Regenerate-with-feedback loop** — up to 3 attempts incorporating the critique; if still failing, **flag for human review** (the draft is never shipped silently).

## 6. Downstream contract

- The markup vocabulary (§1) is the fixed input contract for **#7 Narration** (edge-tts voice + SSML prosody mapping) and the renderer (partial-bolding, figure treatment, pauses).
- The Script Standard gate runs before narration and before the first-segment preview (the pipeline's review gate, per the map's "Not yet specified").
