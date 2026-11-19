import { useEffect, useState, useCallback, useRef } from "react";
import { toast } from "sonner";
import { fetchPolicySettings, updatePolicyPreset, updatePolicyConfig, updateUsdCeilings } from "../api/client";
import type { PolicyState, UsdCeiling } from "../api/client";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
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
  const [batchWindow, setBatchWindow] = useState(5);
  const batchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ceilings, setCeilings] = useState<Record<string, { warn: string; ceiling: string }>>({});

  const syncCeilings = useCallback((values: Record<string, UsdCeiling>) => {
    setCeilings(Object.fromEntries(PRESETS.map(({ value }) => [value, {
      warn: values[value]?.warnUsd == null ? "" : String(values[value].warnUsd),
      ceiling: values[value]?.ceilingUsd == null ? "" : String(values[value].ceilingUsd),
    }])));
  }, []);

  const load = useCallback(async () => {
    try {
      const loaded = await fetchPolicySettings();
      setPolicy(loaded);
      setBatchWindow(loaded.config.batchWindowSeconds);
      syncCeilings(loaded.usdCeilings);
    } catch (error) {
      toast.error(String(error));
      setPolicy(null);
    } finally {
      setLoading(false);
    }
  }, [syncCeilings]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { if (batchTimer.current) clearTimeout(batchTimer.current); }, []);
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

  const saveCeilings = async () => {
    const parse = (value: string): number | null => {
      if (value.trim() === "") return null;
      const number = Number(value);
      if (!Number.isFinite(number) || number < 0) throw new Error("USD thresholds must be non-negative numbers.");
      return number;
    };
    setSaving(true);
    try {
      const payload = Object.fromEntries(PRESETS.map(({ value }) => [value, {
        warnUsd: parse(ceilings[value]?.warn ?? ""),
        ceilingUsd: parse(ceilings[value]?.ceiling ?? ""),
      }]));
      const result = await updateUsdCeilings(payload);
      setPolicy((current) => current ? { ...current, usdCeilings: result.ceilings } : current);
      syncCeilings(result.ceilings);
      toast.success("Spend ceilings saved");
    } catch (error) {
      toast.error(String(error));
    } finally {
      setSaving(false);
    }
  };

  const changeBatchWindow = (value: number) => {
    setBatchWindow(value);
    if (batchTimer.current) clearTimeout(batchTimer.current);
    if (!Number.isFinite(value) || value < 0.5 || value > 30) return;
    batchTimer.current = setTimeout(async () => {
      try {
        const config = await updatePolicyConfig({ batchWindowSeconds: value });
        setPolicy((current) => current ? { ...current, config } : current);
      } catch (error) {
        toast.error(String(error));
      }
    }, 500);
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
          Each preset keeps its count and effect limits. Hard gates and incomplete evidence always need individual approval.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="policy-batch-window">Batch Window (seconds)</Label>
        <Input id="policy-batch-window" type="number" step="0.5" min="0.5" max="30" value={batchWindow}
          onChange={(event) => changeBatchWindow(Number(event.target.value))} />
        <p className="text-xs text-muted-foreground">Group concurrent routine actions needing human approval.</p>
      </div>
      <div className="space-y-2">
        <Label>Spend ceilings (verified USD)</Label>
        <table className="w-full text-xs text-left">
          <thead><tr><th>Preset</th><th>Warn at ($)</th><th>Escalate at ($)</th></tr></thead>
          <tbody>
            {PRESETS.map((preset) => {
              const edit = ceilings[preset.value] ?? { warn: "", ceiling: "" };
              return (
                <tr key={preset.value}>
                  <th scope="row" className="font-normal">{preset.label}</th>
                  <td><Input type="number" min="0" step="0.01" placeholder="none"
                    aria-label={`${preset.label} warn USD`} value={edit.warn}
                    onChange={(event) => setCeilings((current) => ({ ...current, [preset.value]: { ...edit, warn: event.target.value } }))} /></td>
                  <td><Input type="number" min="0" step="0.01" placeholder="none"
                    aria-label={`${preset.label} escalate USD`} value={edit.ceiling}
                    onChange={(event) => setCeilings((current) => ({ ...current, [preset.value]: { ...edit, ceiling: event.target.value } }))} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <Button size="sm" variant="outline" disabled={saving} onClick={saveCeilings}>Save ceilings</Button>
        <p className="text-xs text-muted-foreground">
          Native cost units and unavailable currency are not USD. Blank disables a line; zero takes effect immediately.
          Spend escalation is a soft guard, eligible for scoped approval, not a security hard gate.
        </p>
      </div>
    </div>
  );
}
