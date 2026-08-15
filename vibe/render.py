"""Render stage: PIL-frames + ffmpeg per-segment 16:9 clips with burned captions.

Consumes approved per-segment scripts + narration (`.mp3` + `.timing.jsonl`) + a hero
still and produces self-contained clips (`build/segments/segment-<n>.mp4`). Drawing
(Pillow) and the mux/encode (ffmpeg) live behind the `ImageRenderer`/`Encoder` seams
so the core stays offline-testable and deterministic. Markers are structural: never
rendered; they only style captions.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Any, NamedTuple, Protocol, cast

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


def _pillow() -> tuple[Any, Any]:
    """Lazy-import Pillow so the module stays importable without it."""
    from PIL import Image, ImageDraw

    return Image, ImageDraw


def _open_hero(hero: object) -> Any:
    """Decode hero PNG bytes into a PIL Image (or None when absent/invalid)."""
    if not isinstance(hero, (bytes, bytearray)):
        return None
    Image, _ = _pillow()
    try:
        return Image.open(io.BytesIO(bytes(hero)))
    except Exception:  # noqa: BLE001 - invalid bytes: fall back to the plain bg
        return None


def _font_width(font: Any, text: str) -> int:
    bbox = font.getbbox(text)
    return int(bbox[2] - bbox[0])


def _paste_zoom(canvas: Any, hero_img: Any, scale: float) -> None:
    if hero_img is None:
        return
    w, h = hero_img.size
    nw = max(1, round(w * scale))
    nh = max(1, round(h * scale))
    if (nw, nh) == (w, h):
        scaled = hero_img
    else:
        Image, _ = _pillow()
        scaled = hero_img.resize((nw, nh), Image.LANCZOS)
    x = (canvas.width - nw) // 2
    y = (canvas.height - nh) // 2
    canvas.paste(scaled, (x, y))


# ChunkKind -> design-standard §6 palette role (color carries meaning).
_KIND_COLOUR: dict[ChunkKind, str] = {
    "base": "ink",
    "keyword": "positive",
    "figure": "risk",
    "gold": "gold",
}


def _draw_caption(
    frame: Any,
    caption: Caption,
    cap_font: object,
    fig_font: object,
    foot_font: object,
    palette: dict[str, str],
) -> None:
    """Draw a single line caption, partial-emphasis by colour/size, centered."""
    _, ImageDraw = _pillow()
    draw = ImageDraw.Draw(frame)
    fonts = [fig_font if s.kind == "figure" else cap_font for s in caption.spans]
    widths = [_font_width(f, s.text) for s, f in zip(caption.spans, fonts)]
    total = sum(widths)
    x = (frame.width - total) / 2.0
    baseline = frame.height - 260
    for span, font, w in zip(caption.spans, fonts, widths):
        draw.text((x, baseline), span.text, font=font,
                  fill=palette[_KIND_COLOUR[span.kind]], anchor="ls")
        x += w
    if caption.footline:
        draw.text((frame.width / 2.0, frame.height - 70), caption.footline,
                  font=foot_font, fill=palette["ink"], anchor="ms")


def pillow_renderer(
    *,
    width: int = config.FULL_WIDTH,
    height: int = config.FULL_HEIGHT,
    font: object | None = None,
) -> ImageRenderer:
    """Real frame renderer: paper-bg + zoomed hero + burned caption per FrameSpec."""

    def _r(specs: tuple[FrameSpec, ...], hero: object, *, palette: dict[str, str]) -> tuple[bytes, ...]:
        Image, _ = _pillow()
        hero_img = _open_hero(hero)
        cap_font = font if font is not None else resolve_font(config.CAPTION_SIZE)
        fig_font = font if font is not None else resolve_font(int(config.CAPTION_SIZE * 1.15))
        foot_font = font if font is not None else resolve_font(config.FOOTLINE_SIZE)
        out: list[bytes] = []
        for spec in specs:
            frame = Image.new("RGB", (width, height), palette["bg"])
            _paste_zoom(frame, hero_img, spec.scale)
            if spec.caption is not None:
                _draw_caption(frame, spec.caption, cap_font, fig_font, foot_font, palette)
            out.append(frame.tobytes())
        return tuple(out)

    return _r


def make_hero(brief: dict[str, object], *, font: object | None = None) -> bytes:
    """The deterministic 16:9 title still (PNG) the zoom opens from."""
    Image, ImageDraw = _pillow()
    tb = cast(dict[str, object], brief["topic_brief"])
    palette = config.PALETTE
    img = Image.new("RGB", (config.FULL_WIDTH, config.FULL_HEIGHT), palette["bg"])
    draw = ImageDraw.Draw(img)
    title_font = font if font is not None else resolve_font(72)
    seg_font = font if font is not None else resolve_font(36)
    title = str(tb["title"])
    draw.text((img.width / 2.0, img.height * 0.4), title, font=title_font,
              fill=palette["ink"], anchor="mm")
    y = img.height * 0.62
    segs = cast(list[object], tb.get("segments", []))
    for seg in segs:
        segd = cast(dict[str, object], seg)
        draw.text((img.width / 2.0, y), str(segd["title"]), font=seg_font,
                  fill=palette["positive"], anchor="mm")
        y += 64
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()