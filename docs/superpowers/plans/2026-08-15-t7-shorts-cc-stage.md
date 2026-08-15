# T7 — Shorts (9:16) + CC sidecars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `vibe shorts` stage that natively re-renders each approved segment to 1080×1920 (never letterboxed) and writes verbatim, playhead-aligned CC sidecars: one per segment plus one for the full video with running offsets.

**Architecture:** New `vibe/shorts.py` with a pure, offline SRT builder (verbatim markers-stripped cues derived from `render._caption_lines`) and a `vertical_renderer` (cover-fill center crop of the hero + lower-safe-zone captions) that reuses `render.py`'s caption/encoder machinery and the existing `config.SHORT_WIDTH/HEIGHT` + `check` `short`/`.srt` validation. Full-video SRT offsets use the contract duration formula (`OPEN_PADDING_S + timing_end`), so the whole stage is deterministic and offline. `render.py`, `assembly.py`, `config.py`, `check.py` are untouched.

**Tech Stack:** Python 3.11+ (strict mypy, ruff), Pillow (frame compositing), ffmpeg (encode via existing `render.ffmpeg_encoder`), pytest.

## Global Constraints

- `requires-python = ">=3.11"`; strict mypy (`python_version = "3.11"`, `strict = true`); ruff `line-length = 100`, `target-version = "py311"`.
- Reuse, never reinvent: SRT validation is `check.check_srt` / `check.check_artifact`; captions already stripped by `render.parse_caption_line`; encoder is `render.ffmpeg_encoder` (fixed `config` flags).
- Never letterbox. A short is a repackaged segment (same narration audio + timing; `CONTEXT.md`).
- Markers (`**`, `##`, `~`) are structural and must never appear in SRT text.
- Do not modify `vibe/render.py`, `vibe/assembly.py`, `vibe/config.py`, or `vibe/check.py`.
- Shorts resolution is `config.SHORT_WIDTH × config.SHORT_HEIGHT` = 1080×1920@30, same video/audio codec parameters as §2.2.
- All writes atomic (`render._write_atomic`); build layout dirs `shorts/`, `cc/` already created by `layout.create_layout`.

---

### Task 1: Pure SRT core (`vibe/shorts.py`)

**Files:**
- Create: `vibe/shorts.py` (pure part only, this task)
- Test: `tests/test_shorts.py`

**Interfaces:**
- Consumes: `render._caption_lines(script_text, timing) -> list[CaptionLine]` (private, accessible); `narrate.WordTiming(word, start_s, end_s)`; `config.OPEN_PADDING_S`; `check.check_srt(path)`.
- Produces:
  - `caption_cues(script_text: str, timing: Sequence[WordTiming], *, offset_s: float = 0.0) -> list[tuple[float, float, str]]` — `(start_s, end_s, verbatim_text)` per spoken script line, markers stripped, all cues shifted by `offset_s`.
  - `timing_end(timing: Sequence[WordTiming]) -> float` — `max(w.end_s, default=0.0)`.
  - `_srt_block(n: int, start_s: float, end_s: float, text: str) -> str` — one SRT cue block.
  - `build_segment_srt(script_text: str, timing: Sequence[WordTiming]) -> str` — playhead-aligned (`offset_s = config.OPEN_PADDING_S`) full SRT document, cues numbered `1..N`.
  - `build_full_srt(segments: Sequence[tuple[str, Sequence[WordTiming]]]) -> str` — running offsets across segments, renumbered continuously.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_shorts.py
from __future__ import annotations

from pathlib import Path

from vibe import check, config
from vibe.narrate import WordTiming
from vibe.shorts import build_full_srt, build_segment_srt, caption_cues, timing_end


def _words(*items: tuple[str, float, float]) -> list[WordTiming]:
    return [WordTiming(w, s, e) for w, s, e in items]


def test_timing_end():
    assert timing_end(_words(("a", 0.0, 0.2), ("b", 0.2, 0.5))) == 0.5
    assert timing_end([]) == 0.0


def test_caption_cues_verbatim_with_offset():
    timing = _words(("the", 0.0, 0.2), ("rates", 0.2, 0.4), ("climbed", 0.4, 0.6))
    assert caption_cues("the **rates** climbed", timing, offset_s=config.OPEN_PADDING_S) == \
        [(1.15, 1.75, "the rates climbed")]


def test_caption_cues_strips_markers_always():
    timing = _words(("Money", 0.0, 0.2), ("fast", 0.2, 0.4), ("hop", 0.4, 0.6))
    for _, _, text in caption_cues("~ Money ##5.25## **fast** ~ hop", timing, offset_s=0.0):
        assert "*" not in text and "#" not in text


def test_caption_cues_skips_line_with_no_spoken_word():
    timing = _words(("done", 0.5, 0.7))
    assert [t for _, _, t in caption_cues("**gold**\ndone", timing, offset_s=0.0)] == ["done"]


def test_build_segment_srt_playhead_aligned(tmp_path: Path):
    timing = _words(("hello", 0.0, 0.2), ("world", 0.2, 0.5))
    text = build_segment_srt("hello world", timing)
    assert text.startswith("1\n00:00:01,150 --> 00:00:01,650\nhello world\n\n")
    p = tmp_path / "seg.srt"
    p.write_text(text, encoding="utf-8")
    assert check.check_srt(p).ok


def test_build_full_srt_running_offsets(tmp_path: Path):
    timing = _words(("hello", 0.0, 0.2), ("world", 0.2, 0.5))  # end 0.5 -> contract dur 1.65
    text = build_full_srt([("hello world", timing), ("hello world", timing)])
    cues = [line for line in text.splitlines() if "-->" in line]
    assert cues == ["00:00:01,150 --> 00:00:01,650", "00:00:02,800 --> 00:00:03,300"]
    assert [int(l) for l in text.splitlines() if l.isdigit()] == [1, 2]
    p = tmp_path / "full.srt"
    p.write_text(text, encoding="utf-8")
    assert check.check_srt(p).ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shorts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vibe.shorts'` (imports in the test can't be resolved).

- [ ] **Step 3: Write the minimal implementation**

```python
# vibe/shorts.py
"""Shorts stage (T7): native 9:16 re-render + verbatim CC sidecars (.srt).

Consumes approved per-segment scripts + narration (`.mp3` + `.timing.jsonl`) and the
hero still, and produces `build/shorts/short-<n>.mp4` (native 1080x1920, never
letterboxed) plus `build/cc/segment-<n>.srt` and `build/cc/full.srt` (verbatim captions,
markers stripped, playhead-aligned). Markers are structural: never present in SRT text.
"""

from __future__ import annotations

from collections.abc import Sequence

from . import config, render
from .narrate import WordTiming

Cue = tuple[float, float, str]


def _tc(seconds: float) -> str:
    """Format seconds as `HH:MM:SS,mmm` (the grammar check._srt_cues parses)."""
    ms = round(seconds * 1000.0)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt_block(n: int, start_s: float, end_s: float, text: str) -> str:
    return f"{n}\n{_tc(start_s)} --> {_tc(end_s)}\n{text}\n\n"


def caption_cues(
    script_text: str, timing: Sequence[WordTiming], *, offset_s: float = 0.0
) -> list[Cue]:
    """Verbatim per-line cues from script + word timing, markers already stripped."""
    out: list[Cue] = []
    for line in render._caption_lines(script_text, timing):
        text = " ".join(w.surface for w in line.spans)
        out.append((round(line.start_s + offset_s, 3), round(line.end_s + offset_s, 3), text))
    return out


def timing_end(timing: Sequence[WordTiming]) -> float:
    return float(max((w.end_s for w in timing), default=0.0))


def build_segment_srt(script_text: str, timing: Sequence[WordTiming]) -> str:
    """A segment's playhead-aligned verbatim SRT (open padding offset applied)."""
    cues = caption_cues(script_text, timing, offset_s=config.OPEN_PADDING_S)
    return "".join(_srt_block(i + 1, s, e, t) for i, (s, e, t) in enumerate(cues))


def build_full_srt(segments: Sequence[tuple[str, Sequence[WordTiming]]]) -> str:
    """Full-video SRT: running offsets across segments via the contract duration formula."""
    out = ""
    n = 0
    running = 0.0
    for text, timing in segments:
        offset = running + config.OPEN_PADDING_S
        for s, e, t in caption_cues(text, timing, offset_s=offset):
            n += 1
            out += _srt_block(n, s, e, t)
        running += config.OPEN_PADDING_S + timing_end(timing)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shorts.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add vibe/shorts.py tests/test_shorts.py
git commit -m "T7: verbatim CC sidecar pure core (#T7)"
```

---

### Task 2: Vertical renderer (cover-fill crop + safe-zone captions)

**Files:**
- Modify: `vibe/shorts.py`
- Test: `tests/test_shorts.py`

**Interfaces:**
- Consumes: `render._pillow()`, `render._open_hero`, `render.resolve_font`, `render._font_width`, `render._KIND_COLOUR`, `render.image` types `FrameSpec`/`Caption`; `config.PALETTE`, `config.SHORT_WIDTH/HEIGHT`, `config.CAPTION_SIZE`, `config.FOOTLINE_SIZE`.
- Produces:
  - `_cover_scale(canvas_w: float, canvas_h: float, hero_w: float, hero_h: float) -> float` — pure cover factor `max(cw/hw, ch/hh)`.
  - `vertical_renderer(*, width: int = config.SHORT_WIDTH, height: int = config.SHORT_HEIGHT, font: object | None = None) -> render.ImageRenderer` — satisfies `render.ImageRenderer` (`(specs, hero, *, palette) -> tuple[bytes,...]`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_shorts.py
import io as _io

import pytest


def _blank_png(w: int, h: int) -> bytes:
    from PIL import Image as _PILImage

    buf = _io.BytesIO()
    _PILImage.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_cover_scale_vertical_default():
    from vibe.shorts import _cover_scale

    assert abs(_cover_scale(1080, 1920, 1920, 1080) - (1920 / 1080)) < 1e-9


def test_cover_scale_uses_max_ratio():
    from vibe.shorts import _cover_scale

    # hero wider than the (square) canvas -> height ratio governs
    assert abs(_cover_scale(100, 100, 200, 50) - 2.0) < 1e-9


def test_vertical_renderer_produces_rgb_frames():
    pytest.importorskip("PIL")
    from vibe.render import CaptionLine, CaptionWord, plan_frames
    from vibe.shorts import vertical_renderer

    cl = CaptionLine((CaptionWord("hi", "base", 0.0, 0.3),), 0.0, 0.3)
    spec = plan_frames([cl], fps=30, width=108, height=192)
    r = vertical_renderer(width=108, height=192)
    frames = r(spec, hero=b"", palette=config.PALETTE)
    assert frames
    assert all(len(f) == 108 * 192 * 3 for f in frames)


def test_vertical_renderer_accepts_hero_bytes():
    pytest.importorskip("PIL")
    from vibe.render import CaptionLine, CaptionWord, plan_frames
    from vibe.shorts import vertical_renderer

    img = _blank_png(1920, 1080)
    cl = CaptionLine((CaptionWord("hi", "base", 0.0, 0.3),), 0.0, 0.3)
    spec = plan_frames([cl], fps=30, width=108, height=192)
    r = vertical_renderer(width=108, height=192)
    frames = r(spec, hero=img, palette=config.PALETTE)
    assert frames and all(len(f) == 108 * 192 * 3 for f in frames)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shorts.py -v`
Expected: FAIL with `ImportError: cannot import name 'vertical_renderer' from 'vibe.shorts'` (and `_cover_scale`, `_blank_png` undefined).

- [ ] **Step 3: Write the minimal implementation**

Append to `vibe/shorts.py`. First update the module-top import block (added in Task 1) to include `Any`:

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import config, render
from .narrate import WordTiming
```

Then append these definitions:

```python
def _cover_scale(canvas_w: float, canvas_h: float, hero_w: float, hero_h: float) -> float:
    """The factor that scales a hero to fully cover (never letterbox) a canvas."""
    ratio_w, ratio_h = canvas_w / hero_w, canvas_h / hero_h
    return ratio_w if ratio_w > ratio_h else ratio_h


def _paste_cover(canvas: Any, hero_img: Any, scale: float) -> None:
    """Cover-fill the hero (scaled by the zoom `scale`) and center-crop to the canvas."""
    if hero_img is None:
        return
    w, h = hero_img.size
    s = _cover_scale(canvas.width, canvas.height, w, h) * scale
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    if (nw, nh) == (w, h):
        resized = hero_img
    else:
        Image, _ = render._pillow()
        resized = hero_img.resize((nw, nh), Image.LANCZOS)
    canvas.paste(resized, ((canvas.width - nw) // 2, (canvas.height - nh) // 2))


def _draw_caption(
    frame: Any,
    caption: render.Caption,
    cap_font: object,
    fig_font: object,
    foot_font: object,
    palette: dict[str, str],
) -> None:
    """Draw a single-line caption in the vertical lower safe zone (clear of Shorts UI)."""
    _, ImageDraw = render._pillow()
    draw = ImageDraw.Draw(frame)
    fonts = [fig_font if s.kind == "figure" else cap_font for s in caption.spans]
    widths = [render._font_width(f, s.text) for s, f in zip(caption.spans, fonts)]
    total = sum(widths)
    keep = max(1, frame.width - 120)
    x = (frame.width - total) / 2.0 if total <= keep else (frame.width - keep) / 2.0
    baseline = frame.height - 380
    for span, font, w in zip(caption.spans, fonts, widths):
        draw.text((x, baseline), span.text, font=font,
                  fill=palette[render._KIND_COLOUR[span.kind]], anchor="ls")
        x += w
    if caption.footline:
        draw.text((frame.width / 2.0, frame.height - 120), caption.footline,
                  font=foot_font, fill=palette["ink"], anchor="ms")


def vertical_renderer(
    *,
    width: int = config.SHORT_WIDTH,
    height: int = config.SHORT_HEIGHT,
    font: object | None = None,
) -> render.ImageRenderer:
    """Real vertical frame renderer: paper-bg + cover-cropped hero + safe-zone captions."""

    def _r(
        specs: tuple[render.FrameSpec, ...],
        hero: object,
        *,
        palette: dict[str, str],
    ) -> tuple[bytes, ...]:
        Image, _ = render._pillow()
        hero_img = render._open_hero(hero)
        cap_font = font if font is not None else render.resolve_font(config.CAPTION_SIZE)
        fig_font = font if font is not None else render.resolve_font(int(config.CAPTION_SIZE * 1.15))
        foot_font = font if font is not None else render.resolve_font(config.FOOTLINE_SIZE)
        out: list[bytes] = []
        for spec in specs:
            frame = Image.new("RGB", (width, height), palette["bg"])
            _paste_cover(frame, hero_img, spec.scale)
            if spec.caption is not None:
                _draw_caption(frame, spec.caption, cap_font, fig_font, foot_font, palette)
            out.append(frame.tobytes())
        return tuple(out)

    return _r
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shorts.py -v`
Expected: PASS (all). NOTE: the hero test requires Pillow (`pytest.importorskip`), which must exist in the venv (recreate `main`'s venv with `Pillow edge-tts pytest mypy ruff` first — see handoff).

- [ ] **Step 5: Commit**

```bash
git add vibe/shorts.py tests/test_shorts.py
git commit -m "T7: vertical 9:16 cover-crop renderer (#T7)"
```

---

### Task 3: `render_shorts` orchestrator (short + segment SRT + full SRT)

**Files:**
- Modify: `vibe/shorts.py`
- Test: `tests/test_shorts.py`

**Interfaces:**
- Consumes: `layout.Layout` (`topic_brief`, `hero`, `scripts`, `narration`, `shorts`, `cc`); `script.read_index`, `script.STATUS_APPROVED`/`STATUS_NEEDS_HUMAN`; `render.read_timing`, `render.render_segment`, `render._footline`, `render.make_hero`, `render._write_atomic`; `render.ImageRenderer`/`render.Encoder`; `config.SHORT_WIDTH/HEIGHT`.
- Produces:
  - `ShortResult(index: int, status: str, ok: bool, message: str)` (frozen dataclass, mirrors `SegmentRenderResult`).
  - `render_shorts(lay: layout.Layout, *, renderer: render.ImageRenderer, encoder: render.Encoder, font: object | None = None) -> list[ShortResult]` — uses `build_segment_srt`/`build_full_srt` from Task 1 and `vertical_renderer` from Task 2 is NOT referenced here (caller supplies `renderer`; CLI passes `vertical_renderer`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_shorts.py
import json

from vibe import layout, script


def _index(*rows):
    return {"video": "v", "scripts": [dict(r) for r in rows]}


def test_render_shorts_writes_short_and_cc(tmp_path):
    from vibe.render import fake_encoder, fake_renderer
    from vibe.shorts import render_shorts

    lay = layout.create_layout(tmp_path)
    (lay.topic_brief).write_text(json.dumps(
        {"topic_brief": {"title": "t", "segments": [], "sources": [{"publisher": "CNBC"}]}}),
        encoding="utf-8")
    (lay.hero).write_bytes(b"hero")
    (lay.scripts / "index.json").write_text(json.dumps(_index(
        {"index": 1, "file": "segment-1.txt", "word_count": 2,
         "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
        {"index": 2, "file": "segment-2.txt", "word_count": 0,
         "status": script.STATUS_NEEDS_HUMAN, "attempts": 3, "violations": []},
    )), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text("hello world", encoding="utf-8")
    (lay.scripts / "segment-2.txt").write_text("bad", encoding="utf-8")
    (lay.narration / "segment-1.mp3").write_bytes(b"mp3")
    (lay.narration / "segment-1.timing.jsonl").write_text(
        '{"word": "hello", "start_s": 0.0, "end_s": 0.2}\n'
        '{"word": "world", "start_s": 0.2, "end_s": 0.5}\n',
        encoding="utf-8")

    results = render_shorts(lay, renderer=fake_renderer(), encoder=fake_encoder())
    assert (lay.shorts / "short-1.mp4").read_bytes() == b"fake-mp4"
    assert not (lay.shorts / "short-2.mp4").exists()
    assert (lay.cc / "segment-1.srt").read_text(encoding="utf-8").startswith(
        "1\n00:00:01,150 --> 00:00:01,650\nhello world")
    assert (lay.cc / "full.srt").is_file()
    assert results[0].ok and "OK" in results[0].message
    assert results[1].ok is False and "skipped" in results[1].message
    assert results[-1].ok and "full.srt" in results[-1].message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shorts.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_shorts' from 'vibe.shorts'`.

- [ ] **Step 3: Write the minimal implementation**

Update the module-top import block (from Task 2) to the final form:

```python
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from . import config, layout, render, script
from .narrate import WordTiming
```

Then append these definitions:

```python
@dataclass(frozen=True)
class ShortResult:
    index: int
    status: str
    ok: bool
    message: str


def render_shorts(
    lay: layout.Layout,
    *,
    renderer: render.ImageRenderer,
    encoder: render.Encoder,
    font: object | None = None,
) -> list[ShortResult]:
    """Render every approved segment to a native 9:16 short + verbatim CC sidecars.

    Writes `shorts/short-<n>.mp4`, `cc/segment-<n>.srt`, then `cc/full.srt`; skips
    non-approved segments. A short is a repackaged segment (same narration audio/timing).
    """
    brief = json.loads(lay.topic_brief.read_text(encoding="utf-8"))
    footline = render._footline(brief)
    if not lay.hero.is_file():
        render._write_atomic(lay.hero, render.make_hero(brief, font=font))
    hero = lay.hero.read_bytes()
    idx = script.read_index(lay)
    rows = cast(list[object], idx["scripts"])
    results: list[ShortResult] = []
    approved: list[tuple[str, list[WordTiming]]] = []
    for row in rows:
        rec = cast(dict[str, object], row)
        n = int(cast(Any, rec["index"]))
        status = str(rec["status"])
        if status != script.STATUS_APPROVED:
            results.append(ShortResult(n, status, False, f"short-{n}.mp4: skipped ({status})"))
            continue
        try:
            text = (lay.scripts / str(rec["file"])).read_text(encoding="utf-8")
            timing = render.read_timing(lay.narration / f"segment-{n}.timing.jsonl")
            mp3 = (lay.narration / f"segment-{n}.mp3").read_bytes()
            clip = render.render_segment(
                text, timing, mp3, footline, hero,
                renderer=renderer, encoder=encoder,
                width=config.SHORT_WIDTH, height=config.SHORT_HEIGHT,
            )
        except (render.RenderError, OSError, ValueError, KeyError) as exc:
            results.append(ShortResult(n, status, False, f"short-{n}.mp4: error: {exc}"))
            continue
        render._write_atomic(lay.shorts / f"short-{n}.mp4", clip)
        render._write_atomic(lay.cc / f"segment-{n}.srt",
                             build_segment_srt(text, timing).encode("utf-8"))
        approved.append((text, timing))
        results.append(ShortResult(n, status, True, f"short-{n}.mp4: OK"))
    render._write_atomic(lay.cc / "full.srt", build_full_srt(approved).encode("utf-8"))
    results.append(ShortResult(0, "full", True, "full.srt: OK"))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shorts.py -v`
Expected: PASS (all).

- [ ] **Step 5: Run lint/type on the new module**

Run: `python -m mypy vibe/shorts.py && python -m ruff check vibe/shorts.py tests/test_shorts.py`
Expected: clean (no errors, no unused imports).

- [ ] **Step 6: Commit**

```bash
git add vibe/shorts.py tests/test_shorts.py
git commit -m "T7: render_shorts orchestrator (short + segment/full SRT) (#T7)"
```

---

### Task 4: CLI wiring — `vibe shorts`

**Files:**
- Modify: `vibe/cli.py`
- Test: `tests/test_cli_shorts.py`

**Interfaces:**
- Consumes: `shorts.render_shorts`, `shorts.vertical_renderer`, `render.fake_renderer`/`fake_encoder`/`ffmpeg_encoder`; `script.STATUS_APPROVED`; `layout.Layout`.
- Produces: `vibe shorts [--build DIR]` subcommand (exit 0 success, 1 any failure, 2 missing index).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_shorts.py
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vibe import cli, shorts

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _make(build: Path, run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    nav = run_cli("narrate", "--build", str(build), cwd=str(tmp_path),
                  extra_env={"VIBE_NARRATOR": "fake"})
    assert nav.returncode == 0, nav.stderr
    (build / "hero.png").write_bytes(b"hero")  # avoid make_hero/Pillow in the fake CLI path
    return build


def test_shorts_fake_writes_short_and_cc(tmp_path, run_cli):
    build = _make(tmp_path / "build", run_cli, tmp_path)
    proc = run_cli("shorts", "--build", str(build), cwd=str(tmp_path),
                   extra_env={"VIBE_RENDERER": "fake"})
    assert proc.returncode == 0, proc.stderr
    assert (build / "shorts" / "short-1.mp4").is_file()
    assert (build / "cc" / "segment-1.srt").is_file()
    assert (build / "cc" / "full.srt").is_file()


def test_shorts_missing_index_exits_2(tmp_path, run_cli):
    proc = run_cli("shorts", "--build", str(tmp_path), cwd=str(tmp_path),
                   extra_env={"VIBE_RENDERER": "fake"})
    assert proc.returncode == 2
    assert "index.json" in proc.stderr


def _write_index(build: Path) -> Path:
    (build / "scripts").mkdir(parents=True, exist_ok=True)
    (build / "scripts" / "index.json").write_text(
        json.dumps({"scripts": [{"file": "segment-1.txt"}]}), encoding="utf-8")
    return build


def _fake_result(rc: int, message: str = "short-1.mp4: OK"):
    ok = rc == 0
    return lambda *a, **k: [shorts.ShortResult(1, "approved", ok, message)]


def test_shorts_terminal_ok_exits_0(tmp_path, monkeypatch):
    build = _write_index(tmp_path / "build")
    monkeypatch.setenv("VIBE_RENDERER", "fake")
    monkeypatch.setattr(cli.shorts, "render_shorts", _fake_result(0))
    assert cli._cmd_shorts(argparse.Namespace(build=build)) == 0


def test_shorts_terminal_failure_exits_1(tmp_path, monkeypatch):
    build = _write_index(tmp_path / "build")
    monkeypatch.setenv("VIBE_RENDERER", "fake")
    monkeypatch.setattr(cli.shorts, "render_shorts", _fake_result(1, "short-1.mp4: error"))
    assert cli._cmd_shorts(argparse.Namespace(build=build)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_shorts.py -v`
Expected: FAIL — `_cmd_shorts` not defined / `shorts` subcommand missing (usage error).

- [ ] **Step 3: Write the minimal implementation**

In `vibe/cli.py`: add `shorts` to the import line, add a `_select_shorts_renderer` helper, register a `shorts` subparser, and add `_cmd_shorts`.

```python
# 1) import line becomes:
from . import __version__, assembly, check, discover, layout, narrate, render, script, shorts

# 2) add next to _select_renderer:
def _select_shorts_renderer() -> tuple[render.ImageRenderer, render.Encoder]:
    if os.environ.get("VIBE_RENDERER") == "fake":
        return render.fake_renderer(), render.fake_encoder()
    return shorts.vertical_renderer(), render.ffmpeg_encoder()

# 3) in _build_parser, after the `asm` subparser block:
    sh = sub.add_parser("shorts", help="render native 9:16 shorts + verbatim CC sidecars")
    sh.add_argument("--build", type=Path, default=Path("build"), metavar="DIR",
                    help="build root with scripts/index.json (default: ./build)")
    sh.set_defaults(_handler=_cmd_shorts)

# 4) add after _cmd_assemble:
def _cmd_shorts(args: argparse.Namespace) -> int:
    lay = layout.Layout(root=args.build)
    if not (lay.scripts / "index.json").is_file():
        print(f"vibe shorts: no {lay.scripts.joinpath('index.json').as_posix()}; "
              f"run `vibe make` first", file=sys.stderr)
        return 2
    renderer, encoder = _select_shorts_renderer()
    results = shorts.render_shorts(lay, renderer=renderer, encoder=encoder)
    failed = False
    for res in results:
        print(res.message, file=sys.stderr if not res.ok else sys.stdout)
        failed = failed or (not res.ok and res.status == script.STATUS_APPROVED)
    return 1 if failed else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_shorts.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Run lint/type on the modified module**

Run: `python -m mypy vibe/cli.py && python -m ruff check vibe/cli.py tests/test_cli_shorts.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/cli.py tests/test_cli_shorts.py
git commit -m "T7: wire vibe shorts subcommand (#T7)"
```

---

### Task 5: Gated real render — vertical short conforms to the `short` contract

**Files:**
- Test: `tests/test_shorts.py` (append only)

**Interfaces:**
- Consumes: `check.check_video(path, kind="short")`, `check.check_srt(path)`, `config.SHORT_WIDTH/HEIGHT`, `render.render_segment`, `ffmpeg_encoder`, `vertical_renderer`, `build_segment_srt`.

- [ ] **Step 1: Write the failing/gated test**

```python
# append to tests/test_shorts.py
def test_ffmpeg_vertical_short_matches_contract(ffmpeg_available, tmp_path):
    import subprocess

    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    from vibe import check
    from vibe.render import ffmpeg_encoder, render_segment
    from vibe.shorts import build_segment_srt, vertical_renderer

    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "0.1",
         "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True)
    mp3 = proc.stdout
    timing = [WordTiming("a", 0.0, 0.05)]
    clip = render_segment(
        "**a**", timing, mp3, None, b"",
        width=config.SHORT_WIDTH, height=config.SHORT_HEIGHT,
        renderer=vertical_renderer(), encoder=ffmpeg_encoder(),
    )
    path = tmp_path / "short-1.mp4"
    path.write_bytes(clip)
    res = check.check_video(path, kind="short")
    assert res.ok, res.failures
    srt = tmp_path / "short-1.srt"
    srt.write_text(build_segment_srt("**a**", timing), encoding="utf-8")
    assert check.check_srt(srt).ok
```

Note: this test renders full 1080×1920 frames (~36 frames, ~218 MB raw) — identical cost to the existing `test_ffmpeg_encoder_real_clip_matches_contract`, and it is gated off when ffmpeg/ffprobe are absent. It needs `WordTiming` imported at the top of `tests/test_shorts.py` (already imported in Task 1).

- [ ] **Step 2: Run the test (may be skipped)**

Run: `python -m pytest tests/test_shorts.py::test_ffmpeg_vertical_short_matches_contract -v`
Expected: PASS if ffmpeg/ffprobe are on PATH, else SKIP (`ffmpeg/ffprobe not on PATH`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_shorts.py
git commit -m "T7: gated real vertical render conforms to short contract (#T7)"
```

---

### Task 6: Docs — implementation note in `docs/specs/assembly.md`

**Files:**
- Modify: `docs/specs/assembly.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the implementation notes**

Add a T7 implementation note to §2.4 (vertical short render) and §2.5 (CC sidecars), matching the T5/T6 note style:

```markdown
> **T7 implementation note (2026-08):** the vertical short render and the CC sidecars
> are driven by **`vibe shorts`** (`vibe/shorts.py`): per approved segment a native
> 1080×1920@30 clip (`shorts/short-<n>.mp4`) is re-rendered with the hero cover-fill
> center-cropped into the vertical frame (never letterboxed) and captions drawn in the
> lower safe zone, reusing the same narration audio/timing and the §2.2 encoder flags.
> Verbatim sidecars (`cc/segment-<n>.srt` + `cc/full.srt`) are playhead-aligned
> (narration timing + `OPEN_PADDING_S`); the full-video sidecar uses running offsets via
> the contract duration formula (`OPEN_PADDING_S` + segment narration end).
```

- [ ] **Step 2: Review the diff for accuracy**

Run: `git diff docs/specs/assembly.md`
Expected: only the added note(s); no §2.4/§2.5 contract text changed.

- [ ] **Step 3: Commit**

```bash
git add docs/specs/assembly.md
git commit -m "T7: record vibe shorts implementation note (#T7)"
```

---

### Task 7: Full-suite verification

**Files:** none (run commands only).

**Interfaces:** none.

- [ ] **Step 1: Ensure venv + tools present**

Recreate the missing `main` venv (handoff gap) with runtime + dev deps, then confirm the binaries are on PATH:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -c "import PIL, edge_tts, pytest, mypy, ruff; print('ok')"
```

- [ ] **Step 2: Run the full suite**

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python -m mypy vibe
.venv/Scripts/python -m ruff check vibe tests
```

Expected: `pytest` exit 0 (existing 131 + the new T7 tests), `mypy vibe` clean, `ruff check vibe tests` clean.

- [ ] **Step 3: Report results (evidence before green)** — run the three commands, capture output, confirm exit codes before claiming the branch is green.