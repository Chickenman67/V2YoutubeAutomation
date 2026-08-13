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