import type { Job } from "../api/types";

export function CopilotAccountInfo({ job }: { job: Job }) {
  if (job.sdk !== "copilot" || job.source !== "managed") return null;
  return (
    <p className="text-xs text-muted-foreground" title="Credential label, not a verified billing identity. Git and gh authentication are unchanged.">
      Copilot inference: {job.copilotCredentialId
        ? `${job.copilotCredentialLabel || "Selected credential"} (${job.copilotCredentialId})`
        : "Ambient Copilot login"}
    </p>
  );
}
