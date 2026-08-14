"""Shared fixtures: offline synthetic media artifacts for `vibe check`.

Known-good / known-bad videos are generated with ffmpeg at test time (short, low-care
clips) so the checker runs without live services or heavy renders (spec #9 testing
decisions). All tests skip cleanly when ffmpeg/ffprobe are unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _render(
    dest: Path,
    *,
    width: int,
    height: int,
    duration: float,
    vcodec: str = "libx264",
    vopts: list[str] | None = None,
    aopts: list[str] | None = None,
) -> None:
    vopts = vopts or ["-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast"]
    aopts = aopts or ["-b:a", "128k", "-ar", "44100", "-ac", "2"]
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate=30",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-t",
        str(duration),
        "-c:v",
        vcodec,
        *vopts,
        "-c:a",
        "aac",
        *aopts,
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg fixture failed: {proc.stderr[-500:]}")


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    return _has_tool("ffmpeg") and _has_tool("ffprobe")


@pytest.fixture(scope="session")
def tmp_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("vibe-check")


def _video_fixture(ffmpeg_available: bool, tmp_dir: Path, name: str, **kwargs: Any) -> Path:
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    dest = tmp_dir / name
    _render(dest, **kwargs)
    return dest


@pytest.fixture(scope="session")
def good_full(tmp_dir: Path, ffmpeg_available: bool) -> Path:
    return _video_fixture(ffmpeg_available, tmp_dir, "good-full.mp4", width=1920, height=1080, duration=2.0)


@pytest.fixture(scope="session")
def good_short(tmp_dir: Path, ffmpeg_available: bool) -> Path:
    return _video_fixture(ffmpeg_available, tmp_dir, "good-short.mp4", width=1080, height=1920, duration=2.0)


@pytest.fixture(scope="session")
def bad_resolution(tmp_dir: Path, ffmpeg_available: bool) -> Path:
    return _video_fixture(ffmpeg_available, tmp_dir, "bad-resolution.mp4", width=1280, height=720, duration=2.0)


@pytest.fixture(scope="session")
def bad_codec(tmp_dir: Path, ffmpeg_available: bool) -> Path:
    return _video_fixture(
        ffmpeg_available, tmp_dir, "bad-codec.mp4", width=1920, height=1080, duration=2.0, vcodec="mpeg4", vopts=["-q:v", "5"]
    )


@pytest.fixture(scope="session")
def bad_pixfmt(tmp_dir: Path, ffmpeg_available: bool) -> Path:
    return _video_fixture(
        ffmpeg_available,
        tmp_dir,
        "bad-pixfmt.mp4",
        width=1920,
        height=1080,
        duration=2.0,
        vcodec="libx264",
        vopts=["-pix_fmt", "yuv422p", "-crf", "18", "-preset", "veryfast"],
    )


@pytest.fixture(scope="session")
def bad_audio(tmp_dir: Path, ffmpeg_available: bool) -> Path:
    return _video_fixture(
        ffmpeg_available, tmp_dir, "bad-audio.mp4", width=1920, height=1080, duration=2.0, aopts=["-ar", "22050", "-b:a", "128k", "-ac", "2"]
    )


@pytest.fixture(scope="session")
def bad_duration(tmp_dir: Path, ffmpeg_available: bool) -> Path:
    return _video_fixture(ffmpeg_available, tmp_dir, "bad-duration.mp4", width=1920, height=1080, duration=1.0)


def write_timing(dest: Path, *, end: float) -> Path:
    lines = []
    start = 0.0
    for i in range(1, 4):
        s = start
        e = min(end, start + 0.5)
        lines.append({"word": f"w{i}", "start_s": round(s, 3), "end_s": round(e, 3)})
        start = e
        if e >= end:
            break
    dest.write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")
    return dest


@pytest.fixture(scope="session")
def timing_for_good(tmp_dir: Path) -> Path:
    # A 2s clip's container duration ~ narration_end + 1.15s open. narration_end=1.0
    # -> expected 2.15s, within tolerance of the 2.0s clip.
    return write_timing(tmp_dir / "good.timing.jsonl", end=1.0)


@pytest.fixture(scope="session")
def timing_for_bad_duration(tmp_dir: Path) -> Path:
    # A 1s clip vs narration_end=1.0 -> expected 2.15s, far from 1.0s: reject.
    return write_timing(tmp_dir / "bad-duration.timing.jsonl", end=1.0)


@pytest.fixture(scope="session")
def good_srt(tmp_dir: Path) -> Path:
    dest = tmp_dir / "good.srt"
    dest.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nFirst line\n\n"
        "2\n00:00:02,500 --> 00:00:03,500\nSecond line\n",
        encoding="utf-8",
    )
    return dest


@pytest.fixture(scope="session")
def bad_srt(tmp_dir: Path) -> Path:
    dest = tmp_dir / "bad.srt"
    dest.write_text(
        "1\n00:00:02,000 --> 00:00:03,000\nFirst\n\n"
        "2\n00:00:02,200 --> 00:00:03,200\nOverlap\n",
        encoding="utf-8",
    )
    return dest


@pytest.fixture(scope="session")
def bad_timing(tmp_dir: Path) -> Path:
    dest = tmp_dir / "bad.timing.jsonl"
    dest.write_text(
        json.dumps({"word": "a", "start_s": 1.0, "end_s": 1.5})
        + "\n"
        + json.dumps({"word": "b", "start_s": 0.2, "end_s": 0.5})
        + "\n",
        encoding="utf-8",
    )
    return dest


@pytest.fixture(scope="session")
def run_cli():
    import os
    import sys

    # Make `python -m vibe` importable regardless of cwd or install state, so the
    # CLI-seam tests run on a bare checkout (spec #9: tests at the CLI seam).
    project_root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
    # Never reach live feeds from tests (spec #9: no live services). The default
    # `vibe make` network fetch is suppressed; use `--feeds-from` for real discovery.
    env["VIBE_OFFLINE"] = "1"

    def _run(*args: str, cwd: str | None = None,
             extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = dict(env)
        if extra_env:
            run_env.update(extra_env)
        # CLOSE stdin for the subprocess. DEVNULL is unreliable for this on some
        # Windows builds (fd 0 still reports as a console), so use a closed pipe:
        # the child sees a real non-tty stdin and `vibe make` auto-approves instead
        # of prompting (spec #12: non-interactive runs gate scripts as approved).
        rfd, wfd = os.pipe()
        os.close(wfd)
        try:
            return subprocess.run(
                [sys.executable, "-m", "vibe", *args],
                capture_output=True,
                text=True,
                check=False,
                cwd=cwd or os.getcwd(),
                env=run_env,
                stdin=rfd,
            )
        finally:
            os.close(rfd)

    return _run
