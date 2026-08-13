"""`vibe` — the single CLI seam for the explainer pipeline (spec #9).

Subcommands:
  make  <thesis>     create the deterministic build layout + manifest for a video.
  check <artifact>   validate an artifact against the media contract.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, check, discover, layout, script

USAGE = "a video needs a thesis"


def _select_script_author() -> script.Author:
    if os.environ.get("VIBE_SCRIPT_AUTHOR") == "failing":
        return script.failing_author
    return script.author_segment


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibe")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("make", help="create the build layout for a thesis")
    make.add_argument("thesis", nargs="?", help="niche or thesis for the video")
    make.add_argument(
        "--feeds-from",
        type=Path,
        default=None,
        metavar="DIR",
        help="directory of local RSS/XML files for offline topic discovery",
    )
    make.set_defaults(_handler=_cmd_make)

    ck = sub.add_parser("check", help="validate an artifact against the media contract")
    ck.add_argument("artifact", type=Path, help="path to .mp4, .srt, or .timing.jsonl")
    ck.add_argument(
        "--kind",
        choices=tuple(check.KIND_RESOLUTION),
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
    print(f"build layout ready at {root}/")
    print(f"media contract + flags recorded in {created.manifest.as_posix()}")

    niche, thesis = discover.classify_input(args.thesis)
    now = datetime.now(UTC)
    if args.feeds_from is not None:
        items = discover.read_feeds_dir(args.feeds_from)
    elif os.environ.get("VIBE_OFFLINE"):
        items = []
    else:
        items = discover.fetch_feeds(discover.urlopen_fetcher)
    topic_brief = discover.build_topic_brief_from_items(
        items, niche=niche, thesis=thesis, now=now
    )
    if topic_brief is None:
        print(
            "vibe make: no on-topic topic found; brief.json not written (research is "
            "best-effort at the CLI seam)",
            file=sys.stderr,
        )
        return 0
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