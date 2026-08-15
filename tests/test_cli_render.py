"""CLI seam: `vibe render` renders approved segments into self-contained clips.

Offline via the `VIBE_RENDERER=fake` seam (same idiom as `VIBE_NARRATOR`); the real
PIL+ffmpeg encode is covered by the gated test in test_render.py and the offline E2E.
"""

from __future__ import annotations

from pathlib import Path

from vibe import script

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_render_fake_writes_segment_clips(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    build = tmp_path / "build"
    idx = _read_json(build)
    assert any(r["status"] == script.STATUS_APPROVED for r in idx["scripts"])

    nav = run_cli("narrate", "--build", str(build), cwd=str(tmp_path), extra_env={"VIBE_NARRATOR": "fake"})
    assert nav.returncode == 0, nav.stderr

    ren = run_cli("render", "--build", str(build), cwd=str(tmp_path), extra_env={"VIBE_RENDERER": "fake"})
    assert ren.returncode == 0, ren.stderr
    mp4 = build / "segments" / "segment-1.mp4"
    assert mp4.is_file() and mp4.read_bytes() == b"fake-mp4"
    assert (build / "hero.png").is_file()


def test_render_skips_needs_human(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES),
                   cwd=str(tmp_path), extra_env={"VIBE_SCRIPT_AUTHOR": "failing"})
    assert proc.returncode == 0
    build = tmp_path / "build"
    idx = _read_json(build)
    assert all(r["status"] == script.STATUS_NEEDS_HUMAN for r in idx["scripts"])
    ren = run_cli("render", "--build", str(build), cwd=str(tmp_path), extra_env={"VIBE_RENDERER": "fake"})
    assert ren.returncode == 0
    assert "skipped" in ren.stderr
    assert not (build / "segments" / "segment-1.mp4").exists()


def test_render_missing_index_exits_2(run_cli, tmp_path: Path):
    proc = run_cli("render", "--build", str(tmp_path), cwd=str(tmp_path),
                   extra_env={"VIBE_RENDERER": "fake"})
    assert proc.returncode == 2
    assert "index.json" in proc.stderr


def _read_json(build: Path) -> dict:
    import json

    return json.loads((build / "scripts" / "index.json").read_text(encoding="utf-8"))