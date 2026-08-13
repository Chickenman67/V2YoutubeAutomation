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
_WORD = r"\d+(?:\.\d+)?%?|[a-z0-9]+(?:['’\-][a-z0-9]+)*"
_NUMBER = r"\d+(?:\.\d+)?%?"


class Author(Protocol):
    def __call__(self, brief: dict[str, object], index: int, *, attempt: int = 1) -> str: ...


def word_count(text: str) -> int:
    """Word count with markers (`**`, `##`, `~`) stripped; contractions count as one."""
    stripped = re.sub(r"##[^#]+##", " ", text.lower())
    stripped = stripped.replace("~", " ").replace("*", "")
    return len(re.findall(_WORD, stripped))


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
