from __future__ import annotations

from pathlib import Path

from vibe import layout, script


def test_word_count_strips_markers():
    text = "**Rates** are up ~ 5.25 ##figure## for **gold** payoffs."
    assert script.word_count(text) == 7  # rates, are, up, 5.25, for, gold, payoffs


def test_status_constants():
    assert script.STATUS_READY == "ready"
    assert script.STATUS_APPROVED == "approved"
    assert script.STATUS_NEEDS_HUMAN == "needs-human"


def test_layout_exposes_scripts_directory(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.scripts == tmp_path / "scripts"
    assert lay.scripts.is_dir()