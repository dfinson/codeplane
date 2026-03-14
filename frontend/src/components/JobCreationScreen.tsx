import { useEffect, useState, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Paper, Title, Select, Textarea, TextInput, Button, Group, Stack, Text, Divider,
  Collapse, UnstyledButton,
} from "@mantine/core";
import { ChevronDown, ChevronRight, Rocket, Plus } from "lucide-react";
import { notifications } from "@mantine/notifications";
import { createJob, fetchRepos, fetchModels } from "../api/client";
import { VoiceRecorder } from "./VoiceButton";
import { AddRepoModal } from "./AddRepoModal";

export function JobCreationScreen() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<{ value: string; label: string }[]>([]);
  const [repo, setRepo] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [baseRef, setBaseRef] = useState("");
  const [branch, setBranch] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [models, setModels] = useState<{ value: string; label: string }[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [addRepoOpen, setAddRepoOpen] = useState(false);

  const voiceSection = useMemo(
    () => <VoiceRecorder onTranscript={(t) => setPrompt((p) => (p ? p + " " : "") + t)} />,
    []
  );

  useEffect(() => {
    fetchRepos()
      .then((r) => {
        const items = r.items.map((p) => ({
          value: p,
          label: p.split("/").pop() ?? p,
        }));
        setRepos(items);
        setRepo((prev) => prev ?? items[0]?.value ?? null);
      })
      .catch(() => notifications.show({ color: "red", message: "Failed to load repos" }));
    fetchModels()
      .then((m) => {
        setModels(
          m.map((x) => ({
            value: String(x.id ?? x.name ?? ""),
            label: String(x.name ?? x.id ?? "unknown"),
          })).filter((x) => x.value)
        );
      })
      .catch(() => {});
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!repo || !prompt.trim()) return;
    setSubmitting(true);
    try {
      const result = await createJob({
        repo,
        prompt: prompt.trim(),
        base_ref: baseRef || undefined,
        branch: branch || undefined,
      });
      notifications.show({ color: "green", message: `Job ${result.id} created` });
      navigate(`/jobs/${result.id}`);
    } catch (e) {
      notifications.show({ color: "red", title: "Failed", message: String(e) });
    } finally {
      setSubmitting(false);
    }
  }, [repo, prompt, baseRef, branch, navigate]);

  return (
    <div className="max-w-xl mx-auto">
      <Title order={3} mb="lg">New Job</Title>

      <Paper radius="lg" p="lg">
        <Stack gap="md">
          <Group gap="xs" align="flex-end">
            <Select
              label="Repository"
              placeholder="Select a repository…"
              data={repos}
              value={repo}
              onChange={setRepo}
              searchable
              size="sm"
              className="flex-1"
            />
            <Button
              size="sm"
              variant="light"
              leftSection={<Plus size={14} />}
              onClick={() => setAddRepoOpen(true)}
              className="mb-px"
            >
              Add
            </Button>
          </Group>

          <AddRepoModal
            opened={addRepoOpen}
            onClose={() => setAddRepoOpen(false)}
            onAdded={(path) => {
              const label = path.split("/").pop() ?? path;
              setRepos((prev) => {
                if (prev.some((r) => r.value === path)) return prev;
                return [...prev, { value: path, label }];
              });
              setRepo(path);
            }}
          />

          <Textarea
            label="Prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.currentTarget.value)}
            placeholder="Describe the task for the agent…"
            minRows={4}
            autosize
            maxRows={12}
            size="sm"
            rightSection={voiceSection}
            rightSectionWidth={140}
          />

          <Divider />

          <UnstyledButton onClick={() => setShowAdvanced(!showAdvanced)}>
            <Group gap={4}>
              {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <Text size="xs" c="dimmed">Advanced options</Text>
            </Group>
          </UnstyledButton>

          <Collapse in={showAdvanced}>
            <Stack gap="sm">
              {models.length > 0 && (
                <Select
                  label="Model"
                  placeholder="Default (auto)"
                  data={models}
                  value={model}
                  onChange={setModel}
                  clearable
                  searchable
                  size="sm"
                />
              )}
              <TextInput
                label="Base Reference"
                placeholder="e.g., main"
                value={baseRef}
                onChange={(e) => setBaseRef(e.currentTarget.value)}
                size="sm"
              />
              <TextInput
                label="Branch Name"
                placeholder="Auto-generated if empty"
                value={branch}
                onChange={(e) => setBranch(e.currentTarget.value)}
                size="sm"
              />
            </Stack>
          </Collapse>

          <Group justify="flex-end" mt="sm">
            <Button variant="subtle" onClick={() => navigate("/")}>
              Cancel
            </Button>
            <Button
              leftSection={<Rocket size={16} />}
              disabled={!repo || !prompt.trim()}
              loading={submitting}
              onClick={handleSubmit}
            >
              Create Job
            </Button>
          </Group>
        </Stack>
      </Paper>
    </div>
  );
}
