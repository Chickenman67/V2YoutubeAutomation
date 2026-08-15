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

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

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