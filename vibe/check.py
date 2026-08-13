"""The media-contract checker (`vibe check`).

This is the reusable deep seam between mechanical pipeline stages: it verifies that an
artifact honours the media contract (CONTEXT.md; assembly.md §2) so stages can run
independently and in parallel. The CLI (`vibe check`) is a thin wrapper over it per
spec #9 ("contract-checking exercised through the CLI seam").

Artifact dispatch is by extension:
  - video (.mp4)  -> probe with ffprobe, assert codec/resolution/fps/audio, and when a
                     paired `.timing.jsonl` is supplied, that duration == narration.
  - captions (.srt)      -> verbatim CC sidecar, timestamp-ordered.
  - word timing (.jsonl) -> {word, start_s, end_s} lines, monotonic.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    AUDIO_CHANNELS,
    AUDIO_CODEC,
    AUDIO_PROFILE,
    AUDIO_SAMPLE_RATE,
    DURATION_TOLERANCE_S,
    FPS,
    FULL_HEIGHT,
    FULL_WIDTH,
    OPEN_PADDING_S,
    PIX_FMT,
    SHORT_HEIGHT,
    SHORT_WIDTH,
    VIDEO_CODEC,
    VIDEO_PROFILE,
)

FPS_TOLERANCE = 0.01

# The resolution half of the media contract, keyed by the CLI `--kind` value.
KIND_RESOLUTION: dict[str, tuple[int, int]] = {
    "full": (FULL_WIDTH, FULL_HEIGHT),
    "clip": (FULL_WIDTH, FULL_HEIGHT),
    "short": (SHORT_WIDTH, SHORT_HEIGHT),
}


@dataclass(frozen=True)
class CheckResult:
    kind: str
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class MediaProbe:
    video_codec: str | None = None
    video_profile: str | None = None
    pix_fmt: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_codec: str | None = None
    audio_profile: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    container_duration: float | None = None


def _parse_fraction(value: str) -> float | None:
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def probe_media(path: Path) -> MediaProbe:
    """Probe a media file with ffprobe and return its salient streams as a MediaProbe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise MediaNotFound(f"ffprobe failed on {path}: {proc.stderr.strip()}")

    data = json.loads(proc.stdout)
    video: dict[str, Any] = {}
    audio: dict[str, Any] = {}
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not video:
            video = stream
        elif stream.get("codec_type") == "audio" and not audio:
            audio = stream

    container_duration: float | None = None
    try:
        container_duration = float(data.get("format", {}).get("duration", ""))
    except (TypeError, ValueError):
        container_duration = None

    def _int(d: dict[str, Any], key: str) -> int | None:
        val = d.get(key)
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    fps: float | None = None
    rate = video.get("r_frame_rate")
    if isinstance(rate, str):
        fps = _parse_fraction(rate)

    return MediaProbe(
        video_codec=str(video.get("codec_name")) if video.get("codec_name") else None,
        video_profile=str(video.get("profile")) if video.get("profile") else None,
        pix_fmt=str(video.get("pix_fmt")) if video.get("pix_fmt") else None,
        width=_int(video, "width"),
        height=_int(video, "height"),
        fps=fps,
        audio_codec=str(audio.get("codec_name")) if audio.get("codec_name") else None,
        audio_profile=str(audio.get("profile")) if audio.get("profile") else None,
        sample_rate=_int(audio, "sample_rate"),
        channels=_int(audio, "channels"),
        container_duration=container_duration,
    )


class MediaNotFound(RuntimeError):
    """Raised when a media artifact cannot be probed."""


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if condition:
        return
    failures.append(message)


def check_video(path: Path, *, kind: str, timing: Path | None = None) -> CheckResult:
    """Check a self-contained video clip against the media contract.

    kind is "full" or "clip" (1920x1080@30) or "short" (1080x1920@30). When a timing
    file is supplied, the container duration must match the narration (open + body).
    """
    failures: list[str] = []
    try:
        want_w, want_h = KIND_RESOLUTION[kind]
    except KeyError:
        raise ValueError(f"unknown kind {kind!r} (use full, clip, or short)") from None
    probe = probe_media(path)

    _require(probe.video_codec is not None, "no video stream", failures)
    if probe.video_codec is not None:
        _require(probe.video_codec.lower() == VIDEO_CODEC, f"video codec {probe.video_codec!r} != {VIDEO_CODEC}", failures)
        _require((probe.video_profile or "").lower() == VIDEO_PROFILE, f"video profile {probe.video_profile!r} != {VIDEO_PROFILE}", failures)
        _require((probe.pix_fmt or "").lower() == PIX_FMT, f"pix_fmt {probe.pix_fmt!r} != {PIX_FMT}", failures)
        _require(probe.width == want_w and probe.height == want_h, f"resolution {probe.width}x{probe.height} != {want_w}x{want_h}", failures)
        if probe.fps is not None:
            mismatch = abs(probe.fps - FPS) / FPS > FPS_TOLERANCE
            _require(not mismatch, f"fps {probe.fps:.2f} != {FPS}", failures)

    _require(probe.audio_codec is not None, "no audio stream", failures)
    if probe.audio_codec is not None:
        _require(probe.audio_codec.lower() == AUDIO_CODEC, f"audio codec {probe.audio_codec!r} != {AUDIO_CODEC}", failures)
        _require((probe.audio_profile or "").lower() == AUDIO_PROFILE, f"audio profile {probe.audio_profile!r} != {AUDIO_PROFILE}", failures)
        _require(probe.sample_rate == AUDIO_SAMPLE_RATE, f"sample rate {probe.sample_rate!r} != {AUDIO_SAMPLE_RATE}", failures)
        _require(probe.channels == AUDIO_CHANNELS, f"channels {probe.channels!r} != {AUDIO_CHANNELS}", failures)

    if timing is not None:
        timing_end = _timing_end(timing)
        if probe.container_duration is not None and timing_end is not None:
            expected = timing_end + OPEN_PADDING_S
            ok = abs(probe.container_duration - expected) <= DURATION_TOLERANCE_S
            _require(ok, f"duration {probe.container_duration:.2f}s != narration+open {expected:.2f}s", failures)

    return CheckResult(kind=kind, failures=tuple(failures))


# --- word timing (.timing.jsonl) ---

_TIMING_KEYS = ("word", "start_s", "end_s")


def _timing_end(path: Path) -> float | None:
    try:
        lines = _parse_timing(path)
    except ValueError:
        return None
    if not lines:
        return None
    return float(max(l[2] for l in lines))


def _parse_timing(path: Path) -> list[tuple[str, float, float]]:
    lines: list[tuple[str, float, float]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not all(k in obj for k in _TIMING_KEYS):
                raise ValueError(f"missing keys in {path}: {line!r}")
            start = float(obj["start_s"])
            end = float(obj["end_s"])
            if end < start:
                raise ValueError(f"end before start in {path}: {line!r}")
            lines.append((str(obj["word"]), start, end))
    return lines


def check_timing(path: Path) -> CheckResult:
    """Check a word-timing JSONL: parseable lines, and monotonic, ordered timings."""
    failures: list[str] = []
    try:
        lines = _parse_timing(path)
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        return CheckResult(kind="timing", failures=(f"unparseable timing file: {exc}",))

    for i, (word, start, end) in enumerate(lines):
        if not word:
            failures.append(f"line {i}: empty word")
        if start < 0 or end < 0:
            failures.append(f"line {i}: negative timing")

    for i in range(1, len(lines)):
        if lines[i][1] < lines[i - 1][1]:
            failures.append(f"line {i}: start_s not monotonic")
    return CheckResult(kind="timing", failures=tuple(failures))


# --- captions (.srt) ---

_TIMECODE = re.compile(
    r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2,}):(\d{2}):(\d{2}),(\d{3})$"
)

SRT_PARSE_ERROR = "malformed .srt"


def _srt_cues(path: Path) -> list[tuple[float, float]]:
    cues: list[tuple[float, float]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    in_cue = False
    cue_start: float | None = None
    cue_end: float | None = None
    while index < len(lines):
        raw = lines[index].strip()
        if not in_cue:
            match = _TIMECODE.match(raw)
            if match is not None:
                nums = [int(g) for g in match.groups()]
                cue_start = _to_seconds(nums[0:4])
                cue_end = _to_seconds(nums[4:8])
                if cue_end < cue_start:
                    raise ValueError(SRT_PARSE_ERROR)
                in_cue = True
            elif raw == "" or raw.isdigit():
                pass
            else:
                raise ValueError(SRT_PARSE_ERROR)
        else:
            if raw == "":
                assert cue_start is not None and cue_end is not None
                cues.append((cue_start, cue_end))
                in_cue = False
                cue_start = None
                cue_end = None
        index += 1
    if in_cue:
        assert cue_start is not None and cue_end is not None
        cues.append((cue_start, cue_end))
    return cues


def _to_seconds(hmsm: list[int]) -> float:
    h, m, s, ms = hmsm
    return h * 3600 + m * 60 + s + ms / 1000.0


def check_srt(path: Path) -> CheckResult:
    """Check a CC sidecar: well-formed cues, and timestamps ordered (no overlap/regress)."""
    failures: list[str] = []
    try:
        cues = _srt_cues(path)
    except ValueError as exc:
        return CheckResult(kind="srt", failures=(f"{exc}: {path.name}",))
    if not cues:
        return CheckResult(kind="srt", failures=("no cues found",))
    for i in range(1, len(cues)):
        if cues[i][0] < cues[i - 1][1]:
            failures.append(f"cue {i + 1} starts before previous cue ends")
    return CheckResult(kind="srt", failures=tuple(failures))


# --- dispatch ---

_VIDEO_EXTENSIONS = {".mp4"}


def check_artifact(path: Path, *, kind: str | None = None, timing: Path | None = None) -> CheckResult:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        if kind is not None or timing is not None:
            raise ValueError("kind/timing do not apply to .srt")
        return check_srt(path)
    if suffix == ".jsonl":
        if kind is not None or timing is not None:
            raise ValueError("kind/timing do not apply to .jsonl")
        return check_timing(path)
    if suffix in _VIDEO_EXTENSIONS:
        kind = kind or "clip"
        if kind not in KIND_RESOLUTION:
            raise ValueError(f"unknown kind {kind!r} (use full, clip, or short)")
        return check_video(path, kind=kind, timing=timing)
    raise ValueError(f"unsupported artifact type {path.name!r} (use .mp4, .srt, or .timing.jsonl)")