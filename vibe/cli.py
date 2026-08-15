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

from . import __version__, assembly, check, discover, layout, narrate, render, script

USAGE = "a video needs a thesis"


def _select_script_author() -> script.Author:
    if os.environ.get("VIBE_SCRIPT_AUTHOR") == "failing":
        return script.failing_author
    return script.author_segment


def _select_narrator() -> tuple[narrate.Synthesizer, narrate.Encoder]:
    if os.environ.get("VIBE_NARRATOR") == "fake":
        return narrate.fake_synthesizer(), narrate.fake_encoder()
    return narrate.edge_tts_synthesizer(), narrate.ffmpeg_encoder()


def _select_renderer() -> tuple[render.ImageRenderer, render.Encoder]:
    if os.environ.get("VIBE_RENDERER") == "fake":
        return render.fake_renderer(), render.fake_encoder()
    return render.pillow_renderer(), render.ffmpeg_encoder()


def _select_assembler() -> tuple[assembly.RecapEncoder, assembly.Concatener]:
    if os.environ.get("VIBE_ASSEMBLER") == "fake":
        return assembly.fake_recap_encoder(), assembly.fake_concatener()
    return assembly.ffmpeg_recap_encoder(), assembly.ffmpeg_concatener()


def _gate_prompt() -> bool:
    if sys.stdin is None or not sys.stdin.isatty():
        return True  # non-tty (CI/offline): auto-approve segment 1
    try:
        answer = input("Approve segment 1? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


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

    nar = sub.add_parser("narrate", help="synthesize narration for approved segments")
    nar.add_argument("--build", type=Path, default=Path("build"), metavar="DIR",
                     help="build root with scripts/index.json (default: ./build)")
    nar.set_defaults(_handler=_cmd_narrate)

    rend = sub.add_parser("render", help="render approved segments to self-contained clips")
    rend.add_argument("--build", type=Path, default=Path("build"), metavar="DIR",
                      help="build root with scripts/index.json (default: ./build)")
    rend.set_defaults(_handler=_cmd_render)

    asm = sub.add_parser("assemble", help="assemble the full video (preview -> fan-out -> concat)")
    asm.add_argument("--build", type=Path, default=Path("build"), metavar="DIR",
                     help="build root with scripts/index.json (default: ./build)")
    asm.set_defaults(_handler=_cmd_assemble)
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
        try:
            answer = input("Approve scripts to proceed to narration? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"  # EOF on a tty == declining: block, but still exit 0 (best-effort)
        script.approve_scripts(created, approve=answer in ("y", "yes"))
        if any(r.status == script.STATUS_NEEDS_HUMAN for r in records):
            print("vibe make: some scripts need human review; narration is blocked "
                  "for those segments (best-effort)", file=sys.stderr)
    else:
        script.approve_scripts(created, approve=True)  # non-interactive: auto-approve
    return 0


def _cmd_narrate(args: argparse.Namespace) -> int:
    lay = layout.Layout(root=args.build)
    if not (lay.scripts / "index.json").is_file():
        print(f"vibe narrate: no {lay.scripts.joinpath('index.json').as_posix()}; "
              f"run `vibe make` first", file=sys.stderr)
        return 2
    synthesizer, encoder = _select_narrator()
    results = narrate.narrate_approved(lay, synthesizer=synthesizer, encoder=encoder)
    failed = False
    for res in results:
        print(res.message, file=sys.stderr if not res.ok else sys.stdout)
        failed = failed or (not res.ok and res.status == script.STATUS_APPROVED)
    return 1 if failed else 0


def _cmd_render(args: argparse.Namespace) -> int:
    lay = layout.Layout(root=args.build)
    if not (lay.scripts / "index.json").is_file():
        print(f"vibe render: no {lay.scripts.joinpath('index.json').as_posix()}; "
              f"run `vibe make` first", file=sys.stderr)
        return 2
    renderer, encoder = _select_renderer()
    results = render.render_approved(lay, renderer=renderer, encoder=encoder)
    failed = False
    for res in results:
        print(res.message, file=sys.stderr if not res.ok else sys.stdout)
        failed = failed or (not res.ok and res.status == script.STATUS_APPROVED)
    return 1 if failed else 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    lay = layout.Layout(root=args.build)
    if not (lay.scripts / "index.json").is_file():
        print(f"vibe assemble: no {lay.scripts.joinpath('index.json').as_posix()}; "
              f"run `vibe make` first", file=sys.stderr)
        return 2
    synth, nar_enc = _select_narrator()
    renderer, enc = _select_renderer()
    recap_enc, concatener = _select_assembler()
    verify = os.environ.get("VIBE_ASSEMBLER") != "fake"
    results = assembly.assemble_approved(
        lay, synth=synth, nar_enc=nar_enc, renderer=renderer, enc=enc,
        recap_enc=recap_enc, concatener=concatener, approve=_gate_prompt,
        verify_video=verify,
    )
    failed = False
    for res in results:
        print(res.message, file=sys.stderr if not res.ok else sys.stdout)
        failed = failed or (not res.ok or res.message.startswith("needs-human"))
    return 1 if failed else 0


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