/**
 * DemoVideo — main composition using TransitionSeries and real captures.
 */
import {
  TransitionSeries,
  linearTiming,
} from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { SCENES, TRANSITION_FRAMES } from "./constants";
import { S01_Opening } from "./scenes/S01_Opening";
import { S02_Problem } from "./scenes/S02_Problem";
import { S03_Dashboard } from "./scenes/S03_Dashboard";
import { S04_LiveExec } from "./scenes/S04_LiveExec";
import { S05_PlanDiff } from "./scenes/S05_PlanDiff";
import { S06_Approval } from "./scenes/S06_Approval";
import { S07_Analytics } from "./scenes/S07_Analytics";
import { S08_Mobile } from "./scenes/S08_Mobile";
import { S09_Closing } from "./scenes/S09_Closing";

const T = TRANSITION_FRAMES;
const timing = linearTiming({ durationInFrames: T });

export const DemoVideo: React.FC = () => {
  return (
    <TransitionSeries>
      {/* Opening title cards use fade transitions */}
      <TransitionSeries.Sequence durationInFrames={SCENES.opening}>
        <S01_Opening />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition presentation={fade()} timing={timing} />

      <TransitionSeries.Sequence durationInFrames={SCENES.problem}>
        <S02_Problem />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition presentation={fade()} timing={timing} />

      {/* Product scenes use slide transitions */}
      <TransitionSeries.Sequence durationInFrames={SCENES.dashboard}>
        <S03_Dashboard />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition
        presentation={slide({ direction: "from-left" })}
        timing={timing}
      />

      <TransitionSeries.Sequence durationInFrames={SCENES.liveExecution}>
        <S04_LiveExec />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition
        presentation={slide({ direction: "from-right" })}
        timing={timing}
      />

      <TransitionSeries.Sequence durationInFrames={SCENES.planDiff}>
        <S05_PlanDiff />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition
        presentation={slide({ direction: "from-left" })}
        timing={timing}
      />

      <TransitionSeries.Sequence durationInFrames={SCENES.approval}>
        <S06_Approval />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition
        presentation={slide({ direction: "from-right" })}
        timing={timing}
      />

      <TransitionSeries.Sequence durationInFrames={SCENES.analytics}>
        <S07_Analytics />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition presentation={fade()} timing={timing} />

      <TransitionSeries.Sequence durationInFrames={SCENES.mobile}>
        <S08_Mobile />
      </TransitionSeries.Sequence>
      <TransitionSeries.Transition presentation={fade()} timing={timing} />

      <TransitionSeries.Sequence durationInFrames={SCENES.closing}>
        <S09_Closing />
      </TransitionSeries.Sequence>
    </TransitionSeries>
  );
};
