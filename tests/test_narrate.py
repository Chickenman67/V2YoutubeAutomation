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


from vibe.narrate import WordTiming, build_word_timings, timing_jsonl


def test_build_word_timings_plain_base():
    chunks = [Chunk("hello world", "base", 0, 0)]
    events = [[WordTiming("hello", 0.0, 0.2), WordTiming("world", 0.2, 0.4)]]
    ts = build_word_timings(chunks, events)
    assert ts == [
        WordTiming("hello", 0.0, 0.2),
        WordTiming("world", 0.2, 0.4),
    ]


def test_build_word_timings_keyword_pre_silence_offsets():
    chunks = [
        Chunk("A", "base", 0, 0),
        Chunk("rates", "keyword", 120, 0),
    ]
    events = [
        [WordTiming("A", 0.0, 0.2)],
        [WordTiming("rates", 0.0, 0.3)],
    ]
    ts = build_word_timings(chunks, events)
    assert ts[0] == WordTiming("A", 0.0, 0.2)
    assert ts[1] == WordTiming("rates", 0.32, 0.62)


def test_build_word_timings_figure_post_silence_gap():
    chunks = [
        Chunk("Up", "base", 0, 0),
        Chunk("5.25", "figure", 0, 450),
        Chunk("now", "base", 0, 0),
    ]
    events = [
        [WordTiming("Up", 0.0, 0.2)],
        [WordTiming("5.25", 0.0, 0.3)],
        [WordTiming("now", 0.0, 0.2)],
    ]
    ts = build_word_timings(chunks, events)
    # base 0.0-0.2; figure 0.2-0.5 then 450ms post -> next starts at 0.95; now 0.95-1.15
    assert ts[0] == WordTiming("Up", 0.0, 0.2)
    assert ts[1] == WordTiming("5.25", 0.2, 0.5)
    assert ts[2] == WordTiming("now", 0.95, 1.15)


def test_build_word_timings_pause_chunk_advances_cursor():
    chunks = [
        Chunk("Money", "base", 0, 0),
        Chunk("", "pause", 300, 0),
        Chunk("fast", "base", 0, 0),
    ]
    events = [
        [WordTiming("Money", 0.0, 0.2)],
        [],
        [WordTiming("fast", 0.0, 0.2)],
    ]
    ts = build_word_timings(chunks, events)
    assert ts[1] == WordTiming("fast", 0.5, 0.7)


def test_timing_jsonl_matches_check_contract():
    out = timing_jsonl([WordTiming("rates", 0.0, 0.2), WordTiming("now", 0.2, 0.4)])
    assert out == '{"word": "rates", "start_s": 0.0, "end_s": 0.2}\n{"word": "now", "start_s": 0.2, "end_s": 0.4}\n'


from vibe.narrate import fake_encoder, fake_synthesizer


def test_fake_synthesizer_deterministic_words():
    synth = fake_synthesizer()
    a = synth("rates are up", voice="v", rate="-8%", volume="+12%")
    b = synth("rates are up", voice="v", rate="-8%", volume="+12%")
    assert a == b
    assert a[0] == b"fake-audio"
    assert a[1] == (
        WordTiming("rates", 0.0, 0.2),
        WordTiming("are", 0.25, 0.45),
        WordTiming("up", 0.5, 0.7),
    )


def test_fake_encoder_deterministic():
    enc = fake_encoder()
    assert enc([(b"abc", 0, 450)], sample_rate=44100, channels=2) == b"fake-mp3"
    assert enc([(b"abc", 0, 450)], sample_rate=44100, channels=2) == enc(
        [(b"abc", 0, 450)], sample_rate=44100, channels=2
    )


import subprocess

import pytest

from vibe.narrate import NarrationError, edge_tts_synthesizer, ffmpeg_encoder


def test_edge_tts_synthesizer_factory_is_callable():
    assert callable(edge_tts_synthesizer())


def _tiny_mp3() -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "0.1", "-c:a", "libmp3lame", "-f", "mp3", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return proc.stdout


def test_ffmpeg_encoder_roundtrip(ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    enc = ffmpeg_encoder()
    unit = (_tiny_mp3(), 120, 450)
    out = enc([unit], sample_rate=44100, channels=2)
    assert out[:3] == b"ID3" or b"\xff\xfb" in out[:32]  # mp3 frame magic


def test_ffmpeg_encoder_bad_audio_raises(ffmpeg_available):
    if not ffmpeg_available:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    enc = ffmpeg_encoder()
    with pytest.raises(NarrationError):
        enc([(b"not-an-mp3", 0, 0)], sample_rate=44100, channels=2)


import json

from vibe import script
from vibe.narrate import (
    SegmentNarration,
    narrate_approved,
    narrate_segment,
)

SCRIPT_1 = (
    "Here's the thing though, the **rates** is the story everyone is chasing.\n"
    "Money moves ~ fast.\n"
    "And every single rate decision reshapes the monthly number.\n"
)


def test_narrate_segment_fake_roundtrip():
    out = narrate_segment(
        SCRIPT_1, synthesizer=fake_synthesizer(), encoder=fake_encoder()
    )
    assert isinstance(out, SegmentNarration)
    assert out.mp3_bytes == b"fake-mp3"
    assert out.timings  # non-empty
    # cumulative: no gaps between consecutive words of the same chunk
    for a, b in zip(out.timings, out.timings[1:]):
        assert b.start_s >= a.end_s


def test_narrate_segment_pause_creates_gap():
    out = narrate_segment(
        SCRIPT_1, synthesizer=fake_synthesizer(), encoder=fake_encoder()
    )
    # the '~' line ("Money moves ~ fast.") has a 300ms pause between 'moves' and 'fast'
    words = [t.word for t in out.timings]
    i_fast = words.index("fast.")
    prev = out.timings[i_fast - 1]
    gap = out.timings[i_fast].start_s - prev.end_s
    assert gap >= 0.299


def test_narrate_approved_writes_artifacts(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    lay.scripts.mkdir(parents=True, exist_ok=True)
    idx = {
        "video": "test",
        "scripts": [
            {"index": 1, "file": "segment-1.txt", "word_count": 210,
             "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
            {"index": 2, "file": "segment-2.txt", "word_count": 0,
             "status": script.STATUS_NEEDS_HUMAN, "attempts": 3, "violations": []},
        ],
    }
    (lay.scripts / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text(SCRIPT_1, encoding="utf-8")
    (lay.scripts / "segment-2.txt").write_text("bad", encoding="utf-8")

    results = narrate_approved(lay, synthesizer=fake_synthesizer(), encoder=fake_encoder())
    assert [r.index for r in results] == [1, 2]
    assert results[0].ok and results[1].ok is False
    mp3 = lay.narration / "segment-1.mp3"
    timing = lay.narration / "segment-1.timing.jsonl"
    assert mp3.read_bytes() == b"fake-mp3"
    assert timing.is_file()
    assert "segment-1.mp3: OK" in results[0].message
    assert "skipped" in results[1].message
    assert not (lay.narration / "segment-2.mp3").exists()


def test_narrate_approved_synth_failure_writes_no_partial(tmp_path: Path, monkeypatch):
    lay = layout.create_layout(tmp_path)
    lay.scripts.mkdir(parents=True, exist_ok=True)
    idx = {
        "video": "test",
        "scripts": [
            {"index": 1, "file": "segment-1.txt", "word_count": 210,
             "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
        ],
    }
    (lay.scripts / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    (lay.scripts / "segment-1.txt").write_text(SCRIPT_1, encoding="utf-8")

    def _boom(text, *, voice, rate, volume):
        raise NarrationError("boom")

    results = narrate_approved(lay, synthesizer=_boom, encoder=fake_encoder())
    assert results[0].ok is False and "boom" in results[0].message
    assert not (lay.narration / "segment-1.mp3").exists()
    assert not (lay.narration / "segment-1.timing.jsonl").exists()
