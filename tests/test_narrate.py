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


from vibe.narrate import Chunk, parse_line


def _kinds(chunks: list[Chunk]) -> list[str]:
    return [c.kind for c in chunks]


def _texts(chunks: list[Chunk]) -> list[str]:
    return [c.text for c in chunks]


def test_parse_line_no_markers_is_single_base_chunk():
    chunks = parse_line("Rates are up this month.")
    assert _kinds(chunks) == ["base"]
    assert _texts(chunks) == ["Rates are up this month."]


def test_parse_line_keyword_marker():
    chunks = parse_line("Here's the thing though, the **rates** is the story.")
    assert _kinds(chunks) == ["base", "keyword", "base"]
    assert _texts(chunks) == ["Here's the thing though, the ", "rates", " is the story."]
    kw = chunks[1]
    assert kw.pre_silence_ms == 120 and kw.post_silence_ms == 0


def test_parse_line_figure_marker():
    chunks = parse_line("Up ##5.25## now.")
    assert _kinds(chunks) == ["base", "figure", "base"]
    assert _texts(chunks) == ["Up ", "5.25", " now."]
    fig = chunks[1]
    assert fig.pre_silence_ms == 0 and fig.post_silence_ms == 450


def test_parse_line_gold_marker_is_structural():
    chunks = parse_line("**gold** for you.")
    assert _kinds(chunks) == ["gold", "base"]
    assert chunks[0].text == ""
    assert chunks[0].post_silence_ms == 450


def test_parse_line_beat_pause():
    chunks = parse_line("Money moves ~ fast.")
    assert _kinds(chunks) == ["base", "pause", "base"]
    assert chunks[1].text == ""
    assert chunks[1].pre_silence_ms == 300


def test_parse_line_consecutive_base_runs_merge():
    chunks = parse_line("a **b** c d")
    assert _kinds(chunks) == ["base", "keyword", "base"]
    assert _texts(chunks) == ["a ", "b", " c d"]


def test_parse_line_markers_never_appear_in_text():
    chunks = parse_line("**rates** ~ ##5.25## **gold** tail.")
    for c in chunks:
        assert "*" not in c.text and "#" not in c.text and "~" not in c.text
