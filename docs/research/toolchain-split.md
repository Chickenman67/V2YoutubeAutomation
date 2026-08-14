# Toolchain split: HyperFrames / Remotion / Manim / PIL

## Summary decision

**Consolidate on Remotion + PIL** as the core pair, keep **Manim** only for heavy animated data/chart sequences, and **do not adopt HyperFrames** as a dependency until its capabilities are independently verified.

Every job in the explainer pipeline is owned by exactly one tool, so the look stays consistent across per-segment sub-agents.

## Tool-to-job assignment

| Job | Owner | Why |
|-----|-------|-----|
| Hero-frame stills & static art | **PIL (Pillow)** | Deterministic static rendering of frames and illustration assets; trivial to script headless; zero heavyweight runtime. Best for single-frame output. |
| Segment animation (the explainer) | **Remotion** | React/TypeScript, fully programmatic, renders headless via `remotion render` with bundled Chromium. Native timeline, transforms, `interpolate()`, `spring()` for easing. One-command headless render is the exact ergonomic the one-sub-agent-per-segment model needs. |
| Data / chart sequences (finance) | **Remotion first, Manim when a chart must *evolve*** | For most finance charts, render the static chart with PIL then animate frames/elements inside Remotion — keeps a single visual language. Reserve Manim for genuinely animated mathematical sequences it is built for. |
| Easing / motion polish | **Remotion** (`spring()`, `interpolate()`, custom easing) | Easing is a first-class concept; keeps pacing consistent and replicated across segments. |
| On-screen captions | **Remotion** (typed text components) | Captions must follow a typeface and layout from the Design Standard; Remotion text components allow styled, positioned captions rendered with the same headless pipeline. |
| Full-video + shorts assembly | **ffmpeg** (out of scope for this ticket, owned by the assembly ticket) | Not a render tool; composite in assembly. |

## Why not spread jobs across all four

- **PIL** for *animation* would require manual frame loops; uneconomical and produces non-idiomatic motion.
- **Manim** as the default animator produces a school-math aesthetic that fights the "designer-made" Design Standard; its default text/colour styling needs heavy work for a finance explainer.
- **HyperFrames** — could not establish definitive, current documentation. Until verified it should not become a dependency; if its actual purpose (e.g. a DALL-E-style frame model or an easing harness) is confirmed useful later, revisit. **Open risk to resolve before the canonical-camera prototype.**

## Constraints that shape the canonical-camera prototype

1. Remotion owns the **hero-frame -> zoom -> return -> offset** camera: implement each segment excursion as a transform/scale+translate curve with a shared easing so all segments feel like one template.
2. PIL owns the hero-frame still: the single big image the camera zooms from. Output a known resolution so Remotion can treat it as a compositable layer.
3. Keep a shared visual theme file (colour palette, typeface, spacing) consumed by both Remotion and PIL so sub-agents cannot drift.
4. Captions run through Remotion using the Design Standard typeface; source footlines (finance component) render as a captions variant.

## Free / cost

Remotion is free to use open-source (some hosted/cloud features are paid, but local headless rendering is not required to pay). PIL and Manim (MIT) and ffmpeg (GPL) are free. No paid rendering step is forced.

## Verified sources

- Remotion: context7 `/remotion-dev/remotion` (High reputation, 9211 snippets) and `/websites/remotion_dev`.
- Manim: context7 `/manimcommunity/manim` and `/websites/manim_community_en_stable`.
- PIL/Pillow: context7 `/python-pillow/pillow`.
- HyperFrames: **no authoritative documentation located — flag pending verification.**