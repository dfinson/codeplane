import { useShallow } from "zustand/react/shallow";
import { useStore, selectSignoffJobs, selectActiveJobs, selectAttentionJobs } from "../store";
import { KanbanColumn } from "./KanbanColumn";
import { KANBAN_COLUMNS } from "../constants/kanban";

export function KanbanBoard() {
  const activeJobs = useStore(useShallow(selectActiveJobs));
  const signoffJobs = useStore(useShallow(selectSignoffJobs));
  const attentionJobs = useStore(useShallow(selectAttentionJobs));

  return (
    <div className="grid grid-cols-3 gap-3 h-[calc(100vh-140px)] max-lg:grid-cols-2 max-sm:hidden">
      <KanbanColumn title={KANBAN_COLUMNS.IN_PROGRESS} jobs={activeJobs} />
      <KanbanColumn title={KANBAN_COLUMNS.NEEDS_REVIEW} jobs={signoffJobs} />
      <KanbanColumn title={KANBAN_COLUMNS.NEEDS_ATTENTION} jobs={attentionJobs} />
    </div>
  );
}
