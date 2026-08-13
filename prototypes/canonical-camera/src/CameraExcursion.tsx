import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
  Easing,
  staticFile,
} from "remotion";

type Target = { t: number; scale: number; cx: number; cy: number };

// Hero tile centers (from gen_assets.py layout). Index = topic number.
const TILES = [
  { cx: 510, cy: 300 },
  { cx: 1410, cy: 300 },
  { cx: 510, cy: 780 },
  { cx: 1410, cy: 780 },
];

const FULL_SCALE = 2.45; // scale where a tile fills the 1920x1080 viewport.
const CENTER = { cx: 960, cy: 540 };

// Camera timeline: [startSec, endSec] for hero-zoom legs and the segment holds.
type SegmentPlan = {
  topic: number;
  zoomIn: [number, number];
  hold: [number, number];
  zoomOut: [number, number];
};

// TODO tune: this encodes one segment excursion = zoom-in, hold the segment, zoom-out.
// The hero offset (slide to the next topic) is just the NEXT segment's zoom-in
// starting from the full-hero frame, so it is implicit in the timeline.
const PLAN: SegmentPlan[] = [
  { topic: 0, zoomIn: [1.2, 2.4], hold: [3.0, 5.5], zoomOut: [5.5, 6.1] },
  { topic: 1, zoomIn: [6.6, 7.8], hold: [8.4, 9.4], zoomOut: [9.4, 10.0] },
];

function cameraAt(t: number): { scale: number; cx: number; cy: number; activeTopic: number | null } {
  // Before the first zoom-in: full hero.
  let state: Target = { t: 0, scale: 1, cx: CENTER.cx, cy: CENTER.cy };
  let activeTopic: number | null = null;

  for (const plan of PLAN) {
    const tile = TILES[plan.topic];
    // Zoom in
    if (t >= plan.zoomIn[0] && t <= plan.zoomIn[1]) {
      const p = interpolate(t, plan.zoomIn, [0, 1], {
        easing: Easing.inOut(Easing.cubic),
      });
      return {
        scale: 1 + (FULL_SCALE - 1) * p,
        cx: CENTER.cx + (tile.cx - CENTER.cx) * p,
        cy: CENTER.cy + (tile.cy - CENTER.cy) * p,
        activeTopic: null,
      };
    }
    // Hold: topic fills screen at full scale.
    if (t >= plan.zoomIn[1] && t < plan.hold[1]) {
      return { scale: FULL_SCALE, cx: tile.cx, cy: tile.cy, activeTopic: plan.topic };
    }
    // Zoom out back to hero.
    if (t >= plan.zoomOut[0] && t <= plan.zoomOut[1]) {
      const p = interpolate(t, plan.zoomOut, [0, 1], {
        easing: Easing.inOut(Easing.cubic),
      });
      return {
        scale: FULL_SCALE + (1 - FULL_SCALE) * p,
        cx: tile.cx + (CENTER.cx - tile.cx) * p,
        cy: tile.cy + (CENTER.cy - tile.cy) * p,
        activeTopic: null,
      };
    }
  }
  return { ...state, activeTopic, scale: 1, cx: CENTER.cx, cy: CENTER.cy };
}

export const CameraExcursion: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const cam = cameraAt(t);

  const currentPlan = PLAN.find(
    (p) => t >= p.zoomIn[1] && t < p.hold[1]
  );
  const topicOpacity = currentPlan ? 1 : 0;

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
      {/* Active topic fullscreen overlay - crossfaded in on the hold. */}
      {currentPlan && (
        <img
          src={staticFile(`assets/topic${currentPlan.topic}.png`)}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: 1920,
            height: 1080,
            opacity: topicOpacity,
          }}
        />
      )}
      {/* Debug readout: shows the live camera state (surface the state after every move). */}
      <div
        style={{
          position: "absolute",
          bottom: 16,
          left: 16,
          color: "#fff",
          fontFamily: "monospace",
          fontSize: 22,
          backgroundColor: "rgba(0,0,0,0.6)",
          padding: "8px 14px",
          borderRadius: 6,
        }}
      >
        t={t.toFixed(2)}s scale={cam.scale.toFixed(2)} center=({cam.cx},{cam.cy}) topic={cam.activeTopic}
      </div>
    </AbsoluteFill>
  );
};