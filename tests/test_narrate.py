from __future__ import annotations

from pathlib import Path

from vibe import config, layout


def test_layout_exposes_narration_directory(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.narration == tmp_path / "narration"
    assert lay.narration.is_dir()


def test_narration_config_constants():
    assert config.NARRATION_VOICE == "en-US-ChristopherNeural"
    assert config.NARRATION_MP3_BITRATE == "192k"
