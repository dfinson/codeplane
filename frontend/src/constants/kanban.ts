export const KANBAN_COLUMNS = {
  IN_PROGRESS: "In Progress",
  AWAITING_INPUT: "Awaiting Input",
  FAILED: "Failed",
} as const;

export type KanbanColumn = (typeof KANBAN_COLUMNS)[keyof typeof KANBAN_COLUMNS];
