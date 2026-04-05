/**
 * S09 — Closing: logo + tagline + URL.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["400", "600"] });

export const S09_Closing: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.closing;

  const logoOpacity = interpolate(frame, [0, 25], [0, 1], { extrapolateRight: "clamp" });
  const logoScale = interpolate(frame, [0, 25], [0.9, 1], { extrapolateRight: "clamp" });
  const tagOpacity = interpolate(frame, [30, 50], [0, 1], { extrapolateRight: "clamp" });
  const urlOpacity = interpolate(frame, [50, 70], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: C.bg,
        justifyContent: "center",
        alignItems: "center",
        fontFamily,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 50 }}>
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
            fontSize: 44,
            color: C.muted,
            textAlign: "center",
            opacity: tagOpacity,
          }}
        >
          The operating layer for your coding agents.
        </p>
        <p
          style={{
            fontSize: 38,
            color: C.primary,
            fontWeight: 600,
            letterSpacing: "0.02em",
            opacity: urlOpacity,
          }}
        >
          github.com/dfinson/codeplane
        </p>
      </div>
    </AbsoluteFill>
  );
};
