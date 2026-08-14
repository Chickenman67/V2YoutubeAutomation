# T5 — Segment render (16:9, captions burned): design

**Date:** 2026-08-14
**Branch:** `build/t5` (forked from `main`, which holds merged T1–T4)
**Sources of truth:** `docs/specs/assembly.md` (render contract §2.2, amended excursion §3, caption burn-in §4), `docs/research/design-standard.md` (§5 typography, §6 palette, §8 captions), `docs/research/toolchain-split.md` (PIL owns hero stills), `docs/specs/narration.md` + `vibe/narrate.py` (marker→word-timing contract), `vibe/check.py` (the media contract the clip must satisfy), `vibe/config.py` (fixed encode flags / OPEN_PADDING_S).

## 1. Decisions (confirmed with the operating partner)

- **Renderer is PIL-frames + ffmpeg, not Remotion.** The operating partner chose this over the toolchain-split research's Remotion recommendation. Rationale: keeps `vibe` a single self-contained, offline-verifiable Python build; reuses the `narrate.py` pure-core + injectable-seam pattern; every T5 acceptance criterion is satisfiable (self-contained 1920x1080@30 clip, t=0 zoom open, caption/footline burn-in, muxed audio, duration from timing.jsonl); and it fits the fixture-based offline E2E in the plan (T8). This amends `toolchain-split.md`'s "PIL for animation is uneconomical" verdict **only as a stage-specific, deterministic-build trade**; it is recorded here, not as a repo-wide override. Do not edit `toolchain-split.md` without a ticket.
- **Real ffmpeg is the default encoder.** Like T4's real TTS/ffmpeg, the render genuinely encodes with ffmpeg (already on the system and used by `check.py`/`narrate.py`) using the fixed `VIDEO_ENCODE_FLAGS`/`AUDIO_ENCODE_FLAGS`/`MUX_FLAGS` from `config.py`. Tests stay offline/cheap via an injected fake frame-renderer + fake encoder; the real encoder is exercised by a small, gated integration test.
- **Pillow is a new runtime dependency.** It is the only newly added dependency (`Pillow`). font rendering, hero still, and per-frame compositing all use it. Type-checked strictly like the rest.
- **`vibe render` is a distinct subcommand**, symmetric with `vibe narrate`, operating on `approved` segments in `build/scripts/index.json`. `make` stays fast/offline; `render` is where the heavy frame encode lands.
- **Fonts resolve through a seam, defaulting to a deterministic fallback.** Typography is spec-grade (design-standard §5) when a real outline font is available and best-effort otherwise. See §2 (Fonts). This keeps the build offline and deterministic on any machine.
- **Footline source = the topic brief's publisher.** A figure's source footline is "Source: {publisher}" derived from `brief.json → topic_brief.sources[].publisher` (the only source datum present; design-standard §8 example "Source: BLS, 2025"). No figure → no footline. Resolves the "where does the source text come from" gap deterministically.

## 2. Module boundary

New pure module `vibe/render.py` plus a thin CLI seam, mirroring `vibe/narrate.py`. Split: all layout/timing/geometry is **pure** (offline-testable); image drawing and the ffmpeg encode are **protocol seams**.

### Pure core (no I/O; math + layout only)

- `Palette` — frozen dataclass of the design-standard §6 colours from `config.py` (bg paper, ink, positive, risk, gold, and their role-names). Single source of truth so `vibe check`/`manifest` can also reference it later.
- `CaptionWord` — `(surface: str, kind: ChunkKind, start_s: float, end_s: float, figure: bool)`; the unit the frame planner timestamps. Built by aligning each `Chunk` (from `narrate.parse_line`) to the cumulative word timings: the chunk's text words get their own `WordTiming` rows, and the chunk's `kind` + `figure` flag rides along. `kind ∈ {base, keyword, figure, gold}` (pause produces no caption).
- `parse_caption_line(line, timing) -> CaptionLine` — one caption line = the surface of one script line with its marker styling metadata and its word-time bounds (`first_word.start_s`, `last_word.end_s`). Markers stripped; `**keyword**` → `kind keyword`; `##…##` → `kind figure`; `**gold**` → `kind gold`.
- `zoom_scale(t: float) -> float` — deterministic ease-out curve for the t=0..`OPEN_PADDING_S` open (§3). Tests assert exact values.
- `HOOK_START_S = config.OPEN_PADDING_S` — narration body starts at ~1.15 s.
- `Caption` (a stylised line on screen at time `t`): `(spans: tuple[StyledSpan,...], figure: StyledSpan | None, footline: str | None)` where `StyledSpan = (text, kind)`.
- `plan_frames(timing, lines, *, fps, width, height) -> tuple[FrameSpec]` — the deterministic plan: one `FrameSpec` per video frame.
  `FrameSpec = (frame_index: int, t: float, scale: float, caption: CaptionSet)`.
  `CaptionSet = (items: tuple[Caption,...], hold_until_s: float)` — the caption(s) active at `t`, with a readability floor: a caption stays on ≥ `MIN_CAPTION_HOLD_S` (default 1.2 s, design-standard §4/assembly §4) after its last word, and a figure's spans are forced visible across `[figure_word.start_s, figure_word.end_s]` so the figure lands on the spoken second (design-standard §8).
  - During the open (`t < HOOK_START_S`): caption empty, `scale = zoom_scale(t)`; the hero zoom is the only motion.
  - During the body: for each active caption, `span` field carries styled text; `scale` = hold value.
  - The private helper `_active_captions(t, lines)` is a pure, unit-testable function (see Task 4).
- `fonts_available(font): bool` and `resolve_font(size: int, *, font) -> Any` — resolve a PIL font; real path uses `ImageFont.truetype`; fallback is `ImageFont.load_default(size=...)` (Pillow ≥ 10.1). Determinism holds because the resolution depends only on `font` (a path-like or `None`).

### Seams (Protocols)

- `ImageRenderer.__call__(specs: tuple[FrameSpec,...], hero: object, palette: Palette) -> tuple[bytes,...]` — draws every frame into raw RGB bytes (`width*height*3`) using Pillow. Real `pillow_renderer(*, font)`; **fake** `fake_renderer()` returns canned bytes (e.g. `b"frame-<i>"` repeated) so tests never construct images.
- `make_hero(brief, *, renderer, font) -> bytes` — the 16:9 title still (PNG bytes) from the brief title/segment titles + palette. Owner: PIL (toolchain-split §2.3). Returns the same deterministic pixels every run (no date/seed).
- `Encoder.__call__(frames: tuple[bytes,...], *, width, height, fps, audio: bytes) -> bytes` — encodes raw RGB frames + muxes the narration mp3 into a `.mp4` honoring `config` fixed flags. Real `ffmpeg_encoder()` feeds `rawvideo` frames on stdin; **fake** `fake_encoder()` returns fixed bytes for tests.

### Orchestrator

- `SegmentRenderResult` — `(index: int, status: str, ok: bool, message: str)`; same shape as `narrate.SegmentResult` so the CLI loop is identical.
- `render_segment(lay, index, *, renderer, encoder) -> RenderResult` — reads the approved script, narration timing + mp3, the (already-built) `hero.png`; computes `plan_frames`; draws via `renderer`; muxes via `encoder`; returns in-memory `mp4_bytes` (no I/O, like `narrate_segment`).
- `render_hero(lay, *, brief, renderer, font) -> Path` — writes `build/hero.png` (temp-then-rename) once, deterministically, from the brief (assembly §2.3: identical across segments).
- `render_approved(lay, *, renderer, encoder, hero) -> list[SegmentRenderResult]` — ensures `hero.png` exists (render it if missing), then for each `approved` segment renders and writes `build/segments/segment-<n>.mp4` (temp-then-rename so no partial clip); skips `needs-human`/not-approved with a warning; on failure reports `ok=False`, writes nothing for that segment, and completed segments stay (best-effort, mirroring `narrate`).

## 3. Timeline & the amended excursion (assembly §3, §2.2)

Per segment, container duration = `OPEN_PADDING_S` (1.15 s) + narration duration (last word end, from `.timing.jsonl`) (+ no fixed tail; the concat chain is pure stream-copy so each clip simply ends at its body end):

- **t ∈ [0, 0.8] s** — mute **zoom open**: the hero still is scaled from `1.0 → 1.10` (ease-out), so the video opens in media res on motion (no spoken-title hold). No caption.
- **t ∈ (0.8, 1.15) s** — zoom holds at its end value; still mute; first narration word lands at the next boundary by construction (timing.jsonl starts at body start).
- **t ≥ ~1.15 s (body)** — narration audio is audible (muxed), captions track words, keyword partial-bolding + accent colour; a spoken figure appears on its spoken second with a 24 px `Source: {publisher}` footline.
- **Hard cut into the next segment** — because every clip is self-contained and the concat is copy-only, consecutive clips join by hard cut; there is no zoom-out and no inter-segment hero rest (assembly §3). This is enforced by T6 (concat), not T5; T5 only guarantees each clip starts with the zoom open and ends at its body end.
- **Dead-air policy** — all-elements-silent stretches < ~2 s: the 0.8 s mute zoom is under budget and has motion; the recap card is T6's designed exemption.

This satisfies the "no silent intro, no title hold, hook lands ≈ 1.15 s" acceptance criteria.

## 4. Caption burn-in (assembly §4, design-standard §8)

- **One glanceable line per script line**, partial-bolding + accent on `**keyword**`, gold on `**gold**`, tabular accent (larger) on `##figure##`.
- **Caption holds** track the line being spoken and stay ≥ `MIN_CAPTION_HOLD_S` = 1.2 s (readability floor) after the line's last word.
- A **figure spoken must appear in the same second** (finance rule): the figure's spans are clamped to its own word timing, guaranteed by the force-visible rule in `plan_frames`.
- **Source footline** 24 px (`Source: {publisher}`) on-screen for every figure, same second as the figure.
- **Markers never rendered**; `~` pauses produce only a timing gap (no caption).
- Full verbatim is never burned in — it ships as T7's CC sidecars.

## 5. Artifacts & build layout

- Add a `segments` dir property + hero to the layout. `build/segments/` already exists in `_LAYOUT_DIRS`; add `Layout.hero -> Path` = `root / "hero.png"`.
- Per **approved** segment `n`:
  - `build/segments/segment-<n>.mp4` — self-contained 1920x1080@30, H.264 high/`yuv420p`, `-crf 18`, AAC-LC 44.1k stereo, captions + footlines burned, duration ≈ `OPEN_PADDING_S + narration_end` (verifiable with `vibe check --kind clip --timing …`).
- `build/hero.png` — 1920×1080 designed title still, the zoom base, produced once.
- Fonts: optionally bundled under `vibe/assets/` (TTF) if one is available in the repo; otherwise the deterministic `ImageFont.load_default(size=N)` fallback is used and typography is documented best-effort. A `VIBE_FONT` (path) env override lets a human point at a real TTF without code changes.

## 6. CLI wiring: `vibe render`

New subcommand in `vibe/cli.py`, symmetric with `vibe narrate`:

```
vibe render              # render all approved segments in ./build
vibe render --build DIR  # point at a different build root (default ./build)
```

- Reads `build/scripts/index.json`. Missing index/build → message + exit 2.
- Ensures `hero.png`; renders each `approved` segment one at a time.
- Success per segment: writes `.mp4`, prints `segment-<n>.mp4: OK`.
- `needs-human` / not approved → stderr `segment-<n>.mp4: skipped (<status>)`, continue, exit 0 (best-effort precedent).
- ffmpeg/render failure → stderr, exit non-zero, no partial clip (temp-then-rename). Already-written segments remain.
- Test seam: `VIBE_RENDERER=fake` selects the fake renderer+encoder so CLI tests are offline (same idiom as `VIBE_NARRATOR`).

## 7. Determinism, error handling, testing

### Determinism

- Pure-core outputs (`parse_caption_line`, `zoom_scale`, `plan_frames`, `resolve_font`, artifact ordering) are fully deterministic and unit-tested.
- The real ffmpeg encode is byte-identical for identical raw frames (fixed flags, fixed order). The fake renderer/encoder make the whole pipeline byte-deterministic in tests.

### Error handling

- Missing index/build → exit 2.
- Missing narration artifact (no `.mp3`/`.timing.jsonl` for an approved segment) → `RenderError`, exit 1, no partial clip.
- ffmpeg failure → `RenderError` → exit 1, no partial clip.
- `needs-human`/unapproved → skip + warning, exit 0.
- `hero.png` integrity: if missing it is re-rendered deterministically; never partially written (temp-then-rename).

### Testing (offline, fixture-driven)

- `parse_caption_line`: per marker — `**keyword**`, `##figure##`, `**gold**`, mixed, no markers — asserting span kinds, no markers in surface, word bounds from a timing fixture.
- `zoom_scale`: exact values at t=0, 0.8, and hold.
- `_active_captions`/`plan_frames`: a caption is active across `[first_word.start, last_word.end + MIN_CAPTION_HOLD_S]`; never two overlapping unrelated captions at the same `t`; figure spans force-visible exactly on the figure word's window; empty caption during the open.
- `render_segment` with fake renderer+encoder: returns fixed mp4 bytes; no I/O.
- `render_approved` (fake seams): approved → `.mp4` written; `needs-human` → skipped; narration missing → `ok=False`, no `.mp4`.
- CLI `vibe render` with `VIBE_RENDERER=fake`: writes `.mp4`; then `vibe check --kind clip --timing …` passes **only if** fake encode emits a real muxed clip — so the CLI test uses a **tiny real ffmpeg encode** (gated, `ffmpeg` present) or asserts presence/exit code with the fake, per the plan Task 7.
- Image/encode integration (gated, `ffmpeg` present): one tiny real clip at small resolution whose codec/resolution/audio pass `check_video` (kind `clip`), confirming the encoder honors `config` flags.
- New pyproject dep: `Pillow`; tests never import Pillow except behind the seam or in gated image tests.
- Full suite: `pytest` / `mypy vibe` / `ruff check vibe tests` all clean.

## 8. Files touched

New: `vibe/render.py`, `tests/test_render.py`, `tests/test_cli_render.py`, optionally `tests/fixtures/` fixture script/timing files, optionally `vibe/assets/` (font).
Modified: `vibe/layout.py` (add `Layout.hero`), `vibe/config.py` (palette + render constants + `MIN_CAPTION_HOLD_S`, `ZOOM_END`, ease params), `vibe/cli.py` (add `render` subcommand + fake seam env), `tests/conftest.py` (test seam env), `pyproject.toml` (add `Pillow`), `docs/specs/assembly.md` (note the PIL-frames renderer as the adopted T5 implementation, amending the Remotion mention).

## 9. Out of scope (for this ticket)

- Full-video concat, recap card, shorts (9:16), CC sidecars — T6/T7.
- E2E smoke at the CLI seam — T8.
- Parallelization of per-segment renders.
- Upload / background music / spoken CTA (assembly §10).
- Choosing or licensing a bundled font (deferred; default deterministic fallback used until then).