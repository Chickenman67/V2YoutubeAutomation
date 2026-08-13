import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
  Easing,
  staticFile,
} from "remotion";

type Phase = { name: string; start: number; end: number };

type Segment = {
  topic: number;
  title: string;
  phases: Phase[];
};

// Hero tile centers (from gen_assets.py layout). Index = topic number.
const TILES = [
  { cx: 510, cy: 300 },
  { cx: 1410, cy: 300 },
  { cx: 510, cy: 780 },
  { cx: 1410, cy: 780 },
];

const FULL_SCALE = 2.45; // scale where a tile fills the 1920x1080 viewport.
const CENTER = { cx: 960, cy: 540 };

// Phase durations (seconds) per segment. This is the template's knobs.
const DUR = {
  zoomIn: 0.8, // immediate zoom onto the topic tile
  nameHold: 1.6, // time for TTS to say the topic title
  cut: 0.35, // crossfade the tile-zoom -> full topic image ("cut to full screen")
  bodyHold: 2.0, // the segment content itself
  zoomOut: 0.7, // back to the hero menu
};

const TOPIC_TITLES = [
  "Treasury Yields",
  "Fed Rate Hikes",
  "CPI Inflation",
  "S&P 500 Rally",
];

// Build the segment plan sequentially from the duration knobs. Adding a topic
// to TOPIC_TITLES (and a matching topicN.png) is all that's needed.
function buildPlan(topics: number[]): Segment[] {
  const segments: Segment[] = [];
  for (let i = 0; i < topics.length; i++) {
    const base = i === 0 ? 0 : segments[i - 1].phases.at(-1)!.end;
    segments.push({
      topic: topics[i],
      title: TOPIC_TITLES[topics[i]],
      phases: [
        { name: "zoomIn", start: base, end: base + DUR.zoomIn },
        { name: "nameHold", start: base + DUR.zoomIn, end: base + DUR.zoomIn + DUR.nameHold },
        { name: "cut", start: base + DUR.zoomIn + DUR.nameHold, end: base + DUR.zoomIn + DUR.nameHold + DUR.cut },
        { name: "bodyHold", start: base + DUR.zoomIn + DUR.nameHold + DUR.cut, end: base + DUR.zoomIn + DUR.nameHold + DUR.cut + DUR.bodyHold },
        { name: "zoomOut", start: base + DUR.zoomIn + DUR.nameHold + DUR.cut + DUR.bodyHold, end: base + DUR.zoomIn + DUR.nameHold + DUR.cut + DUR.bodyHold + DUR.zoomOut },
      ],
    });
  }
  return segments;
}

const PLAN = buildPlan([0, 1]);
const TOTAL = PLAN.at(-1)!.phases.at(-1)!.end;

function phaseOf(seg: Segment, t: number): Phase | undefined {
  return seg.phases.find((p) => t >= p.start && t < p.end);
}

type Cam = { scale: number; cx: number; cy: number; cutProgress: number; nameOpacity: number; showFull: boolean };

function cameraAt(t: number): Cam {
  // base: start from hero (or the hold of the previous segment's zoom-into next tile)
  let scale = 1;
  let cx = CENTER.cx;
  let cy = CENTER.cy;

  for (let i = 0; i < PLAN.length; i++) {
    const seg = PLAN[i];
    const p = phaseOf(seg, t);
    if (!p) continue;
    const tile = TILES[seg.topic];
    const frame = (ph: string) => seg.phases.find((x) => x.name === ph)!;

    if (p.name === "zoomIn") {
      const k = interpolate(t, [p.start, p.end], [0, 1], { easing: Easing.inOut(Easing.cubic) });
      scale = 1 + (FULL_SCALE - 1) * k;
      cx = CENTER.cx + (tile.cx - CENTER.cx) * k;
      cy = CENTER.cy + (tile.cy - CENTER.cy) * k;
      return { scale, cx, cy, cutProgress: 0, nameOpacity: 1, showFull: false };
    }
    if (p.name === "nameHold") {
      return { scale: FULL_SCALE, cx: tile.cx, cy: tile.cy, cutProgress: 0, nameOpacity: 1, showFull: false };
    }
    if (p.name === "cut") {
      const k = interpolate(t, [p.start, p.end], [0, 1], { easing: Easing.inOut(Easing.cubic) });
      return { scale: FULL_SCALE, cx: tile.cx, cy: tile.cy, cutProgress: k, nameOpacity: 1 - k, showFull: true };
    }
    if (p.name === "bodyHold") {
      return { scale: 1, cx: CENTER.cx, cy: CENTER.cy, cutProgress: 1, nameOpacity: 0, showFull: true };
    }
    if (p.name === "zoomOut") {
      const k = interpolate(t, [p.start, p.end], [1, 0], { easing: Easing.inOut(Easing.cubic) });
      return { scale: FULL_SCALE * k + 1 * (1 - k), cx: tile.cx * k + CENTER.cx * (1 - k), cy: tile.cy * k + CENTER.cy * (1 - k), cutProgress: 1, nameOpacity: 0, showFull: false };
    }
  }
  // After the last segment: rest on the hero menu.
  return { scale: 1, cx: CENTER.cx, cy: CENTER.cy, cutProgress: 0, nameOpacity: 0, showFull: false };
}

export const CameraExcursion: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const cam = cameraAt(t);

  const active = PLAN.find((s) => t >= s.phases[0].start && t < s.phases.at(-1)!.end);

  return (
    <AbsoluteFill style={{ backgroundColor: "#0b0b0f", position: "relative", overflow: "hidden" }}>
      {/* Hero underlay - the topic menu. Camera = translate + scale (transform-origin top-left). */}
      <img
        src={staticFile("assets/hero.png")}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 1920,
          height: 1080,
          transformOrigin: "0 0",
          transform: `translate(${960 - cam.cx * cam.scale}px, ${
            540 - cam.cy * cam.scale
          }px) scale(${cam.scale})`,
        }}
      />
      {/* Full topic image - crossfaded in on the cut, showing after the name is said. */}
      {active && (
        <img
          src={staticFile(`assets/topic${active.topic}.png`)}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: 1920,
            height: 1080,
            opacity: cam.cutProgress,
          }}
        />
      )}
{/* Topic name - baked into the hero tile itself; no separate overlay. */}
    </AbsoluteFill>
  );
};