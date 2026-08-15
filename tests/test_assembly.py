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