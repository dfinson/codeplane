import React from "react";
import { AbsoluteFill } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";

import { HookScene } from "./scenes/HookScene";
import { ProblemScene } from "./scenes/ProblemScene";
import { DashboardScene } from "./scenes/DashboardScene";
import { CreateJobScene } from "./scenes/CreateJobScene";
import { LiveMonitoringScene } from "./scenes/LiveMonitoringScene";
import { DiffReviewScene } from "./scenes/DiffReviewScene";
import { AnalyticsScene } from "./scenes/AnalyticsScene";
import { ClosingScene } from "./scenes/ClosingScene";
import { SubtitleOverlay } from "./components/SubtitleOverlay";
import { SCENES, TRANSITION_DURATION } from "./constants";

/**
 * Main composition for the CodePlane demo video.
 *
 * 8 scenes connected with fade transitions.
 * Subtitles are rendered as an overlay on top of all scenes.
 * Total: 90 seconds at 30fps = 2700 frames.
 *
 * Scene durations sum to 2805 frames.
 * 7 transitions of 15 frames each overlap = 105 frames.
 * Effective: 2805 - 105 = 2700 frames. ✓
 */
export const DemoVideo: React.FC = () => {
  const transitionTiming = linearTiming({
    durationInFrames: TRANSITION_DURATION,
  });

  return (
    <AbsoluteFill>
      <TransitionSeries>
        {/* Scene 1: Hook */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.hook.durationInFrames}
        >
          <HookScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={transitionTiming}
        />

        {/* Scene 2: Problem */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.problem.durationInFrames}
        >
          <ProblemScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={transitionTiming}
        />

        {/* Scene 3: Dashboard */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.dashboard.durationInFrames}
        >
          <DashboardScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={transitionTiming}
        />

        {/* Scene 4: Create Job */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.createJob.durationInFrames}
        >
          <CreateJobScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={transitionTiming}
        />

        {/* Scene 5: Live Monitoring */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.liveMonitoring.durationInFrames}
        >
          <LiveMonitoringScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={transitionTiming}
        />

        {/* Scene 6: Diff Review */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.diffReview.durationInFrames}
        >
          <DiffReviewScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={transitionTiming}
        />

        {/* Scene 7: Analytics */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.analytics.durationInFrames}
        >
          <AnalyticsScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={transitionTiming}
        />

        {/* Scene 8: Closing */}
        <TransitionSeries.Sequence
          durationInFrames={SCENES.closing.durationInFrames}
        >
          <ClosingScene />
        </TransitionSeries.Sequence>
      </TransitionSeries>

      {/* Subtitle overlay on top of everything */}
      <SubtitleOverlay />
    </AbsoluteFill>
  );
};
