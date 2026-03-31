import React from "react";
import { useCurrentFrame, useVideoConfig, interpolate, Easing } from "remotion";
import { SUBTITLES } from "../subtitles";
import { COLORS, FONT } from "../constants";

/**
 * Displays timed subtitles at the bottom of the frame.
 * Each subtitle fades in and out based on the global timeline.
 */
export const SubtitleOverlay: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTimeMs = (frame / fps) * 1000;

  const activeSub = SUBTITLES.find(
    (s) => currentTimeMs >= s.startMs && currentTimeMs <= s.endMs
  );

  if (!activeSub) return null;

  const progress = (currentTimeMs - activeSub.startMs) / (activeSub.endMs - activeSub.startMs);

  const opacity = interpolate(
    progress,
    [0, 0.08, 0.85, 1],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const translateY = interpolate(
    progress,
    [0, 0.08, 0.85, 1],
    [20, 0, 0, -10],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <div
      style={{
        position: "absolute",
        bottom: 120,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        zIndex: 100,
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      <div
        style={{
          background: "rgba(0, 0, 0, 0.75)",
          backdropFilter: "blur(12px)",
          borderRadius: 16,
          padding: "24px 56px",
          maxWidth: 2400,
        }}
      >
        <span
          style={{
            color: COLORS.text,
            fontFamily: FONT.family,
            fontSize: 64,
            fontWeight: 500,
            letterSpacing: "-0.01em",
            lineHeight: 1.3,
            textAlign: "center",
          }}
        >
          {activeSub.text}
        </span>
      </div>
    </div>
  );
};
