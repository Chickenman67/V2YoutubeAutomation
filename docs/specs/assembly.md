# Assembly: full-video composition, captions, and shorts split

**Status:** Decided spec, adopted by this effort. Source: wayfinder ticket #8 (HITL, resolved).
**Consumes:** segment renders (#5 camera, amended below), narration audio + word timing (#7), script lines as the caption/cut unit (#6), caption treatment (#2 design-standard §8), toolchain split (#3), retention research (`docs/research/video-retention.md`).
**Downstream consumer:** the pipeline build (`/to-spec` → `/to-tickets` → `/implement`).

---

## 1. Scope & position in pipeline

Assembly is the last deterministic stage. It consumes finished per-segment artifacts and produces:

1. **The full video** — the ordered list of segment clips joined by hard cuts, followed by the recap card.
2. **The shorts** — one native vertical (9:16) re-render per segment, plus its caption sidecar.

Assembly makes no creative decisions. Its one job is to honour the media contracts (§2) so every piece fits together deterministically and can be re-run idempotently.

Pipeline position (per-segment chain, repeated 1..N):

```
Topic brief → script draft → [SCRIPT GATE] → narration (.mp3 + .timing.jsonl)
           → segment render (16:9, captions burned) → [FIRST-SEGMENT PREVIEW on seg 1]
           → …segments 2..N fan out in parallel…
           → full-video concat + recap card (ffmpeg)
           → vertical short re-renders (9:16)
           → CC sidecars (.srt)
```

## 2. Media contracts (the stage boundaries)

Each stage owns its artifact exactly; no stage re-derives another stage's output.

### 2.1 Narration artifact (from #7, unchanged)

- One `segment-<n>.mp3` + `segment-<n>.timing.jsonl` per segment.
- Word timings: `{"word", "start_s", "end_s"}` lines, ticks/1e7, cumulative across chunks and silence.
- **No title utterance.** The segment title is never spoken. Narration is exactly the script (hook → thesis → beats → payoff). (This keeps #7's contract as written; the earlier idea of prepending the title is dropped.)

### 2.2 Segment render (16:9, self-contained)

One clip per segment, produced once by Remotion, consumed by both the full video and (as source) the vertical short:

| Param | Value |
|---|---|
| Resolution / fps | 1920×1080, 30 fps |
| Video | H.264, `yuv420p`, `-crf` fixed (deterministic), high profile |
| Audio | AAC-LC, 44.1 kHz stereo, muxed in (segment's narration mp3) |
| Captions | burned in (keyword partial-bolding + source footlines, §4) |
| Timeline | `zoomIn 0.8s → cut 0.35s → body` (body = measured narration duration) |

The render is **self-contained**: audio and captions are inside the clip. ffmpeg never re-synthesises, re-times, or re-burns.

> **T5 implementation note (2026-08):** the per-segment render is produced by the adopted **PIL-frames + ffmpeg** path (`vibe render`, `vibe/render.py`) — per-frame compositing via Pillow (paper background, eased t=0 zoom of the hero still, keyword partial-bolding + source footlines burned on the spoken word) finished by an ffmpeg encode with the fixed `config` flags, narration audio delayed `OPEN_PADDING_S` and muxed (`-shortest`). This amends `docs/research/toolchain-split.md`'s Remotion recommendation as a stage-specific determinism/offline-build trade; `toolchain-split.md` is not changed.

### 2.3 Stills (PIL)

- **Hero frame** `hero.png`, 1920×1080 — the zoom-in base; baked into every segment render's open. Must be identical across all segments (one deterministic PIL run).
- **Recap card** `recap.png`, 1920×1080 — silent designed summary frame, full video tail only (§5). Produced like a hero still.

### 2.4 Vertical short render (9:16)

- 1080×1920, 30 fps, same video/audio codec parameters as §2.2.
- Native vertical re-render per segment (Remotion), re-framing the same art into the vertical safe zone, with captions repositioned. **Never letterbox the 16:9 clip into the feed** (retention research §5b: horizontal footage in the vertical feed is smaller and reads as wrong-aspect).

### 2.5 CC sidecars (.srt)

- Verbatim captions (markers stripped) derived from `.timing.jsonl` + script lines.
- One per segment (the short's CC) and one for the full video (concatenated with running offsets).

## 3. The amended camera excursion (retention, amends #5)

The camera decision from #5 (`zoomIn → nameHold → cut → bodyHold → zoomOut`, hero rest between segments) is amended for retention. Research (`video-retention.md`): the drop-off cliff is the first ~30 s; dead-air stretches and slow opens bleed viewers; open in media res with voice+motion in the first 1–2 s.

- **No intro hold.** The video opens on the hero menu and immediately zooms into topic 1 (zoom starts at t=0).
- **No nameHold, no title speech.** The title is visible on the hero tile during the zoom; narration begins with the script's hook line at body start (~1.15 s). No silent hold while a title is spoken.
- **No zoomOut, no hero rest between segments.** Between topics, cut hard from one segment's body straight into the next segment's zoom-in. The full video is a chain of these clips.
- **Dead-air policy:** all-elements-silent stretches stay < ~2 s. The 0.8 s mute zoom-open is acceptable because motion is present and it is under the budget; the recap card is the designed exception (§5).
- **Hook-by-5s is inherited by construction:** the hook line lands at body start (~1.15 s), far inside the first-15 s window.

### Contract requirements on segment renders (from retention research)

These bind the Remotion renderer (owned by #5's template) because assembly consumes its output:

- Every narration line = a caption + a visual/text change; never a sustained static frame without motion or text change (hard limit ~6–10 s for a deliberate big-idea beat).
- New visual beat roughly every 2–4 s in explanatory stretches.
- On-screen numbers/figures drop exactly on the spoken word (timing.jsonl), never divorced from the VO.

## 4. Caption burn-in (from design-standard §8 + retention)

- **Keyword captions** by default: one glanceable line, partial-bolding + accent colour on the `**keyword**`, tabular accent-coloured figures for `##figure##`, gold for `**gold**`. Caption = one line = one meaning.
- **Source footline** (24 px, "Source: …") on screen for every statistic, same second as the figure.
- Caption holds track the line being spoken and stay ≥ ~1.2–1.5 s (readability floor).
- **Full verbatim is never burned in** — it ships as the YouTube CC sidecars (§2.5). Markers (`**`, `##`, `~`) are stripped; `~` pauses appear as timing gaps.
- A figure spoken must appear on screen in the same second (finance rule, design-standard §8).

## 5. End treatment

- The final payoff (last script line) lands before the video's last few seconds.
- The video then holds the **recap card** (~3 s, silent, designed summary frame). It is the tail/end-screen surface; a designed frame earns the hold, and it is the one exception to the <2 s dead-air default.
- Recap card appears in the **full video only**; shorts end on their own body.
- **Spoken CTA: deferred.** Optional, "decided per video" (script-standard §3.8). No production video exists yet; the spec reserves the slot but nothing in this pipeline synthesises a CTA line yet.

## 6. Full-video assembly (ffmpeg)

1. Segment clips are already self-contained A/V with identical codec parameters (§2.2).
2. Recap card: `ffmpeg -loop 1 -i recap.png` → still-loop mp4 encoded to §2.2 parameters, silent (~3 s).
3. Concatenate: `ffmpeg -f concat -safe 0 -i list.txt -c copy` over `[seg1, seg2, …, segN, recap]` — **stream copy, no re-encode** (possible only because §2.2 parameters are locked). The last clip (recap) is the only re-encoded input.
4. Final mux: `+faststart`, deterministic flags, no noise logs.

The full-video run is deterministic and cheap (copy-concat); re-runs after a segment fix only re-concat.

## 7. Shorts assembly

Per segment:

- Native 9:16 re-render (§2.4): re-framed art, captions in the vertical safe zone, same open (zoom + talk immediately; first narration word ≈ 1.1–1.2 s).
- The short's package = `short-<n>.mp4` + `short-<n>.srt` + the segment title (as its upload title). No letterboxing.
- The short is never a new idea — only the segment repackaged (CONTEXT.md).

## 8. Review gates

- **Script gate** — upstream of narration, per segment, after each script draft (script-standard §6). It gates entry into production. Unchanged from #6.
- **First-segment preview** — after segment 1's full chain produces its assembled clip (render + narration + captions). Human reviews it. On rejection, a **targeted rework loop**: tune narration knobs → re-synthesize → re-render only segment 1 → re-preview. Only after approval do segments 2..N fan out in parallel.
- **Final full-video check is deterministic** (duration = Σ segments + recap, file presence, no empty streams), not a human gate.
- Assembly itself is never a gate; it runs only after every segment is approved.

## 9. Determinism & output layout

- Fixed encoder flags, fixed `-crf`, fixed seed-less deterministic inputs, fixed order. Same inputs → byte-identical outputs.
- Idempotent re-runs: a re-render of one segment replaces that one clip; concat is recomputed, not accumulated.
- Build layout (proposal, implementation may adapt):

```
build/
  segments/segment-<n>.mp4
  shorts/short-<n>.mp4
  cc/full.srt, cc/segment-<n>.srt
  full.mp4
```

## 10. What this spec deliberately leaves out

- Upload/publish to YouTube Studio (map: out of scope). End-screen elements are added there, not in this pipeline.
- Background music (voice-only pipeline; no music layer was ever specified).
- Proactive topic discovery.
- The spoken CTA line (deferred, §5).
