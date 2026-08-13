from __future__ import annotations

from pathlib import Path

from vibe import layout, script


def test_word_count_strips_markers():
    text = "**Rates** are up ~ 5.25 ##figure## for **gold** payoffs."
    assert script.word_count(text) == 7  # rates, are, up, 5.25, for, gold, payoffs


def test_status_constants():
    assert script.STATUS_READY == "ready"
    assert script.STATUS_APPROVED == "approved"
    assert script.STATUS_NEEDS_HUMAN == "needs-human"


def test_layout_exposes_scripts_directory(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.scripts == tmp_path / "scripts"
    assert lay.scripts.is_dir()


def _seg(overrides=None, **kw):  # test helper
    seg = {"title": "T", "key_points": ["x"], "hook": "H"}
    if overrides:
        seg.update(overrides)
    seg.update(kw)
    return seg


def test_check_script_flags_banned_word():
    body = "Delve into the rates.\n" + "Rates are elevated.\nFed held steady.\n"
    res = script.check_script(body, segment=_seg({}), sources=[])
    assert not res.ok
    assert any("delve" in v for v in res.violations)

def test_check_script_flags_missing_contraction():
    body = "It is a long time since rates moved.\nRates are elevated.\nFed held steady.\n"
    res = script.check_script(body, segment=_seg({}), sources=[])
    assert not res.ok
    assert any("it is" in v for v in res.violations)

def test_check_script_flags_number_without_figure_marker():
    body = "Rates sit at 5.25 today.\nRates are elevated.\nFed held steady.\n"
    res = script.check_script(body, segment=_seg({}), sources=[])
    assert not res.ok
    assert any("5.25" in v for v in res.violations)

def test_check_script_flags_untraceable_figure():
    seg = {"title": "Rates", "key_points": ["rates"], "hook": "X"}
    body = "The rate sits at ##figure## 5.25 today.\nRates are elevated.\nFed held steady.\n"
    res = script.check_script(body, segment=seg, sources=[])
    assert not res.ok
    assert any("untraceable" in v for v in res.violations)

def test_check_script_flag_figure_marker_without_a_number():
    body = "That's a point worth ##figure## clearly.\nRates are elevated.\nFed held steady.\n"
    res = script.check_script(body, segment=_seg({}), sources=[])
    assert not res.ok
    assert any("without a number" in v for v in res.violations)

def _brief_with_segments():
    return {
        "topic_brief": {
            "title": "Fed holds rates",
            "segments": [
                {"index": 1, "title": "Rates stay put",
                 "hook": "What steady rates mean for your mortgage",
                 "key_points": ["fed holds", "mortgage costs climb"]},
                {"index": 2, "title": "The pinch builds",
                 "hook": "How the hold shows up in monthly bills",
                 "key_points": ["borrowers feel it"]},
            ],
            "sources": [{"publisher": "Yahoo Finance", "title": "Fed keeps rates high and "
                        "mortgage costs keep climbing", "url": "https://fx/y"}],
        }
    }

def test_author_is_deterministic_across_runs():
    b = _brief_with_segments()
    assert script.author_segment(b, 1) == script.author_segment(b, 1)

def test_author_segment_satisfies_the_gate():
    b = _brief_with_segments()
    seg = script._segment(b, 1)
    res = script.check_script(script.author_segment(b, 1), segment=seg,
                              sources=script._sources(b))
    assert res.ok, res.violations

def test_author_budget_is_within_range():
    b = _brief_with_segments()
    assert 200 <= script.word_count(script.author_segment(b, 2)) <= 280

def test_author_varies_by_attempt():
    b = _brief_with_segments()
    assert script.author_segment(b, 1, attempt=1) != script.author_segment(b, 1, attempt=2)
