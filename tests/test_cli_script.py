from __future__ import annotations

import json
from pathlib import Path

from vibe import script

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_make_with_feeds_writes_scripts_and_index(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0
    scripts_dir = tmp_path / "build" / "scripts"
    assert (scripts_dir / "segment-1.txt").is_file()
    idx = json.loads((scripts_dir / "index.json").read_text(encoding="utf-8"))
    assert all(r["status"] == script.STATUS_APPROVED for r in idx["scripts"])


def test_make_without_brief_skips_script_stage(run_cli, tmp_path: Path):
    # VIBE_OFFLINE path -> no topic -> no brief -> no scripts, best-effort exit 0.
    proc = run_cli("make", "Treasury yields", cwd=str(tmp_path))
    assert proc.returncode == 0
    scripts_dir = tmp_path / "build" / "scripts"
    assert scripts_dir.is_dir()
    assert not (scripts_dir / "index.json").exists()


def test_make_failing_author_flags_but_exits_zero(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES),
                   cwd=str(tmp_path), extra_env={"VIBE_SCRIPT_AUTHOR": "failing"})
    assert proc.returncode == 0
    idx = json.loads((tmp_path / "build" / "scripts" / "index.json").read_text(encoding="utf-8"))
    assert all(r["status"] == script.STATUS_NEEDS_HUMAN for r in idx["scripts"])