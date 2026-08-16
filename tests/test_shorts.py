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


import io as _io

import pytest


def _blank_png(w: int, h: int) -> bytes:
    from PIL import Image as _PILImage

    buf = _io.BytesIO()
    _PILImage.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_cover_scale_vertical_default():
    from vibe.shorts import _cover_scale

    assert abs(_cover_scale(1080, 1920, 1920, 1080) - (1920 / 1080)) < 1e-9


def test_cover_scale_uses_max_ratio():
    from vibe.shorts import _cover_scale

    # hero wider than the (square) canvas -> height ratio governs
    assert abs(_cover_scale(100, 100, 200, 50) - 2.0) < 1e-9


def test_vertical_renderer_produces_rgb_frames():
    pytest.importorskip("PIL")
    from vibe.render import CaptionLine, CaptionWord, plan_frames
    from vibe.shorts import vertical_renderer

    cl = CaptionLine((CaptionWord("hi", "base", 0.0, 0.3),), 0.0, 0.3, False)
    spec = plan_frames([cl], fps=30, width=108, height=192)
    r = vertical_renderer(width=108, height=192)
    frames = r(spec, hero=b"", palette=config.PALETTE)
    assert frames
    assert all(len(f) == 108 * 192 * 3 for f in frames)


def test_vertical_renderer_accepts_hero_bytes():
    pytest.importorskip("PIL")
    from vibe.render import CaptionLine, CaptionWord, plan_frames
    from vibe.shorts import vertical_renderer

    img = _blank_png(1920, 1080)
    cl = CaptionLine((CaptionWord("hi", "base", 0.0, 0.3),), 0.0, 0.3, False)
    spec = plan_frames([cl], fps=30, width=108, height=192)
    r = vertical_renderer(width=108, height=192)
    frames = r(spec, hero=img, palette=config.PALETTE)
    assert frames and all(len(f) == 108 * 192 * 3 for f in frames)


import json

from vibe import layout, script


def _index(*rows):
    return {"video": "v", "scripts": [dict(r) for r in rows]}


def test_render_shorts_writes_short_and_cc(tmp_path):
    from vibe.render import fake_encoder, fake_renderer
    from vibe.shorts import render_shorts

    lay = layout.create_layout(tmp_path)
    (lay.topic_brief).write_text(json.dumps(
        {"topic_brief": {"title": "t", "segments": [], "sources": [{"publisher": "CNBC"}]}}),
        encoding="utf-8")
    (lay.hero).write_bytes(b"hero")
    (lay.scripts / "index.json").write_text(json.dumps(_index(
        {"index": 1, "file": "segment-1.txt", "word_count": 2,
         "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
        {"index": 2, "file": "segment-2.txt", "word_count": 0,
         "status": script.STATUS_NEEDS_HUMAN, "attempts": 3, "violations": []},
    )), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text("hello world", encoding="utf-8")
    (lay.scripts / "segment-2.txt").write_text("bad", encoding="utf-8")
    (lay.narration / "segment-1.mp3").write_bytes(b"mp3")
    (lay.narration / "segment-1.timing.jsonl").write_text(
        '{"word": "hello", "start_s": 0.0, "end_s": 0.2}\n'
        '{"word": "world", "start_s": 0.2, "end_s": 0.5}\n',
        encoding="utf-8")

    results = render_shorts(lay, renderer=fake_renderer(), encoder=fake_encoder())
    assert (lay.shorts / "short-1.mp4").read_bytes() == b"fake-mp4"
    assert not (lay.shorts / "short-2.mp4").exists()
    assert (lay.cc / "segment-1.srt").read_text(encoding="utf-8").startswith(
        "1\n00:00:01,150 --> 00:00:01,650\nhello world")
    assert (lay.cc / "full.srt").is_file()
    assert results[0].ok and "OK" in results[0].message
    assert results[1].ok is False and "skipped" in results[1].message
    assert results[-1].ok and "full.srt" in results[-1].message


def test_ffmpeg_vertical_short_matches_contract(ffmpeg_available, tmp_path):
    import subprocess

    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    from vibe import check
    from vibe.render import ffmpeg_encoder, render_segment
    from vibe.shorts import build_segment_srt, vertical_renderer

    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "0.1",
         "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True)
    mp3 = proc.stdout
    timing = [WordTiming("a", 0.0, 0.05)]
    clip = render_segment(
        "**a**", timing, mp3, None, b"",
        width=config.SHORT_WIDTH, height=config.SHORT_HEIGHT,
        renderer=vertical_renderer(), encoder=ffmpeg_encoder(),
    )
    path = tmp_path / "short-1.mp4"
    path.write_bytes(clip)
    res = check.check_video(path, kind="short")
    assert res.ok, res.failures
    srt = tmp_path / "short-1.srt"
    srt.write_text(build_segment_srt("**a**", timing), encoding="utf-8")
    assert check.check_srt(srt).ok