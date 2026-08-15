# T6 — Assembly Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single `vibe assemble` subcommand that preview-gates segment 1 (with an automatic self-guided rework loop that re-synthesizes narration at auto-tuned base rates), fans out segments 2..N in parallel, encodes a silent recap clip, copy-concats `seg1..segN, recap` into `build/full.mp4`, and runs the deterministic final check — all through a new pure, seam-isolated module `vibe/assembly.py`.

**Architecture:** Mirror `vibe/render.py`: a pure core (`rework_base_rate`, `concat_list`, `expected_full_duration`, `_fanout`, `make_recap`) that is fully deterministic and offline-testable; two injectable `Protocol` seams (`RecapEncoder`, `Concatener`) with real ffmpeg and fake implementations; and an orchestrator `assemble_approved` plus thin `vibe assemble` CLI wiring. The rework loop reuses `narrate.narrate_segment` (gaining an optional `base_rate` knob) and `render.render_segment` (only segment 1). Every milestone records an `AssembleResult`, so partial progress is never lost on failure.

**Tech Stack:** Python 3.11+, stdlib plus Pillow (already a T5 runtime dep) and ffmpeg/ffprobe (already used by `check.py`/`narrate.py`/`render.py`). pytest 8, mypy strict, ruff.

## Global Constraints

- Python `>=3.11`; mypy `strict`; ruff `line-length=100`, target `py311`. **No new runtime dependency** (Pillow + edge-tts already present).
- Offline: tests never reach the network and never render full-time/res clips. CLI assemble tests pass `VIBE_ASSEMBLER=fake` (alongside `VIBE_NARRATOR=fake`, `VIBE_RENDERER=fake`). Real recap/concat coverage is gated on ffmpeg presence; the full-res multi-minute live run is left to a human/CI (never a gating verification under the shell, per T5 handoff).
- Determinism: pure-core outputs deterministic + unit-tested. `rework_base_rate` returns pre-signed strings (`+0%`/`-6%`...) so edge-tts accepts them. All artifact writes use temp-then-rename (`render._write_atomic`). The default `narrate_segment(base_rate="0%")` path is byte-identical to T4/T5.
- Assembly makes no creative decisions beyond the fixed, capped auto-pacing step (`rework_base_rate`); assembly is never a human gate beyond the segment-1 preview. On reject past the attempt cap, decline with a `needs-human` message and exit non-zero (never silently ship a reworked take).
- Follow existing patterns: pure stage module with typed injectable seams; thin CLI wiring; per-task commits on branch `build/t6`. One commit per task, message prefixed `T6: ` with `(#T6)` tag.

---

### Task 1: Recap config constants + `Layout` recap paths

**Files:**
- Modify: `vibe/config.py` (append recap constants after `FOOTLINE_SIZE`)
- Modify: `vibe/layout.py` (two `@property` on `Layout`)
- Test: `tests/test_assembly.py` (new file)

**Interfaces:**
- Consumes: `config.FULL_WIDTH`/`FULL_HEIGHT` already defined.
- Produces: `config.RECAP_SECONDS: float = 3.0`, `config.RECAP_LABEL: str = "recap"`; `Layout.recap_png == root / "recap.png"`, `Layout.recap_video == root / "recap.mp4"`. Later tasks consume `Layout.recap_png`/`layout.recap_video` and `config.RECAP_SECONDS`.

- [ ] **Step 1: Write the failing test** — create `tests/test_assembly.py`:

```python
from __future__ import annotations

from pathlib import Path

from vibe import config, layout


def test_recap_config_constants():
    assert config.RECAP_SECONDS == 3.0
    assert config.RECAP_LABEL == "recap"


def test_layout_exposes_recap_paths(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.recap_png == tmp_path / "recap.png"
    assert lay.recap_video == tmp_path / "recap.mp4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_assembly.py -v`
Expected: FAIL (`AttributeError` / missing constant).

- [ ] **Step 3: Write minimal implementation**

In `vibe/config.py`, after `FOOTLINE_SIZE = 24`:

```python
# Assembly (docs/specs/assembly.md §5-§6): the silent recap clip tail length and its
# human label. RECAP_SECONDS is the full-video tail; the recap is the only re-encoded
# concat input. Single source of truth for the final-check recap figure.
RECAP_SECONDS = 3.0
RECAP_LABEL = "recap"
```

In `vibe/layout.py`, add two properties to `Layout` (after `full_video`):

```python
    @property
    def recap_png(self) -> Path:
        return self.root / "recap.png"

    @property
    def recap_video(self) -> Path:
        return self.root / "recap.mp4"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_assembly.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check vibe tests && mypy vibe
git add tests/test_assembly.py vibe/config.py vibe/layout.py
git commit -m "T6: recap config constants + layout recap paths (#T6)"
```

---

### Task 2: Narration base-rate knob (`combine_rate` + `narrate_segment(base_rate=...)`)

**Files:**
- Modify: `vibe/narrate.py` (`narrate_segment` + new `combine_rate`)
- Test: `tests/test_assembly.py`

**Interfaces:**
- Consumes: existing `KNOBS`, `narrate_segment`, `Synthesizer`.
- Produces: `combine_rate(base_rate: str, knob_rate: str) -> str` (deterministic signed add); `narrate_segment(script_text, *, synthesizer, encoder, base_rate: str = "0%") -> SegmentNarration`. The default `"0%"` must leave T4/T5 narration byte-identical (edge-tts already signed `"0%"` → `"+0%"`).
- Design spec §2/§4: the one auto-tuned creative knob; `rework_base_rate` returns pre-signed strings, `edge_tts_synthesizer` re-signs harmlessly.

- [ ] **Step 1: Write the failing test** — append to `tests/test_assembly.py`:

```python
from vibe.narrate import combine_rate, fake_encoder, fake_synthesizer, narrate_segment


def test_combine_rate_signed_addition():
    assert combine_rate("0%", "0%") == "+0%"
    assert combine_rate("0%", "-8%") == "-8%"
    assert combine_rate("-6%", "0%") == "-6%"
    assert combine_rate("-6%", "-8%") == "-14%"
    assert combine_rate("+12%", "0%") == "+12%"


def test_narrate_segment_default_preserves_rate_args():
    seen: list[str] = []

    def synth(text: str, *, voice: str, rate: str, volume: str):
        seen.append(rate)
        return fake_synthesizer()(text, voice=voice, rate=rate, volume=volume)

    a = narrate_segment("hello **world**", synthesizer=synth, encoder=fake_encoder())
    seen.clear()
    b = narrate_segment("hello **world**", synthesizer=synth, encoder=fake_encoder(), base_rate="0%")
    assert seen == ["+0%", "-8%"]
    assert a == b


def test_narrate_segment_base_rate_passes_combined_rate():
    seen: list[str] = []

    def synth(text: str, *, voice: str, rate: str, volume: str):
        seen.append(rate)
        return fake_synthesizer()(text, voice=voice, rate=rate, volume=volume)

    narrate_segment("plain", synthesizer=synth, encoder=fake_encoder(), base_rate="-6%")
    narrate_segment("**key**", synthesizer=synth, encoder=fake_encoder(), base_rate="-6%")
    assert seen == ["-6%", "-14%"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_assembly.py -v`
Expected: FAIL (`SyntaxError` / `ImportError`: `combine_rate` missing; `base_rate` kwarg rejected).

- [ ] **Step 3: Write minimal implementation**

In `vibe/narrate.py`, add above `narrate_segment`:

```python
def combine_rate(base_rate: str, knob_rate: str) -> str:
    """Deterministically apply a base speaking-rate offset to a per-kind prosody knob.

    Both are percentages with an optional sign; the result is the signed sum (so edge-tts
    accepts it). The default base `0%` maps to the knob's own value, keeping T4/T5 output
    byte-identical when the knob is unused.
    """
    def _pct(value: str) -> int:
        v = value.strip().rstrip("%").strip()
        return int(v[1:]) if v.startswith("+") else int(v)

    return f"{_pct(base_rate) + _pct(knob_rate):+d}%"
```

Modify `narrate_segment` (line 272) signature and body:

```python
def narrate_segment(
    script_text: str,
    *,
    synthesizer: Synthesizer,
    encoder: Encoder,
    base_rate: str = "0%",
) -> SegmentNarration:
    """Synthesize one segment's script into audio bytes + cumulative word timing."""
    units: list[tuple[bytes, int, int]] = []
    chunk_events: list[Sequence[WordTiming]] = []
    chunks: list[Chunk] = []
    for line in script_text.splitlines():
        if not line.strip():
            continue
        for chunk in parse_line(line):
            chunks.append(chunk)
            if chunk.kind == "pause" or not chunk.text.strip():
                units.append((b"", chunk.pre_silence_ms, chunk.post_silence_ms))
                chunk_events.append([])
                continue
            knob_rate, volume = KNOBS[chunk.kind]
            audio, words = synthesizer(
                chunk.text, voice=config.NARRATION_VOICE,
                rate=combine_rate(base_rate, knob_rate), volume=volume,
            )
            units.append((audio, chunk.pre_silence_ms, chunk.post_silence_ms))
            chunk_events.append(words)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_assembly.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check vibe tests && mypy vibe
git add vibe/narrate.py tests/test_assembly.py
git commit -m "T6: narration base-rate knob for the rework loop (#T6)"
```

---

### Task 3: Pure assembly core (`rework_base_rate`, `concat_list`, `expected_full_duration`, `_fanout`)

**Files:**
- Create: `vibe/assembly.py` (header, `AssemblyError`, `NarrateKnob`, pure core)
- Test: `tests/test_assembly.py`

**Interfaces:**
- Consumes: `config.RECAP_SECONDS`.
- Produces (later tasks rely on these exact names):
  - `class AssemblyError(RuntimeError)`.
  - `@dataclass(frozen=True) class NarrateKnob: base_rate: str`
  - `rework_base_rate(attempt: int) -> str` — attempt 0→`"+0%"`, 1→`"-6%"`, 2→`"-12%"`, 3→`"-18%"`, `>= 4`→`"-18%"` (cap at the 4th).
  - `concat_list(clips: Sequence[Path]) -> str` — `file '<path>'` lines (as_posix), joined with `\n` + trailing newline, ordered as given (callers pass `seg1…segN, recap`).
  - `expected_full_duration(clip_durations: Sequence[float], *, recap_s: float) -> float` — `sum(...) + recap_s`.
  - `_fanout(n: int) -> range` — `range(2, n + 1)`.
  - `REWORK_MAX_INDEX = 3` — the highest rework base-rate index the gate will show before declining.

- [ ] **Step 1: Write the failing test** — append to `tests/test_assembly.py`:

```python
from vibe import assembly


def test_rework_base_rate_steps_and_cap():
    assert assembly.rework_base_rate(0) == "+0%"
    assert assembly.rework_base_rate(1) == "-6%"
    assert assembly.rework_base_rate(2) == "-12%"
    assert assembly.rework_base_rate(3) == "-18%"
    assert assembly.rework_base_rate(4) == "-18%"
    assert assembly.rework_base_rate(99) == "-18%"


def test_concat_list_exact_syntax_and_order(tmp_path):
    seg1 = tmp_path / "segments" / "segment-1.mp4"
    seg2 = tmp_path / "segments" / "segment-2.mp4"
    recap = tmp_path / "recap.mp4"
    text = assembly.concat_list([seg1, seg2, recap])
    assert text == (
        f"file '{seg1.as_posix()}'\n"
        f"file '{seg2.as_posix()}'\n"
        f"file '{recap.as_posix()}'\n"
    )


def test_expected_full_duration_arithmetic():
    assert assembly.expected_full_duration([1.5, 2.0], recap_s=3.0) == 6.5
    assert assembly.expected_full_duration([], recap_s=config.RECAP_SECONDS) == 3.0


def test_fanout_range():
    assert list(assembly._fanout(4)) == [2, 3, 4]
    assert list(assembly._fanout(1)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_assembly.py -v`
Expected: FAIL (`ImportError`: `vibe.assembly` missing).

- [ ] **Step 3: Write minimal implementation** — create `vibe/assembly.py`:

```python
"""Assembly stage: preview gate, parallel fan-out, full-video concat + recap tail.

Consumes per-segment narration + renders and full-video artifacts (`build/segments/
segment-<n>.mp4`, `build/hero.png`) and produces `build/recap.mp4` + `build/full.mp4`.
Real ffmpeg recap-encode and copy-concat live behind the `RecapEncoder`/`Concatener`
seams; the pure core (`rework_base_rate`, `concat_list`, `expected_full_duration`) is
deterministic and offline-testable. The reworked narration base-rate is the single
auto-tuned creative knob here; assembly never makes other creative decisions and is
never a human gate beyond the segment-1 preview.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Imports grow per-task: Task 4 adds `io` + `typing`/`config`/`render`; Task 5 adds
# `os`/`subprocess`/`tempfile`; Task 6 adds `json` + `Callable`/`ThreadPoolExecutor`
# + `check`/`layout`/`narrate`/`script`. Each task's commit leaves every import used.

# The rework gate shows the takes for rework_base_rate(0..REWORK_MAX_INDEX) before
# declining: 0%, -6%, -12%, -18% (4 takes), i.e. the cap is this high index.
REWORK_MAX_INDEX = 3


class AssemblyError(RuntimeError):
    """Raised when a real ffmpeg recap-encode or concat fails."""


@dataclass(frozen=True)
class NarrateKnob:
    """The speaking-rate knob (base_rate) for one rework attempt."""

    base_rate: str


def rework_base_rate(attempt: int) -> str:
    """Deterministic pacing step per rejection iteration, pre-signed for edge-tts."""
    table = ("+0%", "-6%", "-12%", "-18%")
    return table[min(attempt, 3)]


def concat_list(clips: Sequence[Path]) -> str:
    """Exact `ffmpeg -f concat -safe 0` list text: one `file '<path>'` per clip."""
    return "\n".join(f"file '{p.as_posix()}'" for p in clips) + "\n"


def expected_full_duration(clip_durations: Sequence[float], *, recap_s: float) -> float:
    """Deterministic full-video container duration: the sum of clips + recap tail."""
    return float(sum(clip_durations) + recap_s)


def _fanout(n: int) -> range:
    """The post-approval segment ordering (2..N); empty when N == 1."""
    return range(2, n + 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_assembly.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check vibe tests && mypy vibe
git add vibe/assembly.py tests/test_assembly.py
git commit -m "T6: pure assembly core (rework pacing, concat list, duration) (#T6)"
```

---

### Task 4: Recap card (`make_recap`) + recap/concat seams + fakes

**Files:**
- Modify: `vibe/assembly.py`
- Test: `tests/test_assembly.py`

**Interfaces:**
- Consumes: `config.PALETTE`, `config.FULL_WIDTH`/`FULL_HEIGHT`, `render._pillow()`/`render.resolve_font()`, `render._footline(brief)` (reused for the source line).
- Produces:
  - `make_recap(brief: dict[str, object], *, font: object | None = None) -> bytes` — deterministic 1920×1080 PNG (paper bg, ink title, segment titles in `positive`, a `Source: …` line from the brief publisher). No date/seed.
  - `class RecapEncoder(Protocol): def __call__(self, png: bytes, *, width: int, height: int, fps: int, seconds: float) -> bytes: ...`
  - `class Concatener(Protocol): def __call__(self, clips: Sequence[Path], out: Path, *, list_text: str) -> None: ...`
  - `fake_recap_encoder() -> RecapEncoder` → returns `b"recap-clip"`.
  - `fake_concatener() -> Concatener` → writes `out` (offline CLI).

- [ ] **Step 1: Write the failing test** — append to `tests/test_assembly.py`:

```python
import pytest

from vibe import assembly


def test_fake_recap_encoder_deterministic():
    enc = assembly.fake_recap_encoder()
    a = enc(b"png", width=1920, height=1080, fps=30, seconds=3.0)
    b = enc(b"png", width=1920, height=1080, fps=30, seconds=3.0)
    assert a == b"recap-clip"
    assert a == b


def test_fake_concatener_writes_out(tmp_path):
    con = assembly.fake_concatener()
    out = tmp_path / "full.mp4"
    con([tmp_path / "a.mp4"], out, list_text="file 'a.mp4'\n")
    assert out.read_bytes() == b"full.mp4"


def test_make_recap_deterministic_png():
    pytest.importorskip("PIL")
    import io as _io
    from PIL import Image as _PILImage

    from vibe import assembly

    brief = {"topic_brief": {
        "title": "Rates Are Up",
        "segments": [{"title": "The Context"}],
        "sources": [{"publisher": "CNBC"}],
    }}
    a = assembly.make_recap(brief)
    b = assembly.make_recap(brief)
    assert a == b
    assert a.startswith(b"\x89PNG")
    assert _PILImage.open(_io.BytesIO(a)).size == (config.FULL_WIDTH, config.FULL_HEIGHT)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_assembly.py -v`
Expected: FAIL (`AttributeError`: `make_recap`/`fake_recap_encoder`/`fake_concatener` missing).

- [ ] **Step 3: Write minimal implementation** — append to `vibe/assembly.py`:

```python
class RecapEncoder(Protocol):
    def __call__(self, png: bytes, *, width: int, height: int, fps: int, seconds: float) -> bytes: ...


class Concatener(Protocol):
    def __call__(self, clips: Sequence[Path], out: Path, *, list_text: str) -> None: ...


def fake_recap_encoder() -> RecapEncoder:
    """Deterministic offline recap-clip encoder for tests and the CLI fake seam."""

    def _enc(png: bytes, *, width: int, height: int, fps: int, seconds: float) -> bytes:
        return b"recap-clip"

    return _enc


def fake_concatener() -> Concatener:
    """Deterministic offline concatener that writes `out` (offline CLI tests)."""

    def _concat(clips: Sequence[Path], out: Path, *, list_text: str) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"full.mp4")

    return _concat


def make_recap(brief: dict[str, object], *, font: object | None = None) -> bytes:
    """The deterministic 1920x1080 designed summary (PNG) tail card (assembly.md §5)."""
    Image, ImageDraw = render._pillow()
    tb = cast(dict[str, object], brief["topic_brief"])
    palette = config.PALETTE
    img = Image.new("RGB", (config.FULL_WIDTH, config.FULL_HEIGHT), palette["bg"])
    draw = ImageDraw.Draw(img)
    title_font = font if font is not None else render.resolve_font(72)
    seg_font = font if font is not None else render.resolve_font(36)
    pub_font = font if font is not None else render.resolve_font(24)
    draw.text((img.width / 2.0, img.height * 0.32), str(tb["title"]),
              font=title_font, fill=palette["ink"], anchor="mm")
    y = img.height * 0.5
    segs = cast(list[object], tb.get("segments", []))
    for seg in segs:
        segd = cast(dict[str, object], seg)
        draw.text((img.width / 2.0, y), str(segd["title"]), font=seg_font,
                  fill=palette["positive"], anchor="mm")
        y += 56
    foot = render._footline(brief)
    if foot is not None:
        draw.text((img.width / 2.0, img.height * 0.84), foot, font=pub_font,
                  fill=palette["ink"], anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
```

Extend the module imports to (Task 4 now uses these; keep alphabetical):

```python
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from . import config, render
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_assembly.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check vibe tests && mypy vibe
git add vibe/assembly.py tests/test_assembly.py
git commit -m "T6: recap card + recap/concat seams and fakes (#T6)"
```

---

### Task 5: Real recap encoder + concatener (`ffmpeg_recap_encoder`, `ffmpeg_concatener`)

**Files:**
- Modify: `vibe/assembly.py`
- Test: `tests/test_assembly.py` (gated on ffmpeg)

**Interfaces:**
- Consumes: `config` video/audio `*_ENCODE_FLAGS`, `config.MUX_FLAGS`, `config.RECAP_SECONDS`, `AssemblyError`, `concat_list`.
- Produces:
  - `ffmpeg_recap_encoder(*, fps: int = config.FPS, width: int = config.FULL_WIDTH, height: int = config.FULL_HEIGHT) -> RecapEncoder` — `-loop 1 -framerate fps -i <png>` + `-f lavfi -i anullsrc=r=44100:cl=stereo` (silent AAC keeps one A+V stream for the copy-concat) + fixed flags + `-t seconds`, returned as bytes. Wraps `OSError`/non-zero in `AssemblyError`, **no partial output** (temp-then-read, never a temp left behind).
  - `ffmpeg_concatener() -> Concatener` — write `list_text` to a temp file, run `ffmpeg -f concat -safe 0 -i <list> -c copy +faststart <tmp>`, temp-then-rename to `out`. Wraps failures in `AssemblyError`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_assembly.py`:

```python
import subprocess

from vibe import check


@pytest.fixture()
def ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_ffmpeg_recap_encoder_real_clip_matches_contract(ffmpeg, tmp_path):
    if not ffmpeg:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    from vibe import assembly

    recap = assembly.make_recap({"topic_brief": {"title": "t", "segments": [], "sources": []}})
    clip = assembly.ffmpeg_recap_encoder()(
        recap, width=1920, height=1080, fps=30, seconds=config.RECAP_SECONDS
    )
    path = tmp_path / "recap.mp4"
    path.write_bytes(clip)
    res = check.check_video(path, kind="full")
    assert res.ok, res.failures


def test_ffmpeg_concatener_real_concat(ffmpeg, tmp_path):
    if not ffmpeg:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    from vibe import assembly

    # two tiny self-contained clips via the real T5 encoder
    from vibe.render import ffmpeg_encoder, pillow_renderer, render_segment
    from vibe.narrate import WordTiming

    mp3 = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "0.1", "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True).stdout
    clips = []
    for n in (1, 2):
        clip = render_segment("**a**", [WordTiming("a", 0.0, 0.05)], mp3, None, b"",
                              renderer=pillow_renderer(width=1920, height=1080),
                              encoder=ffmpeg_encoder())
        p = tmp_path / f"seg-{n}.mp4"
        p.write_bytes(clip)
        clips.append(p)
    out = tmp_path / "full.mp4"
    assembly.ffmpeg_concatener()(clips, out, list_text=assembly.concat_list(clips))
    res = check.check_video(out, kind="full")
    assert res.ok, res.failures
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_assembly.py -v`
Expected: FAIL (`ImportError`: `ffmpeg_recap_encoder`/`ffmpeg_concatener` missing).

- [ ] **Step 3: Write minimal implementation** — append to `vibe/assembly.py`:

```python
def ffmpeg_recap_encoder(
    *,
    fps: int = config.FPS,
    width: int = config.FULL_WIDTH,
    height: int = config.FULL_HEIGHT,
) -> RecapEncoder:
    """Real recap-clip encoder: still-loop + silent AAC -> deterministic .mp4."""

    def _enc(png: bytes, *, width: int, height: int, fps: int, seconds: float) -> bytes:
        png_path: str | None = None
        mp4_path: str | None = None
        proc = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as pf:
                png_path = pf.name
                pf.write(png)
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as mf:
                mp4_path = mf.name
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-loop", "1", "-framerate", str(fps), "-i", png_path,
                "-f", "lavfi", "-i", f"anullsrc=r={config.AUDIO_SAMPLE_RATE}:cl=stereo",
                *config.VIDEO_ENCODE_FLAGS,
                *config.AUDIO_ENCODE_FLAGS,
                "-t", str(seconds),
                *config.MUX_FLAGS,
                mp4_path,
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, check=False)
            except OSError as exc:
                raise AssemblyError(f"ffmpeg not found: {exc}") from exc
            if proc is None or proc.returncode != 0:
                detail = repr(proc.stderr) if proc is not None else "not run"
                raise AssemblyError(f"ffmpeg recap encode failed: {detail}")
            with open(mp4_path, "rb") as mh:
                return mh.read()
        finally:
            if png_path is not None:
                os.remove(png_path)
            if mp4_path is not None:
                os.remove(mp4_path)

    return _enc


def ffmpeg_concatener() -> Concatener:
    """Real copy-concatener: `ffmpeg -f concat -safe 0 -i list -c copy +faststart`."""

    def _concat(clips: Sequence[Path], out: Path, *, list_text: str) -> None:
        list_path: str | None = None
        tmp = out.with_suffix(out.suffix + ".tmp")
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as lf:
                list_path = lf.name
                lf.write(list_text)
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-c", "copy", *config.MUX_FLAGS,
                str(tmp),
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, check=False)
            except OSError as exc:
                raise AssemblyError(f"ffmpeg not found: {exc}") from exc
            if proc is None or proc.returncode != 0:
                detail = repr(proc.stderr) if proc is not None else "not run"
                raise AssemblyError(f"ffmpeg concat failed: {detail}")
            os.replace(tmp, out)
        except AssemblyError:
            raise
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise
        finally:
            if list_path is not None:
                os.remove(list_path)

    return _concat
```

Extend the module imports to (Task 5 now uses these; keep alphabetical):

```python
import io
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from . import config, render
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_assembly.py -v`
Expected: PASS (ffmpeg-only; both skip cleanly when ffmpeg/ffprobe absent).

- [ ] **Step 5: Lint + commit**

```bash
ruff check vibe tests && mypy vibe
git add vibe/assembly.py tests/test_assembly.py
git commit -m "T6: real recap encoder + concatener (#T6)"
```

---

### Task 6: Orchestrator (`AssembleResult` + `assemble_approved` + helpers)

**Files:**
- Modify: `vibe/assembly.py`
- Test: `tests/test_assembly.py`

**Interfaces:**
- Consumes: `render.render_segment`, `render.render_approved` pieces (`render.*_atomic`, `render.read_timing`, `render.make_hero`, `render._footline`), `narrate.narrate_segment`/`timing_jsonl`, `check.check_video`/`probe_media`, `script.read_index`, all prior-task produces.
- Produces:
  - `@dataclass(frozen=True) class AssembleResult: step: str; index: int | None; ok: bool; message: str`
  - `assemble_approved(lay, *, synth, nar_enc, renderer, enc, recap_enc, concatener, font=None, approve: Callable[[], bool] | None = None) -> list[AssembleResult]` — the §3 flow: hero+`make_recap` ensure → segment-1 preview gate + auto rework loop → parallel fan-out 2..N → recap clip (atomic) → concat → deterministic check. On gate-reject past the cap, append a `"needs-human"` result and return (no fan-out/concat). `approve` defaults to auto-approve (True); I/O all through seams + `Layout`.
  - `WHEN verify_video: bool = True`, run `check.check_video(full.mp4, kind="full")` and compare container duration ≈ `expected_full_duration(...)`. When `verify_video` is False (fake seam), record a deterministic skip result so the offline CLI test exits 0 without forcing a real probe on fake bytes. Add `verify_video: bool = True` to the `assemble_approved` signature.

- [ ] **Step 1: Write the failing test** — append to `tests/test_assembly.py`:

```python
import json

from vibe import layout, script


def _write_fixture_build(tmp_path) -> layout.Layout:
    lay = layout.create_layout(tmp_path)
    brief = {"topic_brief": {
        "title": "Rates Are Up",
        "segments": [{"title": "A"}, {"title": "B"}],
        "sources": [{"publisher": "CNBC"}],
    }}
    (lay.topic_brief).write_text(json.dumps(brief), encoding="utf-8")
    (lay.hero).write_bytes(b"hero")
    (lay.recap_png).write_bytes(b"recap-png")
    idx = {"video": "Rates Are Up", "scripts": [
        {"index": 1, "file": "segment-1.txt", "word_count": 2,
         "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
        {"index": 2, "file": "segment-2.txt", "word_count": 2,
         "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
    ]}
    (lay.scripts / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    for n in (1, 2):
        (lay.scripts / f"segment-{n}.txt").write_text("hello world", encoding="utf-8")
        (lay.narration / f"segment-{n}.mp3").write_bytes(b"mp3")
        (lay.narration / f"segment-{n}.timing.jsonl").write_text(
            '{"word": "hello", "start_s": 0.0, "end_s": 0.2}\n'
            '{"word": "world", "start_s": 0.2, "end_s": 0.5}\n',
            encoding="utf-8")
    return lay


def _seams():
    from vibe import assembly, narrate, render
    return {
        "synth": narrate.fake_synthesizer(),
        "nar_enc": narrate.fake_encoder(),
        "renderer": render.fake_renderer(),
        "enc": render.fake_encoder(),
        "recap_enc": assembly.fake_recap_encoder(),
        "concatener": assembly.fake_concatener(),
    }


def test_assemble_fake_end_to_end_approve(tmp_path):
    from vibe import assembly

    lay = _write_fixture_build(tmp_path)
    calls = iter([False, True])
    results = assembly.assemble_approved(
        lay, **_seams(), verify_video=False, approve=lambda: next(calls))
    assert any(r.ok and r.step == "rework" and r.index == 1 for r in results)
    assert (lay.segments / "segment-1.mp4").is_file()
    assert (lay.segments / "segment-2.mp4").is_file()
    assert (lay.recap_video).is_file()
    assert (lay.full_video).is_file()
    assert (lay.full_video).read_bytes() == b"full.mp4"


def test_assemble_fake_skip_existing_and_no_rework(tmp_path):
    from vibe import assembly

    lay = _write_fixture_build(tmp_path)
    (lay.segments / "segment-2.mp4").write_bytes(b"existing")
    results = assembly.assemble_approved(lay, **_seams(), verify_video=False, approve=lambda: True)
    assert not any(r.step == "rework" for r in results)
    assert any("segment-2.mp4: skipped (exists)" in r.message for r in results)
    assert (lay.segments / "segment-2.mp4").read_bytes() == b"existing"
    assert (lay.full_video).is_file()


def test_assemble_fake_reject_past_cap_declines(tmp_path):
    from vibe import assembly

    lay = _write_fixture_build(tmp_path)
    results = assembly.assemble_approved(lay, **_seams(), verify_video=False, approve=lambda: False)
    assert any(r.message.startswith("needs-human") for r in results)
    assert not (lay.full_video).exists()
    assert not (lay.segments / "segment-2.mp4").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_assembly.py -v`
Expected: FAIL (`ImportError`/`AttributeError`: `assemble_approved` missing).

- [ ] **Step 3: Write minimal implementation** — append to `vibe/assembly.py`, and extend the module imports to (Task 6 now uses these; keep alphabetical):

```python
import io
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from . import check, config, layout, narrate, render, script
```

Then append the orchestrator:

```python
@dataclass(frozen=True)
class AssembleResult:
    step: str
    index: int | None
    ok: bool
    message: str


def _render_segment_from_disk(
    lay: layout.Layout, n: int, rec: dict[str, object],
    hero: bytes, footline: str | None, *, renderer: render.ImageRenderer, enc: render.Encoder,
) -> AssembleResult:
    """Render one missing segment N from its on-disk narration; skip if it exists."""
    mp4 = lay.segments / f"segment-{n}.mp4"
    if mp4.is_file():
        return AssembleResult("segment", n, True, f"segment-{n}.mp4: skipped (exists)")
    try:
        text = (lay.scripts / str(rec["file"])).read_text(encoding="utf-8")
        timing = render.read_timing(lay.narration / f"segment-{n}.timing.jsonl")
        mp3 = (lay.narration / f"segment-{n}.mp3").read_bytes()
        clip = render.render_segment(text, timing, mp3, footline, hero,
                                     renderer=renderer, encoder=enc)
    except (render.RenderError, OSError, ValueError, KeyError) as exc:
        return AssembleResult("segment", n, False, f"segment-{n}.mp4: error: {exc}")
    render._write_atomic(mp4, clip)
    return AssembleResult("segment", n, True, f"segment-{n}.mp4: OK")


def _synthesize_and_render(
    lay: layout.Layout, n: int, rec: dict[str, object],
    hero: bytes, footline: str | None, *, base_rate: str,
    synth: narrate.Synthesizer, nar_enc: narrate.Encoder,
    renderer: render.ImageRenderer, enc: render.Encoder,
) -> AssembleResult:
    """Re-synthesize + re-render ONLY segment N at `base_rate` (the rework loop)."""
    try:
        text = (lay.scripts / str(rec["file"])).read_text(encoding="utf-8")
        seg = narrate.narrate_segment(text, synthesizer=synth, encoder=nar_enc,
                                      base_rate=base_rate)
    except (narrate.NarrationError, OSError, ValueError, KeyError) as exc:
        return AssembleResult("rework", n, False, f"segment-{n}.mp4: narration error: {exc}")
    render._write_atomic(lay.narration / f"segment-{n}.mp3", seg.mp3_bytes)
    render._write_atomic(lay.narration / f"segment-{n}.timing.jsonl",
                         narrate.timing_jsonl(seg.timings).encode("utf-8"))
    try:
        clip = render.render_segment(text, seg.timings, seg.mp3_bytes, footline, hero,
                                     renderer=renderer, encoder=enc)
    except (render.RenderError, OSError, ValueError, KeyError) as exc:
        return AssembleResult("rework", n, False, f"segment-{n}.mp4: render error: {exc}")
    render._write_atomic(lay.segments / f"segment-{n}.mp4", clip)
    return AssembleResult("rework", n, True, f"segment-{n}.mp4: OK (rate {base_rate})")


def _preview_gate(
    lay: layout.Layout, rec1: dict[str, object], hero: bytes, footline: str | None,
    *, approve: Callable[[], bool],
    synth: narrate.Synthesizer, nar_enc: narrate.Encoder,
    renderer: render.ImageRenderer, enc: render.Encoder,
) -> list[AssembleResult]:
    """Segment-1 preview gate + self-guided rework loop. Only segment 1 is touched."""
    results: list[AssembleResult] = []
    attempt = 0
    while True:
        base_rate = rework_base_rate(attempt)
        if attempt == 0:
            res = _render_segment_from_disk(lay, 1, rec1, hero, footline,
                                            renderer=renderer, enc=enc)
        else:
            res = _synthesize_and_render(lay, 1, rec1, hero, footline, base_rate=base_rate,
                                         synth=synth, nar_enc=nar_enc, renderer=renderer, enc=enc)
        results.append(res)
        if not res.ok:
            return results
        approved = approve()
        results.append(AssembleResult(
            "gate", 1, approved,
            "segment-1 preview: approved" if approved else "segment-1 preview: rejected"))
        if approved:
            return results
        if attempt >= REWORK_MAX_INDEX:
            results.append(AssembleResult(
                "gate", 1, False,
                "needs-human: segment 1 rejected after rework cap (max 4 takes)"))
            return results
        attempt += 1


def _fan_out(
    lay: layout.Layout, records: list[object], hero: bytes, footline: str | None,
    *, renderer: render.ImageRenderer, enc: render.Encoder,
) -> list[AssembleResult]:
    """Render any missing segments 2..N in parallel (skip-existing -> idempotent)."""
    n_total = len(records)

    def _one(n: int) -> AssembleResult:
        rec = cast(dict[str, object], records[n - 1])
        return _render_segment_from_disk(lay, n, rec, hero, footline,
                                         renderer=renderer, enc=enc)

    with ThreadPoolExecutor() as pool:
        return list(pool.map(_one, _fanout(n_total)))


def _final_check(
    lay: layout.Layout, records: list[object], *, verify_video: bool,
) -> list[AssembleResult]:
    """Deterministic final check (never a human gate): contract + duration."""
    if not verify_video:
        return [AssembleResult("check", None, True, "full.mp4: check skipped (fake seams)")]
    clips = [lay.segments / f"segment-{i}.mp4" for i in range(1, len(records) + 1)]
    clips.append(lay.recap_video)
    durations: list[float] = []
    for clip in clips:
        probe = check.probe_media(clip)
        if probe.container_duration is None:
            return [AssembleResult("check", None, False,
                                   f"full.mp4: check failed: no duration for {clip.name}")]
        durations.append(probe.container_duration)
    expected = expected_full_duration(durations, recap_s=config.RECAP_SECONDS)
    try:
        res = check.check_video(lay.full_video, kind="full")
    except check.MediaNotFound as exc:
        return [AssembleResult("check", None, False, f"full.mp4: check failed: {exc}")]
    if not res.ok:
        return [AssembleResult("check", None, False,
                               f"full.mp4: check failed: {'; '.join(res.failures)}")]
    actual = check.probe_media(lay.full_video).container_duration
    tol = config.DURATION_TOLERANCE_S
    if actual is not None and abs(actual - expected) > tol:
        return [AssembleResult("check", None, False,
                               f"full.mp4: check failed: duration {actual:.2f}s != expected {expected:.2f}s")]
    return [AssembleResult("check", None, True,
                           f"full.mp4: OK (full) {actual:.2f}s (expected {expected:.2f}s)")]


def assemble_approved(
    lay: layout.Layout,
    *,
    synth: narrate.Synthesizer,
    nar_enc: narrate.Encoder,
    renderer: render.ImageRenderer,
    enc: render.Encoder,
    recap_enc: RecapEncoder,
    concatener: Concatener,
    font: object | None = None,
    approve: Callable[[], bool] | None = None,
    verify_video: bool = True,
) -> list[AssembleResult]:
    """The T6 assembly flow: gate -> fan-out -> recap -> concat -> deterministic check."""
    approve = approve or (lambda: True)
    results: list[AssembleResult] = []
    brief = json.loads(lay.topic_brief.read_text(encoding="utf-8"))
    footline = render._footline(brief)
    idx = script.read_index(lay)
    records = cast(list[object], idx["scripts"])

    if not lay.hero.is_file():
        render._write_atomic(lay.hero, render.make_hero(brief, font=font))
    hero = lay.hero.read_bytes()
    if not lay.recap_png.is_file():
        render._write_atomic(lay.recap_png, make_recap(brief, font=font))

    rec1 = cast(dict[str, object], records[0])
    results += _preview_gate(lay, rec1, hero, footline, approve=approve,
                             synth=synth, nar_enc=nar_enc, renderer=renderer, enc=enc)
    if any(not r.ok for r in results):  # gate declined (needs-human) or a render failed
        return results

    results += _fan_out(lay, records, hero, footline, renderer=renderer, enc=enc)
    if any(not r.ok for r in results):  # a fan-out segment failed -> no concat, no partial full.mp4
        return results

    try:
        recap_clip = recap_enc(
            lay.recap_png.read_bytes(), width=config.FULL_WIDTH,
            height=config.FULL_HEIGHT, fps=config.FPS, seconds=config.RECAP_SECONDS,
        )
    except AssemblyError as exc:
        results.append(AssembleResult("recap", None, False, f"recap.mp4: error: {exc}"))
        return results
    render._write_atomic(lay.recap_video, recap_clip)
    results.append(AssembleResult("recap", None, True,
                                  f"{lay.recap_video.name}: OK ({config.RECAP_LABEL})"))

    clips = [lay.segments / f"segment-{i}.mp4" for i in range(1, len(records) + 1)]
    clips.append(lay.recap_video)
    try:
        concatener(clips, lay.full_video, list_text=concat_list(clips))
    except AssemblyError as exc:
        results.append(AssembleResult("concat", None, False, f"full.mp4: error: {exc}"))
        return results
    results.append(AssembleResult("concat", None, True, "full.mp4: OK"))

    results += _final_check(lay, records, verify_video=verify_video)
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_assembly.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check vibe tests && mypy vibe
git add vibe/assembly.py tests/test_assembly.py
git commit -m "T6: assembly orchestrator + deterministic final check (#T6)"
```

---

### Task 7: CLI wiring (`vibe assemble`) + fake seam

**Files:**
- Modify: `vibe/cli.py`, `tests/test_cli_assemble.py` (new)
- `tests/conftest.py` unchanged — `run_cli` already sets `VIBE_OFFLINE`; the assemble CLI test passes `VIBE_ASSEMBLER=fake` via `extra_env` (the seam travels in env, matching `VIBE_NARRATOR`/`VIBE_RENDERER`).

**Interfaces:**
- Consumes: `assembly.assemble_approved`, `assembly.fake_recap_encoder`/`fake_concatener`/`ffmpeg_recap_encoder`/`ffmpeg_concatener`, existing `_select_narrator`/`_select_renderer`.
- Produces:
  - `_select_assembler() -> tuple[assembly.RecapEncoder, assembly.Concatener]` — `VIBE_ASSEMBLER == "fake"` → fakes, else real.
  - `_gate_prompt() -> bool` — tty → `input("Approve segment 1? [y/N] ")`; non-tty → `True` (auto-approve).
  - `vibe assemble [--build DIR]` (default `./build`). Exit codes: `0` success (incl. skips + approved gate), `2` missing index, `1` any error or `needs-human` decline.
  - `_cmd_assemble` — passes `verify_video = os.environ.get("VIBE_ASSEMBLER") != "fake"`.

- [ ] **Step 1: Write the failing test** — create `tests/test_cli_assemble.py`:

```python
"""CLI seam: `vibe assemble` builds the full video, gated on segment-1 preview.

Offline via the `VIBE_ASSEMBLER=fake` seam; the real recap/concat + check path is
covered by the gated tests in test_assembly.py and the Task 8 integration.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _make(build: Path, run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    nav = run_cli("narrate", "--build", str(build), cwd=str(tmp_path),
                  extra_env={"VIBE_NARRATOR": "fake"})
    assert nav.returncode == 0, nav.stderr
    return build


def test_assemble_fake_writes_full(tmp_path, run_cli):
    build = _make(tmp_path / "build", run_cli, tmp_path)
    proc = run_cli("assemble", "--build", str(build), cwd=str(tmp_path),
                   extra_env={"VIBE_ASSEMBLER": "fake", "VIBE_NARRATOR": "fake",
                              "VIBE_RENDERER": "fake"})
    assert proc.returncode == 0, proc.stderr
    assert (build / "full.mp4").is_file()


def test_assemble_missing_index_exits_2(tmp_path, run_cli):
    proc = run_cli("assemble", "--build", str(tmp_path), cwd=str(tmp_path),
                   extra_env={"VIBE_ASSEMBLER": "fake"})
    assert proc.returncode == 2
    assert "index.json" in proc.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_assemble.py -v`
Expected: FAIL (argparse error: `invalid choice: 'assemble'`).

- [ ] **Step 3: Write minimal implementation** — modify `vibe/cli.py`:

Add `assembly` to the import line (`from . import __version__, assembly, check, discover, layout, narrate, render, script`), and add these functions after `_select_renderer`:

```python
def _select_assembler() -> tuple[assembly.RecapEncoder, assembly.Concatener]:
    if os.environ.get("VIBE_ASSEMBLER") == "fake":
        return assembly.fake_recap_encoder(), assembly.fake_concatener()
    return assembly.ffmpeg_recap_encoder(), assembly.ffmpeg_concatener()


def _gate_prompt() -> bool:
    if sys.stdin is None or not sys.stdin.isatty():
        return True  # non-tty (CI/offline): auto-approve segment 1
    try:
        answer = input("Approve segment 1? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")
```

Add a subcommand in `_build_parser` (after `rend`):

```python
    asm = sub.add_parser("assemble", help="assemble the full video (preview -> fan-out -> concat)")
    asm.add_argument("--build", type=Path, default=Path("build"), metavar="DIR",
                     help="build root with scripts/index.json (default: ./build)")
    asm.set_defaults(_handler=_cmd_assemble)
```

Add the handler:

```python
def _cmd_assemble(args: argparse.Namespace) -> int:
    lay = layout.Layout(root=args.build)
    if not (lay.scripts / "index.json").is_file():
        print(f"vibe assemble: no {lay.scripts.joinpath('index.json').as_posix()}; "
              f"run `vibe make` first", file=sys.stderr)
        return 2
    synth, nar_enc = _select_narrator()
    renderer, enc = _select_renderer()
    recap_enc, concatener = _select_assembler()
    verify = os.environ.get("VIBE_ASSEMBLER") != "fake"
    results = assembly.assemble_approved(
        lay, synth=synth, nar_enc=nar_enc, renderer=renderer, enc=enc,
        recap_enc=recap_enc, concatener=concatener, approve=_gate_prompt,
        verify_video=verify,
    )
    failed = False
    for res in results:
        print(res.message, file=sys.stderr if not res.ok else sys.stdout)
        failed = failed or (not res.ok or res.message.startswith("needs-human"))
    return 1 if failed else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_assemble.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check vibe tests && mypy vibe
git add vibe/cli.py tests/test_cli_assemble.py
git commit -m "T6: wire vibe assemble subcommand + fake seam (#T6)"
```

---

### Task 8: Final verification, spec alignment, docs, push

**Files:** `docs/specs/assembly.md` (§6 implementation note), `docs/superpowers/specs/2026-08-14-t6-assembly-design.md` (align), this plan (align).

- [ ] **Verify full checks + offline E2E** — from the `build-t6` worktree:

```powershell
pytest
mypy vibe
ruff check vibe tests
```

Offline CLI E2E (fake seams; verifies assemble writes `full.mp4`):

```powershell
$env:VIBE_OFFLINE='1'
.\.venv\Scripts\python -m vibe make "mortgage rates" --feeds-from tests/fixtures
$env:VIBE_NARRATOR='fake'; .\.venv\Scripts\python -m vibe narrate
$env:VIBE_RENDERER='fake'; .\.venv\Scripts\python -m vibe assemble
Get-ChildItem build
Remove-Item Env:\VIBE_ASSEMBLER; Remove-Item Env:\VIBE_RENDERER; Remove-Item Env:\VIBE_NARRATOR; Remove-Item Env:\VIBE_OFFLINE
```

- [ ] **Gated real integration** (ffmpeg present, no network): run only the real seams on a **tiny** 1920×1080 build (small frame counts via short narration) → `assemble_approved` with real recap + concat + `verify_video=True` → `check.check_video(full.mp4, kind="full")` OK and its duration ≈ `expected_full_duration`. Do **not** run a full-res multi-minute live render under the shell (T5 handoff: it times out); report the live full-res run as left-for-human/CI.
- [ ] **Update `docs/specs/assembly.md`** — add a one-line implementation note in §6 recording that T6 assembles via `vibe assemble` (`vibe/assembly.py`): segment-1 preview gate + auto rework, `ThreadPoolExecutor` fan-out, silent-AAC recap clip, `-c copy +faststart` concat, deterministic duration check. Do not edit `toolchain-split.md`.
- [ ] **Align design doc + this plan** — verify every named function/type/constant in `docs/superpowers/specs/2026-08-14-t6-assembly-design.md` exists as implemented (`rework_base_rate`, `concat_list`, `expected_full_duration`, `_fanout`, `RecapEncoder`, `Concatener`, `AssembleResult`, `assemble_approved`, `make_recap`, `Layout.recap_png`/`recap_video`, `config.RECAP_SECONDS`/`RECAP_LABEL`, `narrate_segment(base_rate=...)`); fix any drift. Commit `T6: spec/plan alignment + docs (#T6)`.
- [ ] **Push branch** `git push -u origin build/t6`. Report: branch, suite green, seams + gated real-recap+concat verified, the auto-rework-loop behavior exercised (approve False→True, decline-at-cap), and the live full-res run flagged as human/CI-only. Watch PR #20 (T5): if changes are requested, fix in `.worktrees\build-t5`, re-run the suite there, push — do not let T6 block on it.