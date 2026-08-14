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