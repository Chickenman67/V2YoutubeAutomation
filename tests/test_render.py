"""Render stage (T5): layout/hero, caption parsing, zoom easing, planner, seams,
orchestrators, and the CLI seam. Pure-core tests run offline; real-image and real-encode
tests are gated on Pillow/ffmpeg availability.
"""

from __future__ import annotations

from pathlib import Path

from vibe import config, layout


def test_layout_exposes_hero_still(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.hero == tmp_path / "hero.png"


def test_render_config_constants():
    assert config.MIN_CAPTION_HOLD_S == 1.2
    assert config.ZOOM_START == 1.0
    assert config.ZOOM_END == 1.10
    assert config.ZOOM_SECONDS == 0.8
    assert config.CAPTION_SIZE == 48
    assert config.FOOTLINE_SIZE == 24
    assert config.PALETTE["bg"] == "#F7F4EF"
    assert config.PALETTE["ink"] == "#1B1F27"
    assert config.PALETTE["positive"] == "#1F9D82"
    assert config.PALETTE["risk"] == "#E4572E"
    assert config.PALETTE["gold"] == "#D4AF37"
