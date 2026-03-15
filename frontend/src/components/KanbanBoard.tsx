import { useShallow } from "zustand/react/shallow";
import { useTowerStore, selectSignoffJobs, selectActiveJobs, selectAttentionJobs } from "../store";
import { KanbanColumn } from "./KanbanColumn";

export function KanbanBoard() {
  const activeJobs = useTowerStore(useShallow(selectActiveJobs));
  const signoffJobs = useTowerStore(useShallow(selectSignoffJobs));
  const attentionJobs = useTowerStore(useShallow(selectAttentionJobs));

  return (
    <div className="grid grid-cols-3 gap-3 h-[calc(100vh-140px)] max-lg:grid-cols-2 max-sm:hidden">
      <KanbanColumn title="Active" jobs={activeJobs} />
      <KanbanColumn title="Sign-off" jobs={signoffJobs} />
      <KanbanColumn title="Attention" jobs={attentionJobs} />
    </div>
  );
}
