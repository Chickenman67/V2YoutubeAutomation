"""`vibe make` — the CLI seam for the pipeline (spec #9, ticket #13).

Establishes the deterministic build layout and manifest, idempotently. No/invalid args
exit cleanly with usage; a valid run creates build/ (segments, shorts, cc) and
records the fixed media contract.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_make_without_args_exits_with_usage(run_cli):
    proc = run_cli("make")
    assert proc.returncode == 2
    assert "thesis" in (proc.stderr + proc.stdout).lower()


def test_make_with_blank_thesis_exits_with_usage(run_cli):
    proc = run_cli("make", "   ")
    assert proc.returncode == 2
    assert "non-empty thesis" in proc.stderr


def test_make_creates_build_layout(run_cli, tmp_path: Path):
    proc = run_cli("make", "Treasury yields", cwd=str(tmp_path))
    assert proc.returncode == 0
    root = tmp_path / "build"
    assert root.is_dir()
    for sub in ("segments", "shorts", "cc"):
        assert (root / sub).is_dir()
    assert (root / "manifest.json").is_file()


def test_make_manifest_is_deterministic(run_cli, tmp_path: Path):
    proc = run_cli("make", "Treasury yields", cwd=str(tmp_path))
    assert proc.returncode == 0
    manifest = tmp_path / "build" / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    contract = payload["full"]
    assert contract["width"] == 1920 and contract["height"] == 1080
    assert payload["short"]["width"] == 1080 and payload["short"]["height"] == 1920
    assert payload["video"]["pix_fmt"] == "yuv420p"
    assert payload["audio"]["sample_rate"] == 44100
    assert "libx264" in payload["encode"]["video"]


def test_make_is_idempotent(run_cli, tmp_path: Path):
    first = run_cli("make", "Treasury yields", cwd=str(tmp_path))
    second = run_cli("make", "Treasury yields", cwd=str(tmp_path))
    assert first.returncode == 0 and second.returncode == 0
    a = (tmp_path / "build" / "manifest.json").read_bytes()
    b = (tmp_path / "build" / "manifest.json").read_bytes()
    assert a == b
    assert (tmp_path / "build" / "segments").is_dir()


def test_make_segments_two_bounds_topic(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from",
                   str(Path(__file__).resolve().parent / "fixtures"),
                   "--segments", "2", cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    build = tmp_path / "build"
    idx = json.loads((build / "scripts" / "index.json").read_text(encoding="utf-8"))
    assert len(idx["scripts"]) == 2
    brief = json.loads((build / "brief.json").read_text(encoding="utf-8"))
    assert len(brief["topic_brief"]["segments"]) == 2


def test_make_segments_out_of_range_exits_2(run_cli, tmp_path: Path):
    for bad in ("0", "6"):
        proc = run_cli("make", "mortgage rates", "--segments", bad, cwd=str(tmp_path))
        assert proc.returncode == 2
        assert "segments" in proc.stderr.lower()