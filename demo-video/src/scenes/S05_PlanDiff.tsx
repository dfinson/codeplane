/**
 * S05 — Plan + Diff: show the diff viewer (Changes tab).
 * Cross-fades from live job to diff view.
 */
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";
import { C, SCENES } from "../constants";

export const S05_PlanDiff: React.FC = () => {
  const frame = useCurrentFrame();
  const dur = SCENES.planDiff;

  // Diff screenshot fades in and slowly zooms
  const imgOpacity = interpolate(frame, [0, 25], [0, 1], { extrapolateRight: "clamp" });
  const imgScale = interpolate(frame, [0, dur], [1.08, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: C.bg }}>
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          overflow: "hidden",
          opacity: imgOpacity,
        }}
      >
        <Img
          src={staticFile("captures/job-diff.png")}
          style={{
            width: "94%",
            borderRadius: 20,
            boxShadow: "0 30px 100px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)",
            transform: `scale(${imgScale})`,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
