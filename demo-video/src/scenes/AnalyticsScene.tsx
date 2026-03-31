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
 * Scene 7: Analytics — Cost tracking and model comparison.
 * Asset: analytics-dashboard.png (real UI screenshot from docs).
 */
export const AnalyticsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const imgOpacity = interpolate(frame, [0, 0.8 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const imgScale = interpolate(frame, [0, 1 * fps], [0.93, 1], {
    extrapolateRight: "clamp",
  });

  // Stat badges
  const badgeOpacity = interpolate(
    frame,
    [2 * fps, 3 * fps],
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
        gap: 50,
      }}
    >
      <div>
        <span
          style={{
            fontFamily: FONT.family,
            fontSize: 52,
            fontWeight: 600,
            color: COLORS.primary,
            textTransform: "uppercase",
            letterSpacing: "0.15em",
          }}
        >
          Cost Analytics
        </span>
      </div>

      {/* Screenshot */}
      <div
        style={{
          opacity: imgOpacity,
          transform: `scale(${imgScale})`,
          borderRadius: 24,
          overflow: "hidden",
          border: `2px solid ${COLORS.border}`,
          boxShadow: "0 30px 80px rgba(99, 102, 241, 0.12)",
        }}
      >
        <Img
          src={staticFile("assets/analytics-dashboard.png")}
          style={{ width: 2800, height: "auto" }}
        />
      </div>

      {/* Analytics badges */}
      <div
        style={{
          opacity: badgeOpacity,
          display: "flex",
          gap: 40,
        }}
      >
        {[
          "Token usage",
          "Cost per job",
          "Model comparison",
          "Repo breakdown",
        ].map((label) => (
          <div
            key={label}
            style={{
              background: COLORS.bgCard,
              border: `1px solid ${COLORS.border}`,
              borderRadius: 12,
              padding: "14px 32px",
            }}
          >
            <span
              style={{
                fontFamily: FONT.family,
                fontSize: 36,
                fontWeight: 500,
                color: COLORS.accent,
              }}
            >
              {label}
            </span>
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
