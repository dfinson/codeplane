"""Stateless AGT rule selection over locally constructed, evidence-bound facts."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Literal

import structlog

from backend.services.action_policy.classifier import Preset

if TYPE_CHECKING:
    from agentmesh.governance.policy import PolicyEngine

    from backend.models.action_assessment import ActionAssessment
    from backend.services.action_policy.syntax import SyntaxFacts

log = structlog.get_logger()
POLICY_VERSION = "cp-policy-v1"


@dataclass(frozen=True, slots=True)
class PolicyInput:
    job_id: str
    preset: Preset
    assessment: ActionAssessment | None = None
    syntax: SyntaxFacts | None = None
    terminal: bool = False
    paused: bool = False
    disabled: bool = False
    stale: bool = False
    hard_gate: bool = False
    protected_write: bool = False
    mcp_instruction: bool = False
    mcp_drift: bool = False
    budget_pressure: bool = False
    accounting_unresolved: bool = False
    trusted_file_read: bool = False
    local_effect: Literal["read_only", "mutating", "destructive", "unknown"] | None = None
    local_network: Literal["none", "outbound", "unknown"] | None = None
    local_scope: Literal["workspace", "external", "mixed", "unknown"] | None = None
    local_reversibility: Literal["not_applicable", "git", "external", "irreversible", "unknown"] | None = None


@dataclass(frozen=True, slots=True)
class FactDecision:
    action: Literal["allow", "warn", "escalate", "deny", "reject"]
    reason_code: str
    individual_human: bool
    monitor_eligible: bool = False
    policy_version: str = POLICY_VERSION
    risk_score: int | None = None
    risk_band: str = "unknown"


_RULES = (
    ("execution_ineligible", "deny", 1000),
    ("hard_gate", "deny", 900),
    ("incomplete_evidence", "require_approval", 800),
    ("budget_pressure", "require_approval", 750),
    ("external_mutation", "require_approval", 700),
    ("git_mutation_autonomous", "allow", 630),
    ("git_mutation_supervised", "warn", 620),
    ("git_mutation_locked", "require_approval", 610),
    ("readonly_shell_locked", "warn", 510),
    ("known_read", "allow", 500),
    ("unknown_combination", "require_approval", 0),
)
_HARD_CAPABILITIES = frozenset(
    {
        "credential_exposure",
        "raw_device_write",
        "filesystem_format",
        "persistence_write",
        "fork_bomb",
        "network_exec",
        "privilege_change",
    }
)


def _context(request: PolicyInput) -> dict[str, bool]:
    facts = request.assessment
    effect = facts.effect if facts is not None else "read_only" if request.trusted_file_read else "unknown"
    network = facts.network if facts is not None else "none" if request.trusted_file_read else "unknown"
    scope = facts.scope if facts is not None else "workspace" if request.trusted_file_read else "unknown"
    reversibility = facts.reversibility if facts is not None else "not_applicable"
    if request.local_effect == "destructive" or (
        request.local_effect == "mutating" and effect in {"read_only", "unknown"}
    ):
        effect = request.local_effect
    if request.local_network == "outbound":
        network = request.local_network
    if request.local_scope == "mixed" or (request.local_scope == "external" and scope in {"workspace", "unknown"}):
        scope = request.local_scope
    if request.local_reversibility == "irreversible" or (
        request.local_reversibility == "external" and reversibility in {"not_applicable", "git", "unknown"}
    ):
        reversibility = request.local_reversibility
    incomplete = (
        (facts is None and not request.trusted_file_read)
        or (facts is not None and not facts.complete)
        or (facts is not None and facts.mechanism == "shell" and request.syntax is None)
        or (request.syntax is not None and not request.syntax.complete)
        or "unknown"
        in {
            request.local_effect,
            request.local_network,
            request.local_scope,
            request.local_reversibility,
        }
        or "unknown" in {effect, network, scope, reversibility}
        or (effect in {"mutating", "destructive"} and reversibility == "not_applicable")
        or request.mcp_instruction
        or request.mcp_drift
        or request.accounting_unresolved
    )
    hard = (
        request.hard_gate
        or request.protected_write
        or effect == "destructive"
        or (facts is not None and bool(_HARD_CAPABILITIES.intersection(facts.capabilities)))
    )
    mutation = effect == "mutating"
    git_mutation = mutation and scope == "workspace" and network == "none" and reversibility == "git"
    readonly_shell = effect == "read_only" and facts is not None and facts.mechanism == "shell"
    return {
        "execution_ineligible": request.terminal or request.paused or request.disabled or request.stale,
        "hard_gate": hard,
        "incomplete_evidence": incomplete,
        "budget_pressure": request.budget_pressure,
        "external_mutation": mutation and (network == "outbound" or scope != "workspace" or reversibility != "git"),
        "git_mutation_autonomous": git_mutation and request.preset == Preset.autonomous,
        "git_mutation_supervised": git_mutation and request.preset == Preset.supervised,
        "git_mutation_locked": git_mutation and request.preset == Preset.locked,
        "readonly_shell_locked": readonly_shell and request.preset == Preset.locked,
        "known_read": effect == "read_only",
        "unknown_combination": True,
    }


class AgtPolicy:
    def __init__(self) -> None:
        self._engine: PolicyEngine | None = None
        self.unavailable_reason: str | None = None
        try:
            if version("agent-governance-toolkit-core") != "5.0.0":
                raise ValueError("The selected governance runtime version is not installed")
            from agentmesh.governance.policy import Policy, PolicyEngine

            engine = PolicyEngine(conflict_strategy="priority_first_match")
            engine.load_policy(
                Policy.model_validate(
                    {
                        "name": POLICY_VERSION,
                        "agents": ["*"],
                        "default_action": "deny",
                        "rules": [
                            {
                                "name": name,
                                "condition": f"cp.{name}",
                                "action": action,
                                "priority": priority,
                                "stage": "pre_tool",
                            }
                            for name, action, priority in _RULES
                        ],
                    }
                )
            )
            self._engine = engine
        except (ImportError, PackageNotFoundError, ValueError) as exc:
            self.unavailable_reason = type(exc).__name__
            log.error("governance_policy_unavailable", reason=self.unavailable_reason)

    def decide(self, request: PolicyInput) -> FactDecision:
        flags = _context(request)
        if flags["execution_ineligible"]:
            return FactDecision("reject", "execution_ineligible", False)
        if self._engine is None or self._engine.get_policy(POLICY_VERSION) is None:
            return FactDecision("escalate", "policy_unavailable", True)
        result = self._engine.evaluate(request.job_id, {"cp": flags}, stage="pre_tool")
        rule = result.matched_rule
        if rule is None or rule not in {name for name, _, _ in _RULES}:
            return FactDecision("escalate", "unrecognized_policy_result", True)
        if result.action not in {"allow", "warn", "require_approval", "deny"}:
            return FactDecision("escalate", "incomplete_policy_result", True)
        actions: dict[str, Literal["allow", "warn", "escalate", "deny", "reject"]] = {
            "require_approval": "escalate",
            "allow": "allow",
            "warn": "warn",
            "deny": "deny",
        }
        action = actions[result.action]
        individual = rule in {"hard_gate", "incomplete_evidence", "unknown_combination"}
        return FactDecision(
            action,
            rule,
            individual,
            monitor_eligible=action == "escalate" and not individual and request.preset != Preset.locked,
        )
