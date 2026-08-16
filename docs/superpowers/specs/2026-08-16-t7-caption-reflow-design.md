# T7 follow-up — vertical short caption shrink-to-fit: design

**Date:** 2026-08-16
**Branch:** `build/t7-followup` (fresh worktree off `main` @ `3d357f1`)
**Sources of truth:** `docs/specs/assembly.md` §4 (Keyword captions: "one glanceable line = one meaning"), §2.4 (vertical short render); `vibe/shorts.py` `_draw_caption` (the overflow site); the T7 design/plan (`2026-08-15-t7-shorts-cc-design.md`, `2026-08-15-t7-shorts-cc-stage.md`).

## 1. Problem

`vibe/shorts.py::_draw_caption` draws a single-line caption with only a centering shift:

```python
keep = max(1, frame.width - 120)                      # 960 px usable on a 1080 canvas
x = (frame.width - total) / 2.0 if total <= keep else (frame.width - keep) / 2.0
```

When the summed caption width `total` exceeds `keep`, the line is left-shifted so it anchors at the left safe edge and runs past the **right** edge of the 1080×1920 frame — clipped. At `CAPTION_SIZE=48` a ~17-word authored line measures ~2400–2800 px, far beyond the vertical canvas. This is the vertical deliverable's core visual-correctness surface (a caption is exactly what a viewer reads, spec §4).

Resolver note: the T7 plan's verbatim Task-2 code drew single-line (mirroring the shared 16:9 `render._draw_caption`), while design §2 mentioned "modest horizontal-reflow margins" — an intent ambiguity. Resolved in this follow-up by a decision with the operating partner.

## 2. Decision

**Shrink the caption to fit one glanceable line inside the safe zone; no shrink floor.**

- The governing tenet is spec §4's "Caption = one line = one meaning": a caption is one spoken line, so the fix keeps it on **one line** rather than wrapping it onto multiple.
- **Scale = `min(1.0, keep / total)`** — never upscale. No lower floor: pathological lines are allowed to shrink to whatever size fits, so the line is always entirely within the safe zone (never clipped, never letterboxed out).
- The change is confined to `vibe/shorts.py` (`_draw_caption` + a pure `_fit_factor` helper). `render.py`, `config.py`, `check.py`, `assembly.py` are **not** modified — the shared 16:9 path keeps its behavior (its canvas is much wider; not in scope).

## 3. Design

### Pure helper

- `_fit_factor(total: float, keep: float) -> float` — `min(1.0, keep / max(total, 1e-9))`. Deterministic, no Pillow, no I/O → unit-testable offline.

### `_draw_caption` becomes shrink-aware

1. Measure `total` as today (figures use `fig_font`, the rest `cap_font`).
2. `factor = _fit_factor(total, keep)`.
3. If `factor < 1.0`, rebuild **scaled** caption fonts from `config` sizes (mirroring `vertical_renderer` sizing — this is the default-production `font=None` path):
   - `cap = render.resolve_font(max(1, int(config.CAPTION_SIZE * factor)))`
   - `fig = render.resolve_font(max(1, int(config.CAPTION_SIZE * 1.15 * factor)))`
4. **Re-measure** with the scaled fonts; if the rescaled `total` still exceeds `keep` (integer-size rounding can undershoot the exact factor), decrement the size by 1 and re-measure — a small **bounded** loop that deterministically lands the line inside `keep` (guarantee, not approximation).
5. Center `x = (frame.width - total_scaled) / 2.0`; baseline + footline unchanged.

### Custom-font seam

`vertical_renderer(font=...)` can inject an arbitrary font object whose size is unknowable, so shrink is skipped there (factor effectively 1). That path is a synthetic test seam, not production (`font=None`); production shrinks correctly from `config` sizes.

## 4. Determination & error handling

Pure factor math + bounded integer re-fit ⇒ byte-identical output for identical inputs, consistent with the repo determinism convention. No new failure modes: `_fit_factor` guards divide-by-zero; font sizes floor at 1. A `None` (absent caption) is untouched upstream.

## 5. Testing (offline, T5/T6 rhythm)

- `_fit_factor(total ≤ keep)` → `1.0`; `_fit_factor(2800, 960)` → `960/2800`; `_fit_factor(0, 960)` → `1.0` (no division by zero).
- **One long-caption pin (`pytest.importorskip("PIL")`):** render a caption whose natural width `> keep`; assert the rescaled drawn width `≤ keep` (this is what the re-fit loop pins).
- Control: a short caption (`total ≤ keep`) — shrink is a no-op (factor `1.0`), existing output identical.
- Full suite: `pytest` / `mypy vibe` / `ruff check vibe tests` clean.

## 6. Files touched

- Modified: `vibe/shorts.py` (`_fit_factor` + `_draw_caption`), `tests/test_shorts.py` (new tests), `docs/specs/assembly.md` (§2.4 brief implementation note).
- New: this design doc.
- **NOT modified:** `vibe/render.py`, `vibe/config.py`, `vibe/check.py`, `vibe/assembly.py`, `vibe/cli.py`.

## 7. Out of scope

Wrapping onto multiple lines, a shrink floor, and tuning the safe-zone margins themselves. The upstream 16:9 single-line path (`render._draw_caption`) is left as-is.