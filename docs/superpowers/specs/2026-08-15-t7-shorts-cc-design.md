# T7 — Shorts (9:16) + CC sidecars: design

**Date:** 2026-08-15
**Branch:** `build/t7` (fresh worktree off `main` @ `94ce085`)
**Sources of truth:** `docs/specs/assembly.md` (vertical short render §2.4, CC sidecars §2.5, shorts assembly §7, determinism & layout §9, marker semantics §4), issue #16 (T7 AC), `CONTEXT.md` (Short = a segment repackaged). Consumes the T5/T6 artifacts: `build/segments/segment-<n>.mp4`, `build/narration/segment-<n>.mp3` + `.timing.jsonl`, `vibe/render.py` (caption mapping, encoder), `vibe/check.py` (`.srt` + `short`-kind validation).

## 1. Decisions (confirmed with the operating partner)

- **A separate subcommand `vibe shorts`** drives the whole stage, mirroring `vibe render`/`vibe assemble` as a single-entry-point idiom. `vibe assemble` stays as T6 left it (full video only); it does *not* gain shorts output. T7 consumes the already-built segment clips + narration and produces `shorts/` + `cc/`, so shorts can be re-rendered/checked independently of re-running assembly.
- **A clean-split module `vibe/shorts.py`** (approach A): a pure, fully-offline SRT builder + a vertical renderer that reuses `render.py`'s caption machinery and `ffmpeg_encoder`. The approved T5 `render.py` 16:9 path and the T6 `assembly.py` orchestrator are **not** changed. The only T7 code in `shorts.py` is two aspect-specific pieces (cover-fill paste + safe-zone caption draw) and the SRT/offset math.
- **Cover-fill center crop for the 9:16 art re-frame** (approach A on Q2): scale the hero so it covers 1080×1920 (`max(cw/hw, ch/hh) ≈ 1.78`), zoom-animate on top (`× spec.scale`), then center-crop. Never letterbox. The center-anchored title/tile art survives the crop (title drawn `mm`, segment tiles centered).
- **Playhead-aligned SRT timestamps** (approach A on Q3): every cue time = narration body timing **+ `OPEN_PADDING_S`**; the full-video SRT adds each segment's running clip start. Captions land where a viewer hears the word on the self-contained clip / full video.
- **Full-video running offsets use the contract duration formula** (`OPEN_PADDING_S + timing_end(segment)`), not ffprobe. Deterministic and offline (no live service / no probe), and consistent with `check.check_video`'s own expected-duration formula. A CC sidecar is not embedded, so there is no need to probe real `full.mp4`.

## 2. Module boundary

New module `vibe/shorts.py`, mirroring the repo seam pattern (pure core + injectable seams + orchestrator + thin CLI wiring). The two capabilities are cleanly split:

### Pure core (no I/O; no Pillow/ffmpeg import required to build SRT)

- `Cue = (start_s: float, end_s: float, text: str)` — one verbatim caption cue.
- `caption_cues(script_text: str, timing: Sequence[WordTiming], *, offset_s: float = 0.0) -> list[Cue]` — drives each script line through `render._caption_lines` (which already yields `CaptionLine`s with markers stripped from the surfaces). Per line: `start = first span.start_s`, `end = last span.end_s`, `text = " ".join(span.surface for span in spans)` (verbatim spoken line). Lines with no spoken words (`parse_caption_line` returns `None`) are skipped. Adds `offset_s` to every start/end.
- `_srt_block(n: int, start_s: float, end_s: float, text: str) -> str` — one cue as `n\nHH:MM:SS,mmm --> HH:MM:SS,mmm\ntext\n\n` (the grammar `check._srt_cues` already parses).
- `build_segment_srt(script_text: str, timing: Sequence[WordTiming]) -> str` — `caption_cues(..., offset_s=config.OPEN_PADDING_S)`, numbered `1..N`.
- `build_full_srt(segments: Sequence[tuple[str, Sequence[WordTiming]]]) -> str` — running offsets, renumbered continuously. Segment *k*'s cues get offset `Σ_{i<k}(OPEN_PADDING_S + timing_end(segment_i)) + OPEN_PADDING_S`. `timing_end(seg) = max(w.end_s)`. The contract duration of segment *i* is `OPEN_PADDING_S + timing_end(seg_i)`.
- `timing_end(timing: Sequence[WordTiming]) -> float` — `max(w.end_s, default=0.0)`; the pure building block for the full-video offsets.

### Seams / renderer

- `vertical_renderer(*, width: int = config.SHORT_WIDTH, height: int = config.SHORT_HEIGHT, font: object | None = None) -> render.ImageRenderer` — satisfies the existing `ImageRenderer` protocol (`(specs, hero, *, palette) -> tuple[bytes, ...]`), so it plugs straight into `render.render_segment`. Differences from `pillow_renderer`:
  - cover-fill paste (scale to cover + center-crop the canvas), with the zoom `spec.scale` applied on top;
  - captions drawn in the **lower safe zone** (baseline clear of the Shorts UI chrome, e.g. `~height - 380`; footline below it), with modest horizontal-reflow margins.
  - Same `config.PALETTE`, `resolve_font`, `_open_hero` reuse as `pillow_renderer`.
- Reuses the existing `render.ImageRenderer`/`render.Encoder`, `render.render_segment`, `render.read_timing`, `render.fake_renderer`/`fake_encoder`, `render.ffmpeg_encoder`, `render.make_hero`, `render._footline`, and `narrate.WordTiming`.

### Result model

- `ShortResult(index: int, status: str, ok: bool, message: str)` — mirrors `SegmentRenderResult`/`AssembleResult` for the printed run-down.

### Orchestrator

- `render_shorts(lay: layout.Layout, *, renderer: render.ImageRenderer, encoder: render.Encoder, font: object | None = None) -> list[ShortResult]` — the whole stage:
  1. Load `brief.json` + `scripts/index.json` (missing index → CLI exits 2 upstream). Ensure `hero.png` (`render.make_hero` if missing).
  2. For each `approved` segment: read script + timing + mp3; `clip = render.render_segment(text, timing, mp3, footline, hero, renderer=renderer, encoder=encoder, width=config.SHORT_WIDTH, height=config.SHORT_HEIGHT)`; atomic-write `shorts/short-<n>.mp4`. Newline: this uses the **same narration audio/timing** — a short is a repackaged segment, never a new idea (§7, CONTEXT.md). Non-approved segments are skipped and reported.
  3. Write `cc/segment-<n>.srt` (`build_segment_srt`) atomically per approved segment.
  4. Write `cc/full.srt` (`build_full_srt` over the approved segments, contract-formula durations) atomically last (after all segments, so its offsets are complete).
- `ShortResult` per segment (render + sidecar) and one for `full.srt`.

## 3. Output contract (assembly §9)

```
build/
  shorts/short-<n>.mp4        # native 1080x1920@30, same codecs, never letterboxed
  cc/segment-<n>.srt          # playhead-aligned verbatim, markers stripped
  cc/full.srt                 # running offsets across segments, renumbered
```

Each artifact is validatable: `check_artifact(shorts/short-<n>.mp4, kind="short")`, `check_srt(cc/segment-<n>.srt)`, `check_srt(cc/full.srt)`.

## 4. Determinism, error handling, testing

### Determinism

- SRT builder + offset math are pure → byte-identical for identical inputs. `.srt` written atomically (`_write_atomic`).
- Contract-formula durations keep `full.srt` offline + reproducible; no ffprobe, no live service.
- Vertical render uses the same fixed `config` encoder flags via `ffmpeg_encoder` → `short-<n>.mp4` is deterministic like `segment-<n>.mp4`. Re-render replaces that one clip (assembly §9 idempotent re-runs); no accumulation.

### Error handling (T5/T6 hygiene)

- Missing `scripts/index.json` → CLI exit 2.
- A segment render/read failure → recorded as `ok=False` in its `ShortResult`, CLI exit 1; no partial `short-<n>.mp4` (temp-then-rename per write).
- `render.RenderError`/`OSError`/`ValueError`/`KeyError` caught per segment and reported; other segments continue.

### Testing (offline, fixture-driven; T5/T6 rhythm)

- Pure unit tests (no ffmpeg/Pillow): `caption_cues` / `build_segment_srt` (verbatim, markers stripped, playhead offset, skip empty lines), `build_full_srt` (running offsets + continuous renumbering), each result passing `check.check_srt`. Contract-formula duration math.
- Render unit tests: `vertical_renderer` cover/crop geometry (fake renderer asserting width/height passed through + the cover-scale math); gated so real Pillow/ffmpeg only run when available.
- `render_shorts` with fake seams over a fixture build (mirror `test_assembly._write_fixture_build`): writes `short-1.mp4` + `cc/segment-1.srt` + `cc/full.srt`; skips non-approved; reports failures.
- CLI `vibe shorts` (fake seam): fixture build → writes shorts + cc, exit 0; missing index → exit 2.
- **Gated real test** (ffmpeg present): a tiny real 1080×1920 short conforms to `check_video(kind="short")`; its `.srt` passes `check_srt`.
- Full suite before green: `pytest` / `mypy vibe` / `ruff check vibe tests` clean.

## 5. Files touched

New: `vibe/shorts.py`, `tests/test_shorts.py`, `tests/test_cli_shorts.py`.
Modified: `vibe/cli.py` (`shorts` subcommand + `_cmd_shorts`), `tests/conftest.py` (reuse the fixture-build idiom; no new seam env needed beyond existing `VIBE_RENDERER=fake`), `docs/specs/assembly.md` (implementation note, matching T5/T6 style).
Docs: this design spec + the T7 plan (`docs/superpowers/plans/2026-08-15-t7-shorts-cc-stage.md`), `#T7` commit tags.

**NOT modified:** `vibe/render.py`, `vibe/assembly.py`, `vibe/config.py`, `vibe/check.py` (all required contract + seams already exist).

## 6. Out of scope

- Upload/publish to YouTube, end-screen elements, background music, spoken CTA (assembly §10) — unchanged.
- E2E smoke at the CLI seam in CI — T8 (#14), which will consume T7's output.
- A bundled/real font, and choosing a Shorts title card — deferred (existing deterministic default + hero reuse).