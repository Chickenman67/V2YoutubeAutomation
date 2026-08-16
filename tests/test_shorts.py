from __future__ import annotations

from pathlib import Path

from vibe import check, config
from vibe.narrate import WordTiming
from vibe.shorts import build_full_srt, build_segment_srt, caption_cues, timing_end


def _words(*items: tuple[str, float, float]) -> list[WordTiming]:
    return [WordTiming(w, s, e) for w, s, e in items]


def test_timing_end():
    assert timing_end(_words(("a", 0.0, 0.2), ("b", 0.2, 0.5))) == 0.5
    assert timing_end([]) == 0.0


def test_caption_cues_verbatim_with_offset():
    timing = _words(("the", 0.0, 0.2), ("rates", 0.2, 0.4), ("climbed", 0.4, 0.6))
    assert caption_cues("the **rates** climbed", timing, offset_s=config.OPEN_PADDING_S) == \
        [(1.15, 1.75, "the rates climbed")]


def test_caption_cues_strips_markers_always():
    timing = _words(("Money", 0.0, 0.2), ("fast", 0.2, 0.4), ("hop", 0.4, 0.6))
    for _, _, text in caption_cues("~ Money ##5.25## **fast** ~ hop", timing, offset_s=0.0):
        assert "*" not in text and "#" not in text


def test_caption_cues_skips_line_with_no_spoken_word():
    timing = _words(("done", 0.5, 0.7))
    assert [t for _, _, t in caption_cues("**gold**\ndone", timing, offset_s=0.0)] == ["done"]


def test_build_segment_srt_playhead_aligned(tmp_path: Path):
    timing = _words(("hello", 0.0, 0.2), ("world", 0.2, 0.5))
    text = build_segment_srt("hello world", timing)
    assert text.startswith("1\n00:00:01,150 --> 00:00:01,650\nhello world\n\n")
    p = tmp_path / "seg.srt"
    p.write_text(text, encoding="utf-8")
    assert check.check_srt(p).ok


def test_build_full_srt_running_offsets(tmp_path: Path):
    timing = _words(("hello", 0.0, 0.2), ("world", 0.2, 0.5))  # end 0.5 -> contract dur 1.65
    text = build_full_srt([("hello world", timing), ("hello world", timing)])
    cues = [line for line in text.splitlines() if "-->" in line]
    assert cues == ["00:00:01,150 --> 00:00:01,650", "00:00:02,800 --> 00:00:03,300"]
    assert [int(l) for l in text.splitlines() if l.isdigit()] == [1, 2]
    p = tmp_path / "full.srt"
    p.write_text(text, encoding="utf-8")
    assert check.check_srt(p).ok