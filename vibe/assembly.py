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