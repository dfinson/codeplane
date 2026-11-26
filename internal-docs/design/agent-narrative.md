# Agent narrative and context handoff

CodePlane's Story explains the agent's work using its transcript, audit trail,
motivations, and Git changes. It is separate from the Changes view, which shows
the actual diff. A narrative is an explanation of recorded work, not permission
to merge or proof that verification succeeded.

## Evidence and generation

`backend/services/story/service.py` builds references from job and step data,
file reads and writes, telemetry motivations, and enriched trail decisions.
The narrator groups those references into headings, narrative, references, and
turning-point beats. References can carry real Git diff snippets. Large inputs
use multiple generation passes rather than silently dropping references.

The `jobs.story_text` cache serves the `/api/jobs/{job_id}/story` endpoint.
Pending enrichment can defer generation. Trail changes invalidate affected
cached stories. Regeneration is explicit, and the UI presents pending, empty,
and failed states rather than a verdict unsupported by evidence.

Retain the causal connections that help a reviewer understand the work:
investigation before an edit, a rejected approach, a failed operation followed
by recovery, a changed decision, and a session boundary. Do not invent motives,
test outcomes, or certainty from activity alone.

## Scout brief

Before primary execution, the Scout uses the configured utility model to read
and search the job worktree. Its read-only boundary is enforced by the SDK
restrictions and permission callbacks. The primary session keeps its selected
model and receives the exact Scout brief through `memory_context`.
Copilot exposes only read/search tools to the Scout. Narration accompanying a
tool request or followed by tool activity is not a finished brief. If the stream ends there without a final
response, curation fails and the primary continues without a fabricated handoff.

Scout entries and results are stored as secondary-session data. A
`context.handoff` event records the brief transferred from the preflight session.
The Activity view and snapshot hydration preserve that evidence across reloads.
Cancellation during scouting must prevent the primary session from starting.
Scout history and actual recorded costs remain part of job history.

## Review experience

Changes retains file navigation, step filters, unified/split diffs, large-file
loading, write/retry churn, intent annotations, and questions about selected
changes. Story supplies the complementary narrative. Neither surface substitutes
for operator approval, the actual diff, or execution evidence.

The independent narrative mockups remain in
`mockups/agent-narrative-mockups-v3.html` and the `mockups/narrative-*.png`
gallery. They illustrate presentation ideas, not additional implemented APIs.
