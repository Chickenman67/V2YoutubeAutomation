"""`vibe check` — media-contract checker, exercised at the CLI seam (spec #9).

A known-good artifact passes (exit 0); a known-bad artifact fails (exit 1) naming the
violation. Contract per assembly.md §2: 1920x1080@30 full/clip, 1080x1920 short,
H.264 yuv420p, AAC-LC 44.1kHz, duration matches narration timing.
"""

from __future__ import annotations

from pathlib import Path


def test_check_known_good_full_clip_passes(run_cli, good_full):
    proc = run_cli("check", str(good_full))
    assert proc.returncode == 0
    assert "OK" in proc.stdout


def test_check_known_good_short_passes(run_cli, good_short):
    proc = run_cli("check", str(good_short), "--kind", "short")
    assert proc.returncode == 0
    assert "OK" in proc.stdout


def test_check_short_wrong_kind_rejected(run_cli, good_short):
    # 1080x1920 presented as clip(1920x1080) must fail resolution.
    proc = run_cli("check", str(good_short))
    assert proc.returncode == 1
    assert "FAIL" in proc.stderr


def test_check_rejects_wrong_resolution(run_cli, bad_resolution):
    proc = run_cli("check", str(bad_resolution))
    assert proc.returncode == 1
    assert "1280x720" in proc.stderr


def test_check_rejects_wrong_codec(run_cli, bad_codec):
    proc = run_cli("check", str(bad_codec))
    assert proc.returncode == 1
    assert "mpeg4" in proc.stderr


def test_check_rejects_wrong_pix_fmt(run_cli, bad_pixfmt):
    proc = run_cli("check", str(bad_pixfmt))
    assert proc.returncode == 1
    assert "yuv422p" in proc.stderr


def test_check_rejects_wrong_audio(run_cli, bad_audio):
    proc = run_cli("check", str(bad_audio))
    assert proc.returncode == 1
    assert "22050" in proc.stderr


def test_check_good_clip_with_matching_timing_passes(run_cli, good_full, timing_for_good):
    proc = run_cli("check", str(good_full), "--timing", str(timing_for_good))
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_check_rejects_duration_mismatch(run_cli, bad_duration, timing_for_bad_duration):
    proc = run_cli("check", str(bad_duration), "--timing", str(timing_for_bad_duration))
    assert proc.returncode == 1
    assert "duration" in proc.stderr


def test_check_good_srt_passes(run_cli, good_srt):
    proc = run_cli("check", str(good_srt))
    assert proc.returncode == 0
    assert "OK" in proc.stdout


def test_check_rejects_overlapping_srt(run_cli, bad_srt):
    proc = run_cli("check", str(bad_srt))
    assert proc.returncode == 1
    assert "FAIL" in proc.stderr


def test_check_rejects_unordered_timing(run_cli, bad_timing):
    proc = run_cli("check", str(bad_timing))
    assert proc.returncode == 1
    assert "monotonic" in proc.stderr


def test_check_missing_file_errors(run_cli, tmp_path: Path):
    proc = run_cli("check", str(tmp_path / "nope.mp4"))
    assert proc.returncode == 2
    assert "ffprobe" in proc.stderr or "error" in proc.stderr.lower()