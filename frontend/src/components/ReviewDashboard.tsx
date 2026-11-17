/**
 * ReviewDashboard — orchestrator for the Review tab.
 *
 * Sub-views:
 * - Changes: code diff viewer (was a top-level tab)
 * - Story: trail-based narrative
 */
import { Suspense, useEffect, useState } from "react";
import { Spinner } from "./ui/spinner";
import { ReviewSubTabs, type ReviewSubView } from "./review/ReviewSubTabs";
import { StorySubView } from "./review/StorySubView";
import { lazyRetry } from "../lib/lazyRetry";
import type { StepFilter } from "./DiffViewer";
import { RecipeReviewPanel } from "./RecipeReviewPanel";

const DiffViewer = lazyRetry(() => import("./DiffViewer"));

// -- Main Component --

interface ReviewDashboardProps {
  jobId: string;
  hasChanges?: boolean;
  jobState?: string;
  resolution?: string | null;
  archivedAt?: string | null;
  onAskSent?: () => void;
  stepFilter?: StepFilter | null;
  onClearStepFilter?: () => void;
  onNavigateToStep?: (seq: number, turnId?: string) => void;
  /** Externally-requested sub-view (e.g. navigating to changes from activity feed) */
  requestedSubView?: ReviewSubView | null;
  onSubViewHandled?: () => void;
}

export function ReviewDashboard({
  jobId,
  hasChanges,
  jobState,
  resolution,
  archivedAt,
  onAskSent,
  stepFilter,
  onClearStepFilter,
  onNavigateToStep,
  requestedSubView,
  onSubViewHandled,
}: ReviewDashboardProps) {
  // Sub-view state — default to "changes" when diffs exist, else story
  const [subView, setSubView] = useState<ReviewSubView>(
    hasChanges ? "changes" : "story"
  );

  // Respond to externally-requested sub-view (e.g. "View step changes" from activity feed)
  useEffect(() => {
    if (requestedSubView) {
      setSubView(requestedSubView);
      onSubViewHandled?.();
    }
  }, [requestedSubView, onSubViewHandled]);

  return (
    <div className="flex flex-col h-full">
      {/* Sub-view tabs */}
      <ReviewSubTabs
        active={subView}
        onChange={setSubView}
        showChanges={hasChanges}
      />

      {/* Sub-view content */}
      <div className="flex-1 min-h-0 overflow-auto lg:grid lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-4">
        <div className="min-w-0 min-h-[24rem] lg:overflow-hidden">
        {subView === "changes" && hasChanges && (
          <Suspense fallback={<div className="flex justify-center py-10"><Spinner /></div>}>
            <DiffViewer
              jobId={jobId}
              jobState={jobState}
              resolution={resolution}
              archivedAt={archivedAt}
              onAskSent={onAskSent}
              stepFilter={stepFilter}
              onClearStepFilter={onClearStepFilter}
              onNavigateToStep={onNavigateToStep}
            />
          </Suspense>
        )}
        {subView === "story" && (
          <StorySubView jobId={jobId} />
        )}
        </div>
        <RecipeReviewPanel jobId={jobId} />
      </div>
    </div>
  );
}

export default ReviewDashboard;
