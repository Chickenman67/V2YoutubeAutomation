from __future__ import annotations

from pathlib import Path

from vibe import config, layout


def test_recap_config_constants():
    assert config.RECAP_SECONDS == 3.0
    assert config.RECAP_LABEL == "recap"


def test_layout_exposes_recap_paths(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.recap_png == tmp_path / "recap.png"
    assert lay.recap_video == tmp_path / "recap.mp4"


from vibe.narrate import combine_rate, fake_encoder, fake_synthesizer, narrate_segment


def test_combine_rate_signed_addition():
    assert combine_rate("0%", "0%") == "+0%"
    assert combine_rate("0%", "-8%") == "-8%"
    assert combine_rate("-6%", "0%") == "-6%"
    assert combine_rate("-6%", "-8%") == "-14%"
    assert combine_rate("+12%", "0%") == "+12%"


def test_narrate_segment_default_preserves_rate_args():
    seen: list[str] = []

    def synth(text: str, *, voice: str, rate: str, volume: str):
        seen.append(rate)
        return fake_synthesizer()(text, voice=voice, rate=rate, volume=volume)

    a = narrate_segment("hello **world**", synthesizer=synth, encoder=fake_encoder())
    seen.clear()
    b = narrate_segment("hello **world**", synthesizer=synth, encoder=fake_encoder(), base_rate="0%")
    assert seen == ["+0%", "-8%"]
    assert a == b


def test_narrate_segment_base_rate_passes_combined_rate():
    seen: list[str] = []

    def synth(text: str, *, voice: str, rate: str, volume: str):
        seen.append(rate)
        return fake_synthesizer()(text, voice=voice, rate=rate, volume=volume)

    narrate_segment("plain", synthesizer=synth, encoder=fake_encoder(), base_rate="-6%")
    narrate_segment("**key**", synthesizer=synth, encoder=fake_encoder(), base_rate="-6%")
    assert seen == ["-6%", "-14%"]


from vibe import assembly


def test_rework_base_rate_steps_and_cap():
    assert assembly.rework_base_rate(0) == "+0%"
    assert assembly.rework_base_rate(1) == "-6%"
    assert assembly.rework_base_rate(2) == "-12%"
    assert assembly.rework_base_rate(3) == "-18%"
    assert assembly.rework_base_rate(4) == "-18%"
    assert assembly.rework_base_rate(99) == "-18%"


def test_concat_list_exact_syntax_and_order(tmp_path):
    seg1 = tmp_path / "segments" / "segment-1.mp4"
    seg2 = tmp_path / "segments" / "segment-2.mp4"
    recap = tmp_path / "recap.mp4"
    text = assembly.concat_list([seg1, seg2, recap])
    assert text == (
        f"file '{seg1.as_posix()}'\n"
        f"file '{seg2.as_posix()}'\n"
        f"file '{recap.as_posix()}'\n"
    )


def test_expected_full_duration_arithmetic():
    assert assembly.expected_full_duration([1.5, 2.0], recap_s=3.0) == 6.5
    assert assembly.expected_full_duration([], recap_s=config.RECAP_SECONDS) == 3.0


def test_fanout_range():
    assert list(assembly._fanout(4)) == [2, 3, 4]
    assert list(assembly._fanout(1)) == []


import pytest


def test_fake_recap_encoder_deterministic():
    enc = assembly.fake_recap_encoder()
    a = enc(b"png", width=1920, height=1080, fps=30, seconds=3.0)
    b = enc(b"png", width=1920, height=1080, fps=30, seconds=3.0)
    assert a == b"recap-clip"
    assert a == b


def test_fake_concatener_writes_out(tmp_path):
    con = assembly.fake_concatener()
    out = tmp_path / "full.mp4"
    con([tmp_path / "a.mp4"], out, list_text="file 'a.mp4'\n")
    assert out.read_bytes() == b"full.mp4"


def test_make_recap_deterministic_png():
    pytest.importorskip("PIL")
    import io as _io

    from PIL import Image as _PILImage

    from vibe import assembly

    brief = {"topic_brief": {
        "title": "Rates Are Up",
        "segments": [{"title": "The Context"}],
        "sources": [{"publisher": "CNBC"}],
    }}
    a = assembly.make_recap(brief)
    b = assembly.make_recap(brief)
    assert a == b
    assert a.startswith(b"\x89PNG")
    assert _PILImage.open(_io.BytesIO(a)).size == (config.FULL_WIDTH, config.FULL_HEIGHT)


import subprocess

from vibe import check


@pytest.fixture()
def ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_ffmpeg_recap_encoder_real_clip_matches_contract(ffmpeg, tmp_path):
    if not ffmpeg:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    from vibe import assembly

    recap = assembly.make_recap({"topic_brief": {"title": "t", "segments": [], "sources": []}})
    clip = assembly.ffmpeg_recap_encoder()(
        recap, width=1920, height=1080, fps=30, seconds=config.RECAP_SECONDS
    )
    path = tmp_path / "recap.mp4"
    path.write_bytes(clip)
    res = check.check_video(path, kind="full")
    assert res.ok, res.failures


def test_ffmpeg_concatener_real_concat(ffmpeg, tmp_path):
    if not ffmpeg:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    from vibe import assembly
    from vibe.narrate import WordTiming

    # two tiny self-contained clips via the real T5 encoder
    from vibe.render import ffmpeg_encoder, pillow_renderer, render_segment

    mp3 = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "0.1", "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True).stdout
    clips = []
    for n in (1, 2):
        clip = render_segment("**a**", [WordTiming("a", 0.0, 0.05)], mp3, None, b"",
                              renderer=pillow_renderer(width=1920, height=1080),
                              encoder=ffmpeg_encoder())
        p = tmp_path / f"seg-{n}.mp4"
        p.write_bytes(clip)
        clips.append(p)
    out = tmp_path / "full.mp4"
    assembly.ffmpeg_concatener()(clips, out, list_text=assembly.concat_list(clips))
    res = check.check_video(out, kind="full")
    assert res.ok, res.failures


import json

from vibe import script


def _write_fixture_build(tmp_path) -> layout.Layout:
    lay = layout.create_layout(tmp_path)
    brief = {"topic_brief": {
        "title": "Rates Are Up",
        "segments": [{"title": "A"}, {"title": "B"}],
        "sources": [{"publisher": "CNBC"}],
    }}
    (lay.topic_brief).write_text(json.dumps(brief), encoding="utf-8")
    (lay.hero).write_bytes(b"hero")
    (lay.recap_png).write_bytes(b"recap-png")
    idx = {"video": "Rates Are Up", "scripts": [
        {"index": 1, "file": "segment-1.txt", "word_count": 2,
         "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
        {"index": 2, "file": "segment-2.txt", "word_count": 2,
         "status": script.STATUS_APPROVED, "attempts": 1, "violations": []},
    ]}
    (lay.scripts / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    for n in (1, 2):
        (lay.scripts / f"segment-{n}.txt").write_text("hello world", encoding="utf-8")
        (lay.narration / f"segment-{n}.mp3").write_bytes(b"mp3")
        (lay.narration / f"segment-{n}.timing.jsonl").write_text(
            '{"word": "hello", "start_s": 0.0, "end_s": 0.2}\n'
            '{"word": "world", "start_s": 0.2, "end_s": 0.5}\n',
            encoding="utf-8")
    return lay


def _seams():
    from vibe import assembly, narrate, render
    return {
        "synth": narrate.fake_synthesizer(),
        "nar_enc": narrate.fake_encoder(),
        "renderer": render.fake_renderer(),
        "enc": render.fake_encoder(),
        "recap_enc": assembly.fake_recap_encoder(),
        "concatener": assembly.fake_concatener(),
    }


def test_assemble_fake_end_to_end_approve(tmp_path):
    from vibe import assembly

    lay = _write_fixture_build(tmp_path)
    calls = iter([False, True])
    results = assembly.assemble_approved(
        lay, **_seams(), verify_video=False, approve=lambda: next(calls))
    assert any(r.ok and r.step == "rework" and r.index == 1 for r in results)
    assert (lay.segments / "segment-1.mp4").is_file()
    assert (lay.segments / "segment-2.mp4").is_file()
    assert (lay.recap_video).is_file()
    assert (lay.full_video).is_file()
    assert (lay.full_video).read_bytes() == b"full.mp4"


def test_assemble_fake_skip_existing_and_no_rework(tmp_path):
    from vibe import assembly

    lay = _write_fixture_build(tmp_path)
    (lay.segments / "segment-2.mp4").write_bytes(b"existing")
    results = assembly.assemble_approved(lay, **_seams(), verify_video=False, approve=lambda: True)
    assert not any(r.step == "rework" for r in results)
    assert any("segment-2.mp4: skipped (exists)" in r.message for r in results)
    assert (lay.segments / "segment-2.mp4").read_bytes() == b"existing"
    assert (lay.full_video).is_file()


def test_assemble_fake_reject_past_cap_declines(tmp_path):
    from vibe import assembly

    lay = _write_fixture_build(tmp_path)
    results = assembly.assemble_approved(lay, **_seams(), verify_video=False, approve=lambda: False)
    assert any(r.message.startswith("needs-human") for r in results)
    assert not (lay.full_video).exists()
    assert not (lay.segments / "segment-2.mp4").exists()