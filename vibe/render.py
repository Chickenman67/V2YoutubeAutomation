"""Render stage: PIL-frames + ffmpeg per-segment 16:9 clips with burned captions.

Consumes approved per-segment scripts + narration (`.mp3` + `.timing.jsonl`) + a hero
still and produces self-contained clips (`build/segments/segment-<n>.mp4`). Drawing
(Pillow) and the mux/encode (ffmpeg) live behind the `ImageRenderer`/`Encoder` seams
so the core stays offline-testable and deterministic. Markers are structural: never
rendered; they only style captions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple, Protocol

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


class StyledSpan(NamedTuple):
    text: str
    kind: ChunkKind


class Caption(NamedTuple):
    spans: tuple[StyledSpan, ...]
    figure: StyledSpan | None
    footline: str | None


class FrameSpec(NamedTuple):
    frame_index: int
    t: float
    scale: float
    caption: Caption | None


def _active_captions(
    t: float, lines: Sequence[CaptionLine], *, min_hold: float = config.MIN_CAPTION_HOLD_S
) -> list[CaptionLine]:
    """The caption lines whose window `[start, end + hold]` covers body-time `t`."""
    active = [line for line in lines if line.start_s <= t <= line.end_s + min_hold]
    return active


def _build_caption(line: CaptionLine, footline: str | None) -> Caption:
    spans = tuple(StyledSpan(w.surface, w.kind) for w in line.spans)
    figure = next((StyledSpan(w.surface, w.kind) for w in line.spans if w.kind == "figure"), None)
    return Caption(spans, figure, footline if line.has_figure else None)


def plan_frames(
    lines: Sequence[CaptionLine],
    *,
    fps: int,
    width: int,
    height: int,
    open_s: float = config.OPEN_PADDING_S,
    min_hold: float = config.MIN_CAPTION_HOLD_S,
    footline: str | None = None,
) -> tuple[FrameSpec, ...]:
    """The deterministic per-frame plan: zoom open, then word-timed captions.

    `t` is clip time (0 = clip start). The narration body begins at `open_s`; caption
    body-times offset by `open_s`. Total clip length = `open_s +` last word end, so the
    container honors the media contract (`OPEN_PADDING_S +` narration end).
    """
    last_end = max((line.end_s for line in lines), default=0.0)
    duration = open_s + last_end
    n = round(duration * fps)
    specs: list[FrameSpec] = []
    for i in range(n):
        t = i / fps
        scale = zoom_scale(t)
        caption = None
        if t >= open_s:
            body_t = t - open_s
            active = _active_captions(body_t, lines, min_hold=min_hold)
            if active:
                caption = _build_caption(active[-1], footline)
        specs.append(FrameSpec(i, round(t, 6), scale, caption))
    return tuple(specs)


def resolve_font(size: int, *, font: str | None = None) -> object:
    """A PIL font: a real outline font when `font` names a TTF, else the default.

    Lazy-imports Pillow so the module stays importable where Pillow is absent; the
    returned object is Duck-typed (has `getbbox`/`draw`), so callers need no PIL types.
    """
    from PIL import ImageFont

    if font:
        return ImageFont.truetype(font, size)
    return ImageFont.load_default(size=size)


class ImageRenderer(Protocol):
    def __call__(
        self,
        specs: tuple[FrameSpec, ...],
        hero: object,
        *,
        palette: dict[str, str],
    ) -> tuple[bytes, ...]: ...


class Encoder(Protocol):
    def __call__(
        self,
        frames: tuple[bytes, ...],
        *,
        width: int,
        height: int,
        fps: int,
        audio: bytes,
    ) -> bytes: ...


def fake_renderer() -> ImageRenderer:
    """Deterministic offline frame renderer for tests and the CLI fake seam."""

    def _r(specs: tuple[FrameSpec, ...], hero: object, *, palette: dict[str, str]) -> tuple[bytes, ...]:
        return tuple(b"frame-%d" % s.frame_index for s in specs)

    return _r


def fake_encoder() -> Encoder:
    """Deterministic offline mux/encode for tests and the CLI fake seam."""

    def _enc(frames: tuple[bytes, ...], *, width: int, height: int, fps: int, audio: bytes) -> bytes:
        return b"fake-mp4"

    return _enc