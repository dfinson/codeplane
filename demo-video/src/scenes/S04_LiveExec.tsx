/**
 * S04 — Live execution: running job detail with transcript.
 * Zooms into the transcript area to show tool calls and agent reasoning.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["500"] });

export const S04_LiveExec: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.liveExecution;

  // Title overlay at start
  const titleOpacity = interpolate(frame, [0, 15, 55, 70], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Full page view → zoom into transcript area
  const imgScale = interpolate(frame, [40, dur], [1, 1.25], { extrapolateRight: "clamp" });
  const imgY = interpolate(frame, [40, dur], [0, -120], { extrapolateRight: "clamp" });
  const imgOpacity = interpolate(frame, [10, 35], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, fontFamily }}>
      {/* Title */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          zIndex: 2,
          pointerEvents: "none",
          opacity: titleOpacity,
        }}
      >
        <h2
          style={{
            fontSize: 72,
            fontWeight: 500,
            color: C.white,
            textShadow: "0 4px 40px rgba(0,0,0,0.9)",
            letterSpacing: "-0.02em",
          }}
        >
          Watch it think. Watch it build.
        </h2>
      </div>

      {/* Screenshot with zoom effect */}
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          overflow: "hidden",
          opacity: imgOpacity,
        }}
      >
        <Img
          src={staticFile("captures/job-running-live.png")}
          style={{
            width: "94%",
            borderRadius: 20,
            boxShadow: "0 30px 100px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)",
            transform: `scale(${imgScale}) translateY(${imgY}px)`,
            transformOrigin: "center 40%",
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
