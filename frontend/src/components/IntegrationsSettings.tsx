import { useEffect, useState, useCallback } from "react";
import { Trash2, Plus } from "lucide-react";
import { toast } from "sonner";
import {
  fetchCredentials,
  fetchCredentialGuidance,
  createCredential,
  deleteCredential,
  updateJiraCredentialEmail,
} from "../api/client";
import type { Credential, CredentialProvider } from "../api/client";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Spinner } from "./ui/spinner";
import { ConfirmDialog } from "./ui/confirm-dialog";

const PROVIDERS: Array<{ value: CredentialProvider; label: string }> = [
  { value: "github", label: "GitHub Projects" },
  { value: "jira", label: "Jira" },
  { value: "azure_devops", label: "Azure DevOps" },
  { value: "copilot", label: "Copilot inference" },
];

/**
 * Settings > Integrations — global Credential registry (Story 3.1, CAP-6/CAP-7).
 *
 * A Credential (provider + label + base URL + PAT) is registered once here,
 * independent of any Project, and may later be attached to any number of
 * Projects via a TrackerLink (Story 3.2, not implemented by this screen).
 * The PAT is write-only: it is sent once on creation and never returned or
 * rendered by any subsequent read.
 */
export function IntegrationsSettings() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [guidance, setGuidance] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  const [provider, setProvider] = useState<CredentialProvider>("github");
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [email, setEmail] = useState("");
  const [pat, setPat] = useState("");
  const [creating, setCreating] = useState(false);

  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [remediationEmails, setRemediationEmails] = useState<Record<string, string>>({});
  const [remediatingId, setRemediatingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [credsResp, guidanceResp] = await Promise.all([fetchCredentials(), fetchCredentialGuidance()]);
      setCredentials(credsResp.credentials);
      setGuidance(guidanceResp.guidance);
    } catch (e) {
      toast.error(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!label.trim() || (provider !== "copilot" && !baseUrl.trim()) || !pat.trim() || (provider === "jira" && !email.trim())) {
      toast.error("Provider, label, base URL, token, and Jira account email (for Jira) are required.");
      return;
    }
    setCreating(true);
    try {
      await createCredential({
        provider,
        label: label.trim(),
        baseUrl: provider === "copilot" ? "https://github.com" : baseUrl.trim(),
        pat: pat.trim(),
        email: provider === "jira" ? email.trim() : null,
      });
      setLabel("");
      setBaseUrl("");
      setEmail("");
      setPat("");
      toast.success("Credential registered.");
      await load();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteCredential(id);
      toast.success("Credential deleted.");
      await load();
    } catch (e) {
      // Delete is blocked (409) while any TrackerLink still references this Credential.
      toast.error(String(e));
    }
  };

  const handleJiraRemediation = async (credential: Credential) => {
    const remediationEmail = remediationEmails[credential.id]?.trim() ?? "";
    if (!remediationEmail) {
      toast.error("Enter the Jira account email used to create this API token.");
      return;
    }
    setRemediatingId(credential.id);
    try {
      const updated = await updateJiraCredentialEmail(credential.id, remediationEmail);
      setCredentials((current) => current.map((item) => item.id === updated.id ? updated : item));
      setRemediationEmails((current) => {
        const next = { ...current };
        delete next[credential.id];
        return next;
      });
      toast.success("Jira credential updated.");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setRemediatingId(null);
    }
  };

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="text-sm font-semibold mb-4">Integrations</p>
        <div className="flex justify-center py-4">
          <Spinner />
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card p-5 space-y-5">
      <div>
        <p className="text-sm font-semibold">Integrations</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Register tracker credentials or a separate Copilot inference credential.
          Tokens are encrypted at rest and never shown again after saving.
          Copilot selection does not change Git or gh authentication.
        </p>
      </div>

      {/* Registered credentials */}
      <div className="space-y-2">
        {credentials.length === 0 && (
          <p className="text-xs text-muted-foreground">No credentials registered yet.</p>
        )}
        {credentials.map((cred) => (
          <div
            key={cred.id}
            className="rounded-md border border-border px-3 py-2 text-xs"
          >
            <div className="flex items-center justify-between">
              <div>
                <span className="font-medium">{cred.label}</span>
                <span className="text-muted-foreground ml-2 capitalize">
                  {cred.provider.replace("_", " ")}
                </span>
                <p className="text-muted-foreground">{cred.baseUrl}</p>
                {cred.email && <p className="text-muted-foreground">{cred.email}</p>}
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Delete ${cred.label}`}
                onClick={() => setPendingDeleteId(cred.id)}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
            {cred.requiresEmailUpdate && (
              <div role="alert" className="mt-2 space-y-2 rounded border border-amber-500/40 bg-amber-500/5 p-2">
                <p>Action required: add the Jira account email before this credential can authenticate.</p>
                <div className="flex gap-2">
                  <Input
                    type="email"
                    aria-label={`Jira account email for ${cred.label}`}
                    value={remediationEmails[cred.id] ?? ""}
                    onChange={(event) => setRemediationEmails((current) => ({
                      ...current,
                      [cred.id]: event.target.value,
                    }))}
                    placeholder="you@example.com"
                  />
                  <Button
                    size="sm"
                    disabled={remediatingId === cred.id}
                    onClick={() => void handleJiraRemediation(cred)}
                  >
                    {remediatingId === cred.id ? "Updating…" : "Update email"}
                  </Button>
                </div>
                <p className="text-muted-foreground">The existing encrypted API token is retained and never displayed.</p>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Register a new credential */}
      <div className="space-y-2 border-t border-border pt-4">
        <Label>Provider</Label>
        <div className="grid gap-2 sm:grid-cols-3">
          {PROVIDERS.map((p) => (
            <button
              key={p.value}
              onClick={() => setProvider(p.value)}
              className={`text-left rounded-md border px-3 py-2 text-xs transition-colors ${
                provider === p.value
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border bg-background text-foreground hover:bg-muted"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        {guidance[provider] && (
          <p className="text-xs text-muted-foreground">{guidance[provider]}</p>
        )}

        {provider === "copilot" && (
          <p className="text-xs text-muted-foreground">
            This label is not a verified billing identity. Choose this credential on New Job.
            It is retained for that job, its helpers, resumes, reruns, and follow-ups.
          </p>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="credential-label">Label</Label>
          <Input
            id="credential-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Personal GitHub"
          />
        </div>
        {provider !== "copilot" && <div className="space-y-1.5">
          <Label htmlFor="credential-base-url">Base URL</Label>
          <Input
            id="credential-base-url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.github.com"
          />
        </div>}
        {provider === "jira" && (
          <div className="space-y-1.5">
            <Label htmlFor="credential-email">Jira account email</Label>
            <Input
              id="credential-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="credential-pat">Personal Access Token</Label>
          <Input
            id="credential-pat"
            type="password"
            value={pat}
            onChange={(e) => setPat(e.target.value)}
            placeholder="•••••••••"
          />
        </div>
        <Button onClick={handleCreate} disabled={creating} className="mt-1">
          <Plus className="h-4 w-4 mr-1" />
          {creating ? "Registering…" : "Register Credential"}
        </Button>
      </div>

      <ConfirmDialog
        open={pendingDeleteId !== null}
        onClose={() => setPendingDeleteId(null)}
        onConfirm={async () => {
          if (pendingDeleteId) await handleDelete(pendingDeleteId);
        }}
        title="Delete Credential"
        description="Tracker-linked credentials cannot be deleted. Deleting a Copilot credential makes future invocations for bound jobs fail, without switching to another account. Delete this credential?"
        confirmLabel="Delete"
        variant="destructive"
      />
    </div>
  );
}
