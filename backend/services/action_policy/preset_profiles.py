"""Per-preset TraceForge governance profiles (SPEC §18.3).

Each CodePlane preset (autonomous / supervised / locked) selects a distinct
``GovernanceConfig``-shaped profile — a recommendation-rules file, a count-based
budget, the default-off policy primitives (protected paths + cost-pressure
ceiling), and CodePlane's own per-preset **USD** spend ceiling. These replace the
retired ``resolve_tier`` preset branches: the preset now *selects a governance
profile* rather than mapping ``(reversible, contained)`` to a tier. See
``governance.py`` for how the profiles are compiled into pipelines.

Two coexisting budget dimensions:

* TraceForge's native **count/effect** ``BudgetConfig`` (max tool calls, max
  destructive ops) — new capability we never had.
* CodePlane's **USD** ceiling (``ceiling_usd`` / ``warn_usd``), enforced natively
  via :class:`~backend.services.action_policy.cost_ceiling.JobSpendCeilingAssessor`
  (a TraceForge ``PolicyAssessor``). The dollar ceiling is preserved from the
  retired ``cost_rules`` — only the CodePlane ``Tier``-promotion logic on top of
  the spend read was dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from traceforge.config.models import (
    BudgetConfig,
    CostCeilingPolicyConfig,
    PolicyConfig,
    ProtectedPathsPolicyConfig,
)

from backend.services.action_policy.classifier import USD_CEILING_DEFAULTS, Preset

_RULES_DIR = Path(__file__).parent / "data" / "governance"

# Secret / credential path shapes that must never be written or exfiltrated
# without a human in the loop, regardless of preset (SPEC §18.2). Enforced via
# TraceForge's ProtectedPathsPolicyConfig rather than a CodePlane path table.
_SECRET_PATH_PATTERNS = (
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa",
    "**/id_ed25519",
    "**/.ssh/**",
    "**/.aws/**",
    "**/.git/config",
    "**/secrets/**",
    "**/*.pfx",
)


@dataclass(frozen=True)
class PresetProfile:
    """A compiled governance profile for one preset."""

    preset: Preset
    rules_path: Path
    budget: BudgetConfig
    policy: PolicyConfig
    # CodePlane USD spend ceiling for this preset (see cost_ceiling.py). ``None``
    # disables that threshold. These are CP-authored product config (not part of
    # TraceForge's count-based budget, which coexists) and may be overridden per
    # preset at runtime from ``policy_config`` (usd_ceilings_json).
    ceiling_usd: float | None = None
    warn_usd: float | None = None

    def with_usd_ceilings(self, *, ceiling_usd: float | None, warn_usd: float | None) -> PresetProfile:
        """Return a copy with the USD ceiling/warn overridden (runtime config)."""
        return replace(self, ceiling_usd=ceiling_usd, warn_usd=warn_usd)


def _profile(
    preset: Preset,
    *,
    max_tool_calls: int | None,
    max_destructive: int | None,
    protected_action: str,
    pressure_action: str | None,
    hard_max_tool_calls: int | None,
) -> PresetProfile:
    max_by_effect = {"destructive": max_destructive} if max_destructive is not None else None
    warn_usd, ceiling_usd = USD_CEILING_DEFAULTS[preset]
    return PresetProfile(
        preset=preset,
        rules_path=_RULES_DIR / f"{preset.value}.yaml",
        budget=BudgetConfig(max_tool_calls=max_tool_calls, max_by_effect=max_by_effect),
        policy=PolicyConfig(
            protected_paths=ProtectedPathsPolicyConfig(
                patterns=list(_SECRET_PATH_PATTERNS),
                action=protected_action,
            ),
            cost_ceiling=CostCeilingPolicyConfig(
                pressure_action=pressure_action,
                hard_max_tool_calls=hard_max_tool_calls,
                hard_action="escalate",
            ),
        ),
        ceiling_usd=ceiling_usd,
        warn_usd=warn_usd,
    )


# Autonomous: generous budget, protected paths escalate, no hard ceiling, no USD
#   ceiling (looser — long autonomous runs are expected to spend).
# Supervised: moderate budget, pressure escalates, moderate USD ceiling.
# Locked: tight budget, protected paths deny, hard call ceiling escalates, tight
#   USD ceiling.
PROFILES: dict[Preset, PresetProfile] = {
    Preset.autonomous: _profile(
        Preset.autonomous,
        max_tool_calls=None,
        max_destructive=25,
        protected_action="escalate",
        pressure_action=None,
        hard_max_tool_calls=None,
    ),
    Preset.supervised: _profile(
        Preset.supervised,
        max_tool_calls=750,
        max_destructive=10,
        protected_action="escalate",
        pressure_action="escalate",
        hard_max_tool_calls=None,
    ),
    Preset.locked: _profile(
        Preset.locked,
        max_tool_calls=250,
        max_destructive=3,
        protected_action="deny",
        pressure_action="escalate",
        hard_max_tool_calls=400,
    ),
}


def profile_for(preset: Preset) -> PresetProfile:
    """Return the governance profile for ``preset`` (defaults to supervised)."""
    return PROFILES.get(preset, PROFILES[Preset.supervised])
