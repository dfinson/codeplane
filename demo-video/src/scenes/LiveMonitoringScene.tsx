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
 * Scene 5: Live Monitoring — Real-time transcript streaming.
 * Asset: transcript-streaming.gif (real animated UI capture from docs).
 * Shows live reasoning, tool calls, and approval gates.
 */

const CAPABILITIES = [
  { icon: "💭", text: "Agent reasoning" },
  { icon: "🔧", text: "Tool calls" },
  { icon: "📋", text: "Plan progress" },
  { icon: "🛑", text: "Approval gates" },
  { icon: "💬", text: "Operator messages" },
];

export const LiveMonitoringScene: React.FC = () => {
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
      {/* Section heading */}
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
          Live Monitoring
        </span>
      </div>

      {/* GIF screenshot */}
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
          src={staticFile("assets/transcript-streaming.gif")}
          width={2800}
          fit="contain"
        />
      </div>

      {/* Capability badges */}
      <div
        style={{
          display: "flex",
          gap: 32,
          flexWrap: "wrap",
          justifyContent: "center",
        }}
      >
        {CAPABILITIES.map((cap, i) => (
          <Sequence
            key={i}
            from={Math.round((2 + i * 1.2) * fps)}
            premountFor={Math.round(0.5 * fps)}
            layout="none"
          >
            <CapBadge icon={cap.icon} text={cap.text} />
          </Sequence>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const CapBadge: React.FC<{ icon: string; text: string }> = ({ icon, text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const opacity = interpolate(frame, [0, 0.4 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const scale = interpolate(frame, [0, 0.4 * fps], [0.8, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        opacity,
        transform: `scale(${scale})`,
        background: COLORS.bgCard,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 16,
        padding: "16px 32px",
        display: "flex",
        alignItems: "center",
        gap: 16,
      }}
    >
      <span style={{ fontSize: 44 }}>{icon}</span>
      <span
        style={{
          fontFamily: FONT.family,
          fontSize: 38,
          fontWeight: 500,
          color: COLORS.text,
        }}
      >
        {text}
      </span>
    </div>
  );
};
