# T6 — Assembly: preview, fan-out, full concat: design

**Date:** 2026-08-14
**Branch:** `build/t6` (forked from `build/t5`, a stacked PR off `main`; rebased onto `main` once T5 lands)
**Sources of truth:** `docs/specs/assembly.md` (end treatment §5, full-video assembly §6, review gates §8, determinism & layout §9, scope §1/§10), issue #11 (T6 AC), `docs/research/video-retention.md`, and the T5 artifacts it consumes (`build/segments/segment-<n>.mp4`, `vibe/render.py`, `vibe/check.py::check_video(kind="full")`).

## 1. Decisions (confirmed with the operating partner)

- **Branch base is `build/t5`** (stacked PR). T6 integrates against the real T5 render code from day one; `build/t6` is rebased onto `main` once T5 merges (validated via `finishing-a-development-branch`).
- **One new subcommand `vibe assemble`** drives the whole stage: segment-1 preview gate → self-guided rework loop → parallel fan-out of remaining segments → recap clip + copy-concat → `build/full.mp4` → deterministic check. Mirrors the single-entry-point idiom of `vibe render`/`vibe narrate`. `vibe render` stays as T5 left it (sequential all-segments, no gate).
- **The rework loop re-synthesizes and re-renders segment 1 automatically** — the human never edits narration knobs by hand. Each rejection iteration re-synthesizes segment 1's narration at an automatically tuned base rate, re-renders only segment 1, and re-previews, looping until approval. (The narration knobs exist: `narrate.KNOBS` per-kind prosody + the base speaking `rate` passed to the synth; see §4.)
- **Fan-out uses `ThreadPoolExecutor`** over segments 2..N calling `render_segment`. The encode bottleneck is a child ffmpeg process, so real renders genuinely run concurrently; identical inputs → byte-identical files. Closures (renderer/encoder) are reused as-is — no pickling.
- **Assembly never makes creative decisions (assembly §1).** The auto-tuning *policy* is a pure, deterministic, seam-isolated function with a conservative default; it is not ad-hoc creative judgement inside the orchestrator. Assembly is never a human gate beyond the segment-1 preview (assembly §8).

## 2. Module boundary

New module `vibe/assembly.py`, mirroring `vibe/render.py`: a pure core (offline-testable), injectable seams (real vs fake), and an orchestrator + thin CLI wiring.

### Pure core (no I/O; math, ordering, policy only)

- `NarrateKnob` — the prosody set for a rework attempt: a `base_rate: str` plus the per-kind `KNOBS` offsets. Default = the spec's `KNOBS` with `base_rate` applied.
- `rework_base_rate(attempt: int) -> str` — deterministic pacing step per rejection iteration: `0% → -6% → -12% → -18%`, capped at the 4th. Signed prosody (`+0%`/`-6%`…), so edge-tts accepts it. Unit-tested for exact values and the cap. This is the *one* auto-tuned creative default; everything else is reuse.
- `concat_list(clips: Sequence[Path]) -> str` — exact `ffmpeg -f concat -safe 0` list-file text: `file '<path>'` per clip, ordered `seg1 … segN, recap`. Unit-tested for exact syntax and ordering.
- `expected_full_duration(clip_durations: Sequence[float], *, recap_s: float) -> float` — `sum(clip_durations) + recap_s`; the deterministic final-check figure. Unit-tested.
- `_fanout() -> range` — `range(2, N+1)`; the post-approval segment ordering.
- `RECAP_SECONDS = config.RECAP_SECONDS` — the full-video tail length; single source of truth.
- `SegmentsReducer`-style helpers, if needed, for "which segments lack a clip" (skip-existing → idempotent re-runs, assembly §9).

### Seams (Protocols), mirroring T5's `ImageRenderer`/`Encoder`

- `RecapEncoder.__call__(png: bytes, *, width: int, height: int, fps: int, seconds: float) -> bytes` — real `ffmpeg_recap_encoder()`: `-loop 1 -i <png>` + `-f lavfi -i anullsrc` (silent AAC, keeping one A+V stream for the copy-concat) + `config` fixed video/audio flags + `-t seconds` → §2.2 clip bytes. This is assembly §6's "recap is the only re-encoded input". Fake: `fake_recap_encoder()` → `b"recap-clip"`.
- `Concatener.__call__(clips: Sequence[Path], out: Path, *, list_text: str) -> None` — real `ffmpeg_concatener()`: write the list file, run `ffmpeg -f concat -safe 0 -i <list> -c copy +faststart <out>`, wrap non-zero in `AssemblyError` (mirrors `render.ffmpeg_encoder` hygiene: `check=False`, capture stderr, no partial output via temp-then-rename). Fake: `fake_concatener()` writes `out` (offline CLI tests).
- The existing `render.ImageRenderer`/`render.Encoder` and `narrate.Synthesizer`/`narrate.Encoder` are reused for the segment renders and the segment-1 re-synthesis.

### Orchestrator

- `AssembleResult` — `(step: str, index: int | None, ok: bool, message: str)` covering gate, each rendered segment, recap, concat, and the final check. One per milestone, so the CLI prints a deterministic run-down.
- `assemble_approved(lay, *, synth, nar_enc, renderer, enc, recap_enc, concatener, font=None) -> list[AssembleResult]` — the flow in §3. Pure-ish decisions; all I/O through seams and `Layout` paths. Any heavy-job result is recorded, never lost on failure.

## 3. Orchestration flow (`assemble_approved`; `vibe assemble` wraps it)

1. Load `brief.json` + `scripts/index.json`. Missing index → CLI exits 2. Ensure `hero.png` exists (`render.make_hero` if missing, temp-then-rename).
2. Produce `build/recap.png` via `make_recap(brief, font=...)` (deterministic, like hero) if missing.
3. **Segment-1 preview gate.** Ensure `build/segments/segment-1.mp4` exists (render via `render.render_segment` if missing). Pause: on a tty prompt (`Approve segment 1? [y/N]`), non-tty **auto-approves** (CI/offline). On **reject**, run the **self-guided rework loop** (§4), then return to the gate prompt. Only segment 1 is touched during the loop.
4. **Parallel fan-out.** After approval, render any missing segments **2..N** via `ThreadPoolExecutor` over `render.render_segment` (real ffmpeg subprocesses; skip-existing → idempotent). Collect per-segment results.
5. **Recap clip + concat.** Encode `build/recap.mp4` (`RecapEncoder`), then `Concatener` over `[seg1.mp4 … segN.mp4, recap.mp4]` → `build/full.mp4` (stream-copy, `+faststart`).
6. **Deterministic final check** (never a human gate, assembly §8): `check.check_video(full.mp4, kind="full")` OK **and** container duration ≈ `expected_full_duration(Σ clip durations, recap_s=RECAP_SECONDS)`. Report.

Exit codes mirror `vibe render`: `0` success (incl. skips + gate-approved), `1` any error, `2` missing index. On gate-reject past the attempt cap → exit non-zero with a clear `needs-human` message (never silently ships a reworked take).

## 4. The self-guided rework loop (automatic knobs)

The narration "knobs" the loop tunes are the base speaking `rate` passed to the synthesizer (per-kind emphasis `KNOBS` stay fixed — the spec's §4 mapping). On rejection iteration `attempt`:

1. `rate = rework_base_rate(attempt)`.
2. `narrate_segment(text_1, base_rate=rate, synthesizer=synth, encoder=nar_enc)` → new `.mp3` + `.timing.jsonl` for segment 1, atomically written (`narrate._write_atomic`).
3. `render.render_segment(text_1, new_timing, new_mp3, footline, hero, …)` → re-render only `segment-1.mp4`.
4. Re-preview (back to the gate prompt).

Lower `base_rate` slows the take (edge-tts adjusts word timing), so successive rejects yield progressively slower, re-prosodied takes the human can react to. Attempts cap at 4 (`rework_base_rate`'s ceiling); past the cap the gate declines and exits non-zero with `needs-human`, so a bad take can't loop forever. The first sight vs. first-gate nuance is moot: iteration 1 uses `0%` (default pacing), so a fresh, un-tuned segment-1 render is what the human first previews. Segments 2..N always narrate/render at the default knob (no re-tuning).

`narrate_segment` gains an optional `base_rate: str = "0%"` keyword (default unchanged), so T4/T5 narration output stays byte-identical when the knob isn't used. The rate string passed to the synth is the per-kind `KNOBS` rate with `rework_base_rate`'s value applied as the base (combined deterministically; the pure combining helper is unit-tested). `edge_tts_synthesizer` already signs prosody (`_signed_prosody`), and `rework_base_rate` returns pre-signed strings.

## 5. Recap card (assembly §5, §6)

- `make_recap(brief, *, font=None) -> bytes` — a deterministic 1920×1080 designed summary frame (PNG), produced with Pillow exactly like `make_hero`: paper-bg, ink title area, segment titles in `positive`, a figure/source line from the brief publisher. **No date/no seed → byte-identical on repeat.** Owner: PIL.
- `Layout.recap_png -> root/recap.png`; `Layout.recap_video -> root/recap.mp4` (the §6 still-loop clip, the only re-encoded input).
- `build/recap.mp4` carries a **silent AAC** track (anullsrc) so the copy-concat keeps one video + one audio stream and `check_video(kind="full")` finds an audio stream. Duration `RECAP_SECONDS = 3.0`.
- The recap appears in the **full video only** (assembly §5); shorts (§2.4) and CC sidecars (§2.5) are T7.

## 6. CLI wiring: `vibe assemble`

```
vibe assemble                # gated: seg1 preview -> fan-out -> recap -> concat -> full.mp4 (./build)
vibe assemble --build DIR    # different build root (default ./build)
```

- Missing index/build → message + exit 2.
- Gate: tty → interactive `input()` approve/reject; non-tty → auto-approve.
- Fan-out/recap/concat errors → stderr, exit 1; partial artifacts stay (best-effort, temp-then-rename per write).
- Successful `full.mp4` → print `full.mp4: OK (full) <duration>s` + the deterministic check line.
- Test seam: `VIBE_ASSEMBLER=fake` selects the fake recap-encoder + concatener alongside `VIBE_RENDERER=fake`, so the CLI test is offline and instant (same idiom as `VIBE_NARRATOR`/`VIBE_RENDERER`).

## 7. Determinism, error handling, testing

### Determinism

- Pure core (`rework_base_rate`, `concat_list`, `expected_full_duration`, `make_recap`, ordering) fully deterministic + unit-tested.
- Copy-concat produces `full.mp4` whose container duration = Σ clip durations + `RECAP_SECONDS`; recomputed, never accumulated (assembly §9: idempotent re-runs). Skip-existing on the fan-out keeps re-runs cheap and deterministic.
- Real recap + concat are the only new real-ffmpeg steps; both honor `config` fixed flags.

### Error handling

- Missing index/build → exit 2.
- Missing segment-1 narration/script during rework → `AssemblyError`, exit 1, no partial write.
- ffmpeg failure (recap or concat) → `AssemblyError`, exit 1, no partial `full.mp4`/`recap.mp4` (temp-then-rename).
- Reject past the attempt cap → decline + exit non-zero + `needs-human` message (no silent auto-ship).
- `hero.png`/`recap.png` integrity: re-rendered deterministically if missing; never partially written.

### Testing (offline, fixture-driven; T5 rhythm)

- Pure unit tests: `rework_base_rate` exact steps + cap; `concat_list` exact syntax + order; `expected_full_duration` arithmetic; `make_recap` deterministic PNG bytes (gated on Pillow, no network).
- `narrate_segment(base_rate=...)`: default path byte-identical to T4/T5; a non-zero `base_rate` changes the rate passed to a fake synth (assert on captured args).
- `assemble_approved` with all-fake seams: renders seg1 → gate (inject `approve=False` then `True`) → re-renders seg1 on reject (assert other segments untouched) → fans out 2..N → recap + concat → full.mp4 written; skip-existing honored; `needs-human`/not-approved handled.
- CLI `vibe assemble` (fake seams): fixture build (`make` + fake narrate/render) → `assemble` writes `full.mp4`, exit 0; missing index → exit 2.
- **Gated real test** (ffmpeg present, no network): a few tiny real 1920×1080 clips (small frame counts via small `duration`/`open_s`) → real recap → real concat → `check_video(full.mp4, kind="full")` OK + duration matches `expected_full_duration`. Kept small; the full-res multi-minute live run is left to a human/CI (never a gating verification under this shell, per T5 handoff).
- Full suite: `pytest` / `mypy vibe` / `ruff check vibe tests` all clean.

## 8. Files touched

New: `vibe/assembly.py`, `tests/test_assembly.py`, `tests/test_cli_assemble.py`.
Modified: `vibe/config.py` (`RECAP_SECONDS`, `RECAP_LABEL`), `vibe/layout.py` (`Layout.recap_png`, `Layout.recap_video`), `vibe/cli.py` (`assemble` subcommand + `_select_assembler` + `_cmd_assemble` + env seam), `vibe/narrate.py` (`narrate_segment` optional `base_rate`), `tests/conftest.py` (seam env), `docs/specs/assembly.md` (implementation note).
Docs: this design spec + the T6 plan (`docs/superpowers/plans/2026-08-14-t6-assembly-stage.md`), `#T6` commit tags.

## 9. Out of scope (for this ticket)

- Shorts (9:16 re-render) + CC sidecars (`.srt`) — T7 (issues #16/#17).
- Upload/publish, background music, spoken CTA (assembly §10).
- E2E smoke at the CLI seam in CI — T8.
- Choosing/licensing a bundled font (deferred; deterministic default fallback used).
- A narration-knob UI / principled pacing science — assembly only applies a fixed, capped automatic step; deeper tuning is narration-stage work.