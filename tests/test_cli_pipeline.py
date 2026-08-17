from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe import check, config

FIXTURES = Path(__file__).resolve().parent / "fixtures"
APPROVED = "approved"


def _spoken_lines(text: str) -> list[str]:
    """Verbatim spoken lines (markers stripped, word-normalized) -- one per CC cue.

    A marker-only (e.g. a lone `~`) or blank line yields no spoken words and so no
    cue, and is therefore skipped -- it must not enter the cue-count comparison.
    """
    out: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        stripped = " ".join(line.replace("**", " ").replace("##", " ").replace("~", " ").split())
        if stripped:
            out.append(stripped)
    return out


def _srt_texts(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    cur: list[str] = []
    for raw in lines[1:]:
        if raw.strip() == "":
            if cur:
                out.append(" ".join(cur))
                cur = []
        elif "-->" in raw or raw.strip().isdigit():
            continue
        else:
            cur.append(raw.strip())
    if cur:
        out.append(" ".join(cur))
    return out


def _assert_srt_sync(build: Path, n: int) -> None:
    """CC sidecar syncs to .timing.jsonl: first cue at open, last at narration end,
    cue texts == the script's spoken words (markers stripped), in order."""
    text = (build / "scripts" / f"segment-{n}.txt").read_text(encoding="utf-8")
    timing_path = build / "narration" / f"segment-{n}.timing.jsonl"
    lines = timing_path.read_text(encoding="utf-8").splitlines()
    ends = [float(json.loads(l)["end_s"]) for l in lines if l.strip()]
    srt = build / "cc" / f"segment-{n}.srt"
    cues = check._srt_cues(srt)
    assert len(cues) == len(_spoken_lines(text)), (len(cues), len(_spoken_lines(text)))
    assert cues[0][0] >= config.OPEN_PADDING_S - 0.5
    assert abs(cues[-1][1] - (config.OPEN_PADDING_S + ends[-1])) <= 0.5
    assert [_normalize(t) for t in _srt_texts(srt)] == _spoken_lines(text)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def test_pipeline_smoke(tmp_path, run_cli, ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    build = tmp_path / "build"

    def run(*args, **kw):
        proc = run_cli(*args, cwd=str(tmp_path), **kw)
        assert proc.returncode == 0, proc.stderr
        return proc

    run("make", "mortgage rates", "--feeds-from", str(FIXTURES), "--segments", "2")
    run("narrate", "--build", str(build), extra_env={"VIBE_NARRATOR": "offline"})
    run("render", "--build", str(build))
    run("assemble", "--build", str(build), extra_env={"VIBE_NARRATOR": "offline"})
    run("shorts", "--build", str(build))

    idx = json.loads((build / "scripts" / "index.json").read_text(encoding="utf-8"))
    approved = [r for r in idx["scripts"] if r["status"] == APPROVED]
    assert approved, "fixture topic produced no approved segments"

    # AC #2: media contract on every clip, every short, and the full video.
    assert check.check_video(build / "full.mp4", kind="full").ok
    clip_durs: list[float] = []
    for rec in approved:
        n = int(rec["index"])
        clip = build / "segments" / f"segment-{n}.mp4"
        short = build / "shorts" / f"short-{n}.mp4"
        assert check.check_video(clip, kind="clip").ok, clip
        assert check.check_video(short, kind="short").ok, short
        # AC #3: duration matches narration.
        timing = build / "narration" / f"segment-{n}.timing.jsonl"
        assert check.check_video(clip, kind="clip", timing=timing).ok, clip
        # AC #3: CC ordered + synced.
        assert check.check_srt(build / "cc" / f"segment-{n}.srt").ok
        _assert_srt_sync(build, n)
        probe = check.probe_media(clip)
        assert probe.container_duration is not None
        clip_durs.append(probe.container_duration)

    assert check.check_srt(build / "cc" / "full.srt").ok
    # Full-video aggregate duration = sum of clips + recap tail.
    full = check.probe_media(build / "full.mp4")
    assert full.container_duration is not None
    expected = sum(clip_durs) + config.RECAP_SECONDS
    assert abs(full.container_duration - expected) <= config.DURATION_TOLERANCE_S