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

from vibe.narrate import WordTiming
from vibe.render import CaptionLine, parse_caption_line, zoom_scale


def _words(items):
    return [(w.surface, w.kind) for w in items]


def test_zoom_scale_open_and_hold():
    assert zoom_scale(0.0) == 1.0
    assert zoom_scale(0.4) == 1.0875  # ease-out cubic midpoints
    assert zoom_scale(0.8) == 1.10
    assert zoom_scale(2.0) == 1.10
    assert zoom_scale(-1.0) == 1.0


def test_parse_caption_line_keyword():
    timings = [
        WordTiming("the", 0.0, 0.2),
        WordTiming("rates", 0.2, 0.4),
        WordTiming("climbed", 0.4, 0.6),
    ]
    cap = parse_caption_line("the **rates** climbed", timings)
    assert isinstance(cap, CaptionLine)
    assert _words(cap.spans) == [("the", "base"), ("rates", "keyword"), ("climbed", "base")]
    assert cap.start_s == 0.0 and cap.end_s == 0.6
    assert cap.has_figure is False


def test_parse_caption_line_figure_flagged():
    timings = [
        WordTiming("Up", 0.0, 0.2),
        WordTiming("5.25", 0.2, 0.4),
        WordTiming("now", 0.4, 0.6),
    ]
    cap = parse_caption_line("Up ##5.25## now", timings)
    assert _words(cap.spans) == [("Up", "base"), ("5.25", "figure"), ("now", "base")]
    assert cap.has_figure is True


def test_parse_caption_line_gold_is_not_a_word():
    timings = [WordTiming("tail", 0.0, 0.3)]
    cap = parse_caption_line("**gold** tail", timings)
    assert _words(cap.spans) == [("tail", "base")]


def test_parse_caption_line_no_markers():
    timings = [WordTiming("plain", 0.0, 0.3), WordTiming("words", 0.3, 0.6)]
    cap = parse_caption_line("plain words", timings)
    assert _words(cap.spans) == [("plain", "base"), ("words", "base")]


def test_parse_caption_line_markers_never_in_surface():
    timings = [WordTiming("rates", 0.0, 0.2)]
    cap = parse_caption_line("**rates**", timings)
    for w in cap.spans:
        assert "*" not in w.surface and "#" not in w.surface


def test_parse_caption_line_pause_adds_gap_not_word():
    timings = [
        WordTiming("Money", 0.0, 0.2),
        WordTiming("fast", 0.5, 0.7),
    ]
    cap = parse_caption_line("Money ~ fast", timings)
    assert _words(cap.spans) == [("Money", "base"), ("fast", "base")]
    assert cap.start_s == 0.0 and cap.end_s == 0.7
