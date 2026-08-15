"""Build output layout and the deterministic build manifest.

`vibe make` establishes the layout described in assembly.md §9 and records a
deterministic manifest (the media contract + fixed encoder flags). Re-runs are
idempotent: the layout already exists, and the manifest bytes are unchanged when the
contract is unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from . import config

MANIFEST_NAME = "manifest.json"

# Directories created under the build root, per assembly.md §9.
_LAYOUT_DIRS = ("segments", "shorts", "cc", "scripts", "narration")


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def hero(self) -> Path:
        return self.root / "hero.png"

    @property
    def topic_brief(self) -> Path:
        return self.root / "brief.json"

    @property
    def segments(self) -> Path:
        return self.root / "segments"

    @property
    def shorts(self) -> Path:
        return self.root / "shorts"

    @property
    def cc(self) -> Path:
        return self.root / "cc"

    @property
    def scripts(self) -> Path:
        return self.root / "scripts"

    @property
    def narration(self) -> Path:
        return self.root / "narration"

    @property
    def full_video(self) -> Path:
        return self.root / "full.mp4"

    @property
    def recap_png(self) -> Path:
        return self.root / "recap.png"

    @property
    def recap_video(self) -> Path:
        return self.root / "recap.mp4"


def create_layout(root: Path) -> Layout:
    """Create the build layout and write a deterministic manifest. Idempotent."""
    layout = Layout(root=root)
    for sub in _LAYOUT_DIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    write_manifest(layout)
    return layout


def write_manifest(layout: Layout) -> None:
    """Write manifest.json deterministically (sorted keys, fixed formatting)."""
    payload = config.contract_dict()
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    layout.manifest.write_text(text, encoding="utf-8")


def read_manifest(layout: Layout) -> dict[str, object]:
    with layout.manifest.open("r", encoding="utf-8") as fh:
        return cast(dict[str, object], json.load(fh))