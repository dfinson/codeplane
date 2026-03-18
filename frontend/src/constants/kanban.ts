export const KANBAN_COLUMNS = {
  IN_PROGRESS: "In Progress",
  NEEDS_REVIEW: "Needs Review",
  NEEDS_ATTENTION: "Needs Attention",
} as const;

export type KanbanColumn = (typeof KANBAN_COLUMNS)[keyof typeof KANBAN_COLUMNS];
