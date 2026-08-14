from __future__ import annotations

import json
from pathlib import Path

import vibe.cli
from vibe import narrate, script

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _make_approved_build(run_cli, tmp_path: Path) -> Path:
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0
    return tmp_path / "build"


def test_narrate_fake_writes_artifacts_and_checks(run_cli, tmp_path: Path):
    build = _make_approved_build(run_cli, tmp_path)
    proc = run_cli(
        "narrate", "--build", str(build),
        cwd=str(tmp_path), extra_env={"VIBE_NARRATOR": "fake"},
    )
    assert proc.returncode == 0, proc.stderr
    mp3 = build / "narration" / "segment-1.mp3"
    timing = build / "narration" / "segment-1.timing.jsonl"
    assert mp3.is_file() and mp3.read_bytes() == b"fake-mp3"
    assert timing.is_file()
    lines = [json.loads(l) for l in timing.read_text(encoding="utf-8").splitlines() if l]
    assert lines and all("word" in l and "start_s" in l and "end_s" in l for l in lines)
    # the checker accepts the timing artifact
    ck = run_cli("check", str(timing))
    assert ck.returncode == 0, ck.stderr


def test_narrate_skips_needs_human(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES),
                   cwd=str(tmp_path), extra_env={"VIBE_SCRIPT_AUTHOR": "failing"})
    assert proc.returncode == 0
    build = tmp_path / "build"
    idx = json.loads((build / "scripts" / "index.json").read_text(encoding="utf-8"))
    assert all(r["status"] == script.STATUS_NEEDS_HUMAN for r in idx["scripts"])
    proc = run_cli("narrate", "--build", str(build),
                   cwd=str(tmp_path), extra_env={"VIBE_NARRATOR": "fake"})
    assert proc.returncode == 0
    assert "skipped" in proc.stderr
    assert not (build / "narration" / "segment-1.mp3").exists()


def test_narrate_missing_index_exits_2(run_cli, tmp_path: Path):
    proc = run_cli("narrate", "--build", str(tmp_path), cwd=str(tmp_path),
                   extra_env={"VIBE_NARRATOR": "fake"})
    assert proc.returncode == 2
    assert "index.json" in proc.stderr


def test_narrate_synth_failure_exits_1(run_cli, tmp_path: Path, monkeypatch, capsys):
    build = _make_approved_build(run_cli, tmp_path)

    def raiser(text, *, voice, rate, volume):
        raise narrate.NarrationError("boom")

    monkeypatch.setattr(vibe.cli, "_select_narrator",
                        lambda: (raiser, narrate.fake_encoder()))
    rc = vibe.cli.main(["narrate", "--build", str(build)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "boom" in captured.err
    assert not (build / "narration" / "segment-1.mp3").exists()
