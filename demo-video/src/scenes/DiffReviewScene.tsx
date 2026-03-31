import React from "react";
import {
  AbsoluteFill,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  Sequence,
} from "remotion";
import { Gif } from "@remotion/gif";
import { COLORS, FONT } from "../constants";

/**
 * Scene 6: Diff Review — Code review with side-by-side diffs.
 * Asset: job-diff-viewer.gif (real animated UI capture from docs).
 * Shows syntax-highlighted diffs and resolution options.
 */

const RESOLUTIONS = [
  { label: "Merge", color: COLORS.accentGreen, desc: "Cherry-pick onto main" },
  { label: "Create PR", color: COLORS.accent, desc: "Push branch, open PR" },
  { label: "Discard", color: COLORS.danger, desc: "Throw changes away" },
];

export const DiffReviewScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const gifOpacity = interpolate(frame, [0, 0.8 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        background: COLORS.bg,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        gap: 60,
      }}
    >
      {/* GIF of diff viewer */}
      <div
        style={{
          opacity: gifOpacity,
          borderRadius: 24,
          overflow: "hidden",
          border: `2px solid ${COLORS.border}`,
          boxShadow: "0 30px 80px rgba(99, 102, 241, 0.12)",
        }}
      >
        <Gif
          src={staticFile("assets/job-diff-viewer.gif")}
          width={2800}
          fit="contain"
        />
      </div>

      {/* Resolution options */}
      <div
        style={{
          display: "flex",
          gap: 48,
          justifyContent: "center",
        }}
      >
        {RESOLUTIONS.map((res, i) => (
          <Sequence
            key={i}
            from={Math.round((3 + i * 1.5) * fps)}
            premountFor={Math.round(0.5 * fps)}
            layout="none"
          >
            <ResolutionCard
              label={res.label}
              color={res.color}
              desc={res.desc}
            />
          </Sequence>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const ResolutionCard: React.FC<{
  label: string;
  color: string;
  desc: string;
}> = ({ label, color, desc }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const opacity = interpolate(frame, [0, 0.5 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const translateY = interpolate(frame, [0, 0.5 * fps], [30, 0], {
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        opacity,
        transform: `translateY(${translateY}px)`,
        background: COLORS.bgCard,
        border: `2px solid ${color}`,
        borderRadius: 20,
        padding: "28px 48px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 12,
        minWidth: 360,
      }}
    >
      <span
        style={{
          fontFamily: FONT.family,
          fontSize: 48,
          fontWeight: 600,
          color: color,
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: FONT.family,
          fontSize: 32,
          fontWeight: 400,
          color: COLORS.textMuted,
        }}
      >
        {desc}
      </span>
    </div>
  );
};
