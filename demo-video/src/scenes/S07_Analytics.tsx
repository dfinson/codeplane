/**
 * S07 — Analytics: scorecard + model comparison.
 * Cross-fades between top analytics view and scrolled-to model table.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["500"] });

export const S07_Analytics: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.analytics;
  const half = Math.floor(dur / 2);

  // Title
  const titleOpacity = interpolate(frame, [0, 15, 55, 70], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // First half: analytics-top
  const img1Opacity = interpolate(frame, [10, 30, half - 10, half + 5], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const img1Scale = interpolate(frame, [10, half], [1.05, 1], { extrapolateRight: "clamp" });

  // Second half: analytics-models
  const img2Opacity = interpolate(frame, [half - 5, half + 15], [0, 1], { extrapolateRight: "clamp" });
  const img2Scale = interpolate(frame, [half, dur], [1.05, 1], { extrapolateRight: "clamp" });

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
            fontSize: 64,
            fontWeight: 500,
            color: C.white,
            textAlign: "center",
            lineHeight: 1.4,
            textShadow: "0 4px 40px rgba(0,0,0,0.9)",
            letterSpacing: "-0.02em",
          }}
        >
          Know what it costs{"\n"}and what it{"'"}s worth.
        </h2>
      </div>

      {/* Analytics top */}
      <div
        style={{
          position: "absolute",
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          overflow: "hidden",
          opacity: img1Opacity,
        }}
      >
        <Img
          src={staticFile("captures/analytics-top.png")}
          style={{
            width: "94%",
            borderRadius: 20,
            boxShadow: "0 30px 100px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)",
            transform: `scale(${img1Scale})`,
          }}
        />
      </div>

      {/* Analytics models */}
      <div
        style={{
          position: "absolute",
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          overflow: "hidden",
          opacity: img2Opacity,
        }}
      >
        <Img
          src={staticFile("captures/analytics-models.png")}
          style={{
            width: "94%",
            borderRadius: 20,
            boxShadow: "0 30px 100px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)",
            transform: `scale(${img2Scale})`,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
