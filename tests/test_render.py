"""Render stage (T5): layout/hero, caption parsing, zoom easing, planner, seams,
PIL renderer/hero, orchestrators, and the CLI seam. Pure-core tests run offline;
real-image and real-encode tests are gated on Pillow/ffmpeg availability.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe import config, layout, script
from vibe.narrate import WordTiming
from vibe.render import (
    Caption,
    CaptionLine,
    CaptionWord,
    FrameSpec,
    StyledSpan,
    _active_captions,
    parse_caption_line,
    plan_frames,
    zoom_scale,
)


def _cl(spans, start, end, has_figure=False):
    return CaptionLine(tuple(spans), start, end, has_figure)


def _w(surface, kind, s, e):
    return CaptionWord(surface, kind, s, e)


def _words(items):
    return [(w.surface, w.kind) for w in items]


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


def test_active_captions_window_and_hold():
    l1 = _cl((_w("a", "base", 0.0, 0.2), _w("b", "base", 0.2, 0.5)), 0.0, 0.5)
    l2 = _cl((_w("c", "base", 0.8, 1.0),), 0.8, 1.0)
    lines = [l1, l2]
    assert _active_captions(0.3, lines, min_hold=1.2) == [l1]
    assert _active_captions(0.6, lines, min_hold=1.2) == [l1]
    assert _active_captions(0.9, lines, min_hold=1.2) == [l1, l2]
    assert _active_captions(2.5, lines, min_hold=1.2) == []


def test_plan_frames_open_has_no_caption_and_zooms():
    l1 = _cl((_w("a", "base", 0.0, 0.2), _w("b", "base", 0.2, 0.5)), 0.0, 0.5)
    spec = plan_frames([l1], fps=30, width=1920, height=1080)
    assert spec[0].caption is None
    assert spec[0].scale == 1.0
    assert spec[0].t == 0.0


def test_plan_frames_count_covers_open_and_body():
    l1 = _cl((_w("a", "base", 0.0, 0.5),), 0.0, 0.5)
    spec = plan_frames([l1], fps=30, width=1920, height=1080)
    duration = 1.15 + 0.5
    assert len(spec) == round(duration * 30)
    assert spec[-1].t == round((len(spec) - 1) / 30, 6)


def test_plan_frames_body_shows_caption():
    l1 = _cl((_w("a", "base", 0.0, 0.2), _w("b", "base", 0.2, 0.5)), 0.0, 0.5)
    spec = plan_frames([l1], fps=30, width=1920, height=1080)
    body = [f for f in spec if f.caption is not None]
    assert body
    assert all(f.caption.spans == (StyledSpan("a", "base"), StyledSpan("b", "base")) for f in body)
    assert spec[-1].scale == 1.10


def test_plan_frames_figure_captured_with_footline():
    lf = _cl(
        (_w("Up", "base", 0.0, 0.2), _w("5.25", "figure", 0.2, 0.4), _w("now", "base", 0.4, 0.6)),
        0.0, 0.6, has_figure=True,
    )
    spec = plan_frames([lf], fps=30, width=1920, height=1080, footline="Source: Yahoo Finance")
    body = [f for f in spec if f.caption is not None]
    cap = body[0].caption
    assert isinstance(cap, Caption)
    assert cap.figure == StyledSpan("5.25", "figure")
    assert cap.footline == "Source: Yahoo Finance"


def test_resolve_font_default_returns_font():
    pytest.importorskip("PIL")
    from vibe.render import resolve_font

    font = resolve_font(32)
    assert callable(getattr(font, "getbbox", None))


def test_fake_renderer_deterministic():
    from vibe.render import fake_renderer

    spec = FrameSpec(0, 0.0, 1.0, None)
    r = fake_renderer()
    assert r((spec,), hero=b"hero", palette=config.PALETTE) == \
        r((spec,), hero=b"hero", palette=config.PALETTE)


def test_fake_encoder_deterministic():
    from vibe.render import fake_encoder

    frames = (b"f0", b"f1")
    e = fake_encoder()
    first = e(frames, width=1920, height=1080, fps=30, audio=b"a")
    assert first == b"fake-mp4"
    assert e(frames, width=1920, height=1080, fps=30, audio=b"a") == first


def test_make_hero_deterministic_png():
    pytest.importorskip("PIL")
    from vibe.render import make_hero

    brief = {"topic_brief": {"title": "Rates Are Up", "segments": [{"title": "The Context"}]}}
    a = make_hero(brief)
    b = make_hero(brief)
    assert a == b
    assert a.startswith(b"\x89PNG")


def test_pillow_renderer_produces_rgb_frames():
    pytest.importorskip("PIL")
    from vibe.render import pillow_renderer

    spec = plan_frames([_cl((_w("hi", "base", 0.0, 0.3),), 0.0, 0.3)], fps=30, width=64, height=64)
    r = pillow_renderer(width=64, height=64)
    frames = r(spec, hero=b"", palette=config.PALETTE)
    assert frames
    assert all(len(f) == 64 * 64 * 3 for f in frames)


def test_render_segment_with_fakes():
    from vibe.render import fake_encoder, fake_renderer, render_segment

    timing = [WordTiming("a", 0.0, 0.2), WordTiming("b", 0.2, 0.5)]
    out = render_segment(
        "**a** b", timing, b"mp3", None, b"hero",
        renderer=fake_renderer(), encoder=fake_encoder(),
    )
    assert out == b"fake-mp4"


def _index(*rows):
    return {"video": "v", "scripts": [dict(r) for r in rows]}


def _timing_text():
    return '{"word": "hello", "start_s": 0.0, "end_s": 0.2}\n{"word": "world", "start_s": 0.2, "end_s": 0.5}\n'


def test_render_approved_writes_and_skips(tmp_path):
    from vibe.render import fake_encoder, fake_renderer, render_approved

    lay = layout.create_layout(tmp_path)
    (lay.topic_brief).write_text(json.dumps(
        {"topic_brief": {"title": "t", "segments": [], "sources": [{"publisher": "CNBC"}]}}),
        encoding="utf-8")
    (lay.hero).write_bytes(b"hero")
    (lay.scripts / "index.json").write_text(
        json.dumps(_index(
            {"index": 1, "file": "segment-1.txt", "word_count": 2,
             "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
            {"index": 2, "file": "segment-2.txt", "word_count": 0,
             "status": script.STATUS_NEEDS_HUMAN, "attempts": 3, "violations": []},
        )), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text("hello world", encoding="utf-8")
    (lay.scripts / "segment-2.txt").write_text("bad", encoding="utf-8")
    (lay.narration / "segment-1.mp3").write_bytes(b"mp3")
    (lay.narration / "segment-1.timing.jsonl").write_text(_timing_text(), encoding="utf-8")

    results = render_approved(lay, renderer=fake_renderer(), encoder=fake_encoder())
    assert results[0].ok and "OK" in results[0].message
    assert (lay.segments / "segment-1.mp4").read_bytes() == b"fake-mp4"
    assert results[1].ok is False and "skipped" in results[1].message
    assert not (lay.segments / "segment-2.mp4").exists()


def test_render_approved_missing_narration_no_partial(tmp_path):
    from vibe.render import fake_encoder, fake_renderer, render_approved

    lay = layout.create_layout(tmp_path)
    (lay.topic_brief).write_text(json.dumps(
        {"topic_brief": {"title": "t", "segments": [], "sources": [{"publisher": "CNBC"}]}}),
        encoding="utf-8")
    (lay.hero).write_bytes(b"hero")
    (lay.scripts / "index.json").write_text(
        json.dumps(_index(
            {"index": 1, "file": "segment-1.txt", "word_count": 2,
             "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
        )), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text("hello world", encoding="utf-8")

    results = render_approved(lay, renderer=fake_renderer(), encoder=fake_encoder())
    assert results[0].ok is False
    assert not (lay.segments / "segment-1.mp4").exists()

def test_ffmpeg_encoder_real_clip_matches_contract(ffmpeg_available, tmp_path):
    import subprocess

    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    from vibe import check
    from vibe.render import ffmpeg_encoder, pillow_renderer, render_segment

    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "0.1",
         "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True)
    mp3 = proc.stdout
    timing = [WordTiming("a", 0.0, 0.05)]
    clip = render_segment(
        "**a**", timing, mp3, None, b"", width=1920, height=1080,
        renderer=pillow_renderer(width=1920, height=1080),
        encoder=ffmpeg_encoder(),
)
    path = tmp_path / "segment-1.mp4"
    path.write_bytes(clip)
    res = check.check_video(path, kind="clip")
    assert res.ok, res.failures
    timing = tmp_path / "segment-1.timing.jsonl"
    timing.write_text('{"word": "a", "start_s": 0.0, "end_s": 0.05}\n', encoding="utf-8")
    timed = check.check_video(path, kind="clip", timing=timing)
    assert timed.ok, timed.failures
