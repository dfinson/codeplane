import React from "react";
import {
  AbsoluteFill,
  Img,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  Sequence,
} from "remotion";
import { COLORS, FONT } from "../constants";

/**
 * Scene 4: Create Job — The job creation workflow.
 * Asset: create-job-flow.gif (real animated UI capture from docs).
 * Shows the flow: select repo → write prompt → choose agent/model → set permission mode → launch.
 */

const STEPS = [
  "Select repository",
  "Write prompt",
  "Choose agent & model",
  "Set permission mode",
  "Launch",
];

export const CreateJobScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // GIF fades in
  const gifOpacity = interpolate(frame, [0, 0.8 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        background: COLORS.bg,
        display: "flex",
        flexDirection: "row",
        justifyContent: "center",
        alignItems: "center",
        gap: 120,
        padding: "0 200px",
      }}
    >
      {/* Left: GIF of creation flow */}
      <div
        style={{
          opacity: gifOpacity,
          borderRadius: 24,
          overflow: "hidden",
          border: `2px solid ${COLORS.border}`,
          boxShadow: "0 30px 80px rgba(99, 102, 241, 0.12)",
          flex: "0 0 auto",
        }}
      >
        <Img
          src={staticFile("assets/create-job-flow.gif")}
          style={{ width: 2000, height: "auto" }}
        />
      </div>

      {/* Right: Steps list */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 32,
        }}
      >
        <span
          style={{
            fontFamily: FONT.family,
            fontSize: 52,
            fontWeight: 600,
            color: COLORS.primary,
            textTransform: "uppercase",
            letterSpacing: "0.15em",
            marginBottom: 20,
          }}
        >
          Create a Job
        </span>

        {STEPS.map((step, i) => (
          <Sequence
            key={i}
            from={Math.round((1.5 + i * 1.5) * fps)}
            premountFor={Math.round(0.5 * fps)}
          >
            <StepItem index={i + 1} text={step} />
          </Sequence>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const StepItem: React.FC<{ index: number; text: string }> = ({
  index,
  text,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const opacity = interpolate(frame, [0, 0.5 * fps], [0, 1], {
    extrapolateRight: "clamp",
  });
  const translateX = interpolate(frame, [0, 0.5 * fps], [40, 0], {
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        opacity,
        transform: `translateX(${translateX}px)`,
        display: "flex",
        alignItems: "center",
        gap: 28,
      }}
    >
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 28,
          background: COLORS.primary,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <span
          style={{
            fontFamily: FONT.family,
            fontSize: 32,
            fontWeight: 700,
            color: "#fff",
          }}
        >
          {index}
        </span>
      </div>
      <span
        style={{
          fontFamily: FONT.family,
          fontSize: 52,
          fontWeight: 400,
          color: COLORS.text,
        }}
      >
        {text}
      </span>
    </div>
  );
};
