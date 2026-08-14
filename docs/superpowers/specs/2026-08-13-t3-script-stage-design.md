# T3 — Script stage + script gate: design

**Date:** 2026-08-13
**Ticket:** https://github.com/Chickenman67/V2YoutubeAutomation/issues/12 (T3 — Script stage + script gate)
**Branch:** `build/t3` (chains on `build/t2`)
**Sources of truth:** `docs/specs/script-standard.md` (decided spec), `docs/specs/assembly.md` (layout + gate position), ticket #9 Testing Decisions.

## 1. Decisions (confirmed with the operating partner)

- **Authoring is fully deterministic templated prose — no human prose editing.** T3's script author is a pure function of `(brief, segment index, attempt)`. This matches the T2 pattern (creative stages approximated in deterministic code with an injectable seam) and keeps the stage offline and byte-idempotent.
- **Best-effort gate failures.** A script that still fails after 3 regeneration attempts is flagged `needs-human` (never auto-shipped, `make` prints a warning, still exits 0). Narration is blocked downstream via the index, mirroring T2's best-effort `make`.
- No new CLI subcommand. The script stage runs inside the single `vibe make` seam (spec: one integration seam).

## 2. Module boundary

New module `vibe/script.py` — pure; no I/O except injected seams. Public functions:

- `author_segment(brief, index, *, attempt=1) -> str` — deterministic templated prose for one segment.
- `check_script(script, *, segment, sources) -> CheckResult` — the Script-Standard deterministic checks; rejects on **any** violation, including the mechanical soft-checks.
- `author_and_gate(brief, index, *, author: Author | None = None, max_attempts: int = 3) -> ScriptRecord` — the gate loop.
- `write_scripts(brief, lay: layout.Layout, *, author: Author | None = None) -> list[ScriptRecord]` — every segment, plus the index.
- `approve_scripts(lay, *, approve: bool) -> None` — flips gate statuses post human approval.
- `read_index(lay: layout.Layout) -> dict[str, object]` — the current gate index (reads `index.json`).

`CheckResult` = `{ok: bool, violations: list[str]}`. `ScriptRecord` = the index row (below).

## 3. Artifacts & build layout

- Layout gains a `scripts` directory (add `"scripts"` to `_LAYOUT_DIRS`); `Layout.scripts` property.
- One script per segment: `build/scripts/segment-<n>.txt` — plain text, one spoken line per caption/cut, markers inline.
- Per-video index: `build/scripts/index.json` — the single source of truth for gate state (downstream narration blocks `needs-human` via this, not file presence).

```json
{
  "video": "<brief title>",
  "scripts": [
    {"index": 1, "file": "segment-1.txt", "word_count": 231, "status": "ready", "attempts": 1, "violations": []},
    {"index": 2, "file": "segment-2.txt", "word_count": 0, "status": "needs-human", "attempts": 3, "violations": ["banned word: delve"]}
  ]
}
```

Status values: `ready` (passed gate, awaiting or granted approval) → `approved` (human approved / auto-approved non-interactively). `needs-human` = flagged after max attempts.

## 4. The deterministic checks (`check_script`)

Hard fails per Script-Standard §5 layer 1:

- **Banned words** (§3.1): whole-word, case-insensitive against the register list. Emit the offending word.
- **Banned openings** (§3.2) and **"in conclusion" / "to summarize"** anywhere.
- **Punctuation/artifacts** (§3.5): no em dash, colon, semicolon, ellipsis; no stray `*` outside `**` marker pairs; no double spaces.
- **Mechanical conclusion** (§3.6): last line must not overlap the hook beyond a threshold (word-overlap heuristic) and has no `in conclusion`/`to summarize`.
- **Contractions** (§3 register): flag spelled-out `cannot / will not / it is / do not` and `is not / does not / was not / were not / are not / would not / could not / should not`.
- **Numbers without `##figure##`** (§4): every standalone number must sit inside a `##figure##`; a `##figure##` must contain a number.
- **Figure traceability** (§4): each spoken figure's digit-string must be a substring of that segment's `key_points` or `sources[]` text — no invented figures.
- **Word budget** (§2): markers stripped → 200–280 words. Emit the count.

Mechanical critique pass (the §3.7 soft checks that are computable):

- ≥1 And/But/So sentence start.
- Sentence-length mixing: non-uniform length distribution (variance-based floor).
- Opener variety: no >80% of lines sharing the same opener word.

Documented out of deterministic scope: tone/rhythm/LLM-critique judgment (§3.7, §5 layer 2) — not reducible to code; the generator is constructed to never trigger them.

## 5. The deterministic generator (`author_segment`)

Templated prose building the Script-Standard §2 skeleton from one brief segment:

- **Hook** — the brief `hook`, deterministically polished (no trailing punctuation artifacts, no banned opener).
- **Thesis** — a claim line derived from the segment `title` + a key figure.
- **Beats** — one per `key_point` (2–4), each citing its figure in `##figure##`, ≤1 `**keyword**` per line.
- **Payoff** — a stakes line drawn from `sources[]`, not restating the hook, no mechanical conclusion.

Constructive guarantees (then re-verified by `check_script`):

- Figures are taken **only** from `key_points`/`sources[]`; every number is wrapped in `##figure##` (on-screen same word).
- Contractions mandatory; punctuation limited to periods/commas/`~`; no colon/em-dash/etc.
- `**gold**` used once on a high-impact figure.
- `attempt` deterministically varies phrasing by cycling connector + sentence-frame pools in a fixed order, so regenerate candidates differ while `author_segment(brief, index, attempt)` stays byte-identical across runs (idempotency).
- Word budget 200–280 met by padding with reusable, concrete, figure-free frames from a fixed pool when the brief's key_points are thin.

**Honest limitation (disclosed, accepted):** fully deterministic templated prose will likely pass the mechanical checks yet read artificial to a human — the accepted cost of the "no human edit" decision; the `needs-human` flag is the backstop.

## 6. The gate (`author_and_gate`)

```
for attempt in 1..max_attempts(3):
    draft = author(segment, attempt)
    result = check_script(draft)
    if result.ok: return ScriptRecord(status="ready", attempts=attempt, violations=[])
return ScriptRecord(status="needs-human", attempts=3, violations=[result.violations])  # never auto-shipped
```

The `author` is an injectable seam (default `author_segment`) so the not-ready path is testable with a stub that always violates.

## 7. CLI wiring & human approval (`vibe make`)

After `brief.json` is written: `write_scripts(brief, layout)` writes `segment-<n>.txt` + `index.json`, then the approval step:

- If stdin is an interactive TTY, print each script path and prompt `Approve scripts to proceed to narration? [y/N]`.
  - `y` → `approve_scripts(layout)` → statuses `approved`.
  - `N` or EOF → statuses remain `needs-human`; print a warning; exit **0** (narration blocked via the index).
- Non-interactive stdin (pipes, CI, the test harness) → auto-approve (spec Testing Decisions: human gates bypassed/auto-approved in test mode).

Test seam: `author` is exposed to the CLI via `VIBE_SCRIPT_AUTHOR=failing` (same idiom as `VIBE_OFFLINE`) so the CLI not-ready path is covered without touching prod defaults.

## 8. Testing (offline, fixture-driven)

- `check_script`: a known-good script passes; one fixture per violating rule fails with the exact violation string.
- `author_segment` golden test: same `(brief, index, attempt)` → same bytes; budget in range; all figures wrapped & traceable; no banned words/openings; markers present.
- `author_and_gate` with a violating stub `author`: `needs-human` at attempt 3, never `ready`.
- CLI: `make --feeds-from <fixtures>` writes `segment-<n>.txt` + `index.json`, auto-approves (non-tty); `VIBE_SCRIPT_AUTHOR=failing` → status stays blocked, exit 0.
- Full suite: `python -m pytest`, `python -m mypy vibe`, `python -m ruff check vibe tests` all clean.

## 9. Files touched

New: `vibe/script.py`, `tests/test_script.py`, `tests/test_cli_script.py`, `tests/fixtures/` script fixtures.
Modified: `vibe/layout.py` (add `scripts` dir + property), `vibe/cli.py` (wire script stage after `brief.json`), `tests/conftest.py` (test seam env), `tests/test_cli_make.py` (assert script artifacts).

## 10. Out of scope (for this ticket)

- Actual narration synthesis (T4), rendering (T5), assembly (T6), shorts/CC (T7), E2E smoke (T8).
- True LLM critique/regeneration — replaced by the deterministic gate described here.