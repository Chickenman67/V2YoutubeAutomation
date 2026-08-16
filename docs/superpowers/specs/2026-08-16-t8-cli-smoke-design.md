# T8 — offline E2E smoke test at the CLI seam: design

**Date:** 2026-08-16
**Branch:** `main` @ `411b47f` (design commits land on main per repo convention; implementation lives in a fresh worktree)
**Sources of truth:** issue #14 (parent spec #9); `docs/specs/assembly.md` §2 (media contracts), §7 (shorts), §9 (output layout); `docs/agents/issue-tracker.md`; the T7 design/plan (`2026-08-15-t7-shorts-cc-design.md`, `2026-08-15-t7-shorts-cc-stage.md`, `2026-08-16-t7-caption-reflow-design.md`).
**Consumes:** `vibe/cli.py` (the single CLI seam), `vibe/narrate.py`, `vibe/render.py`, `vibe/assembly.py`, `vibe/shorts.py`, `vibe/check.py` (the media-contract checker), `tests/conftest.py` (`run_cli` + `ffmpeg_available`).

## 1. Problem & goal

Issue #14 asks for an **offline, fixture-driven end-to-end smoke test exercised strictly through the single CLI seam** (`python -m vibe …`): a small fake topic produces artifacts, and assertions check the media contract, caption sync, and durations — without live edge-tts or heavy real renders.

Three acceptance criteria:
- runs `vibe make` on a small fake topic entirely offline via fixtures;
- asserts `full.mp4` + each segment/short codec and resolution match the media contract;
- asserts CC sidecars are timestamp-ordered and sync to `.timing.jsonl`, and durations match.

## 2. The constraint that shapes the design

Checking **real** codec/resolution (AC #2) requires real, ffprobe-able `.mp4` files. The render/assemble/shorts path muxes the narration `.mp3` into every clip, so real media needs **valid, decodable audio** in-clip. The existing offline narrator seam (`VIBE_NARRATOR=fake`) returns `b"fake-mp3"` placeholder bytes that ffmpeg cannot decode, so the real render path errors on them; and the fake render/assemble seams write placeholder bytes that cannot be probed. Therefore the smoke test must use the **real** ffmpeg encoders for render/assemble/shorts and a **new offline narrator** that yields valid audio.

Two consequences, decided with the operating partner:

1. **Valid offline narration audio** — add a production `VIBE_NARRATOR=offline` narrator mode that emits a short deterministic 440 Hz beep per word, matched to the same deterministic word timings the fake synthesizer uses, encoded by the real `ffmpeg_encoder()`. This keeps the smoke fully offline, produces real AAC clips, and makes container durations line up with the timings (AC #3).
2. **A small topic** — `build_segments` (`vibe/discover.py:229-254`) is fixed at 5 segments, so any `vibe make` yields 5 approved segments; a real `full.mp4` requires concatting all of them, so a *small* real-media smoke is impossible without bounding the segment count. Add a `make --segments N` knob (slices the topic brief's segments **before** scripts are written; downstream reads `index.json`, so the whole pipeline follows N).

## 3. Design

### 3.1 Offline narrator (`vibe/narrate.py` + `vibe/cli.py`)

- `narrate.offline_synthesizer()` — implements the `Synthesizer` protocol. For a chunk's `text` it returns `(audio_bytes, word_timings)` where:
  - `word_timings` are identical to `fake_synthesizer`'s (deterministic: `start_s` at whole strides, `end_s = start_s + 0.2`, stride `0.25`), so caption sync and duration math are unchanged;
  - `audio_bytes` is **valid, decodable** audio spanning the chunk's words — a per-word 440 Hz beep (0.2 s beep + 0.05 s silence per word) emitted as a self-generated WAV/RIFF + PCM buffer, of exactly the chunk's local span (`last word end`). ffmpeg auto-detects by content magic, so the generic `_decode_mp3` path decodes it correctly even though the unit is not named `.mp3`.
- `cli._select_narrator` gains a branch: `VIBE_NARRATOR=offline → narrate.offline_synthesizer(), narrate.ffmpeg_encoder()`.
- The encoder then produces one real mp3 whose total length equals the cumulative timing end; the render path muxes it into AAC clips guarded by `-shortest`, so each clip's container duration = `OPEN_PADDING_S +` narration end — exactly the contract the `vibe check --timing` path expects.

### 3.2 Small-topic knob (`vibe/cli.py` + argparse only; `discover.py` untouched)

- `make --segments N` (`1..5`, default `None` = existing full 5).
- In `_cmd_make`, after `discover.build_topic_brief_from_items`, slice `brief["topic_brief"]["segments"][:N]` when given, then `script.write_scripts` (reads `tb["segments"]`) and everything downstream follow N. `brief.json` reflects the bounded topic; no other stage re-derives the count.

### 3.3 Smoke test (`tests/test_cli_pipeline.py`)

Driven by the `run_cli` subprocess seam (closed stdin ⇒ auto-approves gates, `VIBE_OFFLINE=1`), gated by the `ffmpeg_available` session fixture (skip cleanly when ffmpeg/ffprobe absent). Flow over a temp build root, **real** encoders throughout except narrator:

```
make    --feeds-from tests/fixtures --segments 2
narrate --build build                  VIBE_NARRATOR=offline
render  --build build                  (real PIL + ffmpeg)
assemble --build build                 VIBE_NARRATOR=offline (real recap/concat + real check)
shorts  --build build                  (real 9:16 + CC)
```

Assertions (each via `vibe.check.*`, i.e. ffprobe + SRT/timing parsers), for every approved segment `n`.

- **AC #2 (media contract):** `check_video(segment-<n>.mp4, "clip")`, `check_video(short-<n>.mp4, "short")`, `check_video(full.mp4, "full")` all `ok` — codec (h264/AAC), profile (high/lc), pix_fmt (`yuv420p`), resolution, fps, sample rate, channels.
- **AC #3 (captions + durations):** `check_video(segment-<n>.mp4, timing=narration/segment-<n>.timing.jsonl)` `ok` (container duration == narration + open); `check_srt(segment-<n>.srt)` and `check_srt(full.srt)` `ok` (timestamp-ordered, no overlap).
- **Sync (independent of the SRT author):** parse each SRT cue; assert the first cue start ≈ `config.OPEN_PADDING_S`; each cue's text equals the spoken-word sequence for that line taken from the segment's `.timing.jsonl` (markers stripped); last cue end ≈ `OPEN_PADDING_S` + narration end.
- **Full-video aggregate (AC #3 durations):** `full.mp4` container duration ≈ Σ clip durations + `config.RECAP_SECONDS`.

## 4. Determination, idempotency, error handling

- The offline narrator is deterministic (fixed beat shape, fixed strides) and offline; the topic brief is fixture-driven and deterministic; real encodes use the fixed `config` flags ⇒ byte-stable runs for identical inputs.
- No new failure modes: the narrator's WAV is generated from pure arithmetic; `--segments` is bounds-validated (`1..5`); absent artifacts fail `check` reports loudly rather than silently.
- The smoke skips (not fails) when ffmpeg/ffprobe aren't on PATH, matching the conftest idiom.

## 5. Testing of the slice itself

- `pytest tests/test_cli_pipeline.py` — the smoke (skips without ffmpeg).
- Full suite: `pytest` / `mypy vibe` / `ruff check vibe tests` clean (repo convention).

## 6. Files touched

- Modified: `vibe/narrate.py` (`offline_synthesizer`), `vibe/cli.py` (`--segments` + `VIBE_NARRATOR=offline`), `tests/conftest.py` (no change expected; verify `run_cli` env reachability for the new env value).
- New: `tests/test_cli_pipeline.py`, this design doc.
- **NOT modified:** `vibe/discover.py`, `vibe/render.py`, `vibe/assembly.py`, `vibe/shorts.py`, `vibe/check.py`, `vibe/config.py`.

## 7. Out of scope

Live edge-tts, heavy/full-5 renders, a dedicated new feed fixture (the existing `tests/fixtures/*.rss` suffice), and any non-CLI-seam (in-process) invocation of the pipeline stages.