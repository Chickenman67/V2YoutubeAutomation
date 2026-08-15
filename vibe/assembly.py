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
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from . import config, render

# Imports grow per-task: Task 4 adds `io` + `typing`/`config`/`render`; Task 5 adds
# `os`/`subprocess`/`tempfile`; Task 6 adds `json` + `Callable`/`ThreadPoolExecutor`
# + `check`/`layout`/`narrate`/`script`. Each task's commit leaves every import used.

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