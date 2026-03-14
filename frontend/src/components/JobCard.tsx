import { memo } from "react";
import { useNavigate } from "react-router-dom";
import { Card, Group, Text, Stack } from "@mantine/core";
import { GitBranch } from "lucide-react";
import type { JobSummary } from "../store";
import { StateBadge } from "./StateBadge";

function elapsed(createdAt: string): string {
  const ms = Date.now() - new Date(createdAt).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export const JobCard = memo(function JobCard({ job }: { job: JobSummary }) {
  const navigate = useNavigate();
  const repoName = job.repo.split("/").pop() ?? job.repo;

  return (
    <Card
      className="cursor-pointer transition-all hover:border-[var(--mantine-color-blue-7)] hover:shadow-md"
      padding="sm"
      onClick={() => navigate(`/jobs/${job.id}`)}
    >
      <Stack gap={6}>
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" fw={600} c="blue" truncate>
            {job.id}
          </Text>
          <StateBadge state={job.state} />
        </Group>

        <Group gap={4} wrap="nowrap">
          <GitBranch size={12} className="text-[var(--mantine-color-dimmed)] shrink-0" />
          <Text size="xs" c="dimmed" truncate title={job.repo}>
            {repoName}
          </Text>
        </Group>

        <Text size="xs" lineClamp={2} className="leading-snug">
          {job.prompt}
        </Text>

        <Group justify="space-between" mt={2}>
          <Text size="xs" c="dimmed">{elapsed(job.createdAt)}</Text>
          <Text size="xs" c="dimmed">{job.strategy}</Text>
        </Group>
      </Stack>
    </Card>
  );
});
