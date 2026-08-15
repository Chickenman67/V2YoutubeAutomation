"""CLI seam: `vibe assemble` builds the full video, gated on segment-1 preview.

Offline via the `VIBE_ASSEMBLER=fake` seam; the real recap/concat + check path is
covered by the gated tests in test_assembly.py and the Task 8 integration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vibe import assembly, cli

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _write_index(build: Path) -> Path:
    (build / "scripts").mkdir(parents=True, exist_ok=True)
    (build / "scripts" / "index.json").write_text(
        json.dumps({"scripts": [{"file": "segment-2.md"}]}), encoding="utf-8"
    )
    return build


def _make(build: Path, run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    nav = run_cli("narrate", "--build", str(build), cwd=str(tmp_path),
                  extra_env={"VIBE_NARRATOR": "fake"})
    assert nav.returncode == 0, nav.stderr
    return build


def test_assemble_fake_writes_full(tmp_path, run_cli):
    build = _make(tmp_path / "build", run_cli, tmp_path)
    proc = run_cli("assemble", "--build", str(build), cwd=str(tmp_path),
                   extra_env={"VIBE_ASSEMBLER": "fake", "VIBE_NARRATOR": "fake",
                              "VIBE_RENDERER": "fake"})
    assert proc.returncode == 0, proc.stderr
    assert (build / "full.mp4").is_file()


def test_assemble_missing_index_exits_2(tmp_path, run_cli):
    proc = run_cli("assemble", "--build", str(tmp_path), cwd=str(tmp_path),
                   extra_env={"VIBE_ASSEMBLER": "fake"})
    assert proc.returncode == 2
    assert "index.json" in proc.stderr


def _fake_result(rc: int, message: str = "full.mp4: OK"):
    ok = rc == 0
    return lambda *a, **k: [assembly.AssembleResult("concat", None, ok, message)]


def test_assemble_terminal_ok_exits_0(tmp_path, monkeypatch):
    build = _write_index(tmp_path / "build")
    monkeypatch.setenv("VIBE_ASSEMBLER", "fake")
    monkeypatch.setenv("VIBE_NARRATOR", "fake")
    monkeypatch.setenv("VIBE_RENDERER", "fake")
    monkeypatch.setattr(
        cli.assembly, "assemble_approved", _fake_result(0)
    )
    assert cli._cmd_assemble(argparse.Namespace(build=build)) == 0


def test_assemble_terminal_needs_human_exits_1(tmp_path, monkeypatch):
    build = _write_index(tmp_path / "build")
    monkeypatch.setenv("VIBE_ASSEMBLER", "fake")
    monkeypatch.setenv("VIBE_NARRATOR", "fake")
    monkeypatch.setenv("VIBE_RENDERER", "fake")
    monkeypatch.setattr(
        cli.assembly, "assemble_approved",
        _fake_result(1, "needs-human: segment 1 rejected after rework cap"),
    )
    assert cli._cmd_assemble(argparse.Namespace(build=build)) == 1