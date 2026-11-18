"""Story generation service — assembles a structured code-review narrative
from trail data: validated change references interleaved with LLM-
generated connective prose, enriched with agent decision beats.

The key design principle: *references are never LLM-generated*.  They are
built from trail ``write`` sub-nodes (§13.1), ordered chronologically.
The LLM generates the prose that connects them, weaving in trail beats
(decisions, backtracks, insights, verifications) as inline narrative
turning points.

Stories are generated on demand and cached as JSON on the ``jobs.story_text``
column.  When trail enrichment is still pending, stories are returned
uncached — enabling live rolling generation as the agent works.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, TypedDict, cast

import httpx
import structlog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.services.completers.naming_service import Completable

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Internal typed dicts for story data shapes
# ---------------------------------------------------------------------------


class StoryReference(TypedDict, total=False):
    spanId: str
    file: str
    why: str
    stepNumber: int | None
    stepTitle: str
    turnId: str
    editCount: int
    snippet: str
    editDetails: list[dict[str, str]]
    isRetry: bool
    errorKind: str
    phase: str
    stepIntent: str
    activityLabel: str
    isFileChange: bool
    action: str  # "created" | "modified" | "read"


class _JobContext(TypedDict, total=False):
    id: str
    title: str | None
    description: str | None
    prompt: str
    state: str
    model: str | None


class _TelemetryContext(TypedDict, total=False):
    duration_ms: int | None
    total_cost_usd: float | None
    tool_call_count: int
    tool_failure_count: int
    retry_count: int


class _ApprovalContext(TypedDict, total=False):
    description: str
    resolution: str | None
    requires_explicit_approval: bool
    proposed_action: str


class StoryContext(TypedDict, total=False):
    job: _JobContext
    telemetry: _TelemetryContext
    approvals: list[_ApprovalContext]
    trail_beats: list[TrailBeat]


class StoryBlock(TypedDict, total=False):
    type: str
    text: str
    level: int
    beatKind: str
    spanId: str
    file: str
    why: str
    stepNumber: int | None
    stepTitle: str
    turnId: str
    editCount: int
    snippet: str
    editDetails: list[dict[str, str]]
    isRetry: bool
    errorKind: str
    phase: str
    stepIntent: str
    activityLabel: str
    action: str  # "created" | "modified" | "read"


class TrailBeat(TypedDict, total=False):
    kind: str
    intent: str
    rationale: str
    outcome: str
    supersedes: str | None
    files: list[str]
    seq: int
    activity_label: str | None


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_STORY_SYSTEM = (
    "You write compelling technical narratives about coding sessions. You "
    "receive code changes with snippets, the agent's cognitive trail "
    "(decisions, backtracks, insights, verifications), and session context. "
    "Write a first-person narrative that a human reviewer can follow like "
    "a blog post — one that earns their attention and holds it.\n\n"
    #
    # Narrative voice
    "VOICE: Write like a senior engineer explaining their work to a colleague "
    "over coffee — candid, specific, occasionally wry. Every paragraph "
    "should teach the reader something they didn't know before reading it. "
    "Humor comes from noticing genuine absurdity and stating it as fact — "
    "never manufacture jokes, never be cute, never self-congratulate. "
    "If nothing is genuinely absurd, don't reach for wit.\n\n"
    #
    # Hook and structure — transformation arc
    "STRUCTURE: Open with a hook — the most surprising outcome, the hardest "
    "problem encountered, or what was at stake. Do NOT open with 'I was "
    "asked to...' or 'The task was...'. Pull the reader in with the most "
    "interesting thing that happened, then rewind to set context.\n\n"
    "GROUNDING THE HOOK: The reader arrives knowing only the job title. "
    "Your opening sentence must be self-contained — it should name the "
    "system, the codebase, or the domain concretely enough that a reader "
    "can orient themselves without external context. Never reference 'the "
    "run', 'the task', 'the session', or 'the job' as if the reader already "
    "knows what happened. BAD: 'Two verification failures killed the run.' "
    "(What run? What system? The reader has no idea.) GOOD: 'The Pydantic "
    "model had two fields that meant the same thing, and neither knew about "
    "the other.' The hook can be dramatic, but it must plant the reader in "
    "a specific technical place.\n\n"
    "Frame the "
    "session as a transformation: what was the state of the codebase before, "
    "what complications arose during the work, and what state it reached "
    "after. Walk through the body chronologically, but the opening should "
    "front-load why the reader should care. Close with the final state of "
    "the code — what it looks like now that the work is done. The ending "
    "should read as prose, not as a summary section or diff dump. Do NOT "
    "end with a bulleted list of files/changes, a 'what changed' recap, "
    "or raw diffs. The narrative should land on a concrete observation — "
    "what the system can do now that it couldn't before, what invariant "
    "holds, or what the tests prove. If the story ends with a verification "
    "step, let the test result be the last beat.\n\n"
    #
    # Depth — thorough technical narrative
    "DEPTH: Write a thorough technical narrative — the kind of thing you'd "
    "post on an engineering blog. For each change, explain what the existing "
    "code looked like before, what problem that created, what the new code "
    "does (quote key lines from the snippets), what alternatives were "
    "considered, and what tradeoffs were made. The reader should understand "
    "not just what changed but the full technical context — the shape of "
    "the codebase before the change, the constraint that made the change "
    "necessary, and exactly what the new code looks like.\n\n"
    #
    # Curiosity gaps — forward references that create tension
    "FORWARD REFERENCES: Plant questions in the reader's mind that get "
    "answered later. When you know the narrative will reveal something "
    "surprising — a wrong assumption, an unexpected dependency, a hidden "
    "bug — hint at it early and pay it off later. 'The migration looked "
    "straightforward — three tables, a few foreign keys. The foreign keys "
    "turned out to be the problem.' This creates cognitive tension that "
    "holds attention across paragraphs. Use sparingly — one or two per "
    "story, only when the trail data contains a genuine surprise.\n\n"
    #
    # Rendering context — markers become embedded diff blocks
    "RENDERING: Each [[N]] marker is rendered as a full embedded diff block "
    "showing the filename, line counts, and actual code changes. The reader "
    "sees your prose interrupted by a bordered code card — like a figure in "
    "an article. Your text must set up each diff so it reads naturally:\n"
    '  GOOD: "I added the validation middleware: [[3]]"\n'
    '  GOOD: "The auth handler needed a null check, so I updated it: [[5]]"\n'
    '  BAD:  "I updated the auth module [[3]] and then fixed tests [[4]]" '
    "(markers dropped mid-sentence become visual noise)\n"
    '  BAD:  "[[3]] was the next change" (leading with a diff block is disorienting)\n'
    "Place each [[N]] at a sentence boundary where the reader expects to see "
    "code. The reader should always know what they are about to see BEFORE "
    "the diff appears.\n\n"
    #
    # Beat markers — cognitive turning points
    "BEATS: The AGENT JOURNEY beats are the skeleton of your story — decisions, "
    "backtracks, insights, and verifications. Each beat type demands a "
    "specific narrative shape:\n"
    "  {{DECIDE}} — Frame the fork: what alternatives existed, what "
    "tradeoffs mattered, and why this path won. The reader should understand "
    "the choice even if they would have chosen differently.\n"
    "  {{BACKTRACK}} — This is the complication in your story arc. Set up the "
    "original reasoning so it sounds right, then show the moment it broke. "
    "What was the signal — an error, a test failure, a realization? Then "
    "show the pivot. Backtracks are the most compelling part of any "
    "technical narrative because they reveal how the problem actually works.\n"
    "  {{INSIGHT}} — The discovery moment. What was the state of "
    "understanding before, what changed it, and what does the reader now "
    "know that they didn't.\n"
    "  {{VERIFY}} — Ground the reader: what was tested, what passed, what "
    "the result proves about the changes above.\n"
    "A beat marker goes on its own line, followed by one or more prose "
    "paragraphs about that turning point. Not every beat needs a marker — "
    "minor decisions can be woven into regular prose. Use markers for "
    "moments the reader should notice.\n\n"
    #
    # Pacing
    "PACING: Alternate between dense technical detail and brief orienting "
    "observations. After explaining a multi-line change, step back — why "
    "does it matter, what does the system look like now, what comes next. "
    "After a backtrack or complication, give the reader a beat of relief "
    "before diving into the fix. Monotone density loses readers; monotone "
    "simplicity bores them.\n\n"
    #
    # Length
    "LENGTH: Write enough to actually tell the story. Scale length to "
    "the number and complexity of changes — a session with two small "
    "fixes needs far less prose than one with a multi-file refactor. "
    "If a change involves a design decision, explain the alternatives "
    "considered and why this path won. Do NOT compress the narrative into "
    "terse bullet-point-like sentences. Each paragraph should flow into "
    "the next.\n\n"
    #
    # Investigative milestones
    "INVESTIGATIVE TASKS: When there are no file changes but the agent "
    "explored, analyzed, or investigated — this is an insight story. "
    "The trail beats ARE the story. Chronicle what was examined, what "
    "was discovered, what hypotheses were tested and discarded. The "
    "reader should feel like they walked through the investigation "
    "with the agent. Use {{INSIGHT}} and {{DECIDE}} markers liberally. "
    "Even without code changes, these narratives should be vivid and "
    "specific — name the files examined, the patterns found, the "
    "conclusions reached.\n\n"
    #
    # Inline code
    "INLINE CODE: Weave code into your narrative — quote key lines, name "
    "specific functions, variables, and expressions using `backticks`. "
    "The reader should encounter real code in your prose before they see "
    "the full diff card.\n\n"
    #
    # Objectivity with personality
    "TONE: State what you did and why. No self-assessment of difficulty "
    '("This was complex"), no hedging ("I thought maybe"). Name specific '
    "consequences — 'without this check, expired tokens pass through to "
    "the database layer' grounds the reader in stakes better than 'this "
    "was an important fix'. When something is genuinely absurd — a function "
    "named `handleEverything()`, a config file longer than the service it "
    "configures — state it as fact and move on. Never mean, never sarcastic "
    "about other people's code.\n\n"
    "NEVER give advice, suggest next steps, recommend follow-ups, list "
    "risks, or tell the reader what to do. You are a narrator, not a "
    "consultant. The story ends when the work ends. No 'consider adding', "
    "no 'next steps', no 'what to watch for'. If the code has a known "
    "gap, state it as fact ('the handler has no rate limit') — never "
    "frame it as a suggestion.\n\n"
    #
    # Connective prose
    "TRANSITIONS: Every transition should pull the reader forward. 'Having "
    "fixed the auth handler, the real problem became visible' beats 'Next, "
    "I looked at the auth handler.' Connect each section to the next by "
    "showing what the previous change revealed, required, or unblocked. "
    "The reader should never wonder 'why are we here now?'\n\n"
    #
    # Contextual recall
    "RECALL: When you reference a symbol introduced earlier, add a brief "
    "contextual tag on later mentions — 'the approval entry point "
    "`create_request()`' rather than bare '`create_request()`'. "
    "First mention: full introduction. Mentions within 1-2 paragraphs: "
    "bare name is fine. Later mentions: brief role tag.\n\n"
    #
    # Retry arcs
    "RETRY ARCS: When a change is marked [RETRY], the original attempt "
    "failed. Tell the reader what happened — what error occurred, what the "
    "agent tried first, and why the second attempt succeeded. Use a "
    "{{BACKTRACK}} marker for these.\n\n"
    #
    # Activity groups
    "ACTIVITIES: Changes may be grouped under activity labels. Use these as "
    "natural chapter transitions. When shifting between activities, bridge "
    "the gap — show the connection or contrast between the two concerns "
    "so the reader follows the thread.\n\n"
    #
    # Format constraints
    "FORMAT: Write in plain prose paragraphs. Use `backtick` code spans "
    "freely. First person ('I started by…'). Contractions fine. No emoji "
    "or exclamation marks. NEVER reproduce raw diffs, patches, or "
    "`diff --git` formatted text in your prose — code changes are shown "
    "ONLY via [[N]] markers. The rendering system displays the full diff "
    "automatically. Quote individual lines or expressions with `backticks` "
    "if you want to highlight specific code in the narrative.\n\n"
    "SECTIONS: Break the story into 3–6 sections using `## Section Title` "
    "headers. Within each section, use `### Sub-heading` markers to signal "
    "distinct beats or phases — a decision point, a complication, a "
    "verification step. Sub-headings let the reader scan the story like a "
    "table of contents: the `##` is the chapter, each `###` is a scene "
    "within it. Aim for 1–3 sub-headings per section; not every paragraph "
    "needs one, but every major turning point deserves its own. Section "
    "titles should be short, specific, and name the thing that changed or "
    "happened — 'Untangling the archived state', 'Filter reorder in "
    "the service layer', 'Verification failures'. Sub-headings can be "
    "shorter and punchier — 'The null check', 'Retry logic bites back'. "
    "Never generic titles "
    "like 'Introduction', 'Conclusion', 'Summary', 'What changed', "
    "'Next steps', or 'Risks'. The opening section still needs "
    "a header. Separate paragraphs with blank lines.\n\n"
    "Every change MUST be referenced by its [[N]] marker at least once. "
    "Beat markers ({{DECIDE}}, {{BACKTRACK}}, {{INSIGHT}}, {{VERIFY}}) go "
    "on their own line before the relevant prose."
)


# DB column for cached story text
_STORY_COLUMN = "story_text"


# ---------------------------------------------------------------------------
# Token estimation and model-context lookup
# ---------------------------------------------------------------------------

_tok_enc = None
try:
    import tiktoken as _tiktoken

    _tok_enc = _tiktoken.get_encoding("cl100k_base")
except (ImportError, OSError) as exc:
    log.warning("story_tokenizer_unavailable", error=str(exc), fallback="character_estimate")


def _estimate_tokens(text: str) -> int:
    """Use the tokenizer when available, otherwise the logged character estimate."""
    return len(_tok_enc.encode(text)) if _tok_enc is not None else len(text) // 4


def _truncate(s: str | None, max_len: int) -> str:
    if not s:
        return ""
    return s[:max_len] + ("…" if len(s) > max_len else "")


# ---------------------------------------------------------------------------
# Reference extraction — reads from trail write sub-nodes (§13.1)
# ---------------------------------------------------------------------------


async def _build_references(
    session: AsyncSession,
    job_id: str,
) -> list[StoryReference]:
    """Build validated reference dicts from trail nodes, chronologically.

    Prefers write nodes (file-level changes with snippets).  When no write
    nodes exist — e.g. investigative or audit tasks — falls back to all
    enriched trail nodes so the story still has anchors.
    """
    from sqlalchemy import select, text

    from backend.models.db import TrailNodeRow

    write_kinds = ("write", "modify")

    # Try write/modify nodes first — they're the strongest story anchors.
    # For write/modify nodes, accept any enrichment status: the file path
    # is already present and _ensure_diffs will attach the git diff.
    stmt = (
        select(TrailNodeRow)
        .where(TrailNodeRow.job_id == job_id)
        .where(TrailNodeRow.kind.in_(write_kinds))
        .order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
    )
    result = await session.execute(stmt)
    nodes = list(result.scalars().all())

    # Fallback: use all enriched trail nodes when no writes exist
    if not nodes:
        stmt = (
            select(TrailNodeRow)
            .where(TrailNodeRow.job_id == job_id)
            .where(TrailNodeRow.enrichment == "complete")
            .order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
        )
        result = await session.execute(stmt)
        nodes = list(result.scalars().all())

    if not nodes:
        return []

    # Fetch step metadata (step_number, title, intent) keyed by turn_id
    step_rows = await session.execute(
        text("""
            SELECT turn_id, step_number, title, intent
            FROM steps
            WHERE job_id = :jid
        """),
        {"jid": job_id},
    )
    step_map: dict[str, dict[str, Any]] = {}
    for r in step_rows.mappings():
        if r["turn_id"]:
            step_map[r["turn_id"]] = dict(r)

    # Deduplicate — by file+step for writes, by kind+seq for others
    seen: dict[str, StoryReference] = {}
    for node in nodes:
        file_val = ""
        if node.files:
            try:
                parsed = json.loads(node.files)
                files_list = parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                files_list = []
            file_val = str(files_list[0]) if files_list else ""

        step_info = step_map.get(node.turn_id or "")
        step_number = step_info["step_number"] if step_info else None

        is_write = node.kind in write_kinds

        if is_write:
            key = f"__node_{node.id}" if not file_val or step_number is None else f"{file_val}|{step_number}"
        else:
            # Non-write nodes: one entry per kind+seq
            key = f"__{node.kind}_{node.seq}"

        ref: StoryReference = {
            "spanId": node.id,
            "file": file_val,
            "why": node.write_summary or node.intent or node.rationale or node.activity_label or "",
            "stepNumber": step_number,
            "stepTitle": _truncate(step_info.get("title") if step_info else None, 60),
            "turnId": node.turn_id or "",
            "isFileChange": is_write,
            "action": "modified" if is_write else "read",
        }
        if node.snippet:
            ref["snippet"] = node.snippet
        if node.is_retry:
            ref["isRetry"] = True
        if node.error_kind:
            ref["errorKind"] = node.error_kind
        if node.phase:
            ref["phase"] = node.phase
        if step_info and step_info.get("intent"):
            ref["stepIntent"] = step_info["intent"]
        # Merge per-edit details if available (write nodes only)
        if is_write and node.edit_motivations:
            try:
                edits = json.loads(node.edit_motivations)
                if isinstance(edits, list) and edits:
                    ref["editCount"] = len(edits)
                    ref["editDetails"] = [
                        {"title": e.get("title", ""), "why": e.get("why", "")} for e in edits if e.get("why")
                    ]
            except (json.JSONDecodeError, TypeError):
                pass

        # Activity label from the node itself
        if node.activity_label:
            ref["activityLabel"] = node.activity_label

        if key in seen and not ref.get("isRetry"):
            log.warning("story_duplicate_ref_key", key=key, span_id=node.id)
            key = f"{key}|{node.id}"  # disambiguate — both refs appear
        seen[key] = ref

    return list(seen.values())


# ---------------------------------------------------------------------------
# Trail beats extraction (semantic turning points)
# ---------------------------------------------------------------------------


async def _build_trail_beats(
    session: AsyncSession,
    job_id: str,
) -> list[TrailBeat]:
    """Fetch enriched semantic trail nodes — decisions, backtracks, insights."""
    from sqlalchemy import select

    from backend.models.db import TrailNodeRow

    semantic_kinds = ["decide", "backtrack", "insight", "verify", "plan"]
    stmt = (
        select(TrailNodeRow)
        .where(TrailNodeRow.job_id == job_id)
        .where(TrailNodeRow.enrichment == "complete")
        .where(TrailNodeRow.kind.in_(semantic_kinds))
        .order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
    )
    result = await session.execute(stmt)
    nodes = list(result.scalars().all())

    beats: list[TrailBeat] = []
    for node in nodes:
        files_list: list[str] = []
        if node.files:
            try:
                parsed = json.loads(node.files)
                files_list = [str(f) for f in parsed] if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                files_list = []
        beat: TrailBeat = {
            "kind": node.kind,
            "seq": node.seq,
        }
        if node.intent:
            beat["intent"] = node.intent
        if node.rationale:
            beat["rationale"] = node.rationale
        if node.outcome:
            beat["outcome"] = node.outcome
        if node.supersedes:
            beat["supersedes"] = node.supersedes
        if files_list:
            beat["files"] = files_list
        if node.activity_label:
            beat["activity_label"] = node.activity_label
        beats.append(beat)
    return beats


# ---------------------------------------------------------------------------
# Context collection (non-reference metadata for the prompt)
# ---------------------------------------------------------------------------


async def _collect_context(
    session: AsyncSession,
    job_id: str,
    *,
    job_row: dict[str, Any] | None = None,
) -> StoryContext:
    """Gather lightweight context metadata (no file_write spans — those are
    handled by ``_build_references``)."""
    from sqlalchemy import text

    ctx: StoryContext = {}

    # Job metadata — reuse caller-provided row when available (#8)
    if job_row is None:
        result = await session.execute(
            text("SELECT id, title, description, prompt, state, model FROM jobs WHERE id = :jid"),
            {"jid": job_id},
        )
        mapping = result.mappings().first()
        if not mapping:
            return {}
        job_row = dict(mapping)
    ctx["job"] = cast("_JobContext", job_row)

    # Telemetry summary
    row = await session.execute(
        text("""
            SELECT duration_ms, total_cost_usd, tool_call_count,
                   tool_failure_count, retry_count
            FROM job_telemetry_summary WHERE job_id = :jid AND session_kind = 'job'
        """),
        {"jid": job_id},
    )
    summary = row.mappings().first()
    if summary:
        ctx["telemetry"] = cast("_TelemetryContext", dict(summary))

    # Approvals
    rows = await session.execute(
        text("""
            SELECT description, resolution, requires_explicit_approval,
                   proposed_action
            FROM approvals WHERE job_id = :jid ORDER BY requested_at ASC
        """),
        {"jid": job_id},
    )
    approvals = [dict(r) for r in rows.mappings()]
    if approvals:
        ctx["approvals"] = cast("list[_ApprovalContext]", approvals)

    # Trail beats — semantic turning points from enriched trail nodes
    beats = await _build_trail_beats(session, job_id)
    if beats:
        ctx["trail_beats"] = beats

    return ctx


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(
    refs: list[StoryReference],
    ctx: StoryContext,
) -> str:
    """Build the user prompt listing numbered changes + context."""
    parts: list[str] = []

    job = ctx.get("job", {})
    parts.append("## SESSION CONTEXT")
    parts.append(f"Title: {job.get('title', 'Untitled')}")
    parts.append(f"Task: {_truncate(job.get('prompt') or job.get('description', ''), 400)}")
    telem = ctx.get("telemetry", {})
    if telem:
        dur = round((telem.get("duration_ms") or 0) / 60000, 1)
        parts.append(f"Duration: {dur} min, {telem.get('tool_call_count', 0)} tool calls")
        fails = telem.get("tool_failure_count", 0) or 0
        retries = telem.get("retry_count", 0) or 0
        if fails or retries:
            parts.append(f"Issues: {fails} failures, {retries} retries")

    # Trail beats — narrative turning points
    beats = ctx.get("trail_beats", [])
    if beats:
        parts.append("\n## AGENT JOURNEY (key moments, chronological)")
        for b in beats:
            kind = b.get("kind", "")
            intent = b.get("intent", "")
            line = f"  [{kind.upper()}] {intent}"
            if b.get("rationale"):
                line += f"\n    Rationale: {b['rationale']}"
            if b.get("outcome"):
                line += f"\n    Outcome: {b['outcome']}"
            if kind == "backtrack" and b.get("supersedes"):
                line += " (reverses earlier approach)"
            parts.append(line)

    # Approval decisions with proposed actions
    approvals = ctx.get("approvals", [])
    if approvals:
        parts.append("\n## DECISION POINTS")
        for a in approvals:
            line = f"  - {a.get('description', '')} → {a.get('resolution', 'pending')}"
            if a.get("proposed_action"):
                line += f"\n    Proposed: {a['proposed_action']}"
            parts.append(line)

    # Entries — render in chronological order with activity headers at transitions (#13)
    has_files = any(ref.get("file") for ref in refs)
    section_label = "CHANGES" if has_files else "SESSION EVENTS"
    parts.append(f"\n## {section_label} ({len(refs)} total, chronological)")

    def _fmt_ref(idx: int, ref: StoryReference) -> list[str]:
        lines: list[str] = []
        anchor = ref.get("file") or ref.get("stepTitle") or ref.get("why") or f"event {idx}"
        line = f"{idx}. **{anchor}**"
        if ref.get("file") and ref.get("stepTitle"):
            line = f"{idx}. **{ref['file']}** (step {ref.get('stepNumber', '?')}: {ref['stepTitle']})"
        if ref.get("isRetry"):
            line += " [RETRY]"
        if ref.get("errorKind"):
            line += f" [error: {ref['errorKind']}]"
        if ref.get("why") and ref.get("why") != anchor:
            line += f" — {ref['why']}"
        if ref.get("editCount") and ref["editCount"] > 1:
            line += f" [{ref['editCount']} edits]"
        lines.append(line)
        if ref.get("stepIntent"):
            lines.append(f"   Intent: {ref['stepIntent']}")
        if ref.get("editDetails"):
            for ed in ref["editDetails"]:
                if ed.get("why"):
                    lines.append(f"   • {ed.get('title', 'edit')}: {ed['why']}")
        if ref.get("snippet"):
            lines.append("```")
            lines.append(ref["snippet"])
            lines.append("```")
        return lines

    current_activity: str | None = None
    for i, ref in enumerate(refs, 1):
        label = ref.get("activityLabel", "") or ""
        if label != (current_activity or ""):
            if label:
                parts.append(f"\n### Activity: {label}")
            elif current_activity:
                parts.append("\n### Other changes")
            current_activity = label or None
        parts.extend(_fmt_ref(i, ref))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parser: LLM output → structured blocks
# ---------------------------------------------------------------------------

_MARKER_RE = re.compile(r"\[\[(\d+)\]\]")
_BEAT_RE = re.compile(r"\{\{(DECIDE|BACKTRACK|INSIGHT|VERIFY)\}\}", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_SPLIT_RE = re.compile(
    r"(\[\[\d+\]\]|\{\{(?:DECIDE|BACKTRACK|INSIGHT|VERIFY)\}\})",
    re.IGNORECASE,
)


def _parse_blocks(
    raw: str,
    refs: list[StoryReference],
) -> list[StoryBlock]:
    """Split LLM output on ``[[N]]`` reference markers and ``{{BEAT}}``
    beat markers into narrative, reference, and beat blocks."""
    blocks: list[StoryBlock] = []
    referenced: set[int] = set()

    segments = _SPLIT_RE.split(raw)

    pending_beat: str | None = None

    for seg in segments:
        # Check if this segment is a beat marker
        beat_match = _BEAT_RE.fullmatch(seg)
        if beat_match:
            pending_beat = beat_match.group(1).lower()
            continue

        # Check if this segment is a reference marker
        ref_match = _MARKER_RE.fullmatch(seg)
        if ref_match:
            # Flush any pending beat before the reference (#6)
            if pending_beat:
                blocks.append(
                    {
                        "type": "beat",
                        "text": "",
                        "beatKind": pending_beat,
                    }
                )
                pending_beat = None
            raw_idx = int(ref_match.group(1))
            idx = raw_idx - 1  # 1-based → 0-based
            if 0 <= idx < len(refs):
                blocks.append(cast("StoryBlock", {"type": "reference", **refs[idx]}))
                referenced.add(idx)
            else:
                log.warning(
                    "story_marker_out_of_range",
                    marker=raw_idx,
                    ref_count=len(refs),
                )
            continue

        # Plain text segment — narrative or beat prose.
        # Split on ## headings to emit heading blocks.
        text = seg.strip()
        if not text:
            continue

        # Extract heading lines and interleaved prose
        sub_blocks: list[StoryBlock] = []
        last_end = 0
        for hm in _HEADING_RE.finditer(text):
            before = text[last_end : hm.start()].strip()
            if before:
                sub_blocks.append({"type": "narrative", "text": before})
            sub_blocks.append({"type": "heading", "text": hm.group(2).strip(), "level": len(hm.group(1))})
            last_end = hm.end()
        trailing = text[last_end:].strip()
        if trailing:
            sub_blocks.append({"type": "narrative", "text": trailing})

        if not sub_blocks:
            continue

        if pending_beat:
            # Attach beat kind to the first narrative sub-block
            for sb in sub_blocks:
                if sb.get("type") == "narrative":
                    sb["beatKind"] = pending_beat
                    sb["type"] = "beat"
                    break
            pending_beat = None

        blocks.extend(sub_blocks)

    # Discard any dangling beat marker with no trailing prose (#10)
    if pending_beat:
        log.debug("story_dangling_beat_marker", beat_kind=pending_beat)

    # Append any unreferenced changes at the end
    for i, ref in enumerate(refs):
        if i not in referenced:
            blocks.append(cast("StoryBlock", {"type": "reference", **ref}))

    # Filter out any empty beat blocks that slipped through (#10)
    blocks = [b for b in blocks if not (b.get("type") == "beat" and not b.get("text"))]

    return blocks


# ---------------------------------------------------------------------------
# Multi-pass generation — splits large ref sets across LLM calls (#7)
# ---------------------------------------------------------------------------

_CONTINUATION_SYSTEM = (
    "\n\nCONTINUATION: This is part {pass_num} of {total_passes} of a larger "
    "narrative. The previous part ended with changes up to [[{prev_last}]]. "
    "You are now covering changes [[{first}]] through [[{last}]]. "
    "Continue the narrative naturally from where the previous section left "
    "off — no need to restate context or re-introduce the session. "
    "Open with a brief transitional sentence, then proceed with the "
    "remaining changes. Maintain the same voice and style."
)


def _split_refs_into_chunks(
    refs: list[StoryReference],
    ctx: StoryContext,
    system: str,
    *,
    max_input_tokens: int,
) -> list[list[tuple[int, StoryReference]]]:
    """Split refs into chunks that each fit within the model's context window.

    Each chunk is a list of (global_1based_index, ref) pairs.  The context
    (job metadata, beats, approvals) is repeated in every chunk — only the
    CHANGES section varies.

    We use 75% of max_input_tokens as the budget to leave headroom for the
    model's internal overhead, output generation, and estimation error.
    """
    # Build the fixed portion of the prompt (everything except refs)
    fixed_prompt = _build_prompt([], ctx)
    # Add the continuation suffix size (worst-case)
    continuation_overhead = len(_CONTINUATION_SYSTEM) + 100  # format placeholders
    fixed_tokens = _estimate_tokens(system + fixed_prompt) + _estimate_tokens(" " * continuation_overhead)

    # 75% of the model's max input tokens, minus the fixed overhead
    budget = int(max_input_tokens * 0.75) - fixed_tokens
    if budget <= 0:
        # Context section alone is enormous — can't split usefully,
        # just return all refs in one chunk and let the LLM do its best.
        return [[(i + 1, r) for i, r in enumerate(refs)]]

    # Greedily pack refs into chunks
    chunks: list[list[tuple[int, StoryReference]]] = []
    current_chunk: list[tuple[int, StoryReference]] = []
    current_tokens = 0

    for i, ref in enumerate(refs):
        # Estimate this ref's contribution using _fmt_ref-like formatting
        ref_text = _estimate_ref_text(i + 1, ref)
        ref_tokens = _estimate_tokens(ref_text)

        if current_chunk and (current_tokens + ref_tokens) > budget:
            # Start a new chunk
            chunks.append(current_chunk)
            current_chunk = [(i + 1, ref)]
            current_tokens = ref_tokens
        else:
            current_chunk.append((i + 1, ref))
            current_tokens += ref_tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _estimate_ref_text(idx: int, ref: StoryReference) -> str:
    """Approximate the prompt text a single ref contributes."""
    parts: list[str] = []
    anchor = ref.get("file") or ref.get("stepTitle") or ref.get("why") or f"event {idx}"
    parts.append(f"{idx}. **{anchor}**")
    if ref.get("why"):
        parts.append(f" — {ref['why']}")
    if ref.get("stepIntent"):
        parts.append(f"   Intent: {ref['stepIntent']}")
    if ref.get("editDetails"):
        for ed in ref["editDetails"]:
            if ed.get("why"):
                parts.append(f"   • {ed.get('title', 'edit')}: {ed['why']}")
    if ref.get("snippet"):
        parts.append("```")
        parts.append(ref["snippet"])
        parts.append("```")
    return "\n".join(parts)


def _build_prompt_for_chunk(
    chunk: list[tuple[int, StoryReference]],
    ctx: StoryContext,
    *,
    total_refs: int,
) -> str:
    """Build a prompt for a subset of refs, keeping their global indices.

    The LLM sees ``[[N]]`` with the *original* numbering so parsed blocks
    map back to the global refs list without re-indexing.
    """
    parts: list[str] = []

    job = ctx.get("job", {})
    parts.append("## SESSION CONTEXT")
    parts.append(f"Title: {job.get('title', 'Untitled')}")
    parts.append(f"Task: {_truncate(job.get('prompt') or job.get('description', ''), 400)}")
    telem = ctx.get("telemetry", {})
    if telem:
        dur = round((telem.get("duration_ms") or 0) / 60000, 1)
        parts.append(f"Duration: {dur} min, {telem.get('tool_call_count', 0)} tool calls")
        fails = telem.get("tool_failure_count", 0) or 0
        retries = telem.get("retry_count", 0) or 0
        if fails or retries:
            parts.append(f"Issues: {fails} failures, {retries} retries")

    # Trail beats — same for every chunk
    beats = ctx.get("trail_beats", [])
    if beats:
        parts.append("\n## AGENT JOURNEY (key moments, chronological)")
        for b in beats:
            kind = b.get("kind", "")
            intent = b.get("intent", "")
            line = f"  [{kind.upper()}] {intent}"
            if b.get("rationale"):
                line += f"\n    Rationale: {b['rationale']}"
            if b.get("outcome"):
                line += f"\n    Outcome: {b['outcome']}"
            if kind == "backtrack" and b.get("supersedes"):
                line += " (reverses earlier approach)"
            parts.append(line)

    approvals = ctx.get("approvals", [])
    if approvals:
        parts.append("\n## DECISION POINTS")
        for a in approvals:
            line = f"  - {a.get('description', '')} → {a.get('resolution', 'pending')}"
            if a.get("proposed_action"):
                line += f"\n    Proposed: {a['proposed_action']}"
            parts.append(line)

    # Subset of changes — use global indices
    first_idx = chunk[0][0]
    last_idx = chunk[-1][0]
    has_files = any(ref.get("file") for _, ref in chunk)
    section_label = "CHANGES" if has_files else "SESSION EVENTS"
    parts.append(f"\n## {section_label} ({len(chunk)} of {total_refs}, items {first_idx}-{last_idx}, chronological)")

    def _fmt_ref(idx: int, ref: StoryReference) -> list[str]:
        lines: list[str] = []
        anchor = ref.get("file") or ref.get("stepTitle") or ref.get("why") or f"event {idx}"
        line = f"{idx}. **{anchor}**"
        if ref.get("file") and ref.get("stepTitle"):
            line = f"{idx}. **{ref['file']}** (step {ref.get('stepNumber', '?')}: {ref['stepTitle']})"
        if ref.get("isRetry"):
            line += " [RETRY]"
        if ref.get("errorKind"):
            line += f" [error: {ref['errorKind']}]"
        if ref.get("why") and ref.get("why") != anchor:
            line += f" — {ref['why']}"
        if ref.get("editCount") and ref["editCount"] > 1:
            line += f" [{ref['editCount']} edits]"
        lines.append(line)
        if ref.get("stepIntent"):
            lines.append(f"   Intent: {ref['stepIntent']}")
        if ref.get("editDetails"):
            for ed in ref["editDetails"]:
                if ed.get("why"):
                    lines.append(f"   • {ed.get('title', 'edit')}: {ed['why']}")
        if ref.get("snippet"):
            lines.append("```")
            lines.append(ref["snippet"])
            lines.append("```")
        return lines

    current_activity: str | None = None
    for global_idx, ref in chunk:
        label = ref.get("activityLabel", "") or ""
        if label != (current_activity or ""):
            if label:
                parts.append(f"\n### Activity: {label}")
            elif current_activity:
                parts.append("\n### Other changes")
            current_activity = label or None
        parts.extend(_fmt_ref(global_idx, ref))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Story cache invalidation
# ---------------------------------------------------------------------------


async def invalidate_story_cache_for_jobs(
    session_factory: Any,
    job_ids: set[str],
) -> None:
    """NULL story cache columns for jobs whose trail data has changed.

    Only affects jobs in review/completed state that currently have cached
    stories.  The drain loop will regenerate them on next sweep.
    """
    if not job_ids:
        return

    from sqlalchemy import text

    # Build positional placeholders for IN clause (SQLite-safe)
    placeholders = ", ".join(f":id_{i}" for i in range(len(job_ids)))
    params = {f"id_{i}": jid for i, jid in enumerate(job_ids)}

    async with session_factory() as session:
        result = await session.execute(
            text(
                f"UPDATE jobs "  # noqa: S608
                f"SET story_text = NULL "
                f"WHERE id IN ({placeholders}) "
                f"AND state IN ('review', 'completed') "
                f"AND story_text IS NOT NULL"
            ),
            params,
        )
        await session.commit()
        rowcount = int(getattr(result, "rowcount", 0) or 0)
        if rowcount:
            log.info("story_cache_invalidated", count=rowcount, job_ids=sorted(job_ids))


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StoryService:
    """Generates and caches structured code-review stories for jobs."""

    def __init__(
        self,
        completer: Completable,
        session_factory: Any | None = None,
        model_pricing: Any | None = None,
        git_service: Any | None = None,
    ) -> None:
        self._completer = completer
        self._session_factory = session_factory
        self._model_pricing = model_pricing
        self._git_service = git_service
        self._gen_locks: dict[str, asyncio.Lock] = {}

    async def get_or_generate(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> dict[str, Any] | None:
        """Return cached story blocks, or generate and cache them."""
        from sqlalchemy import text

        col = _STORY_COLUMN

        # Check cache
        row = await session.execute(
            text(f"SELECT {col} FROM jobs WHERE id = :jid"),  # noqa: S608
            {"jid": job_id},
        )
        cached = row.scalar_one_or_none()
        if cached:
            try:
                result = cast("dict[str, Any]", json.loads(cached))
                result["_from_cache"] = True
                # Enrich cached stories with diffs if missing
                await self._ensure_diffs(session, job_id, result, col)
                return result
            except (json.JSONDecodeError, TypeError):
                log.debug("story_cache_decode_failed", job_id=job_id)  # stale plain-text → regenerate

        # Serialize generation per job to avoid duplicate LLM calls.
        lock_key = job_id
        lock = self._gen_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            # Re-check cache — another coroutine may have populated it.
            row = await session.execute(
                text(f"SELECT {col} FROM jobs WHERE id = :jid"),  # noqa: S608
                {"jid": job_id},
            )
            cached = row.scalar_one_or_none()
            if cached:
                try:
                    result = cast("dict[str, Any]", json.loads(cached))
                    result["_from_cache"] = True
                    await self._ensure_diffs(session, job_id, result, col)
                    return result
                except (json.JSONDecodeError, TypeError):
                    log.debug("story_cache_parse_failed", job_id=job_id)
                    pass
            try:
                return await self._generate(session, job_id)
            finally:
                self._gen_locks.pop(lock_key, None)

    async def regenerate(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> dict[str, Any] | None:
        """Force regeneration, ignoring cache."""
        from sqlalchemy import text

        col = _STORY_COLUMN

        # Acquire the same lock as get_or_generate to prevent races (#3)
        lock_key = job_id
        lock = self._gen_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            await session.execute(
                text(f"UPDATE jobs SET {col} = NULL WHERE id = :jid"),  # noqa: S608
                {"jid": job_id},
            )
            # Don't commit the NULL separately — _generate will commit
            # with the new value, or we commit below on failure (#14)
            try:
                return await self._generate(session, job_id)
            except Exception:
                await session.commit()  # persist the NULL on failure
                raise
            finally:
                self._gen_locks.pop(lock_key, None)

    async def _ensure_diffs(
        self,
        session: AsyncSession,
        job_id: str,
        payload: dict[str, Any],
        col: str,
    ) -> None:
        """Enrich a cached story payload with git diffs if any refs are missing snippets."""
        from sqlalchemy import text

        blocks = payload.get("blocks", [])
        refs = [b for b in blocks if b.get("type") == "reference"]
        if not refs or all(r.get("snippet") for r in refs):
            return  # all refs already have snippets (or no refs)

        job_result = await session.execute(
            text("SELECT worktree_path, base_ref FROM jobs WHERE id = :jid"),
            {"jid": job_id},
        )
        job_row = job_result.mappings().first()
        if not job_row:
            return

        before = sum(1 for r in refs if r.get("snippet"))
        await self._enrich_refs_with_diffs(refs, dict(job_row))
        after = sum(1 for r in refs if r.get("snippet"))
        if after > before:
            log.info("story_diffs_enriched", job_id=job_id, added=after - before)

        # If any snippets were added, persist the updated cache
        if after > before:
            # Remove transient keys before caching
            cache_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
            await session.execute(
                text(f"UPDATE jobs SET {col} = :story WHERE id = :jid"),  # noqa: S608
                {"jid": job_id, "story": json.dumps(cache_payload)},
            )
            await session.commit()

    async def _enrich_refs_with_diffs(
        self,
        refs: list[StoryBlock],
        job_row: dict[str, Any],
    ) -> None:
        """Populate ``snippet`` on refs that have a file but no snippet.

        Uses ``git diff base_ref -- <file>`` in the worktree to fetch
        the actual code diff for each referenced file.
        """
        if not self._git_service:
            return
        wt = job_row.get("worktree_path")
        base = job_row.get("base_ref")
        if not wt or not base:
            return

        # Collect unique files that need diffs — only from file-change refs,
        # not explores/verifies that merely touched or read the file.
        files_needing_diff: set[str] = set()
        for ref in refs:
            if ref.get("file") and not ref.get("snippet") and ref.get("isFileChange"):
                files_needing_diff.add(ref["file"])

        if not files_needing_diff:
            return

        # Fetch per-file diffs
        file_diffs: dict[str, str] = {}
        for filepath in files_needing_diff:
            try:
                raw = await self._git_service.run_git(
                    "diff",
                    f"{base}...HEAD",
                    "--",
                    filepath,
                    cwd=wt,
                )
                if raw and raw.strip():
                    file_diffs[filepath] = raw.strip()
            except Exception:
                log.debug("story_file_diff_failed", file=filepath, exc_info=True)

        # Apply diffs only to file-change refs
        for ref in refs:
            f = ref.get("file", "")
            if f and not ref.get("snippet") and ref.get("isFileChange") and f in file_diffs:
                ref["snippet"] = file_diffs[f]
                # Determine created vs modified from the diff header
                if "new file mode" in file_diffs[f]:
                    ref["action"] = "created"

    async def _generate(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> dict[str, Any] | None:
        from sqlalchemy import text

        # Fetch job row once and share with sub-queries (#8)
        job_result = await session.execute(
            text(
                "SELECT id, title, description, prompt, state, model, "
                "repo, worktree_path, base_ref "
                "FROM jobs WHERE id = :jid"
            ),
            {"jid": job_id},
        )
        job_mapping = job_result.mappings().first()
        if not job_mapping:
            return None
        job_row = dict(job_mapping)

        refs = await _build_references(session, job_id)

        ctx = await _collect_context(session, job_id, job_row=job_row)
        if not ctx:
            return None

        # Need at least some trail data or context to generate a story
        beats = ctx.get("trail_beats", [])
        if not refs and not beats:
            return None  # no trail data at all

        # Guard against write-summary staleness — if there are write sub-nodes
        # still missing their write_summary, skip caching so the next
        # request can pick up the complete data.
        unsummarized = await session.execute(
            text("SELECT COUNT(*) FROM trail_nodes WHERE job_id = :jid AND kind = 'write' AND write_summary IS NULL"),
            {"jid": job_id},
        )
        pending_motivations = unsummarized.scalar() or 0

        # Guard against trail enrichment staleness — trail beats need
        # enrichment to be complete before the narrative is meaningful.
        unenriched = await session.execute(
            text("SELECT COUNT(*) FROM trail_nodes WHERE job_id = :jid AND enrichment = 'pending'"),
            {"jid": job_id},
        )
        pending_enrichment = unenriched.scalar() or 0

        system = _STORY_SYSTEM

        # Determine whether multi-pass is needed (#7)
        # Look up the model's context window from pricing service.
        model_name = getattr(self._completer, "model", None) or ""
        max_input_tokens = (
            self._model_pricing.get_max_input_tokens(model_name) if self._model_pricing and model_name else None
        )

        blocks = await self._generate_passes(
            refs=refs,
            ctx=ctx,
            system=system,
            max_input_tokens=max_input_tokens,
            job_id=job_id,
        )

        if not blocks:
            log.warning("story_generate_empty_blocks", job_id=job_id, ref_count=len(refs), beat_count=len(beats))
            return None

        # Enrich reference blocks with actual git diffs for rendering.
        # Done AFTER generation so diffs don't bloat the LLM prompt.
        await self._enrich_refs_with_diffs(
            [b for b in blocks if b.get("type") == "reference"],
            job_row,
        )

        has_decisions = any(b.get("kind") == "decide" for b in beats)
        has_backtracks = any(b.get("kind") == "backtrack" for b in beats)
        payload: dict[str, Any] = {
            "blocks": blocks,
            "beat_count": len(beats),
            "has_decisions": has_decisions,
            "has_backtracks": has_backtracks,
        }

        # Only cache when all enrichment is ready — otherwise the next
        # request will regenerate with richer trail and motivation data.
        if pending_motivations == 0 and pending_enrichment == 0:
            col = _STORY_COLUMN
            await session.execute(
                text(f"UPDATE jobs SET {col} = :story WHERE id = :jid"),  # noqa: S608
                {"jid": job_id, "story": json.dumps(payload)},
            )
            await session.commit()
        else:
            log.info(
                "story_skip_cache",
                job_id=job_id,
                pending_motivations=pending_motivations,
                pending_enrichment=pending_enrichment,
            )

        return payload

    # ------------------------------------------------------------------
    # Background drain loop — pre-generates stories so the GET is instant
    # ------------------------------------------------------------------

    _DRAIN_INTERVAL = 8.0  # seconds between sweeps

    async def drain_loop(self) -> None:
        """Run forever, generating stories for jobs in review state.

        Periodically scans for jobs that entered review but don't yet have
        a cached story, then generates them in the background.  This ensures
        the frontend GET is always a simple DB read.
        """
        from sqlalchemy import text

        if not self._session_factory:
            log.warning("story_drain_loop_no_session_factory")
            return

        while True:
            try:
                async with self._session_factory() as session:
                    # Find jobs in review/completed state missing cached story
                    rows = await session.execute(
                        text(
                            "SELECT id FROM jobs WHERE state IN ('review', 'completed') AND "
                            "  story_text IS NULL "
                            "AND EXISTS (SELECT 1 FROM trail_nodes WHERE trail_nodes.job_id = jobs.id)"
                            " LIMIT 4"
                        )
                    )
                    job_ids = [r[0] for r in rows.fetchall()]

                for job_id in job_ids:
                    try:
                        async with self._session_factory() as session:
                            await self.get_or_generate(session, job_id)
                    except Exception:
                        log.warning(
                            "story_drain_generate_failed",
                            job_id=job_id,
                            exc_info=True,
                        )

                if job_ids:
                    log.info("story_drain_batch", count=len(job_ids))
            except Exception:
                log.warning("story_drain_loop_error", exc_info=True)

            await asyncio.sleep(self._DRAIN_INTERVAL)

    async def _generate_passes(
        self,
        *,
        refs: list[StoryReference],
        ctx: StoryContext,
        system: str,
        max_input_tokens: int | None,
        job_id: str,
    ) -> list[StoryBlock]:
        """Generate story blocks, splitting into multiple LLM passes if the
        prompt would exceed the model's context window.

        Never truncates any information — every ref is included in exactly
        one pass.  When the model's context window is unknown, falls back to
        single-pass (optimistic).
        """
        # Try single-pass first — check if it fits
        user_prompt = _build_prompt(refs, ctx)

        full_prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user_prompt}"
        estimated_tokens = _estimate_tokens(full_prompt)

        # If we don't know the model's limit, or it fits within 75% of the
        # context window, use a single pass.
        needs_multipass = (
            max_input_tokens is not None
            and estimated_tokens > int(max_input_tokens * 0.75)
            and len(refs) > 1  # can't split a single ref
        )

        if not needs_multipass:
            return await self._single_pass(full_prompt, refs, job_id=job_id)

        assert max_input_tokens is not None  # narrowed above
        log.info(
            "story_multipass_triggered",
            job_id=job_id,
            estimated_tokens=estimated_tokens,
            max_input_tokens=max_input_tokens,
            ref_count=len(refs),
        )

        chunks = _split_refs_into_chunks(
            refs,
            ctx,
            system,
            max_input_tokens=max_input_tokens,
        )

        all_blocks: list[StoryBlock] = []
        for pass_num, chunk in enumerate(chunks, 1):
            chunk_prompt = _build_prompt_for_chunk(chunk, ctx, total_refs=len(refs))

            chunk_system = system
            if pass_num > 1:
                prev_chunk = chunks[pass_num - 2]
                chunk_system += _CONTINUATION_SYSTEM.format(
                    pass_num=pass_num,
                    total_passes=len(chunks),
                    prev_last=prev_chunk[-1][0],
                    first=chunk[0][0],
                    last=chunk[-1][0],
                )

            pass_prompt = f"SYSTEM:\n{chunk_system}\n\nUSER:\n{chunk_prompt}"
            pass_blocks = await self._single_pass(pass_prompt, refs, job_id=job_id)
            all_blocks.extend(pass_blocks)

        return all_blocks

    async def _single_pass(
        self,
        full_prompt: str,
        refs: list[StoryReference],
        *,
        job_id: str,
    ) -> list[StoryBlock]:
        """Execute one LLM call and parse the result into blocks."""
        try:
            result = await self._completer.complete(full_prompt)
            raw = result.strip() if isinstance(result, str) else str(result).strip()
        except (httpx.HTTPError, OSError, ValueError):
            log.warning("story_generation_llm_failed", job_id=job_id, exc_info=True)
            return []
        except Exception:
            log.warning("story_generation_llm_unexpected", job_id=job_id, exc_info=True)
            return []

        if not raw:
            log.warning("story_generation_empty_raw", job_id=job_id, prompt_len=len(full_prompt))
            return []

        blocks = _parse_blocks(raw, refs)
        if not blocks:
            log.warning("story_generation_parse_empty", job_id=job_id, raw_len=len(raw), raw_preview=raw[:200])
        return blocks
