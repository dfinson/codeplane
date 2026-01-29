# Refactor Module — Design Spec (v8.7)

## Table of Contents

- [Scope](#scope)
  - [Responsibilities](#responsibilities)
  - [From SPEC.md](#from-specmd)
- [File Plan](#file-plan)
- [Mutation Gate (Two-Axis Model)](#mutation-gate-two-axis-model)
  - [State Model](#state-model)
  - [Response Outcomes](#response-outcomes)
  - [Gate Check](#gate-check)
- [Two-Phase Rename](#two-phase-rename)
  - [Phase 1: Plan](#phase-1-plan)
  - [Phase 2: Commit](#phase-2-commit)
  - [Critical: Decision Commit Re-Validates Gate](#critical-decision-commit-re-validates-gate)
- [Witness Packets](#witness-packets)
- [Decision Capsules](#decision-capsules)
- [Key Interfaces](#key-interfaces)
- [Refactor Flow (Summary)](#refactor-flow-summary)
- [Correctness Invariants](#correctness-invariants)
- [Dependencies](#dependencies)

---

## Scope

The refactor module provides SCIP-based semantic refactoring: rename, move, delete. It queries pre-indexed SCIP semantic data, handles multi-context repos, ambiguity detection, and agent decision flows.

### Responsibilities

- Query SCIP semantic index for symbol occurrences
- Refactor operation planning from SCIP data
- Mutation gate enforcement (CLEAN + CERTAIN required)
- Two-phase rename flow for ambiguous cases
- Decision commit with full gate re-validation
- Witness packet generation for blocked/ambiguous responses
- Multi-context handling (query, merge, detect divergence)
- Coordination with mutation engine for atomic apply

### From SPEC.md

- §7.5: Semantic Layer (SCIP Batch Indexers)
- §8: Deterministic refactor engine
- §8.5: Refactor execution flow
- §8.5a: Two-phase rename
- §8.5b: Witness packets
- §8.5c: Decision capsules
- §8.6: Multi-context handling

### Architecture

![CodePlane Semantic Refactor Architecture](../../docs/images/codeplane-semantic-refactor-architecture.png)

---

## File Plan

    refactor/
    ├── __init__.py
    ├── engine.py        # High-level RefactorEngine facade
    ├── query.py         # SCIP index queries (find occurrences, resolve symbols)
    ├── planner.py       # Refactor planning (rename, move, delete)
    ├── gate.py          # Mutation gate (CLEAN + CERTAIN check)
    ├── decision.py      # Two-phase flow, decision commit, proof verification
    ├── witness.py       # Witness packet and decision capsule generation
    ├── contexts.py      # Multi-context detection and selection
    └── sweep.py         # Comment/docstring sweep (non-semantic)

---

## Mutation Gate (Two-Axis Model)

### State Model

    Freshness × Certainty → Mutation Behavior
    
    CLEAN + CERTAIN    → Automatic semantic edits allowed
    CLEAN + AMBIGUOUS  → Return needs_decision with candidates
    DIRTY + *          → Block, return blocked with witness
    STALE + *          → Block, return blocked with witness
    PENDING_CHECK + *  → Block, return blocked with witness

### Response Outcomes

| Status | Meaning |
|--------|---------|
| `ok` | Edits applied successfully |
| `ok_syntactic` | Edits applied via syntactic fallback |
| `blocked` | Non-CLEAN files, includes witness packet |
| `needs_decision` | CLEAN but AMBIGUOUS, includes candidates |
| `refused` | Operation cannot be performed |
| `unsupported` | Language not supported |

### Gate Check

    def check_mutation_gate(file_ids: list[int], context_id: int) -> MutationGateResult:
        states = get_file_states_batch(file_ids, context_id)
        
        # Check freshness
        non_clean = [f for f, s in states.items() if s.freshness != "clean"]
        if non_clean:
            return MutationGateResult(
                status="blocked",
                reason="files_not_clean",
                non_clean_files=non_clean,
                suggested_refresh_scope=RefreshScope(files=non_clean)
            )
        
        # Check certainty
        ambiguous = [f for f, s in states.items() if s.certainty == "ambiguous"]
        if ambiguous:
            return MutationGateResult(
                status="needs_decision",
                reason="files_ambiguous",
                ambiguous_files=ambiguous
            )
        
        return MutationGateResult(status="ok")

---

## Two-Phase Rename

### Phase 1: Plan

Request rename → return candidates if ambiguous:

    POST /refactor/rename
    {
      "symbol": "MyClass.process",
      "new_name": "handle",
      "context_id": "python-main"
    }

Response (needs_decision):

    {
      "status": "needs_decision",
      "plan_id": "uuid",
      "symbol": "MyClass.process",
      "candidates": [
        {
          "id": "group_0",
          "description": "MyClass.process in src/core.py (semantic)",
          "confidence": 0.95,
          "provenance": "semantic",
          "occurrences": [...],
          "apply_plan": { "edits": [...] }
        }
      ],
      "witness": { ... },
      "decision_capsules": [...],
      "commit_endpoint": "/decisions/commit",
      "expires_at": "..."
    }

### Phase 2: Commit

Agent selects candidate and provides proof:

    POST /decisions/commit
    {
      "plan_id": "uuid",
      "selected_candidate_id": "group_0",
      "proof": {
        "symbol_identity": "MyClass.process",
        "anchors": [...],
        "file_line_evidence": [...]
      }
    }

### Critical: Decision Commit Re-Validates Gate

    def handle_decision_commit(request: DecisionCommitRequest) -> DecisionCommitResponse:
        plan = get_plan(request.plan_id)
        if not plan or plan.expired:
            return DecisionCommitResponse(status="refused", reason="plan_expired")
        
        candidate = get_candidate(plan, request.selected_candidate_id)
        if not candidate:
            return DecisionCommitResponse(status="refused", reason="candidate_not_found")
        
        # CRITICAL: Re-validate mutation gate (not just anchors)
        affected_files = plan.affected_files
        states = get_file_states_batch(affected_files, plan.context_id)
        
        # Check freshness
        non_clean = [f for f in affected_files if states[f].freshness != "clean"]
        if non_clean:
            return DecisionCommitResponse(
                status="blocked",
                reason="files_not_clean",
                non_clean_files=non_clean,
                suggested_refresh_scope=RefreshScope(files=non_clean)
            )
        
        # Check certainty (may have shifted)
        ambiguous = [f for f in affected_files if states[f].certainty == "ambiguous"]
        if ambiguous:
            return DecisionCommitResponse(
                status="needs_decision",
                reason="files_now_ambiguous",
                hint="Re-fetch plan; state changed"
            )
        
        # Verify anchors
        for anchor in request.proof.anchors:
            if not verify_anchor(anchor):
                return DecisionCommitResponse(
                    status="blocked",
                    reason="anchor_mismatch"
                )
        
        # Verify file hashes
        for evidence in request.proof.file_line_evidence:
            if hash_line(evidence.file, evidence.line) != evidence.content_hash:
                return DecisionCommitResponse(
                    status="blocked",
                    reason="file_changed"
                )
        
        # All checks passed - apply
        result = apply_edits_atomically(candidate.apply_plan.edits)
        cache_decision(plan, request)
        
        return DecisionCommitResponse(status="ok", delta=result.delta)

---

## Witness Packets

Every blocked or needs_decision response includes structured evidence:

    @dataclass
    class WitnessPacket:
        bounds: ScanBounds          # What we checked
        facts: list[WitnessFact]    # What we found
        invariants_failed: list[str]  # What failed (for blocked)
        candidate_sets: dict[str, CandidateSet]  # For needs_decision
        disambiguation_checklist: list[DisambiguationItem]

    @dataclass
    class ScanBounds:
        files_scanned: list[str]
        contexts_queried: list[str]
        time_budget_ms: int
        truncated: bool

    @dataclass
    class WitnessFact:
        fact_type: str       # definition, reference, import, scope_chain
        location: Location
        content: str
        provenance: str      # semantic, syntactic, text
        confidence: float

---

## Decision Capsules

Pre-packaged micro-queries for agents to answer:

    @dataclass
    class DecisionCapsule:
        capsule_type: str    # scope_resolution, receiver_resolution, context_membership
        inputs: dict
        candidate_outputs: list[CapsuleOutput]
        verification_method: str
        stop_rule: str

| Type | Question | Agent Action |
|------|----------|--------------|
| scope_resolution | Which definition is in scope at cursor? | Trace imports |
| receiver_resolution | Which receivers reach this call? | Trace assignments |
| context_membership | Which contexts include this file? | Check patterns |

---

## Key Interfaces

    # engine.py
    class RefactorEngine:
        async def rename(self, symbol: str, new_name: str, options: RefactorOptions) -> RefactorResult
        async def move(self, from_path: Path, to_path: Path, options: RefactorOptions) -> RefactorResult
        async def delete(self, target: str, options: RefactorOptions) -> RefactorResult

    # decision.py
    class DecisionService:
        def create_plan(self, symbol: Symbol, operation: str, candidates: list[Candidate]) -> RenamePlan
        def handle_commit(self, request: DecisionCommitRequest) -> DecisionCommitResponse
        def get_cached_decision(self, ambiguity_signature: str) -> CachedDecision | None

    # gate.py
    class MutationGate:
        def check(self, file_ids: list[int], context_id: int) -> MutationGateResult
        def get_file_states(self, file_ids: list[int], context_id: int) -> dict[int, FileState]

    # witness.py
    class WitnessBuilder:
        def build_blocked_witness(self, non_clean_files: list, operation: str) -> WitnessPacket
        def build_decision_witness(self, ambiguous_files: list, candidates: list) -> WitnessPacket
        def build_capsules(self, symbol: Symbol, ambiguity_type: str) -> list[DecisionCapsule]

---

## Refactor Flow (Summary)

    1. Request arrives (rename, move, delete)
    2. Mutation gate check:
       - Non-CLEAN → return blocked + witness + suggested_refresh_scope
       - CLEAN + AMBIGUOUS → return needs_decision + candidates + witness + capsules
       - CLEAN + CERTAIN → proceed
    3. Query SCIP index for occurrences
    4. Generate edit plan
    5. Apply atomically via mutation engine
    6. Mark affected files DIRTY
    7. Return ok + delta

    Decision commit flow:
    1. Retrieve plan and candidate
    2. RE-VALIDATE mutation gate (critical!)
    3. Verify anchors and hashes
    4. Apply edits
    5. Cache decision for replay

---

## Correctness Invariants

| Invariant | Failure Mode | Fix |
|-----------|--------------|-----|
| Gate re-validation at commit | Edits applied against stale state | Always re-check CLEAN + CERTAIN before apply |
| Plan expiration | Stale plans used | TTL on plans, reject expired |
| Anchor verification | Edits at wrong positions | Verify content around each occurrence |
| Atomic apply | Partial edits | Transaction in mutation engine |

---

## Dependencies

- protobuf — SCIP format parsing
- Standard library only for core logic
