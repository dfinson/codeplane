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
 * Scene 1: Hook — Logo reveal + tagline.
 * Shows the CodePlane logo and a concise product descriptor.
 */
export const HookScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Logo fade-in + scale
  const logoOpacity = interpolate(frame, [0, 1 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const logoScale = interpolate(frame, [0, 1 * fps], [0.85, 1], {
    extrapolateRight: "clamp",
  });

  // Tagline appears after logo
  const taglineOpacity = interpolate(
    frame,
    [1.5 * fps, 2.5 * fps],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const taglineY = interpolate(
    frame,
    [1.5 * fps, 2.5 * fps],
    [40, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  // Subtle terminal command
  const cmdOpacity = interpolate(
    frame,
    [3.5 * fps, 4.5 * fps],
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
          style={{ width: 600, height: "auto" }}
        />
      </div>

      {/* Tagline */}
      <div
        style={{
          opacity: taglineOpacity,
          transform: `translateY(${taglineY}px)`,
          marginTop: 60,
        }}
      >
        <span
          style={{
            fontFamily: FONT.family,
            fontSize: 80,
            fontWeight: 300,
            color: COLORS.textMuted,
            letterSpacing: "-0.02em",
          }}
        >
          Control plane for coding agents
        </span>
      </div>

      {/* Terminal hint */}
      <div
        style={{
          opacity: cmdOpacity,
          marginTop: 80,
          background: COLORS.bgCard,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 16,
          padding: "20px 48px",
        }}
      >
        <span
          style={{
            fontFamily: FONT.mono,
            fontSize: 48,
            color: COLORS.accentGreen,
          }}
        >
          $ cpl up
        </span>
      </div>
    </AbsoluteFill>
  );
};
