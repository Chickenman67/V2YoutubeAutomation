# T4 — Narration stage: design

**Date:** 2026-08-14
**Branch:** `build/t4` (forked from `main`, which holds merged T1–T3)
**Sources of truth:** `docs/specs/narration.md` (decided spec, wayfinder #7), `docs/specs/assembly.md` (layout + downstream contract), `docs/specs/script-standard.md` (marker vocabulary), `vibe/check.py` (timing artifact contract), the T3 script stage (`vibe/script.py`).

## 1. Decisions (confirmed with the operating partner)

- **Real edge-tts wired as default.** The narration stage genuinely synthesizes audio using `edge-tts` (`en-US-ChristopherNeural`), matching the decided narration spec. This deviates from T1–T3's "deterministic fake by default" ethos deliberately: narration is the pipeline's real TTS output, not a stand-in. Tests keep it offline via an injected fake synthesizer.
- **Separate `vibe narrate` subcommand.** `vibe make` already owns the human script-approval gate; narration is a distinct, opt-in (network-dependent) execution step. `make` stays fast/offline; `narrate` is where synthesis cost lands.
- **Full 4-marker vocabulary parsed now.** The chunker handles `**keyword**`, `##figure##`, `**gold**`, and `~` per the narration spec, even though the T3 author currently only emits `**keyword**`. Robust and spec-faithful; the current author being figure-free is documented, not encoded as a limitation.
- **ffmpeg for the audio codec.** No new Python audio deps; shell out to ffmpeg (already on the system, and already used by `check.py`) for PCM decode, silence insertion, and deterministic mp3 re-encode.

## 2. Module boundary

New pure module `vibe/narrate.py` plus a thin seam. Split: the chunking/classification, knob lookup, silence placement, and cumulative word-timing math are **pure functions** (offline-testable); the synthesizer and the encoder are **protocol seams**.

### Pure core (no I/O, no deps)

- `Chunk` — `(text: str, kind: ChunkKind, pre_silence_ms: int, post_silence_ms: int)`.
  `ChunkKind ∈ {base, keyword, figure, gold, pause}`.
- `parse_line(line: str) -> list[Chunk]` — splits a script line on the four markers into ordered chunks, markers **never appear in chunk.text**; `~` becomes a `pause` chunk. Unmarked text is `base`. Empty text yield is handled (a `**gold**` that wraps a figure wins classification per spec §4).
- `KNOBS: dict[ChunkKind, tuple[str, str]]` — the narration spec §4 table:
  - base `rate "0%", volume "0%"`
  - keyword `-8%`, `+12%`
  - figure `-5%`, `+10%`
  - gold `-8%`, `+15%`
- `SILENCE_MS: dict[ChunkKind, tuple[int, int]]` — `(pre_ms, post_ms)` per kind: keyword 120 *before*; figure/gold 450 *after*; pause 300.
- `build_word_timings(chunks, chunk_events) -> list[WordTiming]` — pure cumulative timing math: renders `(word, start_s, end_s)` in order, adding each chunk's decoded duration and the inserted silence gaps, so timing stays cumulative across chunks/silence (spec §5). `WordTiming = (word, start_s, end_s)`, matching `check.py`'s `_TIMING_KEYS` exactly.
- `timing_jsonl(timings) -> str` — serializes word timings to the `.timing.jsonl` contract.

### Seams (Protocols)

- `Synthesizer.__call__(text, *, voice, rate, volume) -> SynthResult` —
  `SynthResult = (audio_bytes: bytes, words: tuple[WordTiming, ...])`.
  Real impl `edge_tts_synthesizer()` wraps `edge_tts.Communicate` streaming with `boundary="WordBoundary"`; **fake** `fake_synthesizer()` in tests returns canned bytes + fixed words.
- `Encoder.__call__(units: list[(audio_bytes, pre_silence_ms, post_silence_ms)], *, sample_rate, channels) -> bytes` —
  decodes each chunk to PCM, prepends/inserts silence, concatenates, re-encodes to deterministic mp3. Real impl `ffmpeg_encoder()` shells to ffmpeg with the fixed flags from `config.py`; fake `fake_encoder()` returns fixed bytes for tests.

### Orchestrator

- `narrate_segment(script_text, *, synthesizer, encoder) -> SegmentNarration(mp3_bytes, timings)` — parses the script into lines → parses each line into chunks → synthesizes speech chunks (`base`/`keyword`/`figure`/`gold`) via the seam → concatenates/encodes via the seam → returns in-memory `mp3_bytes` + cumulative `timings` (no I/O). A `pause` chunk contributes **only** silence (300 ms) to the encoder — no synthesis call, no words.
- `narrate_approved(lay: layout.Layout, *, synthesizer, encoder) -> list[SegmentResult]` — reads `scripts/index.json`, narrates each `approved` segment, and writes `.mp3` + `.timing.jsonl` (temp-then-rename so no partial artifact) into the `narrate` build layout; skips `needs-human`/`ready`-not-approved with a warning.

## 3. Artifacts & build layout

- Add `narration` to `_LAYOUT_DIRS` and a `Layout.narration` property in `vibe/layout.py` (assembly §9 keeps narration artifacts under their own dir alongside `segments/`, `shorts/`, `cc/`, `scripts/`).
- Per **approved** segment `n`:
  - `build/narration/segment-<n>.mp3` — finished narration audio, pauses baked in.
  - `build/narration/segment-<n>.timing.jsonl` — `{word, start_s, end_s}` per line, cumulative, monotonic; satisfied by `parse_line`+`build_word_timings`, verifiable with `vibe check`.

Example timing line: `{"word": "rates", "start_s": 0.00, "end_s": 0.24}`

Encode flags live in `config.py` as constants (mirroring how `check.py` reads `AUDIO_*`): `AUDIO_SAMPLE_RATE` (44100), `AUDIO_CHANNELS` (2), `NARRATION_MP3_BITRATE = "192k"`, and `NARRATION_VOICE = "en-US-ChristopherNeural"` (narration spec §1) so the encoder flags and voice are a single deterministic source.

## 4. CLI wiring: `vibe narrate`

New subcommand in `vibe/cli.py`, symmetric with `vibe check`:

```
vibe narrate            # narrate all approved segments in ./build
vibe narrate --build DIR  # point at a different build root (default ./build)
```

- Reads `build/scripts/index.json`. Missing index / missing build → message + exit 2.
- Threads each `approved` segment through `narrate_segment` **one at a time** (spec §7 "one sub-agent per segment"; parallelism deferred).
- Success per segment: writes `.mp3` + `.timing.jsonl`; prints `segment-<n>.mp3: OK`.
- `needs-human` / not-yet-approved → stderr `segment-<n>.mp3: skipped (<reason>)`, continue, exit 0 (best-effort precedent from `vibe make`).
- edge-tts/network failure for a segment → stderr, exit non-zero; no partial `.mp3` (temp-then-rename). Already-written segments remain.
- No interaction prompt (the human gate lives in `vibe make`).
- Test seam: synthesizer/encoder resolve from the seam; `VIBE_NARRATOR=fake` env selects the fake so CLI tests are offline (same idiom as `VIBE_SCRIPT_AUTHOR` / `VIBE_OFFLINE`).

## 5. Determinism, error handling, testing

### Determinism

- Pure-core outputs (chunking, knob choice, silence placement, timing math, artifact ordering) are fully deterministic and unit-tested.
- **Live audio bytes are NOT byte-identical across edge-tts calls** (real network TTS). Honest limit: determinism holds for timing/knobs/chunking — which is what the media contract (`check.py`) verifies (codec, sample rate, channels, duration). The fake synthesizer makes the *pipeline* byte-deterministic so tests are stable.
- Word timings must be cumulative across chunks and inserted silence so captions/cuts stay in sync downstream (spec §5, assembly §3).

### Error handling

- Missing index/build → exit 2 with message.
- edge-tts/ffmpeg failure (network/auth/codec) per segment surfaces as `NarrationError` → stderr, exit 1, no partial file (temp-then-rename); completed segments stay.
- `needs-human` / unapproved → skip + warning, exit 0.

### Doc fix (from T3 handoff)

- Add a short note to `docs/specs/narration.md` §1 (after the "**Input:**" line) clarifying the marker reality: the current T3 author emits only `**keyword**`; `##figure##`/`**gold**`/`~` are handled by the pipeline but only appear once the author produces figures/pauses. Prevents downstream (assembly/captions) from assuming figures always present.

### Testing (offline, fixture-driven)

- `parse_line`: a fixture per marker type — `**keyword**`, `##figure##`, `**gold**`, `~`, `##figure##`-inside-`**gold**`, mixed, and no-marker — asserting chunk kinds/order, and markers never appear in `chunk.text`.
- Knob/silence lookup: exact `rate`/`volume`/`silence` per kind from the spec table.
- `build_word_timings`: given fake synth words + inserted silence, asserts cumulative offsets (gap == pause), monotonic.
- `narrate_segment` with fake synth + fake encoder: writes `.mp3` + `.timing.jsonl`; timing passes `check.check_timing`.
- CLI: `narrate` on a fixture build with `VIBE_NARRATOR=fake` writes `.mp3`+`.timing.jsonl`, then `vibe check` on the `.timing.jsonl` passes (mirrors T3 CLI tests).
- Gate coupling: `needs-human` segment skipped, approved segments narrated.
- New pyproject dep: `edge-tts`; tests never import it except behind the seam.
- Full suite: `pytest` / `mypy vibe` / `ruff check vibe tests` all clean.

## 6. Files touched

New: `vibe/narrate.py`, `tests/test_narrate.py`, `tests/test_cli_narrate.py`, `tests/fixtures/` (offline narration fixtures).
Modified: `vibe/layout.py` (add `narration` dir + property), `vibe/config.py` (narration encode constants), `vibe/cli.py` (add `narrate` subcommand + fake seam env), `tests/conftest.py` (test seam env), `pyproject.toml` (add `edge-tts`), `docs/specs/narration.md` (marker-reality note).

## 7. Out of scope (for this ticket)

- Rendering (T5), assembly/full-video/shorts/CC (T6/T7), E2E smoke (T8).
- Parallelization of per-segment narration.
- SSML (edge-tts has none; emphasis is knob-driven per spec §2).
- Background music / upload / spoken CTA (assembly §10).