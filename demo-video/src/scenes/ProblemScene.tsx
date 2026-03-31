import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  Sequence,
} from "remotion";
import { COLORS, FONT } from "../constants";

/**
 * Scene 2: Problem — Why coding agents need supervision.
 * Animated list of pain points, each appearing sequentially.
 */

const PROBLEMS = [
  { icon: "👁", text: "No visibility into agent reasoning" },
  { icon: "🚧", text: "No approval gates for risky operations" },
  { icon: "🔇", text: "No way to intervene mid-run" },
  { icon: "💸", text: "Invisible token burn" },
];

const ProblemItem: React.FC<{ icon: string; text: string }> = ({
  icon,
  text,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const opacity = interpolate(frame, [0, 0.6 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const translateX = interpolate(frame, [0, 0.6 * fps], [-60, 0], {
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        opacity,
        transform: `translateX(${translateX}px)`,
        display: "flex",
        alignItems: "center",
        gap: 40,
        marginBottom: 48,
      }}
    >
      <span style={{ fontSize: 72 }}>{icon}</span>
      <span
        style={{
          fontFamily: FONT.family,
          fontSize: 64,
          fontWeight: 400,
          color: COLORS.text,
        }}
      >
        {text}
      </span>
    </div>
  );
};

export const ProblemScene: React.FC = () => {
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill
      style={{
        background: COLORS.bg,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "flex-start",
        paddingLeft: 400,
      }}
    >
      {/* Section heading */}
      <div style={{ marginBottom: 80 }}>
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
          The problem
        </span>
      </div>

      {/* Problem items staggered */}
      {PROBLEMS.map((p, i) => (
        <Sequence
          key={i}
          from={Math.round((1 + i * 2) * fps)}
          premountFor={Math.round(0.5 * fps)}
          layout="none"
        >
          <ProblemItem icon={p.icon} text={p.text} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
