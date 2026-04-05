/**
 * S03 — Dashboard reveal: real screenshot with slow zoom-out + title.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["500"] });

export const S03_Dashboard: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.dashboard;

  // Title fades in at start, fades out
  const titleOpacity = interpolate(frame, [0, 15, 60, 75], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleY = interpolate(frame, [0, 15], [20, 0], { extrapolateRight: "clamp" });

  // Screenshot zooms out from slight zoom to full view
  const imgOpacity = interpolate(frame, [20, 50], [0, 1], { extrapolateRight: "clamp" });
  const imgScale = interpolate(frame, [20, dur], [1.12, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, fontFamily }}>
      {/* Title overlay */}
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
          transform: `translateY(${titleY}px)`,
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
          See everything. Across every agent.
        </h2>
      </div>

      {/* Screenshot */}
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
          src={staticFile("captures/dashboard-desktop.png")}
          style={{
            width: "94%",
            borderRadius: 20,
            boxShadow: "0 30px 100px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)",
            transform: `scale(${imgScale})`,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
