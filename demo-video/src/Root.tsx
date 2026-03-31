import { Composition } from "remotion";
import { DemoVideo } from "./DemoVideo";
import { TOTAL_DURATION_FRAMES, FPS, WIDTH, HEIGHT } from "./constants";

/**
 * Root component registering all Remotion compositions.
 */
export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="CodePlaneDemo"
        component={DemoVideo}
        durationInFrames={TOTAL_DURATION_FRAMES}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
      />
    </>
  );
};
