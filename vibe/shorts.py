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