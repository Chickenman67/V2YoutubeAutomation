"""Narration stage: marker chunking, knob/silence mapping, word-timing math.

Consumes approved per-segment scripts (`build/scripts/segment-<n>.txt` + the index)
and produces narration audio (`build/narration/segment-<n>.mp3`) plus cumulative
word timing (`build/narration/segment-<n>.timing.jsonl`). Real TTS (edge-tts) and
the audio codec (ffmpeg) live behind the `Synthesizer`/`Encoder` seams so the core
stays offline-testable and deterministic. Markers are structural: never spoken,
never present in output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

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
            local_end = max(local_end, end)
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