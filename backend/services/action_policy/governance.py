"""GovernanceDecider — CodePlane's façade over ``traceforge.governance``.

This is the single seam between CodePlane's enforcement layer and TraceForge's
governance substrate. It owns:

* one durable :class:`SystemStore` (``~/.codeplane/governance.db``, TraceForge's
  own alembic-on-construct — isolated from CodePlane's alembic chain);
* three :class:`GovernancePipeline` instances, one per preset profile
  (autonomous / supervised / locked), all sharing that one store + the
  CodePlane-overlaid classification engine;
* a job → preset registry so the read (decision) and write (accrual) paths pick
  the right pipeline for a session.

Two touchpoints:

* :meth:`decide` — the **read-only** decision path (permission request). Builds a
  TraceForge event from the CodePlane ``Action``, runs ``enrich_event`` +
  ``preflight_event`` (Phase 1/2/3 on a *detached* state clone — persists
  nothing), and returns a ``SessionMeta``. Any internal error fails **closed**
  to ESCALATE.
* :meth:`observe` — the **accrual** path (executed tool call, off the event bus).
  Runs ``process_event`` (the single writer) so durable budget/taint/session
  state advances. Only executed calls accrue; rejected calls never reach here.

Both paths classify the event through the same overlaid engine, so the decision
and the accrual agree on effect/scope/capability.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from traceforge.classify.config import ClassificationEngine
    from traceforge.classify.core import Classification as TFClassification
    from traceforge.governance.rules import PolicyAssessor

import structlog
from traceforge.classify.risk import RiskAssessment
from traceforge.governance import (
    BudgetThresholds,
    BudgetTracker,
    GovernanceLabeler,
    GovernancePipeline,
    RecommendedAction,
    RiskRecommendation,
    SessionMeta,
    SystemStore,
    ToolCallEvent,
    parse_rules,
)
from traceforge.governance.pipeline import _build_policy_assessors

from backend.services.action_policy.classifier import (
    SECURITY_CRITICAL_REASON_CODES as SECURITY_CRITICAL_REASON_CODES,
)
from backend.services.action_policy.classifier import Action, ActionKind, Classification, Preset
from backend.services.action_policy.classifier import RecommendedAction as ActionRecommendation
from backend.services.action_policy.classifier import is_security_critical as is_security_critical
from backend.services.action_policy.cost_ceiling import JobSpendCeilingAssessor
from backend.services.action_policy.preset_profiles import PROFILES, PresetProfile, profile_for
from backend.services.action_policy.tf_classify_adapter import tf_engine

log = structlog.get_logger()

# Default durable governance store — a SEPARATE SQLite file from CodePlane's
# ``data.db`` so TraceForge's alembic-on-construct never touches CodePlane's
# ``alembic_version`` (F2 / condition §6).
DEFAULT_GOVERNANCE_DB = Path.home() / ".codeplane" / "governance.db"

# Non-security-critical reason codes that a gate can carry — the waivable set.
# A blanket operator "trust this session" grant (see ``grant_session_trust``) is
# exactly ``{every reason code the preset rules can emit} − SECURITY_CRITICAL``,
# so it auto-proceeds routine mutations while NEVER waiving a hard gate. Kept in
# sync with the three preset YAMLs by ``test_governance`` (fail-closed on drift).
WAIVABLE_REASON_CODES = frozenset(
    {
        "mutating_with_network",
        "risk_score_danger",
        "mutating_locked",
        "mutating_savepoint",
        "risk_score_caution",
        "readonly_shell_locked",
    }
)


async def load_usd_ceilings(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[Preset, tuple[float | None, float | None]]:
    """Load per-preset USD ceiling overrides from the policy config.

    Returns a ``{preset: (warn_usd, ceiling_usd)}`` mapping fed into the decider so
    each preset's :class:`JobSpendCeilingAssessor` enforces the operator-configured
    dollar ceiling. Presets absent from the config fall back to the profile's baked
    default; a missing/blank config yields ``{}`` (all baked defaults). Never
    raises — a bad config degrades to defaults so it can't block startup/reload.
    """
    try:
        async with session_factory() as session:
            from backend.persistence.policy_repo import PolicyRepository

            raw = await PolicyRepository(session).get_usd_ceilings()
    except Exception:  # noqa: BLE001 — config read must never block startup/reload
        log.warning("usd_ceilings_load_failed", exc_info=True)
        return {}
    out: dict[Preset, tuple[float | None, float | None]] = {}
    for name, pair in raw.items():
        try:
            preset = Preset(name)
        except ValueError:
            continue
        warn, ceiling = pair
        out[preset] = (warn, ceiling)
    return out


def _classification_from_meta(meta: SessionMeta) -> Classification:
    """Reshape a TraceForge ``SessionMeta`` into CodePlane's ``Classification``.

    A ``None`` recommendation means no rule fired and risk was below every
    threshold — an implicit ALLOW. Only the fail-closed path carries an ESCALATE.
    """
    rec = meta.recommendation
    action = ActionRecommendation(rec.recommended_action.value) if rec is not None else ActionRecommendation.ALLOW
    reason_code = rec.reason_code if rec is not None else "allow"
    risk = meta.risk_assessment
    risk_score = int(risk.score) if risk is not None else 0
    risk_band = risk.level if risk is not None else "unknown"
    cls = meta.classification
    effect = None
    mechanism = "unknown"
    if cls is not None:
        effect_val = getattr(cls, "effect", None)
        effect = str(effect_val) if effect_val is not None else None
        mech_val = getattr(cls, "mechanism", None)
        mechanism = str(mech_val) if mech_val is not None else "unknown"
    reason = f"{action.value}: {reason_code} [{mechanism}/{effect or 'n/a'}/risk={risk_band}({risk_score})]"
    return Classification(
        recommended_action=action,
        reason_code=reason_code,
        risk_score=risk_score,
        risk_band=risk_band,
        effect=effect,
        mechanism=mechanism,
        reason=reason,
    )


_PWSH_HEADS = frozenset({"powershell", "pwsh"})
_CMD_HEADS = frozenset({"cmd", "cmd.exe"})


def _shell_tool_name(command: str) -> str:
    """Pick the shell dialect tool name TraceForge dispatches on.

    CodePlane actions carry no explicit dialect, so default to bash (matching the
    retired classifier) and switch only on an unambiguous leading token.
    """
    stripped = command.strip()
    head = stripped.split(None, 1)[0].lower() if stripped else ""
    if head in _PWSH_HEADS:
        return "powershell"
    if head in _CMD_HEADS:
        return "cmd"
    return "bash"


def _payload_for(action: Action) -> tuple[str, str | None, dict[str, Any]]:
    """Map a CodePlane ``Action`` to ``(tool_name, server_namespace, tool_input)``.

    The tool name is chosen so TraceForge's ``ContextBuilder.from_tool_call``
    classifies the event the same way the retired ``tf_classify_adapter`` did
    (same overlaid engine, same shell-dialect dispatch, same MCP namespace).
    """
    kind = action.kind
    if kind == ActionKind.shell:
        cmd = action.command or ""
        return _shell_tool_name(cmd), None, {"command": cmd}
    if kind == ActionKind.mcp_tool:
        server = action.mcp_server or ""
        tool = action.mcp_tool or action.tool_name or ""
        name = f"mcp__{server}__{tool}" if server else tool
        tool_input = {"path": action.path} if action.path else {}
        return name, (server or None), tool_input
    if kind == ActionKind.file:
        # CodePlane routes reads to ``sdk_tool``; ``file`` kind is always a write /
        # edit, historically git-backed. Classify it as a filesystem write.
        tool_input = {"path": action.path} if action.path else {}
        return (action.tool_name or "write_file"), None, tool_input
    # sdk_tool (reads, url, memory, custom tools) — may carry a shell command.
    if action.command:
        cmd = action.command
        return _shell_tool_name(cmd), None, {"command": cmd}
    tool_input = {"path": action.path} if action.path else {}
    return (action.tool_name or ""), None, tool_input


def _build_tool_call_event(action: Action, *, source_event_key: str | None) -> ToolCallEvent:
    """Build a TraceForge ``ToolCallEvent`` from a CodePlane ``Action``.

    ``source_event_key`` is left ``None`` for the decision path (a fresh
    ``score:`` key is minted; nothing persists) and pinned to a stable
    ``exec:<tool_call_id>`` for accrual so a re-delivered bus event is deduped by
    ``process_event`` and never double-counts.
    """
    tool_name, server_ns, tool_input = _payload_for(action)
    event_id = f"cp-{uuid.uuid4().hex[:12]}"
    key = source_event_key if source_event_key is not None else f"score:{event_id}"
    session_id = action.job_id or f"anon-{uuid.uuid4().hex[:8]}"
    return ToolCallEvent(
        event_id=event_id,
        session_id=session_id,
        timestamp=datetime.now(UTC),
        source_event_key=key,
        span_id=f"cp-span-{uuid.uuid4().hex[:8]}",
        tool_name=tool_name,
        server_namespace=server_ns,
        tool_args_json=json.dumps(tool_input, default=str),
        source_event_id=None,
        mcp_server_name=server_ns,
        tool_description=None,
        tool_schema_json=None,
    )


class GovernanceDecider:
    """Owns the durable store + per-preset pipelines and the two touchpoints."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_GOVERNANCE_DB,
        engine: ClassificationEngine | None = None,
        *,
        spend_reader: Callable[[str], float | Decimal | None] | None = None,
        usd_ceilings: dict[Preset, tuple[float | None, float | None]] | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._engine = engine if engine is not None else tf_engine()
        # Synchronous per-job USD spend reader (job_id -> total spend). When set,
        # each pipeline gets a JobSpendCeilingAssessor so CP's dollar ceiling is
        # enforced natively alongside TraceForge's count/effect budget. ``None``
        # (tests / no telemetry) simply omits the USD assessor.
        self._spend_reader = spend_reader
        # Optional per-preset (warn_usd, ceiling_usd) override from CP config; a
        # missing preset falls back to the profile's baked default.
        self._usd_ceilings = usd_ceilings or {}
        # One durable store shared by all pipelines (single writer, WAL, serialized
        # mutations — see traceforge persistence). Constructed once; alembic runs here.
        self._store = SystemStore(self._db_path)
        self._rebuild_lock = threading.Lock()
        self._job_presets: dict[str, Preset] = {}
        self._pipelines: dict[Preset, GovernancePipeline] = self._build_pipelines()

    # ── construction ──────────────────────────────────────────────────────────

    def _effective_profile(self, preset: Preset) -> PresetProfile:
        """Profile for ``preset`` with any CP per-preset USD override applied."""
        profile = profile_for(preset)
        override = self._usd_ceilings.get(preset)
        if override is None:
            return profile
        warn_usd, ceiling_usd = override
        return profile.with_usd_ceilings(ceiling_usd=ceiling_usd, warn_usd=warn_usd)

    def _policy_assessors_for(self, profile: PresetProfile) -> tuple[PolicyAssessor, ...]:
        """Built-in policy assessors + CP's USD ceiling assessor (when configured)."""
        assessors = _build_policy_assessors(profile.policy)
        if self._spend_reader is not None and (profile.ceiling_usd is not None or profile.warn_usd is not None):
            assessors = (
                *assessors,
                JobSpendCeilingAssessor(profile.ceiling_usd, profile.warn_usd, self._spend_reader),
            )
        return assessors

    def _build_pipelines(self) -> dict[Preset, GovernancePipeline]:
        """Build one pipeline per preset over the shared store + engine."""
        pipelines: dict[Preset, GovernancePipeline] = {}
        for preset in PROFILES:
            profile = self._effective_profile(preset)
            rules = parse_rules(profile.rules_path)
            thresholds = BudgetThresholds(
                max_tool_calls=profile.budget.max_tool_calls,
                max_by_effect=profile.budget.max_by_effect,
                max_by_capability=profile.budget.max_by_capability,
                max_by_scope=profile.budget.max_by_scope,
            )
            pipelines[preset] = GovernancePipeline(
                store=self._store,
                labeler=GovernanceLabeler(),  # PII / integrity / MCP-drift / drift OFF
                budget_tracker=BudgetTracker(thresholds),
                rules=rules,
                engine=self._engine,
                project_root=None,
                policy=None,
                policy_assessors=self._policy_assessors_for(profile),
            )
        return pipelines

    def _pipeline_for(self, job_id: str | None) -> GovernancePipeline:
        pipelines = self._pipelines  # atomic read (rebind swap is GIL-atomic)
        preset = self._job_presets.get(job_id or "", Preset.supervised)
        return pipelines.get(preset) or pipelines[Preset.supervised]

    # ── job registry ──────────────────────────────────────────────────────────

    def register_job(self, job_id: str, preset: Preset) -> None:
        """Bind a job to its preset so decide/observe pick the right pipeline."""
        self._job_presets[job_id] = preset

    def unregister_job(self, job_id: str) -> None:
        self._job_presets.pop(job_id, None)

    def is_registered(self, job_id: str) -> bool:
        """True if ``job_id`` is a governed main-agent job.

        Gates accrual (:class:`GovernanceSubscriber`) so only jobs wired through
        the action-policy setup advance the durable budget — sidecar/unknown
        sessions on the shared bus are ignored.
        """
        return job_id in self._job_presets

    # ── decision (read-only) ────────────────────────────────────────────────────

    def decide(self, action: Action) -> SessionMeta:
        """Score a pending action without persisting. Fail-closed → ESCALATE.

        A ``None`` recommendation means no rule fired and risk was below every
        threshold — i.e. an implicit ALLOW; it is returned as-is (the reshape maps
        it to ALLOW). Only an *exception* fails closed to ESCALATE.
        """
        pipeline = self._pipeline_for(action.job_id)
        try:
            tce = _build_tool_call_event(action, source_event_key=None)
            ctx = pipeline.enrich_event(tce)
            meta = pipeline.preflight_event(ctx)
        except Exception as exc:  # noqa: BLE001 — fail-closed on any internal error
            log.warning("governance_decide_failed", job_id=action.job_id, exc_info=True)
            return self._escalate(exc)
        return meta

    def classify(self, action: Action) -> Classification:
        """Decision reshaped into CodePlane's ``Classification`` (router-facing).

        This is the single call the enforcement layer uses: it maps the pending
        action → TraceForge ``SessionMeta`` → CodePlane ``Classification`` with a
        concrete ``recommended_action`` (``None`` recommendation ⇒ ALLOW).
        """
        return _classification_from_meta(self.decide(action))

    # ── accrual (durable write) ─────────────────────────────────────────────────

    def observe(self, action: Action, *, tool_call_id: str) -> None:
        """Advance durable budget/taint/session state for an EXECUTED call."""
        pipeline = self._pipeline_for(action.job_id)
        try:
            tce = _build_tool_call_event(action, source_event_key=f"exec:{tool_call_id}")
            ctx = pipeline.enrich_event(tce)
            pipeline.process_event(ctx)
        except Exception:  # noqa: BLE001 — accrual must never crash the event bus
            log.warning(
                "governance_observe_failed",
                job_id=action.job_id,
                tool_call_id=tool_call_id,
                exc_info=True,
            )

    # ── trust ───────────────────────────────────────────────────────────────────

    def grant_trust(self, job_id: str, key: str, ttl_seconds: float, *, reason: str = "") -> bool:
        """Grant a time-boxed reason-code trust waiver, persisted in the store.

        Security-critical reason codes (SPEC §18.2 / binding condition §3) can
        never be waived — the grant is refused so a hard gate is never silently
        bypassed by a trust grant. Returns True if granted, False if refused.
        """
        if is_security_critical(key):
            log.warning("trust_grant_refused_security_critical", job_id=job_id, key=key)
            return False
        self._pipeline_for(job_id).grant_trust(job_id, key, ttl_seconds, reason=reason)
        return True

    def grant_session_trust(self, job_id: str, ttl_seconds: float, *, reason: str = "operator trusted session") -> None:
        """Blanket session trust — waive every NON-security-critical gate for the TTL.

        Backs the operator "trust this whole session" action. Because it grants
        only :data:`WAIVABLE_REASON_CODES` (never a security-critical code), a
        blanket trust can never silently waive a SPEC §18.2 hard gate (binding
        condition §3) — only routine mutations auto-proceed.
        """
        for key in WAIVABLE_REASON_CODES:
            self.grant_trust(job_id, key, ttl_seconds, reason=reason)

    # ── lifecycle ────────────────────────────────────────────────────────────────

    def rebuild(self) -> None:
        """Rebuild the per-preset pipelines over the SAME store (settings changed).

        The new set is built fully, then swapped in with a single atomic rebind, so
        a concurrent ``decide``/``observe`` never observes a half-built set. Accrued
        budget/trust survive because the durable store is preserved.
        """
        with self._rebuild_lock:
            self._pipelines = self._build_pipelines()

    def set_usd_ceilings(self, usd_ceilings: dict[Preset, tuple[float | None, float | None]]) -> None:
        """Replace the per-preset USD ceiling overrides (call before ``rebuild``).

        Used when the operator edits the cost-ceiling config mid-run; the next
        :meth:`rebuild` bakes the new ceilings into each preset's
        :class:`JobSpendCeilingAssessor`.
        """
        self._usd_ceilings = usd_ceilings or {}

    def close(self) -> None:
        try:
            self._store.close()
        except Exception:  # noqa: BLE001
            log.warning("governance_store_close_failed", exc_info=True)

    # ── fail-closed helper ───────────────────────────────────────────────────────

    def _escalate(self, exc: Exception, classification: TFClassification | None = None) -> SessionMeta:
        """Mirror ``Scorer._fail_closed``: an ESCALATE SessionMeta on internal error."""
        reason = f"internal_error: {type(exc).__name__}"
        risk = RiskAssessment(
            score=0,
            level="unknown",
            confidence="low",
            factors=(reason,),
            mitre=(),
            version="1",
        )
        recommendation = RiskRecommendation(
            recommended_action=RecommendedAction.ESCALATE,
            assessment=risk,
            reason_code=reason,
            canonical_id="error",
        )
        return SessionMeta(
            classification=classification,
            risk_assessment=risk,
            recommendation=recommendation,
        )
