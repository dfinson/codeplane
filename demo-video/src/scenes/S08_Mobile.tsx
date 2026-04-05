/**
 * S08 — Mobile showcase: real mobile screenshots in phone frames.
 * Dashboard (left) and job detail (right), side by side.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { C, SCENES, PHONE } from "../constants";

const { fontFamily } = loadFont("normal", { weights: ["500"] });

const PhoneFrame: React.FC<{
  src: string;
  x: number;
  y: number;
  opacity: number;
}> = ({ src, x, y, opacity }) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      width: PHONE.width,
      height: PHONE.height,
      borderRadius: PHONE.radius,
      border: `${PHONE.bezel}px solid #2a2a30`,
      background: "#111",
      overflow: "hidden",
      boxShadow: "0 40px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.05)",
      opacity,
    }}
  >
    <Img
      src={staticFile(src)}
      style={{
        width: "100%",
        height: "100%",
        objectFit: "cover",
        objectPosition: "top",
      }}
    />
  </div>
);

export const S08_Mobile: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.mobile;

  // Title
  const titleOpacity = interpolate(frame, [0, 15, 55, 70], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Phone 1 (dashboard) slides in from left
  const phone1X = interpolate(frame, [20, 50], [1100, 1300], { extrapolateRight: "clamp" });
  const phone1Opacity = interpolate(frame, [20, 45], [0, 1], { extrapolateRight: "clamp" });

  // Phone 2 (job detail) slides in from right
  const phone2X = interpolate(frame, [35, 65], [2200, 2100], { extrapolateRight: "clamp" });
  const phone2Opacity = interpolate(frame, [35, 60], [0, 1], { extrapolateRight: "clamp" });

  const centerY = (2160 - PHONE.height) / 2;

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
          Full visibility from anywhere.
        </h2>
      </div>

      {/* Phone frames */}
      <PhoneFrame
        src="captures/dashboard-mobile.png"
        x={phone1X}
        y={centerY}
        opacity={phone1Opacity}
      />
      <PhoneFrame
        src="captures/job-mobile.png"
        x={phone2X}
        y={centerY}
        opacity={phone2Opacity}
      />
    </AbsoluteFill>
  );
};
