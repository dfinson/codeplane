import React from "react";
import {
  AbsoluteFill,
  Img,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
} from "remotion";
import { COLORS, FONT } from "../constants";

/**
 * Scene 3: Dashboard — Real screenshot of the CodePlane dashboard.
 * Shows the Kanban board with active jobs.
 * Asset: hero-dashboard.png (real UI screenshot from docs).
 */
export const DashboardScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Screenshot slides in and scales up
  const imgOpacity = interpolate(frame, [0, 1 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const imgScale = interpolate(frame, [0, 1.2 * fps], [0.92, 1], {
    extrapolateRight: "clamp",
  });

  // Feature callouts appear after screenshot
  const calloutOpacity = interpolate(
    frame,
    [2.5 * fps, 3.5 * fps],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill
      style={{
        background: COLORS.bg,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {/* Screenshot with border */}
      <div
        style={{
          opacity: imgOpacity,
          transform: `scale(${imgScale})`,
          borderRadius: 24,
          overflow: "hidden",
          border: `2px solid ${COLORS.border}`,
          boxShadow: "0 40px 120px rgba(99, 102, 241, 0.15)",
        }}
      >
        <Img
          src={staticFile("assets/hero-dashboard.png")}
          style={{
            width: 3200,
            height: "auto",
          }}
        />
      </div>

      {/* Feature badges */}
      <div
        style={{
          opacity: calloutOpacity,
          display: "flex",
          gap: 40,
          marginTop: 60,
        }}
      >
        {["Kanban Board", "Real-time State", "Multi-agent"].map(
          (label) => (
            <div
              key={label}
              style={{
                background: COLORS.bgCard,
                border: `1px solid ${COLORS.border}`,
                borderRadius: 12,
                padding: "16px 36px",
              }}
            >
              <span
                style={{
                  fontFamily: FONT.family,
                  fontSize: 40,
                  fontWeight: 500,
                  color: COLORS.primaryLight,
                }}
              >
                {label}
              </span>
            </div>
          )
        )}
      </div>
    </AbsoluteFill>
  );
};
