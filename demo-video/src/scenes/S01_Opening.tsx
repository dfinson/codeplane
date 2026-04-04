/**
 * S01 — Opening: Logo + hook question.
 * Dark background, logo fades in, then the question appears.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["400", "600"], subsets: ["latin"] });

export const S01_Opening: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.opening;

  const logoOpacity = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const logoScale = interpolate(frame, [0, 30], [0.85, 1], { extrapolateRight: "clamp" });
  const textOpacity = interpolate(frame, [35, 55], [0, 1], { extrapolateRight: "clamp" });
  const textY = interpolate(frame, [35, 55], [30, 0], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [dur - 15, dur], [1, 0], { extrapolateLeft: "clamp" });

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
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 60 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 40,
            opacity: logoOpacity,
            transform: `scale(${logoScale})`,
          }}
        >
          <Img src={staticFile("mark.png")} style={{ width: 120, height: 120 }} />
          <span style={{ fontSize: 80, fontWeight: 600, color: C.white, letterSpacing: "-0.02em" }}>
            CodePlane
          </span>
        </div>
        <p
          style={{
            fontSize: 56,
            color: C.muted,
            textAlign: "center",
            lineHeight: 1.5,
            maxWidth: 1600,
            opacity: textOpacity,
            transform: `translateY(${textY}px)`,
          }}
        >
          You run 5 agents across 3 repos.{"\n"}
          <span style={{ color: C.fg }}>Where does the work stand?</span>
        </p>
      </div>
    </AbsoluteFill>
  );
};
