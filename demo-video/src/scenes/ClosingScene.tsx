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
 * Scene 8: Closing — Logo, URL, and key value props.
 * Restrained ending with the product identity.
 */

const VALUE_PROPS = [
  "Headless execution",
  "Approval gates",
  "Remote-first",
  "Cost visibility",
];

export const ClosingScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const logoOpacity = interpolate(frame, [0, 0.8 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const logoScale = interpolate(frame, [0, 0.8 * fps], [0.9, 1], {
    extrapolateRight: "clamp",
  });

  const propsOpacity = interpolate(
    frame,
    [1.5 * fps, 2.5 * fps],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const urlOpacity = interpolate(
    frame,
    [3 * fps, 4 * fps],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(ellipse at center, ${COLORS.bgCard} 0%, ${COLORS.bg} 70%)`,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {/* Logo */}
      <div
        style={{
          opacity: logoOpacity,
          transform: `scale(${logoScale})`,
        }}
      >
        <Img
          src={staticFile("assets/logo.png")}
          style={{ width: 500, height: "auto" }}
        />
      </div>

      {/* Value props row */}
      <div
        style={{
          opacity: propsOpacity,
          display: "flex",
          gap: 48,
          marginTop: 60,
          flexWrap: "wrap",
          justifyContent: "center",
        }}
      >
        {VALUE_PROPS.map((prop) => (
          <div
            key={prop}
            style={{
              background: "rgba(99, 102, 241, 0.1)",
              border: `1px solid ${COLORS.primary}`,
              borderRadius: 12,
              padding: "14px 32px",
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
              {prop}
            </span>
          </div>
        ))}
      </div>

      {/* Install command */}
      <div
        style={{
          opacity: urlOpacity,
          marginTop: 60,
          background: COLORS.bgCard,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 16,
          padding: "20px 48px",
        }}
      >
        <span
          style={{
            fontFamily: FONT.mono,
            fontSize: 44,
            color: COLORS.accentGreen,
          }}
        >
          pip install codeplane
        </span>
      </div>
    </AbsoluteFill>
  );
};
