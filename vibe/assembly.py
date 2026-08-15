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
                "-c", "copy", "-f", "mp4", *config.MUX_FLAGS,
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
    if not results[-1].ok:  # gate ended on a decline (needs-human) or a render failure
        return results

    fanout = _fan_out(lay, records, hero, footline, renderer=renderer, enc=enc)
    results += fanout
    if any(not r.ok for r in fanout):  # a fan-out segment failed -> no concat, no partial full.mp4
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