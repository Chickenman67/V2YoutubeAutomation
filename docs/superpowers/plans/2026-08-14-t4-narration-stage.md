# T4 — Narration Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn approved per-segment narration scripts (with `**keyword**`, `##figure##`, `**gold**`, `~` markers) into real synthesized audio (`segment-<n>.mp3`) plus cumulative word timing (`segment-<n>.timing.jsonl`), via a new `vibe narrate` CLI subcommand.

**Architecture:** A new pure module `vibe/narrate.py` owns marker chunking (`parse_line`), the knob/silence table, cumulative word-timing math (`build_word_timings`), and the per-segment orchestrator (`narrate_segment`). Real synthesis (`edge_tts.Communicate`) and the audio codec (ffmpeg) live behind two injectable `Protocol` seams (`Synthesizer`, `Encoder`), so every test runs offline with deterministic fakes while the default CLI path synthesizes for real. `vibe narrate` reads `build/scripts/index.json`, narrates only `approved` segments, and skips `needs-human` with a warning (never auto-shipping).

**Tech Stack:** Python 3.11+, stdlib plus `edge-tts` (new dependency). ffmpeg/ffprobe (already on the system and used by `vibe/check.py`) for the audio codec. Testing: pytest 8, mypy (strict), ruff.

## Global Constraints

- Python `>=3.11`; mypy `strict`; ruff `line-length=100`, target `py311`. Only **one new runtime dependency: `edge-tts`**.
- Offline: tests must never reach the network. `tests/conftest.py` already sets `VIBE_OFFLINE=1` inside `run_cli`; CLI narration tests must pass `VIBE_NARRATOR=fake` (see Task 7) so no real TTS call happens. The real `edge_tts_synthesizer` and the real ffmpeg codec are only exercised by a gated integration test (Task 5) and the manual E2E (Task 8).
- Domain vocabulary per `docs/specs/narration.md`: voice `en-US-ChristopherNeural`; knobs base `0%/+0%`, keyword `-8%/+12%` + 120 ms pre, figure `-5%/+10%` + 450 ms post, gold `-8%/+15%` + 450 ms post, `~` 300 ms. Markers are **never spoken** and **never** appear in audio or timing output.
- Word timings are **cumulative across chunks and inserted silence** (spec §5), matching `vibe/check.py`'s `{word, start_s, end_s}` contract (monotonic, `start_s` = offset/1e7).
- Determinism: pure-core outputs (chunking, knobs, silence, timings, ordering) are deterministic and covered by tests; live TTS audio bytes are acknowledged NOT byte-identical across network calls (only timing/knobs/order are).
- `vibe narrate` is best-effort like `vibe make`: a skipped/failed segment is reported but completed segments stay; missing index exits 2; synth failure exits non-zero with no partial `.mp3` (temp-then-rename).
- Follow existing patterns: pure stage module with typed injectable seam; thin CLI wiring; per-task commits on branch `build/t4`. One commit per task, message prefixed `T4: `.

---

### Task 1: Layout `narration` dir + narration config constants

**Files:**
- Modify: `vibe/layout.py` (`_LAYOUT_DIRS` + a `Layout.narration` property)
- Modify: `vibe/config.py` (voice + mp3 bitrate constants)
- Test: `tests/test_layout.py` (add a small assertion file) or extend `tests/test_script.py` if it already covers layout — prefer a new `tests/test_narrate.py` created here and grown by later tasks.

**Interfaces:**
- Consumes: `vibe.layout.Layout` (existing `root`, `scripts`, `segments`, `cc`, `shorts`, `full_video`).
- Produces:
  - `Layout.narration -> Path` (== `root / "narration"`); `"narration"` added to `_LAYOUT_DIRS`.
  - `config.NARRATION_VOICE = "en-US-ChristopherNeural"`
  - `config.NARRATION_MP3_BITRATE = "192k"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_narrate.py
from __future__ import annotations

from pathlib import Path

from vibe import config, layout


def test_layout_exposes_narration_directory(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.narration == tmp_path / "narration"
    assert lay.narration.is_dir()


def test_narration_config_constants():
    assert config.NARRATION_VOICE == "en-US-ChristopherNeural"
    assert config.NARRATION_MP3_BITRATE == "192k"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: FAIL — `Layout` has no `narration`; `config` has no `NARRATION_*`.

- [ ] **Step 3: Implement**

In `vibe/layout.py`:

```python
_LAYOUT_DIRS = ("segments", "shorts", "cc", "scripts", "narration")
```

Add inside `class Layout` (next to the other path properties):

```python
    @property
    def narration(self) -> Path:
        return self.root / "narration"
```

In `vibe/config.py` (after the `AUDIO_ENCODE_FLAGS` block):

```python
# Narration (docs/specs/narration.md): the fixed voice and the deterministic mp3
# encode bitrate. Downstream stages read these; `vibe narrate` encodes to them.
NARRATION_VOICE = "en-US-ChristopherNeural"
NARRATION_MP3_BITRATE = "192k"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/layout.py vibe/config.py tests/test_narrate.py
git commit -m "T4: narration layout dir + voice/bitrate config (#T4)"
```

---

### Task 2: Marker chunking (`parse_line`) + knob/silence tables

**Files:**
- Create: `vibe/narrate.py` (types + `parse_line` + `KNOBS` + `SILENCE_MS`)
- Test: `tests/test_narrate.py` (extend)

**Interfaces:**
- Consumes: nothing from Task 1 (self-contained pure logic).
- Produces:
  - `ChunkKind = Literal["base", "keyword", "figure", "gold", "pause"]`
  - `@dataclass(frozen=True) class Chunk: text: str; kind: ChunkKind; pre_silence_ms: int; post_silence_ms: int`
  - `KNOBS: dict[ChunkKind, tuple[str, str]]` — `(rate, volume)` for the four speech kinds (pause absent — never synthesized):
    `base ("0%","0%")`, `keyword ("-8%","+12%")`, `figure ("-5%","+10%")`, `gold ("-8%","+15%")`.
  - `SILENCE_MS: dict[ChunkKind, tuple[int, int]]` — `(pre_ms, post_ms)`:
    `base (0,0)`, `keyword (120,0)`, `figure (0,450)`, `gold (0,450)`, `pause (300,0)`.
  - `parse_line(line: str) -> list[Chunk]` — split on `**…**`, `##…##`, `~`; markers stripped from `text`; consecutive base runs merged; exact `**gold**` → kind `gold`, `text ""`; `~` → kind `pause`, `text ""`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_narrate.py`)

```python
from vibe.narrate import Chunk, parse_line


def _kinds(chunks: list[Chunk]) -> list[str]:
    return [c.kind for c in chunks]


def _texts(chunks: list[Chunk]) -> list[str]:
    return [c.text for c in chunks]


def test_parse_line_no_markers_is_single_base_chunk():
    chunks = parse_line("Rates are up this month.")
    assert _kinds(chunks) == ["base"]
    assert _texts(chunks) == ["Rates are up this month."]


def test_parse_line_keyword_marker():
    chunks = parse_line("Here's the thing though, the **rates** is the story.")
    assert _kinds(chunks) == ["base", "keyword", "base"]
    assert _texts(chunks) == ["Here's the thing though, the ", "rates", " is the story."]
    kw = chunks[1]
    assert kw.pre_silence_ms == 120 and kw.post_silence_ms == 0


def test_parse_line_figure_marker():
    chunks = parse_line("Up ##5.25## now.")
    assert _kinds(chunks) == ["base", "figure", "base"]
    assert _texts(chunks) == ["Up ", "5.25", " now."]
    fig = chunks[1]
    assert fig.pre_silence_ms == 0 and fig.post_silence_ms == 450


def test_parse_line_gold_marker_is_structural():
    chunks = parse_line("**gold** for you.")
    assert _kinds(chunks) == ["gold", "base"]
    assert chunks[0].text == ""
    assert chunks[0].post_silence_ms == 450


def test_parse_line_beat_pause():
    chunks = parse_line("Money moves ~ fast.")
    assert _kinds(chunks) == ["base", "pause", "base"]
    assert chunks[1].text == ""
    assert chunks[1].pre_silence_ms == 300


def test_parse_line_consecutive_base_runs_merge():
    chunks = parse_line("a **b** c d")
    assert _kinds(chunks) == ["base", "keyword", "base"]
    assert _texts(chunks) == ["a ", "b", " c d"]


def test_parse_line_markers_never_appear_in_text():
    chunks = parse_line("**rates** ~ ##5.25## **gold** tail.")
    for c in chunks:
        assert "*" not in c.text and "#" not in c.text and "~" not in c.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: FAIL — `ImportError: cannot import name 'narrate' from 'vibe'`.

- [ ] **Step 3: Implement**

Create `vibe/narrate.py`:

```python
"""Narration stage: marker chunking, knob/silence mapping, word-timing math.

Consumes approved per-segment scripts (`build/scripts/segment-<n>.txt` + the index)
and produces narration audio (`build/narration/segment-<n>.mp3`) plus cumulative
word timing (`build/narration/segment-<n>.timing.jsonl`). Real TTS (edge-tts) and
the audio codec (ffmpeg) live behind the `Synthesizer`/`Encoder` seams so the core
stays offline-testable and deterministic. Markers are structural: never spoken,
never present in output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ChunkKind = Literal["base", "keyword", "figure", "gold", "pause"]


@dataclass(frozen=True)
class Chunk:
    text: str
    kind: ChunkKind
    pre_silence_ms: int
    post_silence_ms: int


# docs/specs/narration.md §4: the emphasis -> prosody mapping (rate, volume).
KNOBS: dict[ChunkKind, tuple[str, str]] = {
    "base": ("0%", "0%"),
    "keyword": ("-8%", "+12%"),
    "figure": ("-5%", "+10%"),
    "gold": ("-8%", "+15%"),
}

# (pre_ms, post_ms) silence per kind (spec §4).
SILENCE_MS: dict[ChunkKind, tuple[int, int]] = {
    "base": (0, 0),
    "keyword": (120, 0),
    "figure": (0, 450),
    "gold": (0, 450),
    "pause": (300, 0),
}

_TOKEN_RE = re.compile(r"(\*\*[^*]+\*\*|##[^#]+##|~)")


def parse_line(line: str) -> list[Chunk]:
    """Split one script line into ordered chunks at marker boundaries.

    Markers are stripped and never appear in `Chunk.text`. Consecutive base runs
    merge. An exact `**gold**` marker is structural (kind `gold`, empty text).
    """
    out: list[Chunk] = []
    for part in _TOKEN_RE.split(line):
        if not part:
            continue
        if part == "~":
            out.append(Chunk("", "pause", *SILENCE_MS["pause"]))
        elif part.startswith("##") and part.endswith("##"):
            out.append(Chunk(part[2:-2], "figure", *SILENCE_MS["figure"]))
        elif part.startswith("**") and part.endswith("**"):
            inner = part[2:-2]
            if inner.strip() == "gold":
                out.append(Chunk("", "gold", *SILENCE_MS["gold"]))
            else:
                out.append(Chunk(inner, "keyword", *SILENCE_MS["keyword"]))
        elif out and out[-1].kind == "base":
            out[-1] = Chunk(out[-1].text + part, "base", 0, 0)
        else:
            out.append(Chunk(part, "base", 0, 0))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: PASS (layout/config tests + parse_line tests).

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/narrate.py tests/test_narrate.py
git commit -m "T4: marker chunking + knob/silence tables (#T4)"
```

---

### Task 3: Cumulative word-timing math (`build_word_timings`) + JSONL writer

**Files:**
- Modify: `vibe/narrate.py` (add `WordTiming`, `build_word_timings`, `timing_jsonl`)
- Test: `tests/test_narrate.py` (extend)

**Interfaces:**
- Consumes: `Chunk`, `ChunkKind`, `SILENCE_MS` from Task 2.
- Produces:
  - `class WordTiming(NamedTuple): word: str; start_s: float; end_s: float`
  - `build_word_timings(chunks: Sequence[Chunk], chunk_events: Sequence[Sequence[WordTiming]]) -> list[WordTiming]`
    — cumulative offsets across chunks + inserted silence. Each `chunk_events[i]` holds that chunk's words in **chunk-relative** seconds (from the synthesizer's WordBoundary stream). Returns segment-absolute timings. Speech chunks: cursor += pre, emit each word shifted by cursor, cursor = last word end, cursor += post. `pause`/empty chunks: cursor += (pre + post) / 1000 with no words.
  - `timing_jsonl(timings: Sequence[WordTiming]) -> str` — one `{"word", "start_s", "end_s"}` per line, `start_s`/`end_s` rounded to 3 decimals, trailing newline.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_narrate.py`)

```python
from vibe.narrate import Chunk, WordTiming, build_word_timings, timing_jsonl


def test_build_word_timings_plain_base():
    chunks = [Chunk("hello world", "base", 0, 0)]
    events = [[WordTiming("hello", 0.0, 0.2), WordTiming("world", 0.2, 0.4)]]
    ts = build_word_timings(chunks, events)
    assert ts == [
        WordTiming("hello", 0.0, 0.2),
        WordTiming("world", 0.2, 0.4),
    ]


def test_build_word_timings_keyword_pre_silence_offsets():
    chunks = [
        Chunk("A", "base", 0, 0),
        Chunk("rates", "keyword", 120, 0),
    ]
    events = [
        [WordTiming("A", 0.0, 0.2)],
        [WordTiming("rates", 0.0, 0.3)],
    ]
    ts = build_word_timings(chunks, events)
    assert ts[0] == WordTiming("A", 0.0, 0.2)
    assert ts[1] == WordTiming("rates", 0.32, 0.62)


def test_build_word_timings_figure_post_silence_gap():
    chunks = [
        Chunk("Up", "base", 0, 0),
        Chunk("5.25", "figure", 0, 450),
        Chunk("now", "base", 0, 0),
    ]
    events = [
        [WordTiming("Up", 0.0, 0.2)],
        [WordTiming("5.25", 0.0, 0.3)],
        [WordTiming("now", 0.0, 0.2)],
    ]
    ts = build_word_timings(chunks, events)
    # base 0.0-0.2; figure 0.2-0.5 then 450ms post -> next starts at 0.95; now 0.95-1.15
    assert ts[0] == WordTiming("Up", 0.0, 0.2)
    assert ts[1] == WordTiming("5.25", 0.2, 0.5)
    assert ts[2] == WordTiming("now", 0.95, 1.15)


def test_build_word_timings_pause_chunk_advances_cursor():
    chunks = [
        Chunk("Money", "base", 0, 0),
        Chunk("", "pause", 300, 0),
        Chunk("fast", "base", 0, 0),
    ]
    events = [
        [WordTiming("Money", 0.0, 0.2)],
        [],
        [WordTiming("fast", 0.0, 0.2)],
    ]
    ts = build_word_timings(chunks, events)
    assert ts[1] == WordTiming("fast", 0.5, 0.7)


def test_timing_jsonl_matches_check_contract():
    out = timing_jsonl([WordTiming("rates", 0.0, 0.2), WordTiming("now", 0.2, 0.4)])
    assert out == '{"word": "rates", "start_s": 0.0, "end_s": 0.2}\n{"word": "now", "start_s": 0.2, "end_s": 0.4}\n'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: FAIL — `WordTiming`/`build_word_timings`/`timing_jsonl` not defined.

- [ ] **Step 3: Implement**

Append to `vibe/narrate.py` (keep `Chunk`/`ChunkKind`/`KNOBS`/`SILENCE_MS`/`parse_line`; add imports `json`, `collections.abc.Sequence`, `typing.NamedTuple`):

```python
class WordTiming(NamedTuple):
    word: str
    start_s: float
    end_s: float


def build_word_timings(
    chunks: Sequence[Chunk],
    chunk_events: Sequence[Sequence[WordTiming]],
) -> list[WordTiming]:
    """Rebuild cumulative timings across chunks + inserted silence.

    Each `chunk_events[i]` holds chunk-relative word spans (seconds). Speech chunks
    advance the cursor by their pre/post silence; pause/empty chunks advance by the
    full (pre + post) silence with no words emitted.
    """
    out: list[WordTiming] = []
    cursor = 0.0
    for chunk, events in zip(chunks, chunk_events):
        pre, post = chunk.pre_silence_ms, chunk.post_silence_ms
        if chunk.kind == "pause" or not chunk.text.strip():
            cursor += (pre + post) / 1000.0
            continue
        cursor += pre / 1000.0
        local_end = cursor
        for ev in events:
            start = cursor + ev.start_s
            end = cursor + ev.end_s
            out.append(WordTiming(ev.word, round(start, 3), round(end, 3)))
            if end > local_end:
                local_end = end
        cursor = local_end + post / 1000.0
    return out


def timing_jsonl(timings: Sequence[WordTiming]) -> str:
    """Serialize word timings to the `.timing.jsonl` contract (`vibe/check.py`)."""
    lines = (
        json.dumps(
            {"word": t.word, "start_s": round(t.start_s, 3), "end_s": round(t.end_s, 3)},
            ensure_ascii=False,
        )
        for t in timings
    )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/narrate.py tests/test_narrate.py
git commit -m "T4: cumulative word-timing math + jsonl writer (#T4)"
```

---

### Task 4: The seams — `Synthesizer`/`Encoder` protocols + deterministic fakes

**Files:**
- Modify: `vibe/narrate.py` (protocols, `SynthResult`, fake factory functions)
- Test: `tests/test_narrate.py` (extend)

**Interfaces:**
- Consumes: `WordTiming`, `Chunk`, `KNOBS`, `SILENCE_MS` from Tasks 2–3.
- Produces:
  - `SynthResult = tuple[bytes, tuple[WordTiming, ...]]` — `(audio_bytes, chunk-relative word timings)`.
  - `class Synthesizer(Protocol)` with `def __call__(self, text: str, *, voice: str, rate: str, volume: str) -> SynthResult`
  - `class Encoder(Protocol)` with `def __call__(self, units: list[tuple[bytes, int, int]], *, sample_rate: int, channels: int) -> bytes` — `units` = `(audio_bytes, pre_ms, post_ms)` per chunk; `b""` audio means silence-only.
  - `fake_synthesizer(voice: str = "fake-voice") -> Synthesizer` — deterministic: words from `text.split()` at 0.2 s duration, 0.25 s spacing (start at 0.0), audio = `b"fake-audio"`.
  - `fake_encoder() -> Encoder` — deterministic: returns `b"fake-mp3"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_narrate.py`)

```python
from vibe.narrate import WordTiming, fake_encoder, fake_synthesizer


def test_fake_synthesizer_deterministic_words():
    synth = fake_synthesizer()
    a = synth("rates are up", voice="v", rate="-8%", volume="+12%")
    b = synth("rates are up", voice="v", rate="-8%", volume="+12%")
    assert a == b
    assert a[0] == b"fake-audio"
    assert a[1] == (
        WordTiming("rates", 0.0, 0.2),
        WordTiming("are", 0.25, 0.45),
        WordTiming("up", 0.5, 0.7),
    )


def test_fake_encoder_deterministic():
    enc = fake_encoder()
    assert enc([(b"abc", 0, 450)], sample_rate=44100, channels=2) == b"fake-mp3"
    assert enc([(b"abc", 0, 450)], sample_rate=44100, channels=2) == enc(
        [(b"abc", 0, 450)], sample_rate=44100, channels=2
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: FAIL — `Synthesizer`/`Encoder`/`fake_synthesizer`/`fake_encoder` not defined.

- [ ] **Step 3: Implement**

Append to `vibe/narrate.py` (add `typing.Protocol` import):

```python
class Synthesizer(Protocol):
    def __call__(self, text: str, *, voice: str, rate: str, volume: str) -> SynthResult: ...


class Encoder(Protocol):
    def __call__(self, units: list[tuple[bytes, int, int]], *, sample_rate: int, channels: int) -> bytes: ...


SynthResult = tuple[bytes, tuple[WordTiming, ...]]


def fake_synthesizer(voice: str = "fake-voice") -> Synthesizer:
    """Deterministic offline synthesizer for tests and the CLI fake seam."""

    def _synth(text: str, *, voice: str = voice, rate: str = "0%", volume: str = "0%") -> SynthResult:
        words: list[WordTiming] = []
        t = 0.0
        for w in text.split():
            words.append(WordTiming(w, round(t, 3), round(t + 0.2, 3)))
            t += 0.25
        return (b"fake-audio", tuple(words))

    return _synth


def fake_encoder() -> Encoder:
    """Deterministic offline encoder for tests and the CLI fake seam."""

    def _enc(units: list[tuple[bytes, int, int]], *, sample_rate: int, channels: int) -> bytes:
        return b"fake-mp3"

    return _enc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/narrate.py tests/test_narrate.py
git commit -m "T4: synthesizer/encoder seams + deterministic fakes (#T4)"
```

---

### Task 5: Real edge-tts synthesizer + real ffmpeg encoder

**Files:**
- Modify: `vibe/narrate.py` (add `NarrationError`, `edge_tts_synthesizer`, `ffmpeg_encoder`)
- Modify: `pyproject.toml` (`dependencies = ["edge-tts"]`)
- Test: `tests/test_narrate.py` (gated integration test)

**Interfaces:**
- Consumes: `SynthResult`, `Synthesizer`, `Encoder` from Task 4.
- Produces:
  - `class NarrationError(RuntimeError)` — raised when real TTS/ffmpeg fails.
  - `edge_tts_synthesizer(voice: str = config.NARRATION_VOICE) -> Synthesizer` — wraps `edge_tts.Communicate(text, voice, rate=rate, volume=volume, boundary="WordBoundary")`; iterates `stream_sync()`, accumulating `data` bytes for `type == "audio"` and `(text, offset/1e7, (offset+duration)/1e7)` for `type == "WordBoundary"`. Wraps any exception in `NarrationError`. Lazy-imports `edge_tts` inside the factory (never at module import).
  - `ffmpeg_encoder(*, bitrate: str = config.NARRATION_MP3_BITRATE) -> Encoder` — decodes each unit's mp3 bytes to raw s16le PCM (`ffmpeg -f s16le -ar SR -ac CH`), inserts `pre`/`post` silence as zero bytes, concatenates, then one `ffmpeg` encode to mp3. Uses `subprocess.run(..., capture_output=True, check=True)` and `input=`. Wraps failures in `NarrationError`.

- [ ] **Step 1: Write the gated integration test** (append to `tests/test_narrate.py`)

```python
import subprocess

import pytest

from vibe.narrate import NarrationError, edge_tts_synthesizer, ffmpeg_encoder


def _tiny_mp3() -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "0.1", "-c:a", "libmp3lame", "-f", "mp3", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return proc.stdout


def test_ffmpeg_encoder_roundtrip(ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    enc = ffmpeg_encoder()
    unit = (_tiny_mp3(), 120, 450)
    out = enc([unit], sample_rate=44100, channels=2)
    assert out[:3] == b"ID3" or b"\xff\xfb" in out[:32]  # mp3 frame magic


def test_ffmpeg_encoder_bad_audio_raises(ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    enc = ffmpeg_encoder()
    with pytest.raises(NarrationError):
        enc([(b"not-an-mp3", 0, 0)], sample_rate=44100, channels=2)
```

Note: `edge_tts_synthesizer` itself is **not** unit-tested here (live network call). Its adapter shape is verified by the manual E2E in Task 8. Add only a structural smoke assertion (offline) if useful, e.g. `callable(edge_tts_synthesizer())` — but keep the real call out of the suite.

- [ ] **Step 2: Run tests to verify the new tests fail** (encoder undefined)

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: FAIL — `ffmpeg_encoder`/`NarrationError` not defined.

- [ ] **Step 3: Declare the dependency in `pyproject.toml`**

In `pyproject.toml`, change:

```toml
dependencies = []
```

to:

```toml
dependencies = ["edge-tts"]
```

- [ ] **Step 4: Implement**

Append to `vibe/narrate.py` (add imports `subprocess`, `config`):

```python
class NarrationError(RuntimeError):
    """Raised when real TTS synthesis or the ffmpeg codec fails."""


def edge_tts_synthesizer(voice: str = config.NARRATION_VOICE) -> Synthesizer:
    """Real synthesizer via edge-tts (requires network)."""

    import edge_tts

    def _synth(text: str, *, voice: str = voice, rate: str = "0%", volume: str = "0%") -> SynthResult:
        try:
            comm = edge_tts.Communicate(
                text, voice, rate=rate, volume=volume, boundary="WordBoundary"
            )
            audio = bytearray()
            words: list[WordTiming] = []
            for chunk in comm.stream_sync():
                kind = chunk.get("type", "")
                if kind == "audio":
                    audio += chunk.get("data", b"")
                elif kind == "WordBoundary":
                    offset = float(chunk.get("offset", 0)) / 1e7
                    duration = float(chunk.get("duration", 0)) / 1e7
                    words.append(WordTiming(str(chunk.get("text", "")), offset, offset + duration))
            if not audio:
                raise NarrationError("edge-tts returned no audio")
            return (bytes(audio), tuple(words))
        except NarrationError:
            raise
        except Exception as exc:  # network, auth, service errors
            raise NarrationError(f"edge-tts synthesis failed: {exc}") from exc

    return _synth


def _decode_mp3(audio: bytes, sample_rate: int, channels: int) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", "pipe:0",
            "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels), "pipe:1",
        ],
        input=audio,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise NarrationError(f"ffmpeg decode failed: {proc.stderr[-300:]}")
    return proc.stdout


def ffmpeg_encoder(*, bitrate: str = config.NARRATION_MP3_BITRATE) -> Encoder:
    """Real encoder: mp3 chunks -> s16le PCM -> silence -> concat -> mp3."""

    def _enc(units: list[tuple[bytes, int, int]], *, sample_rate: int, channels: int) -> bytes:
        frame = sample_rate * channels * 2  # bytes per second of s16le PCM
        pcm = bytearray()
        for audio, pre_ms, post_ms in units:
            pre = (frame * pre_ms) // 1000
            post = (frame * post_ms) // 1000
            pcm += b"\x00" * (pre - pre % 2)
            if audio:
                pcm += _decode_mp3(audio, sample_rate, channels)
            pcm += b"\x00" * (post - post % 2)
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-v", "error",
                    "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0",
                    "-c:a", "libmp3lame", "-b:a", bitrate, "pipe:1",
                ],
                input=bytes(pcm),
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise NarrationError(f"ffmpeg not found: {exc}") from exc
        if proc.returncode != 0:
            raise NarrationError(f"ffmpeg encode failed: {proc.stderr[-300:]}")
        return proc.stdout

    return _enc
```

- [ ] **Step 5: Run the integration tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -k encoder -v`
Expected: PASS (roundtrip + bad-audio-raises), ffmpeg skip where unavailable.

- [ ] **Step 6: Full suite + lint + typecheck**

Run: `.venv\Scripts\python -m pytest && .venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add vibe/narrate.py pyproject.toml tests/test_narrate.py
git commit -m "T4: real edge-tts synthesizer + ffmpeg encoder (#T4)"
```

---

### Task 6: Per-segment orchestrator (`narrate_segment`, `narrate_approved`)

**Files:**
- Modify: `vibe/narrate.py` (add `SegmentNarration`, `SegmentResult`, `narrate_segment`, `narrate_approved`, atomic write helper)
- Test: `tests/test_narrate.py` (extend)

**Interfaces:**
- Consumes: `parse_line`, `KNOBS`, `build_word_timings`, `timing_jsonl`, `Synthesizer`, `Encoder` (Tasks 2–5); `vibe.script.read_index`, `vibe.script.STATUS_APPROVED`, `vibe.layout.Layout`, `vibe.config` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class SegmentNarration: mp3_bytes: bytes; timings: tuple[WordTiming, ...]`
  - `narrate_segment(script_text: str, *, synthesizer: Synthesizer, encoder: Encoder) -> SegmentNarration`
    — parse lines → chunks; for each non-pause, non-empty chunk call `synthesizer(chunk.text, voice=config.NARRATION_VOICE, rate=..., volume=...)` from `KNOBS`; collect `(audio, pre, post)` units + chunk-relative events; call `encoder(units, sample_rate=config.AUDIO_SAMPLE_RATE, channels=config.AUDIO_CHANNELS)`; compute timings; return.
  - `@dataclass(frozen=True) class SegmentResult: index: int; status: str; ok: bool; message: str`
  - `narrate_approved(lay: layout.Layout, *, synthesizer: Synthesizer, encoder: Encoder) -> list[SegmentResult]`
    — reads the index; for each `approved` record narrates and writes `lay.narration / segment-<n>.mp3` + `.timing.jsonl` (temp-then-rename); skips `needs-human`/other statuses with `segment-<n>.mp3: skipped (<status>)`; on `NarrationError` reports `error` with `ok=False` and writes nothing.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_narrate.py`)

```python
import json

import pytest

from vibe import layout, script
from vibe.narrate import (
    SegmentNarration,
    NarrationError,
    fake_encoder,
    fake_synthesizer,
    narrate_approved,
    narrate_segment,
)


SCRIPT_1 = (
    "Here's the thing though, the **rates** is the story everyone is chasing.\n"
    "Money moves ~ fast.\n"
    "And every single rate decision reshapes the monthly number.\n"
)


def test_narrate_segment_fake_roundtrip():
    out = narrate_segment(
        SCRIPT_1, synthesizer=fake_synthesizer(), encoder=fake_encoder()
    )
    assert isinstance(out, SegmentNarration)
    assert out.mp3_bytes == b"fake-mp3"
    assert out.timings  # non-empty
    # cumulative: no gaps between consecutive words of the same chunk
    for a, b in zip(out.timings, out.timings[1:]):
        assert b.start_s >= a.end_s


def test_narrate_segment_pause_creates_gap():
    out = narrate_segment(
        SCRIPT_1, synthesizer=fake_synthesizer(), encoder=fake_encoder()
    )
    # the '~' line ("Money moves ~ fast.") has a 300ms pause between 'moves' and 'fast'
    words = [t.word for t in out.timings]
    i_fast = words.index("fast")
    prev = out.timings[i_fast - 1]
    gap = out.timings[i_fast].start_s - prev.end_s
    assert gap >= 0.299


def test_narrate_approved_writes_artifacts(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    lay.scripts.mkdir(parents=True, exist_ok=True)
    idx = {
        "video": "test",
        "scripts": [
            {"index": 1, "file": "segment-1.txt", "word_count": 210,
             "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
            {"index": 2, "file": "segment-2.txt", "word_count": 0,
             "status": script.STATUS_NEEDS_HUMAN, "attempts": 3, "violations": []},
        ],
    }
    (lay.scripts / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text(SCRIPT_1, encoding="utf-8")
    (lay.scripts / "segment-2.txt").write_text("bad", encoding="utf-8")

    results = narrate_approved(lay, synthesizer=fake_synthesizer(), encoder=fake_encoder())
    assert [r.index for r in results] == [1, 2]
    assert results[0].ok and results[1].ok is False
    mp3 = lay.narration / "segment-1.mp3"
    timing = lay.narration / "segment-1.timing.jsonl"
    assert mp3.read_bytes() == b"fake-mp3"
    assert timing.is_file()
    assert "segment-1.mp3: OK" in results[0].message
    assert "skipped" in results[1].message
    assert not (lay.narration / "segment-2.mp3").exists()


def test_narrate_approved_synth_failure_writes_no_partial(tmp_path: Path, monkeypatch):
    lay = layout.create_layout(tmp_path)
    lay.scripts.mkdir(parents=True, exist_ok=True)
    idx = {
        "video": "test",
        "scripts": [
            {"index": 1, "file": "segment-1.txt", "word_count": 210,
             "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
        ],
    }
    (lay.scripts / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text(SCRIPT_1, encoding="utf-8")

    def _boom(text, *, voice, rate, volume):
        raise NarrationError("boom")

    results = narrate_approved(lay, synthesizer=_boom, encoder=fake_encoder())
    assert results[0].ok is False and "boom" in results[0].message
    assert not (lay.narration / "segment-1.mp3").exists()
    assert not (lay.narration / "segment-1.timing.jsonl").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: FAIL — `narrate_segment`/`narrate_approved`/`SegmentNarration`/`SegmentResult` not defined.

- [ ] **Step 3: Implement**

Append to `vibe/narrate.py` (add imports `os`, `json`, `pathlib.Path`, `dataclasses.dataclass`, `collections.abc.Sequence`, `typing.NamedTuple` already added; plus `from . import config, layout, script`):

```python
@dataclass(frozen=True)
class SegmentNarration:
    mp3_bytes: bytes
    timings: tuple[WordTiming, ...]


@dataclass(frozen=True)
class SegmentResult:
    index: int
    status: str
    ok: bool
    message: str


def narrate_segment(
    script_text: str,
    *,
    synthesizer: Synthesizer,
    encoder: Encoder,
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
            rate, volume = KNOBS[chunk.kind]
            audio, words = synthesizer(
                chunk.text, voice=config.NARRATION_VOICE, rate=rate, volume=volume
            )
            units.append((audio, chunk.pre_silence_ms, chunk.post_silence_ms))
            chunk_events.append(words)
    audio_bytes = encoder(
        units, sample_rate=config.AUDIO_SAMPLE_RATE, channels=config.AUDIO_CHANNELS
    )
    timings = tuple(build_word_timings(chunks, chunk_events))
    return SegmentNarration(mp3_bytes=audio_bytes, timings=timings)


def _write_atomic(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def narrate_approved(
    lay: layout.Layout,
    *,
    synthesizer: Synthesizer,
    encoder: Encoder,
) -> list[SegmentResult]:
    """Narrate every `approved` segment; skip others; write `.mp3` + `.timing.jsonl`."""
    idx = script.read_index(lay)
    rows = cast(list[object], idx["scripts"])
    results: list[SegmentResult] = []
    for row in rows:
        rec = cast(dict[str, object], row)
        n = int(rec["index"])
        status = str(rec["status"])
        mp3 = lay.narration / f"segment-{n}.mp3"
        timing = lay.narration / f"segment-{n}.timing.jsonl"
        if status != script.STATUS_APPROVED:
            results.append(SegmentResult(n, status, False, f"segment-{n}.mp3: skipped ({status})"))
            continue
        text = (lay.scripts / str(rec["file"])).read_text(encoding="utf-8")
        try:
            seg = narrate_segment(text, synthesizer=synthesizer, encoder=encoder)
        except NarrationError as exc:
            results.append(SegmentResult(n, status, False, f"segment-{n}.mp3: error: {exc}"))
            continue
        _write_atomic(mp3, seg.mp3_bytes)
        _write_atomic(timing, timing_jsonl(seg.timings).encode("utf-8"))
        results.append(SegmentResult(n, status, True, f"segment-{n}.mp3: OK"))
    return results
```

Append to the Task 6 implementation note: `narrate_approved` uses `cast(list[object], idx["scripts"])` and `cast(dict[str, object], row)` (the same idiom as `script.approve_scripts`) — add `from typing import cast` to the imports in `vibe/narrate.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_narrate.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/narrate.py tests/test_narrate.py
git commit -m "T4: per-segment orchestrator + approved-only narration (#T4)"
```

---

### Task 7: CLI wiring (`vibe narrate`) + fake seam

**Files:**
- Modify: `vibe/cli.py` (import `narrate`; add `narrate` subcommand + `_cmd_narrate` + `_select_narrator`)
- Modify: `tests/conftest.py` (no change needed — `run_cli` already has `extra_env`; verify)
- Test: `tests/test_cli_narrate.py` (new)

**Interfaces:**
- Consumes: `narrate.narrate_approved`, `narrate.Synthesizer`, `narrate.Encoder`, `narrate.fake_synthesizer`, `narrate.fake_encoder`, `narrate.edge_tts_synthesizer`, `narrate.ffmpeg_encoder`, `narrate.SegmentResult`; `script.read_index` (via `narrate_approved`).
- Produces:
  - `vibe narrate [--build DIR]` (default `./build`). Exit codes: `0` success (including skips), `2` missing index/build, `1` any segment error.
  - `_select_narrator() -> tuple[narrate.Synthesizer, narrate.Encoder]` — reads `VIBE_NARRATOR`; `"fake"` → `(fake_synthesizer(), fake_encoder())`; else `(edge_tts_synthesizer(), ffmpeg_encoder())`.

- [ ] **Step 1: Write the failing tests** (new file `tests/test_cli_narrate.py`)

```python
from __future__ import annotations

import json
from pathlib import Path

from vibe import script

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _make_approved_build(run_cli, tmp_path: Path) -> Path:
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0
    return tmp_path / "build"


def test_narrate_fake_writes_artifacts_and_checks(run_cli, tmp_path: Path):
    build = _make_approved_build(run_cli, tmp_path)
    proc = run_cli(
        "narrate", "--build", str(build),
        cwd=str(tmp_path), extra_env={"VIBE_NARRATOR": "fake"},
    )
    assert proc.returncode == 0, proc.stderr
    mp3 = build / "narration" / "segment-1.mp3"
    timing = build / "narration" / "segment-1.timing.jsonl"
    assert mp3.is_file() and mp3.read_bytes() == b"fake-mp3"
    assert timing.is_file()
    lines = [json.loads(l) for l in timing.read_text(encoding="utf-8").splitlines() if l]
    assert lines and all("word" in l and "start_s" in l and "end_s" in l for l in lines)
    # the checker accepts the timing artifact
    ck = run_cli("check", str(timing))
    assert ck.returncode == 0, ck.stderr


def test_narrate_skips_needs_human(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES),
                   cwd=str(tmp_path), extra_env={"VIBE_SCRIPT_AUTHOR": "failing"})
    assert proc.returncode == 0
    build = tmp_path / "build"
    idx = json.loads((build / "scripts" / "index.json").read_text(encoding="utf-8"))
    assert all(r["status"] == script.STATUS_NEEDS_HUMAN for r in idx["scripts"])
    proc = run_cli("narrate", "--build", str(build),
                   cwd=str(tmp_path), extra_env={"VIBE_NARRATOR": "fake"})
    assert proc.returncode == 0
    assert "skipped" in proc.stderr
    assert not (build / "narration" / "segment-1.mp3").exists()


def test_narrate_missing_index_exits_2(run_cli, tmp_path: Path):
    proc = run_cli("narrate", "--build", str(tmp_path), cwd=str(tmp_path),
                   extra_env={"VIBE_NARRATOR": "fake"})
    assert proc.returncode == 2
    assert "index.json" in proc.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_cli_narrate.py -v`
Expected: FAIL — `vibe narrate` is an invalid choice.

- [ ] **Step 3: Implement**

In `vibe/cli.py`:

1. Update the module import: `from . import __version__, check, discover, layout, narrate, script`.

2. Add the subcommand after the `ck` parser block:

```python
    nar = sub.add_parser("narrate", help="synthesize narration for approved segments")
    nar.add_argument("--build", type=Path, default=Path("build"), metavar="DIR",
                     help="build root with scripts/index.json (default: ./build)")
    nar.set_defaults(_handler=_cmd_narrate)
```

3. Add the narrator selector (near `_select_script_author`):

```python
def _select_narrator() -> tuple[narrate.Synthesizer, narrate.Encoder]:
    if os.environ.get("VIBE_NARRATOR") == "fake":
        return narrate.fake_synthesizer(), narrate.fake_encoder()
    return narrate.edge_tts_synthesizer(), narrate.ffmpeg_encoder()
```

4. Add the handler (near `_cmd_check`):

```python
def _cmd_narrate(args: argparse.Namespace) -> int:
    lay = layout.Layout(root=args.build)
    if not (lay.scripts / "index.json").is_file():
        print(f"vibe narrate: no {lay.scripts.joinpath('index.json').as_posix()}; "
              f"run `vibe make` first", file=sys.stderr)
        return 2
    synthesizer, encoder = _select_narrator()
    results = narrate.narrate_approved(lay, synthesizer=synthesizer, encoder=encoder)
    failed = False
    for res in results:
        print(res.message, file=sys.stderr if not res.ok else sys.stdout)
        failed = failed or not res.ok
    return 1 if failed else 0
```

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_narrate.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite** (regression: T1–T3 must stay green)

Run: `.venv\Scripts\python -m pytest`
Expected: PASS.

- [ ] **Step 6: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add vibe/cli.py tests/test_cli_narrate.py
git commit -m "T4: wire `vibe narrate` subcommand + fake seam (#T4)"
```

---

### Task 8: Final verification, spec alignment, docs

**Files:**
- Modify: `docs/specs/narration.md` (§1 add marker-reality note)
- No test changes beyond earlier tasks.

- [ ] **Step 1: Verify full checks + offline E2E**

Run: `.venv\Scripts\python -m pytest && .venv\Scripts\python -m mypy vibe && .venv\Scripts\python -m ruff check vibe tests`
Expected: all clean.

Manual offline E2E (from the `build-t4` worktree):
```powershell
$env:VIBE_OFFLINE='1'
.\.venv\Scripts\python -m vibe make "mortgage rates" --feeds-from tests/fixtures
$env:VIBE_NARRATOR='fake'
.\.venv\Scripts\python -m vibe narrate
Get-Content build\narration\segment-1.timing.jsonl | Select-Object -First 3
.\.venv\Scripts\python -m vibe check build\narration\segment-1.timing.jsonl
Remove-Item Env:\VIBE_NARRATOR; Remove-Item Env:\VIBE_OFFLINE
```
Expected: exit 0; `segment-1.timing.jsonl` has monotonic `{word,start_s,end_s}` rows; `vibe check` prints `segment-1.timing.jsonl: OK (timing)`.

Optional **live** smoke (network, real synthesis — do NOT run in CI/tests):
```powershell
.\.venv\Scripts\python -m vibe narrate
```
Expected: real `.mp3` files present, exit 0. If the network is blocked, expect the `NarrationError` path (exit 1, no partial files) — this also validates the failure branch.

- [ ] **Step 2: Add the marker-reality note to the narration spec**

In `docs/specs/narration.md` §1 (after the "**Input:**" line), add:

> **Marker reality (2026-08):** the current templated author (`vibe/script.py`) emits only `**keyword**`. `##figure##`, `**gold**`, and `~` are fully handled by the narration pipeline (chunking, knobs, silence, timing) but do not appear in current output until the author produces figures/pauses. Downstream consumers (assembly/captions) must not assume figures are always present.

Commit.

- [ ] **Step 3: Align the design doc**

Verify `docs/superpowers/specs/2026-08-14-t4-narration-stage-design.md` matches implementation: function names (`parse_line`, `KNOBS`, `SILENCE_MS`, `build_word_timings`, `timing_jsonl`, `narrate_segment`, `narrate_approved`, `edge_tts_synthesizer`, `ffmpeg_encoder`, `fake_synthesizer`, `fake_encoder`, `SegmentNarration`, `SegmentResult`, `NarrationError`), the `Chunk`/`WordTiming` shapes, and the `VIBE_NARRATOR` seam. Fix any drift. Commit.

- [ ] **Step 4: Final commit (if anything drifted) + push branch**

```bash
git add docs/specs/narration.md docs/superpowers/specs/2026-08-14-t4-narration-stage-design.md
git commit -m "T4: marker-reality doc note + spec alignment (#T4)"
git push -u origin build/t4
```

- [ ] **Step 5: Report**

Report to the operator: branch `build/t4`, suite green (pytest/mypy/ruff), the two skip/error paths exercised, and the marker-reality note added to `docs/specs/narration.md`. Flag the live edge-tts smoke as the one network-dependent verification left for a human/CI-with-network.
