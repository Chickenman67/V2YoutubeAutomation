"""`vibe make "<input>"` performs discovery and writes build/brief.json (ticket #10).

Offline at the CLI seam via `--feeds-from`: the subprocess reads local RSS fixtures
(spec #9 – no network, no live services). Acceptance: a niche/thesis yields a brief with
a title and 4-6 ordered segments; off-topic candidates are rejected; every source is
tracked; the brief follows the Topic Brief schema.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

ON_TOPIC = {
    "Fed keeps rates high and mortgage costs keep climbing",
    "What the Fed's steady rates mean for your mortgage",
}


def test_make_with_feeds_writes_a_topic_brief(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    brief = tmp_path / "build" / "brief.json"
    assert brief.is_file()
    payload = json.loads(brief.read_text(encoding="utf-8"))
    tb = payload["topic_brief"]
    assert tb["title"] in ON_TOPIC
    assert tb["status"] == "ready"
    assert tb["input"]["niche"] == "mortgage rates"
    assert 4 <= len(tb["segments"]) <= 6
    assert [s["index"] for s in tb["segments"]] == list(range(1, len(tb["segments"]) + 1))


def test_make_rejects_off_topic_candidates_that_would_otherwise_score_high(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0
    tb = json.loads((tmp_path / "build" / "brief.json").read_text(encoding="utf-8"))["topic_brief"]
    assert tb["title"] in ON_TOPIC
    assert "Nvidia" not in tb["title"]
    assert "Grimaldi" not in tb["title"]


def test_make_tracks_every_source_in_the_brief(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0
    tb = json.loads((tmp_path / "build" / "brief.json").read_text(encoding="utf-8"))["topic_brief"]
    assert tb["sources"]
    for src in tb["sources"]:
        assert src["title"] and src["url"].startswith("http")
        assert src["publisher"] and src["feed"] and src["published"].endswith("Z")


def test_make_with_fed_feeds_uses_the_thesis_field_and_gates(run_cli, tmp_path: Path):
    proc = run_cli("make", "the Fed hiked rates far too slowly", "--feeds-from", str(FIXTURES), cwd=str(tmp_path))
    assert proc.returncode == 0
    tb = json.loads((tmp_path / "build" / "brief.json").read_text(encoding="utf-8"))["topic_brief"]
    assert tb["input"]["thesis"] == "the Fed hiked rates far too slowly"
    assert tb["input"]["niche"] is None
    assert "fed" in tb["title"].lower() or "rate" in tb["title"].lower()


def test_make_offline_without_feeds_still_builds_layout_but_no_brief(run_cli, tmp_path: Path):
    proc = run_cli("make", "Treasury yields", cwd=str(tmp_path))
    assert proc.returncode == 0
    assert (tmp_path / "build" / "manifest.json").is_file()
    assert not (tmp_path / "build" / "brief.json").exists()
    assert "manifest" in (proc.stdout + proc.stderr).lower()