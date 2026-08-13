"""`vibe` — the single CLI seam for the explainer pipeline (spec #9).

Subcommands:
  make  <thesis>     create the deterministic build layout + manifest for a video.
  check <artifact>   validate an artifact against the media contract.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, check, layout

USAGE = "a video needs a thesis"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibe")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("make", help="create the build layout for a thesis")
    make.add_argument("thesis", nargs="?", help="niche or thesis for the video")
    make.set_defaults(_handler=_cmd_make)

    ck = sub.add_parser("check", help="validate an artifact against the media contract")
    ck.add_argument("artifact", type=Path, help="path to .mp4, .srt, or .timing.jsonl")
    ck.add_argument(
        "--kind",
        choices=("full", "clip", "short"),
        default=None,
        help="expected format for a video artifact (default: clip, 1920x1080@30)",
    )
    ck.add_argument(
        "--timing",
        type=Path,
        default=None,
        help="paired .timing.jsonl; check clip duration matches its narration",
    )
    ck.set_defaults(_handler=_cmd_check)
    return parser


def _cmd_make(args: argparse.Namespace) -> int:
    if not args.thesis or not args.thesis.strip():
        print("error: `vibe make` requires a non-empty thesis", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    root = Path("build")
    created = layout.create_layout(root)
    manifest = created.manifest
    print(f"build layout ready at {root}/")
    print(f"media contract + flags recorded in {manifest.as_posix()}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        result = check.check_artifact(args.artifact, kind=args.kind, timing=args.timing)
    except (check.MediaNotFound, ValueError) as exc:
        print(f"check: {exc}", file=sys.stderr)
        return 2
    label = args.artifact.name
    if result.ok:
        print(f"{label}: OK ({result.kind})")
        return 0
    print(f"{label}: FAIL ({result.kind})", file=sys.stderr)
    for failure in result.failures:
        print(f"  - {failure}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = args._handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())