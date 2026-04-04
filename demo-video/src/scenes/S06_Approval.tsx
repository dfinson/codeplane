/**
 * S06 — Approval flow: job detail with approval banner visible.
 * Shows the real approval banner with Approve/Reject buttons.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["500"] });

export const S06_Approval: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.approval;

  // Title
  const titleOpacity = interpolate(frame, [0, 15, 55, 70], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Screenshot
  const imgOpacity = interpolate(frame, [10, 35], [0, 1], { extrapolateRight: "clamp" });
  const imgScale = interpolate(frame, [10, dur], [1.06, 1], { extrapolateRight: "clamp" });
  // Slowly zoom toward the approval area (upper portion)
  const imgY = interpolate(frame, [60, dur], [0, -40], { extrapolateRight: "clamp" });

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
          Stay in the loop{"\n"}without breaking flow.
        </h2>
      </div>

      {/* Approval screenshot */}
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
          src={staticFile("captures/job-approval.png")}
          style={{
            width: "94%",
            borderRadius: 20,
            boxShadow: "0 30px 100px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)",
            transform: `scale(${imgScale}) translateY(${imgY}px)`,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
