"""Shorts stage (T7): native 9:16 re-render + verbatim CC sidecars (.srt).

Consumes approved per-segment scripts + narration (`.mp3` + `.timing.jsonl`) and the
hero still, and produces `build/shorts/short-<n>.mp4` (native 1080x1920, never
letterboxed) plus `build/cc/segment-<n>.srt` and `build/cc/full.srt` (verbatim captions,
markers stripped, playhead-aligned). Markers are structural: never present in SRT text.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from . import config, layout, render, script
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


def _cover_scale(canvas_w: float, canvas_h: float, hero_w: float, hero_h: float) -> float:
    """The factor that scales a hero to fully cover (never letterbox) a canvas."""
    ratio_w, ratio_h = canvas_w / hero_w, canvas_h / hero_h
    return max(ratio_w, ratio_h)


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


def _fit_factor(total: float, keep: float) -> float:
    """The uniform font scale that fits `total` px of caption within `keep` (<=1)."""
    return min(1.0, keep / max(total, 1e-9))


def _fit_layout(
    spans: Sequence[render.StyledSpan],
    cap_size: int,
    fig_size: int,
    keep: float,
) -> tuple[list[object], list[int], float]:
    """Fonts/widths for one caption, shrinking to fit `keep` (never upscales).

    Falls back to a bounded decrement-by-1 refit when integer font-size rounding
    leaves the line wider than `keep`, so the line always lands inside the safe zone.
    """
    fonts = [render.resolve_font(fig_size if s.kind == "figure" else cap_size) for s in spans]
    widths = [render._font_width(f, s.text) for s, f in zip(spans, fonts)]
    total = float(sum(widths))
    if total <= keep:
        return fonts, widths, total
    factor = _fit_factor(total, keep)
    cap = max(1, int(cap_size * factor))
    fig = max(1, int(fig_size * factor))
    while total > keep and cap > 1:
        fonts = [render.resolve_font(fig if s.kind == "figure" else cap) for s in spans]
        widths = [render._font_width(f, s.text) for s, f in zip(spans, fonts)]
        total = float(sum(widths))
        if total <= keep:
            break
        cap = max(1, cap - 1)
        fig = max(1, fig - 1)
    return fonts, widths, total


def _draw_caption(
    frame: Any,
    caption: render.Caption,
    cap_font: object,
    fig_font: object,
    foot_font: object,
    palette: dict[str, str],
    *,
    shrink: bool = False,
) -> None:
    """Draw a single-line caption in the vertical lower safe zone, shrink-to-fit.

    With `shrink` (the real renderer path) a caption wider than the safe zone is
    scaled down to one centered line inside it; otherwise (custom-font seam) it is
    drawn single-line as before.
    """
    _, ImageDraw = render._pillow()
    draw = ImageDraw.Draw(frame)
    keep = max(1, frame.width - 120)
    if shrink:
        fonts, widths, total = _fit_layout(
            caption.spans, config.CAPTION_SIZE, int(config.CAPTION_SIZE * 1.15), float(keep))
    else:
        fonts = [fig_font if s.kind == "figure" else cap_font for s in caption.spans]
        widths = [render._font_width(f, s.text) for s, f in zip(caption.spans, fonts)]
        total = float(sum(widths))
    x = (frame.width - total) / 2.0
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
                _draw_caption(frame, spec.caption, cap_font, fig_font, foot_font, palette,
                              shrink=font is None)
            out.append(frame.tobytes())
        return tuple(out)

    return _r


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