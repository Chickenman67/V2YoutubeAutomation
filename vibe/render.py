"""Render stage: PIL-frames + ffmpeg per-segment 16:9 clips with burned captions.

Consumes approved per-segment scripts + narration (`.mp3` + `.timing.jsonl`) + a hero
still and produces self-contained clips (`build/segments/segment-<n>.mp4`). Drawing
(Pillow) and the mux/encode (ffmpeg) live behind the `ImageRenderer`/`Encoder` seams
so the core stays offline-testable and deterministic. Markers are structural: never
rendered; they only style captions.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast

from . import config, layout, script
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


class RenderError(RuntimeError):
    """Raised when the real ffmpeg encode fails."""


def ffmpeg_encoder() -> Encoder:
    """Real encoder: raw rgb24 frames + narration mp3 -> deterministic .mp4."""

    def _enc(
        frames: tuple[bytes, ...],
        *,
        width: int,
        height: int,
        fps: int,
        audio: bytes,
    ) -> bytes:
        raw_path: str | None = None
        audio_path: str | None = None
        mp4_path: str | None = None
        proc = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as rf:
                raw_path = rf.name
                for frame in frames:
                    rf.write(frame)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as af:
                audio_path = af.name
                af.write(audio)
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as mf:
                mp4_path = mf.name
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{width}x{height}", "-r", str(fps), "-i", raw_path,
                "-i", audio_path,
                *config.VIDEO_ENCODE_FLAGS,
                *config.AUDIO_ENCODE_FLAGS,
                "-shortest",
                *config.MUX_FLAGS,
                mp4_path,
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, check=False)
            except OSError as exc:
                raise RenderError(f"ffmpeg not found: {exc}") from exc
            if proc is None or proc.returncode != 0:
                detail = repr(proc.stderr) if proc is not None else "not run"
                raise RenderError(f"ffmpeg encode failed: {detail}")
            with open(mp4_path, "rb") as mh:
                return mh.read()
        finally:
            if raw_path is not None:
                os.remove(raw_path)
            if audio_path is not None:
                os.remove(audio_path)
            if mp4_path is not None:
                os.remove(mp4_path)

    return _enc


def _spoken_tokens(line: str) -> int:
    return sum(
        len(chunk.text.split())
        for chunk in parse_line(line)
        if chunk.kind != "pause" and chunk.text.strip()
    )


def _caption_lines(script_text: str, timing: Sequence[WordTiming]) -> list[CaptionLine]:
    out: list[CaptionLine] = []
    cursor = 0
    for line in script_text.splitlines():
        if not line.strip():
            continue
        expected = _spoken_tokens(line)
        line_timings = timing[cursor : cursor + expected]
        cursor += expected
        cap = parse_caption_line(line, line_timings)
        if cap is not None:
            out.append(cap)
    return out


def read_timing(path: Path) -> list[WordTiming]:
    words: list[WordTiming] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            words.append(WordTiming(str(obj["word"]), float(obj["start_s"]), float(obj["end_s"])))
    return words


def render_segment(
    script_text: str,
    timing: Sequence[WordTiming],
    mp3: bytes,
    footline: str | None,
    hero: bytes,
    *,
    renderer: ImageRenderer,
    encoder: Encoder,
    fps: int = config.FPS,
    width: int = config.FULL_WIDTH,
    height: int = config.FULL_HEIGHT,
) -> bytes:
    """Render one approved segment's script + narration into a self-contained clip."""
    lines = _caption_lines(script_text, timing)
    specs = plan_frames(lines, fps=fps, width=width, height=height, footline=footline)
    frames = renderer(specs, hero, palette=config.PALETTE)
    return encoder(frames, width=width, height=height, fps=fps, audio=mp3)


@dataclass(frozen=True)
class SegmentRenderResult:
    index: int
    status: str
    ok: bool
    message: str


def _write_atomic(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _footline(brief: dict[str, object]) -> str | None:
    tb = cast(dict[str, object], brief["topic_brief"])
    sources = cast(list[object], tb.get("sources", []))
    for row in sources:
        source = cast(dict[str, object], row)
        pub = source.get("publisher")
        if pub:
            return f"Source: {pub}"
    return None


def render_approved(
    lay: layout.Layout,
    *,
    renderer: ImageRenderer,
    encoder: Encoder,
    font: object | None = None,
) -> list[SegmentRenderResult]:
    """Render every `approved` segment; skip others; write `segment-<n>.mp4`."""
    brief = json.loads(lay.topic_brief.read_text(encoding="utf-8"))
    footline = _footline(brief)
    if not lay.hero.is_file():
        _write_atomic(lay.hero, make_hero(brief, font=font))
    hero = lay.hero.read_bytes()
    idx = script.read_index(lay)
    rows = cast(list[object], idx["scripts"])
    results: list[SegmentRenderResult] = []
    for row in rows:
        rec = cast(dict[str, object], row)
        n = int(cast(Any, rec["index"]))
        status = str(rec["status"])
        mp4 = lay.segments / f"segment-{n}.mp4"
        if status != script.STATUS_APPROVED:
            results.append(SegmentRenderResult(n, status, False, f"segment-{n}.mp4: skipped ({status})"))
            continue
        try:
            text = (lay.scripts / str(rec["file"])).read_text(encoding="utf-8")
            timing = read_timing(lay.narration / f"segment-{n}.timing.jsonl")
            mp3 = (lay.narration / f"segment-{n}.mp3").read_bytes()
            clip = render_segment(text, timing, mp3, footline, hero,
                                  renderer=renderer, encoder=encoder)
        except (RenderError, OSError, ValueError, KeyError) as exc:
            results.append(SegmentRenderResult(n, status, False, f"segment-{n}.mp4: error: {exc}"))
            continue
        _write_atomic(mp4, clip)
        results.append(SegmentRenderResult(n, status, True, f"segment-{n}.mp4: OK"))
    return results