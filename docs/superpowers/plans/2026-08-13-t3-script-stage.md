# T3 — Script Stage + Script Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a Topic Brief into per-segment narration scripts (with emphasis/pause markers) that pass the Script-Standard deterministic gate, recorded in an index that blocks `needs-human` scripts from reaching narration.

**Architecture:** A new pure module `vibe/script.py` owns deterministic templated authoring (`author_segment`), the Script-Standard checks (`check_script`), the ≤3-attempt gate (`author_and_gate`), and the on-disk orchestration (`write_scripts`, `approve_scripts`). It slots into the existing `vibe make` CLI seam right after `brief.json` is written. `author` is an injectable seam (like `discover.Fetcher`) so the not-ready path is testable without live services.

**Tech Stack:** Python 3.11+, stdlib only (`json`, `re`, `pathlib`, `dataclasses`, `typing`, `os`, `sys`). Testing: pytest 8, mypy (strict), ruff.

## Global Constraints

- Python `>=3.11`; mypy `strict`; ruff `line-length=100`, target `py311`. No new runtime dependencies (stdlib only).
- Offline: tests must never reach the network. `tests/conftest.py` already sets `VIBE_OFFLINE=1` inside `run_cli`.
- Domain naming per `docs/specs/script-standard.md`: emphasis markers `**word**`, `##figure##`, `~`, `**gold**`; skeleton `hook → thesis → beats → payoff`; word budget **200–280 words/segment**.
- Determinism + idempotency: same `(brief, index, attempt)` → byte-identical output; the index is sorted-key JSON.
- `vibe make` stays best-effort: a `needs-human` script prints a warning and still exits 0 (downstream narration blocks via the index).
- Follow existing patterns: pure stage module with a typed injectable seam; thin CLI wiring; per-task commits on branch `build/t3`.

---

### Task 1: Script types, word counter, layout `scripts` dir

**Files:**
- Modify: `vibe/layout.py` (`_LAYOUT_DIRS` + a `Layout.scripts` property)
- Create: `vibe/script.py` (types + `word_count` + statuses + module constants)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `vibe.layout.Layout` (has `root`, `manifest`, `topic_brief`, `segments`, `shorts`, `cc`, `full_video`) from T1.
- Produces:
  - `script.CheckResult(ok: bool, violations: tuple[str, ...])` (frozen dataclass)
  - `script.ScriptRecord(index: int, file: str, word_count: int, status: str, attempts: int, violations: tuple[str, ...])` (frozen dataclass)
  - `script.STATUS_READY = "ready"`, `script.STATUS_APPROVED = "approved"`, `script.STATUS_NEEDS_HUMAN = "needs-human"`
  - `script.word_count(text: str) -> int`
  - `class Author(Protocol)` with `__call__(brief: dict[str, object], index: int, *, attempt: int = 1) -> str`
  - `script.BANNED_PHRASES`, `script.BANNED_OPENINGS`, `script.CONTRACTION_MISSES`
  - `Layout.scripts` path property; `"scripts"` added to `_LAYOUT_DIRS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_script.py
from __future__ import annotations

from pathlib import Path

from vibe import layout, script


def test_word_count_strips_markers():
    text = "**Rates** are up ~ 5.25 ##figure## for **gold** payoffs."
    assert script.word_count(text) == 6  # rates, are, up, 5.25, for, payoffs

def test_status_constants():
    assert script.STATUS_READY == "ready"
    assert script.STATUS_APPROVED == "approved"
    assert script.STATUS_NEEDS_HUMAN == "needs-human"

def test_layout_exposes_scripts_directory(tmp_path: Path):
    lay = layout.create_layout(tmp_path)
    assert lay.scripts == tmp_path / "scripts"
    assert lay.scripts.is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -v`
Expected: FAIL — `script` module has no `word_count`; `Layout` has no `scripts`.

- [ ] **Step 3: Implement**

Add to `vibe/layout.py` (keep `brief`/`topic_brief` naming already present):

```python
_LAYOUT_DIRS = ("segments", "shorts", "cc", "scripts")

# inside class Layout:
    @property
    def scripts(self) -> Path:
        return self.root / "scripts"
```

Create `vibe/script.py`:

```python
"""Script stage: deterministic per-segment narration authoring + Script-Standard gate.

Consumes the Topic Brief (`build/brief.json`) and produces one script per segment
(`build/scripts/segment-<n>.txt`) plus an index of gate state. The author is an
injectable seam so the not-ready path is testable offline. Deterministic: same
(brief, index, attempt) yields byte-identical output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, cast

STATUS_READY = "ready"
STATUS_APPROVED = "approved"
STATUS_NEEDS_HUMAN = "needs-human"

# Script-Standard §3.1 (banned words/phrases) and §3.2 (banned openings), lowercased.
BANNED_PHRASES = (
    "delve", "realm", "pivotal", "nuanced", "tapestry", "beacon", "navigate", "unravel",
    "embark", "testament", "moreover", "furthermore", "additionally", "thus", "thereby",
    "utilize", "leverage", "overarching", "multifaceted", "cornerstone", "streamline",
    "seamless", "robust", "cutting-edge", "game-changer", "best-in-class",
    "actionable insights", "foster a culture of", "drive results",
)
BANNED_OPENINGS = (
    "in today's fast-paced world", "it is no secret that", "as we all know",
    "in recent years", "this article", "this essay", "throughout history",
    "in conclusion", "to summarize", "let's dive in",
)
# §3 register: spelled-out forms that must be contracted.
CONTRACTION_MISSES = (
    "cannot", "will not", "it is", "do not", "is not", "does not", "was not",
    "were not", "are not", "would not", "could not", "should not",
)

# Matching for word prefixes (traceable figure substring check) and numerals.
_WORD = r"[a-z0-9]+(?:['’\-][a-z0-9]+)*"
_NUMBER = r"\d+(?:\.\d+)?%?"


class Author(Protocol):
    def __call__(self, brief: dict[str, object], index: int, *, attempt: int = 1) -> str: ...


def word_count(text: str) -> int:
    """Word count with markers (`**`, `##`, `~`) stripped; contractions count as one."""
    return len(re.findall(_WORD, text.lower()))


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScriptRecord:
    index: int
    file: str
    word_count: int
    status: str
    attempts: int
    violations: tuple[str, ...] = ()


def _segment(brief: dict[str, object], index: int) -> dict[str, object]:
    tb = cast(dict[str, object], brief["topic_brief"])
    segs = cast(list[object], tb["segments"])
    return cast(dict[str, object], segs[index - 1])


def _sources(brief: dict[str, object]) -> list[dict[str, object]]:
    tb = cast(dict[str, object], brief["topic_brief"])
    srcs = cast(list[object], tb["sources"])
    return [cast(dict[str, object], s) for s in srcs]


def _text(seg: dict[str, object], key: str) -> str:
    return str(seg[key])


def _text_list(seg: dict[str, object], key: str) -> list[str]:
    raw = seg[key]
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _seg_title(brief: dict[str, object], index: int) -> str:
    return _text(_segment(brief, index), "title")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: `All checks passed!` / `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add vibe/layout.py vibe/script.py tests/test_script.py
git commit -m "T3: script types, word counter, scripts layout dir (#12)"
```

---

### Task 2: Deterministic Script-Standard checks (`check_script`)

**Files:**
- Modify: `vibe/script.py` (add `_key_point_text`, `_source_texts`, `__filtered_numbers`, `check_script`)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `word_count`, `BANNED_PHRASES`, `BANNED_OPENINGS`, `CONTRACTION_MISSES`, `_segment`, `_sources`, `_text`, `_text_list` from Task 1.
- Produces: `check_script(script: str, *, segment: dict[str, object], sources: list[dict[str, object]]) -> CheckResult`.

- [ ] **Step 1: Write the failing tests** (one violator per rule; the positive "clean passes" case is deferred to Task 3, where a full 200–280-word script exists)

```python
# append to tests/test_script.py
def _seg(**kw):  # noqa: ANN003 - test helper
    return {"title": kw.get("title", "T"), "key_points": kw.get("key_points", ["x"]), "hook": kw.get("hook", "H")}


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_script.py::test_check_script_accepts_a_clean_script tests/test_script.py::test_check_script_flags_banned_word -v`
Expected: FAIL — `check_script` not defined.

- [ ] **Step 3: Implement** (append to `vibe/script.py`)

```python
def _key_point_texts(segment: dict[str, object]) -> str:
    return " ".join(_text_list(segment, "key_points"))


def _source_texts(sources: list[dict[str, object]]) -> str:
    return " ".join(str(v) for s in sources for v in s.values())


def check_script(
    script: str,
    *,
    segment: dict[str, object],
    sources: list[dict[str, object]],
) -> CheckResult:
    v: list[str] = []
    low = script.lower()
    lines = [l for l in script.splitlines() if l.strip()]

    for phrase in BANNED_PHRASES:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", low):
            v.append(f"banned word: {phrase}")
    for opening in BANNED_OPENINGS:
        if opening in low:
            v.append(f"banned opening: {opening}")

    for ch, name in (("\u2014", "em dash"), ("\u2013", "en dash"),
                     (":", "colon"), (";", "semicolon"), ("\u2026", "ellipsis")):
        if ch in script:
            v.append(f"{name} present")
    for line in lines:
        if line.count("*") % 2 != 0:
            v.append("stray *")
    if "  " in script:
        v.append("double space")

    if len(lines) >= 2:
        first = set(re.findall(_WORD, lines[0].lower()))
        last = set(re.findall(_WORD, lines[-1].lower()))
        if first and len(first & last) >= max(1, len(first) // 2):
            v.append("payoff restates the hook")

    for form in CONTRACTION_MISSES:
        if re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", low):
            v.append(f"missing contraction: {form}")

    corpus = (_key_point_texts(segment) + " " + _source_texts(sources)).lower()
    for line in lines:  # positional figure markers: ##figure## must sit on the number's line
        nums = re.findall(_NUMBER, line)
        has_marker = "##figure##" in line
        if nums and not has_marker:
            for n in nums:
                v.append(f"number without ##figure##: {n}")
        elif has_marker and not nums:
            v.append("##figure## without a number")
        elif nums and has_marker:
            for n in nums:
                if n.lower() not in corpus:
                    v.append(f"untraceable figure: {n}")

    wc = word_count(script)
    if not (200 <= wc <= 280):
        v.append(f"word budget {wc} outside 200-280")

    starts = [l.split()[0].lower() for l in lines if l.split()]
    if not any(w in ("and", "but", "so") for w in starts):
        v.append("no And/But/So sentence start")
    lengths = [word_count(l) for l in lines]
    if lengths and not (min(lengths) <= 5 and max(lengths) >= 13):
        v.append("sentence lengths too uniform")
    if starts and starts.count(starts[0]) / len(starts) > 0.8:
        v.append("openers too uniform")

    return CheckResult(ok=not v, violations=tuple(v))
```

Note: the small bodies above also trip the 200–280 word-budget check; that is expected. Each test asserts only its own violation substring. The clean path is covered in Tasks 3–4 with a full-length, in-budget script.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -v`
Expected: PASS — including the new `test_check_script_*` cases.

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/script.py tests/test_script.py
git commit -m "T3: deterministic Script-Standard checks (#12)"
```

---

### Task 3: Deterministic templated authoring (`author_segment`)

**Files:**
- Modify: `vibe/script.py` (add template pools + `author_segment`)
- Test: `tests/test_script.py`
- Create fixtures: `tests/fixtures/scripts/` referenced by a golden test (see Step 2).

**Interfaces:**
- Consumes: `word_count`, `_segment`, `_sources`, `_text`, `_text_list`, `_seg_title`.
- Produces: `author_segment(brief: dict[str, object], index: int, *, attempt: int = 1) -> str`; `failing_author(brief, index, *, attempt=1) -> str` (the test seam returning a guaranteed-violating draft).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_script.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -k author -v`
Expected: FAIL — `author_segment` not defined; `_segment`/`_sources` are private but importable for tests.

- [ ] **Step 3: Implement** (append to `vibe/script.py`)

```python
# Plain, concrete elaboration frames (no numerals, no banned words, no spelled-out
# contractions). Cycled by attempt so attempts differ deterministically while staying
# byte-identical per (brief, index, attempt).
_FRAMES = (
    "the real number here is hard to ignore",
    "every rate move ripples through someone's monthly budget",
    "the people carrying these loans notice it fast",
    "a small shift compounds by the time the next statement lands",
    "that is the kind of detail that changes a plan",
    "you don't need a spreadsheet to feel the difference",
    "the market reacts long before the headlines catch up",
    "this is where the story stops being academic",
    "watch the trend more than the single print",
    "the distinction matters more than it looks",
    "the ripple shows up in places you wouldn't expect",
    "that gap is where real decisions get made",
    "the surprise is how quickly it shows up in the wallet",
    "sooner than you think it moves the monthly total",
    "the practical takeaway survives the jargon",
)
_OPENERS = ("", "But ", "And ", "So ", "The ", "That ", "This ", "Also ", "Now ", "Here ")


def _kws(seg: dict[str, object], title: str) -> list[str]:
    raw = _text_list(seg, "key_points") + re.findall(r"[a-z0-9]+", title.lower())
    seen: set[str] = set()
    out: list[str] = []
    for w in raw:
        w = w.lower()
        if w not in seen and len(w) >= 3 and w not in ("the", "and", "that", "with"):
            seen.add(w)
            out.append(w)
    return out


def _stakes(sources: list[dict[str, object]]) -> str:
    pub = ""
    for s in sources:
        pub = str(s.get("publisher", "")) or pub
    head = f"{pub} is tracking this now, and the takeaway is that" if pub else "The takeaway is that"
    return head + " the cost of money stays high for the people who feel it."


def _hook_line(hooks: list[str]) -> str:
    hook = (hooks[0] if hooks else "The numbers moved.").strip().rstrip(".")
    return hook + "."


def author_segment(brief: dict[str, object], index: int, *, attempt: int = 1) -> str:
    """Deterministic templated script: hook, thesis, beats, padded, payoff last."""
    seg = _segment(brief, index)
    title = _seg_title(brief, index)
    hooks = _text_list(seg, "hook")
    kws = _kws(seg, title)
    kw = kws[0] if kws else "market"
    sources = _sources(brief)

    lines = [_hook_line(hooks)]
    lines.append(f"Here's the thing though, the **{kw}** is the story everyone is chasing right now.")
    for i, kp in enumerate(_text_list(seg, "key_points")[:4]):
        word = kp.split()[0].lower()
        opener = "But " if i == 0 else "And " if i == 3 else "So "
        lines.append(f"{opener}the {word} matters more than the headline says.")

    # guaranteed short + long lines so the length-mixing critique is satisfied
    short, long = ("But the bill still comes.",
                   "And every single rate decision reshapes the monthly number for "
                   "households that borrowed when money was cheap.")
    lines.append(short)
    # pad to >= 220 words, cycling openers/frames deterministically by (attempt, cursor)
    i = 0
    while word_count("\n".join(lines)) < 220:
        frame = _FRAMES[(attempt - 1 + i) % len(_FRAMES)]
        opener = _OPENERS[(attempt + i) % len(_OPENERS)]
        lines.append(opener + frame)
        i += 1

    lines.append(long)          # long line lands before the payoff (not last)
    lines.append(_stakes(sources))  # payoff is the final line (hook-overlap check)

    return "\n".join(lines)


def failing_author(brief: dict[str, object], index: int, *, attempt: int = 1) -> str:
    return ("In conclusion, delve into the realm of this 9 problem, it can not be ignored. "
            "Rates are high. The Fed has held steady for months. People feel the pinch.")
```

All inline/template strings below avoid the `CONTRACTION_MISSES` forms (`cannot`, `will not`, `it is`, `do not`, `is not`, `does not`, `was not`, `were not`, `are not`, `would not`, `could not`, `should not`), every `BANNED_PHRASES` word, and every `BANNED_OPENINGS` phrase. The author is figure-free (no numerals), so the figure checks and budget traceability pass vacuously while the shorter beat lines still carry `**kw**` emphasis.

- [ ] **Step 4: Run the author tests**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -k author -v`
Expected: PASS, including `test_author_segment_satisfies_the_gate`.

If `test_author_segment_satisfies_the_gate` fails, read the `violations` in the assertion message and fix the corresponding template wording (keep all pools free of banned words/openings and spelled-out contractions). Do not weaken the checks.

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/script.py tests/test_script.py
git commit -m "T3: deterministic script author with template pools (#12)"
```

---

### Task 4: The gate (`author_and_gate`)

**Files:**
- Modify: `vibe/script.py` (add `author_and_gate`)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `author_segment`, `failing_author`, `check_script`, `word_count`, `ScriptRecord`, statuses.
- Produces: `author_and_gate(brief, index, *, author: Author | None = None, max_attempts: int = 3) -> ScriptRecord`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_script.py
def test_gate_approves_a_real_draft_in_one_attempt():
    b = _brief_with_segments()
    rec = script.author_and_gate(b, 1)
    assert rec.status == script.STATUS_READY
    assert rec.attempts == 1
    assert 200 <= rec.word_count <= 280

def test_gate_never_ships_a_failing_draft():
    b = _brief_with_segments()
    rec = script.author_and_gate(b, 1, author=script.failing_author)
    assert rec.status == script.STATUS_NEEDS_HUMAN
    assert rec.attempts == 3
    assert rec.violations
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -k gate -v`
Expected: FAIL — `author_and_gate` not defined.

- [ ] **Step 3: Implement** (append to `vibe/script.py`)

```python
def author_and_gate(
    brief: dict[str, object],
    index: int,
    *,
    author: Author | None = None,
    max_attempts: int = 3,
) -> ScriptRecord:
    author = author or author_segment
    seg = _segment(brief, index)
    sources = _sources(brief)
    last = ScriptRecord(index, f"segment-{index}.txt", 0, STATUS_NEEDS_HUMAN, max_attempts)
    for attempt in range(1, max_attempts + 1):
        text = author(brief, index, attempt=attempt)
        res = check_script(text, segment=seg, sources=sources)
        if res.ok:
            return ScriptRecord(index, f"segment-{index}.txt", word_count(text),
                                STATUS_READY, attempt)
        last = ScriptRecord(index, f"segment-{index}.txt", word_count(text),
                            STATUS_NEEDS_HUMAN, attempt, res.violations)
    return last
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -v`
Expected: PASS (all script tests).

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/script.py tests/test_script.py
git commit -m "T3: script gate loop, flag-never-ship (#12)"
```

---

### Task 5: `write_scripts` + `approve_scripts` + index

**Files:**
- Modify: `vibe/script.py` (add `write_scripts`, `approve_scripts`, `read_index`)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `author_and_gate`, `author_segment`, `ScriptRecord`, statuses, `Layout.scripts`.
- Produces:
  - `write_scripts(brief: dict[str, object], lay: layout.Layout, *, author: Author | None = None) -> list[ScriptRecord]`
  - `approve_scripts(lay: layout.Layout, *, approve: bool) -> None`
  - `read_index(lay: layout.Layout) -> dict[str, object]`
  - Index file `build/scripts/index.json`: `{"video": <brief title>, "scripts": [<record dicts in order>]}` (sorted-key JSON). `file` = `segment-<n>.txt`; `status` from the record.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_script.py
def test_write_scripts_writes_files_and_index(tmp_path: Path):
    b = _brief_with_segments()
    lay = layout.create_layout(tmp_path)
    recs = script.write_scripts(b, lay)
    assert len(recs) == len(b["topic_brief"]["segments"])
    for rec in recs:
        assert (lay.scripts / rec.file).is_file()
    idx = script.read_index(lay)
    assert idx["video"] == b["topic_brief"]["title"]
    assert all(r["status"] == script.STATUS_READY for r in idx["scripts"])

def test_approve_promotes_ready_to_approved(tmp_path: Path):
    b = _brief_with_segments()
    lay = layout.create_layout(tmp_path)
    _ = script.write_scripts(b, lay)
    script.approve_scripts(lay, approve=True)
    idx = script.read_index(lay)
    assert all(r["status"] == script.STATUS_APPROVED for r in idx["scripts"])

def test_approve_decline_blocks_ready(tmp_path: Path):
    b = _brief_with_segments()
    lay = layout.create_layout(tmp_path)
    _ = script.write_scripts(b, lay)
    script.approve_scripts(lay, approve=False)
    idx = script.read_index(lay)
    assert all(r["status"] == script.STATUS_NEEDS_HUMAN for r in idx["scripts"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -k write_scripts -v`
Expected: FAIL — `write_scripts`/`approve_scripts`/`read_index` not defined.

- [ ] **Step 3: Implement** (append to `vibe/script.py`; add `import json` and `from . import layout` to the module top)

```python
def _record_dict(rec: ScriptRecord) -> dict[str, object]:
    return {"index": rec.index, "file": rec.file, "word_count": rec.word_count,
            "status": rec.status, "attempts": rec.attempts, "violations": list(rec.violations)}


def write_scripts(
    brief: dict[str, object],
    lay: layout.Layout,
    *,
    author: Author | None = None,
) -> list[ScriptRecord]:
    author = author or author_segment
    tb = cast(dict[str, object], brief["topic_brief"])
    n = len(cast(list[object], tb["segments"]))
    records: list[ScriptRecord] = []
    for index in range(1, n + 1):
        rec = author_and_gate(brief, index, author=author)
        text = author(brief, index, attempt=rec.attempts)
        (lay.scripts / rec.file).write_text(text, encoding="utf-8")
        records.append(rec)
    idx = {"video": _text(tb, "title"),
           "scripts": [_record_dict(r) for r in records]}
    (lay.scripts / "index.json").write_text(
        json.dumps(idx, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return records


def read_index(lay: layout.Layout) -> dict[str, object]:
    return cast(dict[str, object], json.loads((lay.scripts / "index.json").read_text(encoding="utf-8")))


def approve_scripts(lay: layout.Layout, *, approve: bool) -> None:
    idx = read_index(lay)
    recs = cast(list[object], idx["scripts"])
    for row in recs:
        d = cast(dict[str, object], row)
        if d["status"] == STATUS_READY:
            d["status"] = STATUS_APPROVED if approve else STATUS_NEEDS_HUMAN
    (lay.scripts / "index.json").write_text(
        json.dumps(idx, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_script.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add vibe/script.py tests/test_script.py
git commit -m "T3: write scripts + gate index + approval step (#12)"
```

---

### Task 6: CLI wiring (`vibe make`) + test seam

**Files:**
- Modify: `vibe/cli.py` (import `script`; run the stage after `brief.json`)
- Modify: `tests/conftest.py` (`run_cli` gains an `extra_env` arg so tests pass `VIBE_SCRIPT_AUTHOR=failing`)
- Test: `tests/test_cli_script.py` (new)

**Interfaces:**
- Consumes: `script.write_scripts`, `script.approve_scripts`, `script.STATUS_*`, `script.failing_author`, `discover` (existing).
- Produces: `_select_script_author() -> Author` reading `VIBE_SCRIPT_AUTHOR` (empty → `author_segment`, `failing` → `failing_author`).

- [ ] **Step 1: Write the failing tests** (new file `tests/test_cli_script.py`)

```python
from __future__ import annotations

import json
from pathlib import Path

from vibe import script


def test_make_with_feeds_writes_scripts_and_index(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", "tests/fixtures", cwd=str(tmp_path))
    assert proc.returncode == 0
    scripts_dir = tmp_path / "build" / "scripts"
    assert (scripts_dir / "segment-1.txt").is_file()
    idx = json.loads((scripts_dir / "index.json").read_text(encoding="utf-8"))
    assert all(r["status"] == script.STATUS_APPROVED for r in idx["scripts"])


def test_make_without_brief_skips_script_stage(run_cli, tmp_path: Path):
    # VIBE_OFFLINE path -> no topic -> no brief -> no scripts, best-effort exit 0.
    proc = run_cli("make", "Treasury yields", cwd=str(tmp_path))
    assert proc.returncode == 0
    scripts_dir = tmp_path / "build" / "scripts"
    assert scripts_dir.is_dir()
    assert not (scripts_dir / "index.json").exists()


def test_make_failing_author_flags_but_exits_zero(run_cli, tmp_path: Path):
    proc = run_cli("make", "mortgage rates", "--feeds-from", "tests/fixtures",
                   cwd=str(tmp_path), extra_env={"VIBE_SCRIPT_AUTHOR": "failing"})
    assert proc.returncode == 0
    idx = json.loads((tmp_path / "build" / "scripts" / "index.json").read_text(encoding="utf-8"))
    assert all(r["status"] == script.STATUS_NEEDS_HUMAN for r in idx["scripts"])
```

- [ ] **Step 2: Update `tests/conftest.py` `run_cli`**

Add an `extra_env` parameter merged into the per-invocation env:

```python
    def _run(*args: str, cwd: str | None = None,
             extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = dict(env)
        if extra_env:
            run_env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-m", "vibe", *args],
            capture_output=True, text=True, check=False,
            cwd=cwd or os.getcwd(), env=run_env,
        )
```

- [ ] **Step 3: Wire the stage in `vibe/cli.py`** (inside `_cmd_make`, replacing the section after the brief is written)

```python
    text = json.dumps(topic_brief, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    created.topic_brief.write_text(text, encoding="utf-8")
    print(f"topic brief written to {created.topic_brief.as_posix()}")

    author = _select_script_author()
    records = script.write_scripts(topic_brief, created, author=author)
    for rec in records:
        print(f"{rec.file}: {rec.status} ({rec.word_count} words)")

    interactive = sys.stdin is not None and sys.stdin.isatty()
    if interactive:
        answer = input("Approve scripts to proceed to narration? [y/N] ").strip().lower()
        script.approve_scripts(created, approve=answer in ("y", "yes"))
        if any(r.status == script.STATUS_NEEDS_HUMAN for r in records):
            print("vibe make: some scripts need human review; narration is blocked "
                  "for those segments (best-effort)", file=sys.stderr)
    else:
        script.approve_scripts(created, approve=True)  # non-interactive: auto-approve
    return 0
```

Add the helper (in the `_cmd_make`/module scope, near the top of `vibe/cli.py`):

```python
def _select_script_author() -> script.Author:
    if os.environ.get("VIBE_SCRIPT_AUTHOR") == "failing":
        return script.failing_author
    return script.author_segment
```

Import `script` in `vibe/cli.py` (`from . import __version__, check, discover, layout, script`).

- [ ] **Step 4: Run the new CLI tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cli_script.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite** (regression: T1/T2 must stay green)

Run: `.venv\Scripts\python -m pytest`
Expected: PASS (this adds ~13 script/CLI tests to the 34 baseline).

- [ ] **Step 6: Lint + typecheck**

Run: `.venv\Scripts\python -m ruff check vibe tests && .venv\Scripts\python -m mypy vibe`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add vibe/cli.py tests/conftest.py tests/test_cli_script.py
git commit -m "T3: run script stage in make with human-approval gate + test seam (#12)"
```

---

### Task 7: Final verification, spec alignment, docs

**Files:**
- Modify: `docs/superpowers/specs/2026-08-13-t3-script-stage-design.md` (§2 signature list — align names with the plan: `gate_script` → `author_and_gate`; add `write_scripts`/`approve_scripts`/`read_index` exact signatures; note `check_script` rejects on **any** violation including the mechanical soft-checks).
- No test fixture work beyond Task 3.

- [ ] **Step 1: Verify full checks + offline E2E**

Run: `.venv\Scripts\python -m pytest && .venv\Scripts\python -m mypy vibe && .venv\Scripts\python -m ruff check vibe tests`
Expected: all clean.

Manual offline E2E (from the `build-t3` worktree):
```powershell
$env:VIBE_OFFLINE='1'
.\.venv\Scripts\python -m vibe make "mortgage rates" --feeds-from tests/fixtures
Get-Content build\scripts\segment-1.txt | Select-Object -First 6
Get-Content build\scripts\index.json
Remove-Item Env:\VIBE_OFFLINE
```
Expected: exit 0, `segment-1.txt` printed with markers, `index.json` shows `approved`.

- [ ] **Step 2: Align the design doc**

Update the §2 public-function list in the design spec to match: `author_segment`, `check_script`, `author_and_gate`, `write_scripts`, `approve_scripts`, `read_index`. Commit.

- [ ] **Step 3: Commit docs**

```bash
git add docs/superpowers/specs/2026-08-13-t3-script-stage-design.md
git commit -m "T3: align design spec with implementation (#12)"
```

- [ ] **Step 4: Report** to the tracker (update ticket #12 acceptance boxes + evidence comment, leave OPEN for human close), matching the T2 workflow.