# T5 — Segment Render Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn approved per-segment scripts + narration (`.mp3` + `.timing.jsonl`) + a hero still into self-contained 1920x1080@30 clips (`build/segments/segment-<n>.mp4`) with keyword captions + source footlines burned in and narration audio muxed, via a new `vibe render` subcommand.

**Architecture:** A new pure module `vibe/render.py` owns caption-line parsing (`parse_caption_line`), the t=0 zoom easing (`zoom_scale`), the word-timed caption planner (`plan_frames`), and the per-segment/approved orchestrators (`render_segment`, `render_approved`). Image drawing (`ImageRenderer`) and the mux/encode (`Encoder`) live behind two injectable `Protocol` seams, exactly like `Synthesizer`/`Encoder` in `narrate.py`. Real rendering uses Pillow + ffmpeg (fixed `config` flags); fake seams keep every test offline and deterministic. `vibe render` reads `build/scripts/index.json`, renders only `approved` segments, and skips `needs-human` (never auto-shipping).

**Tech Stack:** Python 3.11+, stdlib plus **Pillow** (new runtime dep) and ffmpeg/ffprobe (already used by `check.py`/`narrate.py`). pytest 8, mypy strict, ruff.

## Global Constraints

- Python `>=3.11`; mypy `strict`; ruff `line-length=100`, target `py311`. Add **exactly one** new runtime dependency: `Pillow`.
- Offline: tests never reach the network and never render full-time/res clips. `tests/conftest.py` sets `VIBE_OFFLINE`; CLI render tests pass `VIBE_RENDERER=fake`. A single gated integration test (real ffmpeg, tiny frames/res) exercises the real encoder; it skips when `ffmpeg` is absent.
- Domain vocabulary per `docs/specs/assembly.md` / `docs/research/design-standard.md`: palette (bg `#F7F4EF`, ink `#1B1F27`, positive `#1F9D82`, risk `#E4572E`, gold), caption sizes (caption ~44–56px, source footline 24px at 1080p), `OPEN_PADDING_S = 1.15`, body starts at ~1.15s, caption hold floor `MIN_CAPTION_HOLD_S = 1.2`.
- Markers are never rendered; `~` only produces a timing gap.
- Determinism: pure-core outputs deterministic + unit-tested; real encode byte-identical for identical frames; fake seams make the pipeline byte-deterministic in tests.
- `vibe render` is best-effort like `narrate`: skip/fail reported, completed segments stay, missing index exits 2, error exits non-zero with no partial clip (temp-then-rename).
- Follow existing patterns: pure stage module with typed injectable seam; thin CLI wiring; per-task commits on branch `build/t5`. One commit per task, message prefixed `T5: `.

---

### Task 1: Layout `hero` property + render/palette config constants

**Files:** Modify `vibe/layout.py` (`Layout.hero`), `vibe/config.py` (palette + render constants). Test `tests/test_render.py` (new; step 3 grows it).

**Interfaces:**
- `Layout.hero -> Path` (== `root / "hero.png"`).
- `config.RENDER_*` constants + `config.PALETTE` (design-standard §6):
  - `PALETTE: dict[str, str]` = `{"bg": "#F7F4EF", "ink": "#1B1F27", "positive": "#1F9D82", "risk": "#E4572E", "gold": "#D4AF37"}`
  - `MIN_CAPTION_HOLD_S = 1.2`, `ZOOM_START = 1.0`, `ZOOM_END = 1.10`, `ZOOM_SECONDS = 0.8`
  - `CAPTION_SIZE = 48`, `FOOTLINE_SIZE = 24`
- Write failing tests, run to fail, implement, pass, lint, commit `T5: layout hero dir + render/palette constants (#T5)`. (One `Layout.hero` unit assert; one `PALETTE`/`MIN_CAPTION_HOLD_S`/`ZOOM_END` assert.)

---

### Task 2: Caption-line parsing (`parse_caption_line`) + zoom easing (`zoom_scale`)

**Files:** `vibe/render.py` (types + `parse_caption_line` + `zoom_scale`); `tests/test_render.py`.

**Interfaces:**
- Reuse `narrate.parse_line`, `narrate.Chunk`, `narrate.ChunkKind` (import from `.narrate`; do not duplicate the regex).
- `CaptionWord = NamedTuple(surface: str, kind: ChunkKind, start_s: float, end_s: float)`
- `CaptionLine = NamedTuple(spans: tuple[CaptionWord,...], start_s: float, end_s: float, has_figure: bool)`
- `parse_caption_line(line: str, timings: Sequence[WordTiming]) -> CaptionLine | None` — parse the line into chunks; align chunk-text words to the (monotonic) timings so each spoken word carries a `kind`/`figure` flag; markers stripped; `pause` chunks contribute no words. `None` if the line has no spoken words.
- `zoom_scale(t: float) -> float` — ease-out (cubic) `ZOOM_START→ZOOM_END` over `[0, ZOOM_SECONDS]`, then holds `ZOOM_END`. Round to 4 decimals. `t < 0` clamps to `ZOOM_START`.
- Tests (failing → pass): exact `zoom_scale` at `t = 0`, `0.4`, `0.8`, `2.0`; `parse_caption_line` for `**keyword**`, `##figure##`, `**gold**`, mixed, none (awaiting a timing fixture in Task 3/4 — implement a tiny inline `WordTiming` list in the test). Commit `T5: caption-line parsing + zoom easing (#T5)`.

---

### Task 3: Word-timed caption planner (`_active_captions`, `plan_frames`)

**Files:** `vibe/render.py`; `tests/test_render.py`.

**Interfaces:**
- `StyledSpan = NamedTuple(text: str, kind: ChunkKind)`
- `Caption = NamedTuple(spans: tuple[StyledSpan,...], figure: StyledSpan | None, footline: str | None)`
- `FrameSpec = NamedTuple(frame_index: int, t: float, scale: float, caption: Caption | None)`
- `_active_captions(t: float, lines: Sequence[CaptionLine], *, min_hold: float = MIN_CAPTION_HOLD_S) -> list[CaptionLine]` — pure: a line is active while `t ∈ [line.start_s, line.end_s + min_hold]`. Exactly the caption(s) whose window covers `t`; if two overlap (shouldn't from monotonic timings), the later one wins.
- `plan_frames(timings, lines, *, fps, width, height, footline: str | None = None) -> tuple[FrameSpec,...]` — for each frame `i` at `t = i / fps` up to `ceil(duration * fps)` (duration = last word end + `MIN_CAPTION_HOLD_S`, floored to a frame boundary): `scale = zoom_scale(t)`, caption = active line (None during the open `t < HOOK_START_S`), footline carried on the caption only when `has_figure`. Figure spans are already inside `line.spans`; the planner does **not** need a separate force-visible step because word timing IS the force — assert in tests that the figure's CaptionWord window is a subrange of the line window.
- Tests: caption active only inside its window (+hold); figure word present and its `[start,end]` is within the caption window; empty caption during open; last frame count = `ceil(duration*fps)`; monotonic `t`. Commit `T5: word-timed caption planner (#T5)`.

---

### Task 4: Fonts + seams (`ImageRenderer`/`Encoder` protocols + deterministic fakes)

**Files:** `vibe/render.py`; `tests/test_render.py`.

**Interfaces:**
- `resolve_font(size: int, *, font: str | None) -> Any` — if `font` given, `ImageFont.truetype(font, size)`; else `ImageFont.load_default(size=size)` (Pillow ≥ 10.1). Wrapped so calling it is safe offline.
- `class ImageRenderer(Protocol): def __call__(self, specs: tuple[FrameSpec,...], hero: object, *, palette: dict[str, str]) -> tuple[bytes,...]: ...`
- `make_hero(brief: dict[str, object], *, font: object | None = None) -> bytes` — the 16:9 title still (PNG), drawn directly with Pillow (a still needs no `ImageRenderer` seam); real impl in Task 5. Pillow is declared + installed in this task since `resolve_font` needs it.
- `class Encoder(Protocol): def __call__(self, frames: tuple[bytes,...], *, width: int, height: int, fps: int, audio: bytes) -> bytes: ...`
- `fake_renderer() -> ImageRenderer` — each frame → `b"frame-<i>"` (deterministic).
- `fake_encoder() -> Encoder` — returns `b"fake-mp4"`.
- Tests (fail → pass): `resolve_font` returns a callable font for both `None` and a path (use a tmp font path? no — assert it does not raise for `None`; for a path, skip if unavailable); `fake_renderer`/`fake_encoder` deterministic. Commit `T5: render seams + deterministic fakes (#T5)`.

---

### Task 5: Real PIL renderer + `make_hero`

**Files:** `vibe/render.py`, `pyproject.toml` (`Pillow`); `tests/test_render.py` (gated).

**Interfaces:**
- Add `pyproject.toml` `dependencies = ["edge-tts", "Pillow"]`.
- `_draw_caption(draw, caption: Caption | None, *, font, palette, width)` — centered single-line caption; per-span colour/font (keyword → bold+`positive`, figure → `risk`+larger size, gold → `gold`); strips markers (already stripped by `parse_caption_line`); wraps if wider than width (best-effort two-line). Draws the `Source: …` footline 24px at the lower safe zone when `caption.footline`.
- `pillow_renderer(*, font: object | None = None, palette: dict[str, str] | None = None) -> ImageRenderer` — for each `FrameSpec`: create an RGB `Image.new` (paper bg), `paste` the `hero` scaled by `spec.scale` around center-crop, draw the active caption via `_draw_caption`, return `image.tobytes()`.
- `make_hero(brief, renderer=pillow_renderer(), font=None) -> bytes` — 1920×1080 paper-bg still with the brief title + segment titles as a designed title card (the zoom subject), no date/seed → deterministic bytes.
- Gated test (Pillow present, no network): `make_hero` returns 1920×1080×3 bytes on repeated calls byte-identical; a `pillow_renderer`-produced single frame has the right byte length `1920*1080*3`. Do **not** render multi-second clips here (slow). Commit `T5: real PIL renderer + hero still (#T5)`.

---

### Task 6: Real ffmpeg encoder + orchestrators (`render_segment`, `render_approved`)

**Files:** `vibe/render.py`; `tests/test_render.py` (gated, ffmpeg).

**Interfaces:**
- `class RenderError(RuntimeError)`.
- `ffmpeg_encoder(*, fps: int = config.FPS) -> Encoder` — feeds raw `rgb24` frames via stdin to ffmpeg (`-f rawvideo -pix_fmt rgb24 -s WxH -r fps -i pipe:0`, then `-c:v libx264` + `config.VIDEO_ENCODE_FLAGS[1:]`, `-c:a` mux from `pipe:1` or an mp3 path input, `-shortest`), `-movflags +faststart`. Wraps `OSError`/non-zero in `RenderError`. (Mirror `narrate._decode_mp3`/`ffmpeg_encoder` hygiene: `check=False`, capture stderr, `# type: ignore` for bytes.)
- `SegmentRenderResult = dataclass(index, status, ok, message)` (shape mirrors `narrate.SegmentResult`).
- `render_segment(script_text: str, timing: Sequence[WordTiming], mp3: bytes, footline: str | None, hero: bytes, *, renderer, encoder, fps=config.FPS, width=config.FULL_WIDTH, height=config.FULL_HEIGHT) -> bytes` — parse lines → captions → `plan_frames` → `renderer(...)` → `encoder(...)` muxed with `mp3` (audio delayed `-itsoffset OPEN_PADDING_S`). Pure-ish (no disk reads) so directly unit-testable with fakes.
- `render_approved(lay, *, renderer, encoder, hero: bytes) -> list[SegmentRenderResult]` — read index; for each `approved`: read script `.txt`, timing `.jsonl` (`parse_timing` via a small local reader reusing `check._parse_timing` or its own `read_timing`), mp3 bytes, footline from `brief` publisher; call `render_segment`; atomic-write `build/segments/segment-<n>.mp4`; skip others; `needs-human` → skip warning; `RenderError`/`OSError` → `ok=False`, no partial.
- Tests (fakes): `render_segment` returns fixed bytes; `render_approved` writes `.mp4` for approved, skips needs-human, `ok=False` + no file when narration missing. Gated (ffmpeg): one tiny real clip (`vibe` pure path with a 2-frame `plan_frames` at low res via a reduced test helper or by passing small `width/height` through `render_segment`) → `probe_media`/`check_video(kind="clip")` passes (codec/res/audio/duration). Commit `T5: real ffmpeg encoder + per-segment orchestra (#T5)`.

---

### Task 7: CLI wiring (`vibe render`) + fake seam

**Files:** `vibe/cli.py`, `tests/test_cli_render.py`.

**Interfaces:**
- `_select_renderer() -> tuple[render.ImageRenderer, render.Encoder]` — `VIBE_RENDERER == "fake"` → `(fake_renderer(), fake_encoder())`, else `(pillow_renderer(), ffmpeg_encoder())`.
- `vibe render [--build DIR]` (default `./build`). Exit codes: `0` success (incl. skips), `2` missing index, `1` any segment error or missing narration.
- Handler `_cmd_render`: load `Layout`, ensure `hero.png` (render via `make_hero` if missing), iterate `render_approved`, print `segment-<n>.mp4: OK` / skip / error, `return 1 if failed else 0`.
- Move `Layout.hero` use: hero written under `build/hero.png` (temp-then-rename) before segment loop.
- Tests (`VIBE_RENDERER=fake`): a fixture build (`make` + fake `narrate`) → `render` writes `segment-<n>.mp4`, exit 0; `needs-human` skipped → exit 0; missing index → exit 2. Note: with the fake encoder the `.mp4` is `b"fake-mp4"`, so do **not** run `check` on it (that needs a real mux); the real-encode-and-check coverage lives in Task 6 and the offline E2E (Task 8). Commit `T5: wire vibe render subcommand + fake seam (#T5)`.

---

### Task 8: Final verification, spec alignment, docs

**Files:** `docs/specs/assembly.md` (§2.2 renderer note), `docs/superpowers/specs/2026-08-14-t5-segment-render-design.md` (align), this plan (align).

- [ ] **Verify full checks + offline E2E** — `pytest` / `mypy vibe` / `ruff check vibe tests` all clean, from the `build-t5` worktree.
  Offline E2E (fake renderer encodes shell only — real `.mp4` comes from the gated integration or the live path; here assert presence/exit):
  ```powershell
  $env:VIBE_OFFLINE='1'
  .\.venv\Scripts\python -m vibe make "mortgage rates" --feeds-from tests/fixtures
  $env:VIBE_NARRATOR='fake'; .\.venv\Scripts\python -m vibe narrate
  $env:VIBE_RENDERER='fake'; .\.venv\Scripts\python -m vibe render
  Get-ChildItem build\segments
  Remove-Item Env:\VIBE_RENDERER; Remove-Item Env:\VIBE_NARRATOR; Remove-Item Env:\VIBE_OFFLINE
  ```
  Optional **live/real** smoke (network + real TTS + real ffmpeg render, do NOT run in CI): clear `VIBE_NARRATOR` and `VIBE_RENDERER`, run `vibe narrate` then `vibe render`, confirm real `.mp4`, exit 0; `vibe check build\segments\segment-1.mp4 --kind clip --timing build\narration\segment-1.timing.jsonl` prints `OK (clip)`.
- [ ] **Update `docs/specs/assembly.md`** — add a one-line implementation note in §2.2 recording that T5 renders via the adopted PIL-frames + ffmpeg path (amending the Remotion assumption in `toolchain-split.md`); do not edit `toolchain-split.md` itself without a ticket.
- [ ] **Align design doc** — verify every named function/type/constant in the design spec exists as implemented; fix drift. Commit.
- [ ] **Push branch** `git push -u origin build/t5`. Report: branch, suite green, the fake seam + gated real-encode verified, footline hazard (figures require an author that emits `##figure##`, else no footline) documented, and the live render flagged as the network/real-time verification left for a human/CI.