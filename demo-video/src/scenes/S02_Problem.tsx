/**
 * S02 — Problem statement: "Every coding agent is a black box."
 * Bold text on dark background with subtle reveal.
 */
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["300", "700"], subsets: ["latin"] });

export const S02_Problem: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.problem;

  const opacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const y = interpolate(frame, [0, 20], [40, 0], { extrapolateRight: "clamp" });
  const subOpacity = interpolate(frame, [25, 45], [0, 1], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [dur - 12, dur], [1, 0], { extrapolateLeft: "clamp" });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: C.bg,
        justifyContent: "center",
        alignItems: "center",
        fontFamily,
        opacity: fadeOut,
      }}
    >
      <div style={{ textAlign: "center" }}>
        <h1
          style={{
            fontSize: 88,
            fontWeight: 700,
            color: C.white,
            letterSpacing: "-0.03em",
            opacity,
            transform: `translateY(${y}px)`,
            margin: 0,
          }}
        >
          Every coding agent is a black box.
        </h1>
        <p
          style={{
            fontSize: 44,
            fontWeight: 300,
            color: C.muted,
            marginTop: 40,
            opacity: subOpacity,
          }}
        >
          5 terminals. 5 branches. No shared picture.
        </p>
      </div>
    </AbsoluteFill>
  );
};
