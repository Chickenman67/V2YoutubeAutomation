"""Render stage: PIL-frames + ffmpeg per-segment 16:9 clips with burned captions.

Consumes approved per-segment scripts + narration (`.mp3` + `.timing.jsonl`) + a hero
still and produces self-contained clips (`build/segments/segment-<n>.mp4`). Drawing
(Pillow) and the mux/encode (ffmpeg) live behind the `ImageRenderer`/`Encoder` seams
so the core stays offline-testable and deterministic. Markers are structural: never
rendered; they only style captions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from . import config
from .narrate import ChunkKind, WordTiming, parse_line


class CaptionWord(NamedTuple):
    surface: str
    kind: ChunkKind
    start_s: float
    end_s: float


class CaptionLine(NamedTuple):
    spans: tuple[CaptionWord, ...]
    start_s: float
    end_s: float
    has_figure: bool


def parse_caption_line(
    line: str, timings: Sequence[WordTiming]
) -> CaptionLine | None:
    """Style one script line into timed caption words.

    Markers are stripped (never in the surface). Spoken words carry their chunk kind
    so a keyword bolds, a figure is flagged for the 24px source footline, and a gold
    marker (empty text) contributes no word. `None` when the line has no spoken words.
    """
    chunks = parse_line(line)
    spans: list[CaptionWord] = []
    cursor = 0
    for chunk in chunks:
        if chunk.kind == "pause" or not chunk.text.strip():
            continue
        for _ in chunk.text.split():
            if cursor >= len(timings):
                break
            tw = timings[cursor]
            spans.append(CaptionWord(tw.word, chunk.kind, tw.start_s, tw.end_s))
            cursor += 1
    if not spans:
        return None
    return CaptionLine(
        tuple(spans),
        spans[0].start_s,
        spans[-1].end_s,
        any(s.kind == "figure" for s in spans),
    )


def zoom_scale(t: float) -> float:
    """Deterministic ease-out zoom (t=0 open) from ZOOM_START to ZOOM_END."""
    if t <= 0.0:
        return config.ZOOM_START
    if t >= config.ZOOM_SECONDS:
        return config.ZOOM_END
    p = t / config.ZOOM_SECONDS
    eased = 1.0 - (1.0 - p) ** 3
    return round(config.ZOOM_START + (config.ZOOM_END - config.ZOOM_START) * eased, 4)