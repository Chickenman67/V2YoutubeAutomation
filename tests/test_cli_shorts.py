from __future__ import annotations

import argparse
import json
from pathlib import Path

from vibe import cli, shorts

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _make(build: Path, run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    nav = run_cli("narrate", "--build", str(build), cwd=str(tmp_path),
                  extra_env={"VIBE_NARRATOR": "fake"})
    assert nav.returncode == 0, nav.stderr
    (build / "hero.png").write_bytes(b"hero")  # avoid make_hero/Pillow in the fake CLI path
    return build


def test_shorts_fake_writes_short_and_cc(tmp_path, run_cli):
    build = _make(tmp_path / "build", run_cli, tmp_path)
    proc = run_cli("shorts", "--build", str(build), cwd=str(tmp_path),
                   extra_env={"VIBE_RENDERER": "fake"})
    assert proc.returncode == 0, proc.stderr
    assert (build / "shorts" / "short-1.mp4").is_file()
    assert (build / "cc" / "segment-1.srt").is_file()
    assert (build / "cc" / "full.srt").is_file()


def test_shorts_missing_index_exits_2(tmp_path, run_cli):
    proc = run_cli("shorts", "--build", str(tmp_path), cwd=str(tmp_path),
                   extra_env={"VIBE_RENDERER": "fake"})
    assert proc.returncode == 2
    assert "index.json" in proc.stderr


def _write_index(build: Path) -> Path:
    (build / "scripts").mkdir(parents=True, exist_ok=True)
    (build / "scripts" / "index.json").write_text(
        json.dumps({"scripts": [{"file": "segment-1.txt"}]}), encoding="utf-8")
    return build


def _fake_result(rc: int, message: str = "short-1.mp4: OK"):
    ok = rc == 0
    return lambda *a, **k: [shorts.ShortResult(1, "approved", ok, message)]


def test_shorts_terminal_ok_exits_0(tmp_path, monkeypatch):
    build = _write_index(tmp_path / "build")
    monkeypatch.setenv("VIBE_RENDERER", "fake")
    monkeypatch.setattr(cli.shorts, "render_shorts", _fake_result(0))
    assert cli._cmd_shorts(argparse.Namespace(build=build)) == 0


def test_shorts_terminal_failure_exits_1(tmp_path, monkeypatch):
    build = _write_index(tmp_path / "build")
    monkeypatch.setenv("VIBE_RENDERER", "fake")
    monkeypatch.setattr(cli.shorts, "render_shorts", _fake_result(1, "short-1.mp4: error"))
    assert cli._cmd_shorts(argparse.Namespace(build=build)) == 1