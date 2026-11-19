import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { fetchPolicySettings, updatePolicyPreset } from "../api/client";
import type { PolicyState } from "../api/client";
import { Label } from "./ui/label";
import { Spinner } from "./ui/spinner";
import { useStore } from "../store";

const PRESETS = [
  { value: "autonomous", label: "Autonomous", description: "Broad autonomy. Protected paths and destructive operations still escalate to you." },
  { value: "supervised", label: "Supervised", description: "Moderate spend ceilings. Network and higher-risk actions escalate for review." },
  { value: "locked", label: "Locked", description: "Tight spend ceilings. Protected paths are denied; most actions need approval." },
];

export function PolicySettingsPanel() {
  const [policy, setPolicy] = useState<PolicyState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setPolicy(await fetchPolicySettings());
    } catch (error) {
      toast.error(String(error));
      setPolicy(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const policySettingsVersion = useStore((state) => state.policySettingsVersion);
  useEffect(() => {
    if (policySettingsVersion > 0) load();
  }, [policySettingsVersion, load]);

  const handlePresetChange = async (preset: string) => {
    setSaving(true);
    try {
      const config = await updatePolicyPreset(preset);
      setPolicy((current) => current ? { ...current, config } : current);
      toast.success(`Preset changed to ${preset}`);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="rounded-lg border border-border bg-card p-5"><Spinner /></div>;
  }
  if (!policy) {
    return (
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="text-sm font-semibold">Action Policy</p>
        <p className="text-xs text-muted-foreground">Policy settings are unavailable.</p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-border bg-card p-5 space-y-5">
      <p className="text-sm font-semibold">Action Policy</p>
      <div className="space-y-2">
        <Label>Preset</Label>
        <div className="grid gap-2 sm:grid-cols-3">
          {PRESETS.map((preset) => (
            <button
              key={preset.value}
              disabled={saving}
              onClick={() => handlePresetChange(preset.value)}
              className={`text-left rounded-md border px-3 py-2 text-xs transition-colors ${
                policy.config.preset === preset.value
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border bg-background text-foreground hover:bg-muted"
              }`}
            >
              <span className="font-medium">{preset.label}</span>
              <p className="text-muted-foreground mt-0.5">{preset.description}</p>
            </button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          The selected preset uses the fixed standalone policy. Explicit human decisions remain bound to one action.
        </p>
      </div>
      <div className="space-y-2">
        <Label>Verified USD thresholds</Label>
        <table className="w-full text-xs text-left">
          <thead><tr><th>Preset</th><th>Review at</th><th>Hard gate at</th></tr></thead>
          <tbody>
            {PRESETS.map((preset) => {
              const ceiling = policy.usdCeilings[preset.value];
              return (
                <tr key={preset.value}>
                  <th scope="row" className="font-normal">{preset.label}</th>
                  <td>{ceiling?.warnUsd == null ? "None" : `$${ceiling.warnUsd}`}</td>
                  <td>{ceiling?.ceilingUsd == null ? "None" : `$${ceiling.ceilingUsd}`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="text-xs text-muted-foreground">
          Native cost units and unavailable currency are not USD. Legacy spend overrides and batch settings
          remain historical data for the preserved transition release, not editable final-runtime policy.
        </p>
      </div>
    </div>
  );
}
