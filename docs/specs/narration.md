# Narration: edge-tts voice + emphasis-to-prosody mapping

**Status:** Decided spec, adopted by this effort. Source: wayfinder ticket #7 (HITL, resolved).
**Input:** a segment narration script produced by the Script Standard (`docs/specs/script-standard.md`), carrying the inline markup `**keyword**`, `##figure##`, `**gold**`, and `~`.
**Marker reality (2026-08):** the current templated author (`vibe/script.py`) emits only `**keyword**`. `##figure##`, `**gold**`, and `~` are fully handled by the narration pipeline (chunking, knobs, silence, timing) but do not appear in current output until the author produces figures/pauses. Downstream consumers (assembly/captions) must not assume figures are always present.
**Downstream consumer:** #8 Assembly + captions (consumes the audio + word-timing artifacts defined here).

---

## 1. Voice (default, single)

- **`en-US-ChristopherNeural`** — authoritative male narrator; personality tags "Reliable, Authority". A natural fit for financial explainers, consistent across every segment and video (one recognizable narrator = channel identity).
- The voice is a **fixed, deterministic** choice — never varied at runtime. Changing it is a product decision, not a pipeline knob.

## 2. Why this works despite no SSML

edge-tts **does not support custom SSML** (removed since v5.0.0). Every call wraps your text in a fixed `<speak><voice><prosody>` template and XML-escapes the input, so arbitrary tags (`<emphasis>`, `<break>`, `<prosody>`) are *spoken literally* — never interpreted. Only the library-generated, global `rate` / `volume` / `pitch` attributes are controllable, and **pitch is unreliable** (Microsoft has ignored it on and off). Therefore emphasis is achieved by **segmented synthesis** (§3), not by SSML tags.

## 3. Segmented synthesis recipe

Each narration line is parsed into chunks at marker boundaries. Each chunk is one `edge_tts.Communicate(text, voice, boundary="WordBoundary")` call carrying per-type `rate`/`volume`. Chunk audio is decoded to PCM, concatenated with any inserted silence (§4), and re-encoded, so there are no mp3 edge artifacts at chunk seams. Word timings are rebuilt with **cumulative offsets** (sum of decoded durations of preceding chunks/neutral pauses).

Chunking rule:
1. Split the line on `**…**`, `##…##`, `**gold**`, and `~` into ordered segments.
2. Classify each segment by the marker that precedes it (or base).
3. Synthesize each segment with that type's knobs; render `~` / post-figure pauses as inserted silence, not as words.

Markers are structural: they are **never spoken** and **never** appear in the audio or the word-timing output.

## 4. The emphasis → prosody mapping (knobs)

Defaults; **tune by ear at implementation / review** (these are provisional until listened to).

| Marker | `rate` | `volume` | Silence |
|---|---|---|---|
| base narration | `0%` | `0%` | — |
| `**keyword**` | `-8%` | `+12%` | **120 ms** micro-pause before the punch |
| `##figure##` | `-5%` | `+10%` | **450 ms** pause after (design-standard: "one-beat pause after a money figure") |
| `~` beat | base | base | **300 ms** pause |
| `**gold**` wow figure | `-8%` | `+15%` | (450 ms after, same as figure if it wraps a figure) |

Notes:
- `rate` and `volume` are the verified-working options (`^[+-]\d+%$` for both). Negative values on the CLI need `--rate=-8%`.
- Reducing `rate` (slower) on the emphasized span is what makes the punch land; `volume` gives it weight. `pitch` is **not used**.
- A `##figure##` inside a `**gold**` treats the segment as the gold/wow figure (gold wins the classification).

## 5. Word timing for captions (#8 contract)

- Per segment, synthesize with `boundary="WordBoundary"` and collect the `WordBoundary` events.
- Each event carries `offset` and `duration` in **100 ns ticks**: `start_s = offset / 1e7`, `end_s = (offset + duration) / 1e7`.
- Emit one **per-segment** word-timing file (`.jsonl`), one `{"word", "start_s", "end_s"}` per line, with offsets made **cumulative across chunks and silence** by adding each chunk's decoded duration to subsequent chunks. The silence gaps (§4) appear in the timings as gaps, so captions and video cuts stay in sync.

## 6. Artifacts (one per segment)

- `segment-<n>.mp3` — finished narration audio, pauses baked in, deterministic.
- `segment-<n>.timing.jsonl` — `(word, start_s, end_s)`, cumulative.

#8 consumes exactly these two files per segment; it does not re-synthesize or guess timings.

## 7. Determinism & responsibilities

- Synthesis is **deterministic** (fixed voice, fixed knobs, fixed chunking). The creative part — *where the author places emphasis markers* — is the LLM's work, already governed by the Script Standard; this machinery only renders those choices.
- Generating narration is one sub-agent per segment (standing preference). A segment's audio is independent of every other segment.