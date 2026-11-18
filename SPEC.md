# CodePlane — Product Specification

---

## Runtime admission and evidence invariants

### Standalone interoperability transition

`GET /api/integrations/profiles` exposes the 32 selections from approved
specification `9941bd11323740358f158baf953083d192873f03b610708d9fad10934aee29c5`.
Explorer IDs do not rename the managed `copilot`/`claude` adapters or the 24
native mapping IDs. Each operation separately reports declared support,
implementation, dependency readiness, local fixture qualification and missing
capabilities. Global admission eligibility is not binding enablement or live
control readiness. Metadata discovery constructs no SDK clients, and an
unimplemented selected operation is not advertised as runnable.

The additional `otlp_json` observation format accepts supplied span dictionaries
through historical records. Standard exporters use
`POST /api/integrations/{bindingId}/traces` with OTLP HTTP/JSON and
`x-codeplane-ingest-token`. An operator issues/replaces this single-purpose
credential at `POST /api/integrations/{bindingId}/ingest-credential` with explicit
`enabled: true`, or revokes it with `DELETE` on that route. Only a digest is
stored. Its immutable source/Project/Job scope and generation are checked again
inside the atomic admission transaction. The token grants no event-read,
permission-consumption, steering, MCP or operator authority. Direct-local
operator trust is unchanged, but the trace route always requires its scoped
credential. Copilot-only admission rejects this live intake while explicit
historical record import remains available.

Exporters supply no CodePlane cursor. The server validates the entire request,
deduplicates full-content span observations using durable source-scoped receipts,
and advances the existing source/projection CAS atomically. Exact and overlapping
retries do not reprocess accepted spans. Changed observations of the same native
span remain distinct evidence. Concurrent conflicts return retryable HTTP 503,
not an execution retry. JSON bodies remain bounded to 1 MiB and 1,000 spans.
HTTP/protobuf uses the optional `opentelemetry-proto==1.45.2` and
`protobuf==7.35.0` codec, independent of exporter packages. An absent or
unsupported codec returns explicit unavailability, without loading a client.
Install `codeplane[otlp-ingest]` to enable that codec; the default core omits it.
Binary requests use the same credentials, limits and durable span receipts as
JSON, including deduplication across equivalent wire encodings.
Metadata-only observations retain missing usage and arguments as unknown.

Trusted native acquisition can supply a typed `AcquisitionCheckpoint` to
`ObservationRecords.append`. Source identity, expected checkpoint generation,
transport state and receipt provenance commit with the same accepted batch.
Ordinary record calls cannot erase acquired-source state, and an acquisition
cannot silently adopt old untracked progress. A no-change poll does not advance
its durable cursor or manufacture source termination.

AI Card registration uses the existing project-scoped `integration_identities`
table and creates no Job or execution binding. The installed AGT core 5.0.0
schema and signature verifier are supplemented by explicit DID/key, signed
validity-interval and independently configured service-endpoint checks.
Upstream signatures do not cover service endpoints. Signed capability names
remain claims, not action permissions or verified delegation attestations.
Removal disables retained identity evidence, and subsequent lookup rechecks
validity rather than trusting a cached verification.

OTLP revisions retain immutable canonical events but share the stable
binding/source-session/trace/span operation identity. Step and telemetry totals
count that observed operation once across batches and restarts. The telemetry
read projection retains explicit success, failure, unknown and conflicting
evidence. Unknown outcomes produce no success/failure display hint, are excluded
from known-outcome success rates, and yield a null rate when all outcomes are
unknown. Source-derived trace/session grouping gives model and raw spans local
metadata-only activity labels without fabricated messages or utility calls.
The existing job Tool Breakdown and fleet Tool Health retain unknown counts
separately from failures. They render neutral `Unknown` when no outcomes are
known. Mixed success rates use only known outcomes.
Every accepted-batch replay rechecks its scoped ingest credential, including
the interleaving where another request committed the batch first.

`POST /api/integrations/observations` registers a binding-owned history Job
without launching an SDK, creating a workspace or attaching runtime sidecars.
Repeated registration of the same binding/source returns the same Job. These
Jobs expose `observationOnly` and `integrationBindingId`, may have null `repo`
and `baseRef`, and cannot enter managed execution, workspace or instruction
paths. Artifact collection is `not_applicable`, not a fabricated success.

`POST /api/integrations/{bindingId}/records` accepts bounded, operator-authenticated
record batches with `sourceKey`, `startIndex` and the previously accepted
`prefixSha256`. Request bodies are limited to 1 MiB before JSON parsing, batches
to 1,000 records, and normalized output plus buffered state to 8 MiB.
New binding IDs are URL-safe path segments (ASCII letters, digits, `.`, `_`, `~`
and `-`, excluding `.`/`..`); external session IDs and source keys remain opaque.
`GET /api/integrations/{bindingId}/events` reads canonical history with a separate
storage cursor. These are operator import endpoints, not binding-scoped external
ingest credentials or execution admission.

Migration 0080 owns one versioned projection checkpoint per binding. Decoder
context, pairing, transcript steps and activities are shared across acquisition
channels for the registered source session. Projection CAS, per-source cursor,
canonical events and durable batch acknowledgment commit together. Retrying any
accepted batch returns its acknowledgment without publishing it again. Ambiguous
older per-source projection state requires explicit reconciliation, never an
automatic merge or reset. Downgrade refuses to discard retained projection state.

Observation grouping prefers explicit producer turns, then source-scoped native
request/run-operation identity. Otherwise assistant and metadata records remain
in one derived source-session segment. Genuine operator input can establish a
boundary. Source termination or `endSession: true` closes the segment without
claiming a managed execution outcome. An empty batch is valid only to declare
An error severity alone, including a mapped span emitted as `session.error`,
is not termination evidence and does not close the registered source. An empty batch is valid only to declare
that end explicitly. Acquisition occurrences keep distinct event identities
even when their text and record indexes match.
Authoritative operator messages are deduplicated by canonical event identity,
never equal text across sessions or attempts. The composer settles only its own
local placeholder when submission finishes; a receipt does not invent an
observed transcript event or remove a real echo.

`tool.call.failed` remains a distinct wire kind, pairs by source-qualified call
identity, and renders a failed tool outcome without terminating its Job or
agent step. Observed failure is not proof of execution for governance accrual.
Source control-looking records remain history and cannot dispatch control-plane
actions on live delivery or SSE replay. Observation events do not trigger legacy
sidecars or plan/title inference. Native usage remains retained rather than
being passed to legacy USD rollups without typed accounting qualification.

The standalone replacement is reviewed against product base
`871fc81af41fec13de756338aab2715839357af5` (tree
`ed0ec75798921cd0d907a3131c92f631244b2001`). TraceForge is an explicit
`codeplane[legacy-traceforge]` compatibility extra for version-1 native checkpoints
and historical re-enrichment, and remains installed for the legacy CI tests.
The default dependency graph omits it; this does not establish full default-core
qualification or retirement. Existing history is never discarded to complete migration.
Migration 0079 retains producer-supplied canonical `raw_event` through SQLite
and replay. Historical NULL remains unknown, never reconstructed, and downgrade
refuses to erase retained raw data. Event timestamps are stored as UTC instants.
Raw data remains excluded from metadata-only export.
Migration 0081 separates transcript-step timing provenance from canonical
transport ordering. Source projections use supplied source bounds across batches
and retain the last source clock for empty import-end requests. Unknown bounds
and duration remain NULL in step rows/read models. Existing timestamp bytes are
retained with `legacy_unrecorded` provenance, and live timing remains explicit
`live` timing. Downgrade refuses to erase recorded provenance or nullable bounds.

`services/adapters/contracts.py` separates observation, execution, blocking
permission interception and text-only completion. An observer does not acquire
launch or cancellation authority. Recipe capability requests must be a subset
of both Job policy and actual adapter support. An unsatisfied request fails
before side effects rather than silently reducing or broadening its grant.
The invocation boundary must enforce the resulting grant, not rely on prompts.
Recipe run/revision/step-instance/attempt correlation is additive and never
replaces transcript `step_id` or `turn_id`.

`services/action_policy/syntax.py` provides bounded Tree-sitter evidence using
the declared Bash/POSIX-shell or PowerShell dialect. It retains source bytes,
grammar node spans, conditional/deferred scopes and nested interpreter origins.
Unknown dialects, unresolved expansions, malformed syntax and resource limits
are incomplete evidence, not a safe-command verdict. Syntax structure alone
does not establish command semantics.

Managed events now use application-owned canonical models. Executions still
pinned to TraceForge cross an explicit serialization bridge rather than importing
its title/model runtime through the event type. SessionConfig's optional
`execution_capabilities` is enforced at provider pre-tool hooks and permission
callbacks. `None` retains the existing unrestricted-by-recipe behavior. Shell
capability alone does not authorize mutation or external access, and an
unproven arbitrary executable is not treated as a read.

Recipe attempts register their actual adapter/session with RuntimeService and
retain the returned exact execution token. Attempt controls never fall back to
the primary Job session. Their events carry explicit execution epoch and attempt
metadata. EventProcessor requires a separately registered attempt workspace and
partitions turn, pairing, activity, StepTracker and diff state. Closing an
attempt cannot close or auto-commit another attempt. Read-only observation
does not mutate the Git index while calculating a diff.

`open_attempt_execution` owns deferred SDK creation, exact-handle registration
and cleanup. With `SessionConfig.defer_start=True`, creation sends no initial
prompt. The caller dispatches the pinned prompt only after registration and
durable recipe admission. Registration failure or caller cancellation closes the
owned session. Job cancellation signals the primary task and fences every
attempt before awaiting any slow transport teardown.

`attach_attempt_policy(job_id, attempt_id, expected_execution)` attaches the
existing Job router and current policy to that exact provider session. It does
not register another budget or replace the primary session's workspace. The
adapter validates the attempt/epoch and uses its trusted SessionConfig workspace.
Cross-provider runners attach this policy before admitting their first prompt.
Pending permissions retain the session's ownership generation. Pause/cancellation,
session replacement or a newly attached execution token invalidates that generation.
Both router and hard-gate approvals recheck it before returning permission, even
when transport abort is still draining or the same native session ID is reused.
SDK permission callbacks and pre-tool hooks capture that generation before
awaiting capability parsing, so an old callback cannot adopt a replacement's
ownership when it reaches policy evaluation.
Strict dispatch publishes a durable admission receipt with the exact target
session/token and accepted/unconfirmed markers, not an observed operator input.
Receipts remain visible without feeding title, step or sidecar input processing.
Uncorrelated SDK observations are retained separately.
Attempt open/registration, contract/policy attachment and dispatch share the
same approval-scope guard. A positively bound sibling gate does not block an
independent attempt. Unscoped, stale or conflicting provenance remains Job-wide.
Recipe human ownership comes from its durable gate binding; SDK ownership requires
the persisted permission event and matching recorded execution. Tool arguments
cannot declare their own authority scope.

Internal `execution.contract.pinned` records bind an immutable implementation
selection to a durable application admission ID and the carried Job budget ID.
Recovery reads that original pin without minting one for unrecorded history.
Fresh continuation uses a new admission, not a fresh budget. Actual SDK handles,
live epochs and tokens are recorded separately and may change on recovery.
The trusted root catalog distinguishes implementations still available for old
pins from the selected contract for new admission. Conflicts or unavailable
pinned implementations require reconciliation rather than silent migration.

Non-recipe creation records an internal legacy admission declaration in the same
transaction as the Job. The explicit `recipe_admission` creation flag suppresses
that declaration for recipe-owned admission, including before its run row exists.
Declarations are not successful-start announcements. Immutable abandonment records
restore the previous selection without deleting either admission or contract pin.
Legacy continuation/reset consumers must keep declaration or abandonment atomic
with their corresponding Job reset or restoration.
With a contract catalog configured, legacy pin preparation runs at reserved
startup before policy or preflight. Restart recovery only reads the retained pin.
Fallback handoff generation waits until pin and policy setup, then carries that
contract into utility completion while retaining the old summary-session number.
Native in-process fallback retains the admission. A previous execution that is
still releasing the Job prevents a new legacy continuation from being declared.

Governed utility calls persist metadata-only request/completion records under
their exact admission contract. They retain requested/observed models and native
usage units, not prompt or response text. An unfinished request is not proof of
execution or completion. Completion-receipt failures remain explicit and retain
known usage plus its stable operation identity for accounting without retrying
the model call.

Attempt model reports are session-qualified `model.observed` events carrying
requested/actual identities and match status, with recipe, attempt and epoch
metadata. A mismatch keeps that scope on `model.downgraded`. Attempt reports
never replace the primary Job model/default or another attempt's verification.
Actual provider reports are retained for attributed attempts even when the SDK
selected the default model or the session has a preflight role. With no requested
model, requested identity and match status remain unknown, not synthetic matches.
Managed `metadata.run_id` is the real SDK session ID. Policy attachment adds
the exact registered `execution_token` for subsequent events, distinguishing
reused native IDs within an attempt/epoch. Earlier events keep an unknown token.

For attributed attempts, permission routing and savepoints use the trusted
session workspace, never the primary Job workspace. Adapter description and
dependency readiness are separate from execution admission. The managed
Copilot/Claude descriptors can be read while disabled without constructing SDK
clients. Presets remain per-action approval policy, not categorical bans on
write, shell or delegation capabilities.

Text-only completion rejects tool attempts and reports provider errors, empty
responses and output-limit failures explicitly. Returned typed usage distinguishes
Copilot native cost units from verified USD and retains present zero values.
Legacy numeric completion fields remain available during migration. The strict
`cp-action-v1` evidence schema and deterministic credential filters are local
primitives, not themselves permission authority or a claim of PII detection.

The standalone assessment service binds its exact source fields to the local
action digest, allows one provider attempt, and limits concurrency to two
globally and one per Job. At most 32 requests wait for admission for up to two
seconds. The 20-second decision deadline includes queueing and liveness checks.
Canceled provider cleanup retains its concurrency slot until it actually ends,
without delaying the caller's unavailable result. Redacted semantics, stale
bindings and invalid evidence cannot become automatic permission. A completed
utility call retains its known model and usage even when its assessment is
rejected for stale state or invalid output.

The AGT policy component uses the pinned core package's `PolicyEngine` with
explicit `priority_first_match` priorities and no evaluation-time rate limits.
Its standalone ledger uses separate storage with atomic source-qualified executed-call identity
and counters, marks missing call IDs unaccountable, and sums only verified USD.
The metadata-only export projection excludes raw prompts, arguments, results,
paths and rationale and keys external identities before any exporter queue.
These components are introduced ahead of runtime cutover and full integration
qualification; their presence is not a claim that TraceForge retirement is done.

`cpl governance-transition import-legacy` is explicit stopped-runtime maintenance
for an existing legacy governance database under the active CodePlane home. It
retains a read-only original backup, source digests and exact counter/processed-key
comparisons before a replacement writer can activate. Scoped grants keep their
original TF policy, timestamps and expiry. They do not become AGT waivers.
Conflicting, reserved or unmapped state requires reconciliation. Legacy spend
reader values remain estimated or unknown, never verified currency, and imported
aggregates do not fabricate completed-call receipts. Runtime ownership excludes
concurrent maintenance. This import command does not switch providers or complete
forward-preserving provider rollback, and never restores an old application
database over newer history, approvals or outcomes.

Released 0.1.5 format mappings, preprocessors and Markdown readers are retained
under `backend/compat/traceformats` with original source hashes and the upstream
license. New native event and fallback turn identities are scoped by stable
binding/Job ownership (`codeplane-native-v2`), so reused vendor session labels
do not collide. Explicit legacy identity mode reproduces retained v1 seeds
without rewriting history. The decoder rolls back its motivation/turn buffer
if a record fails. Configured
mapping directories override migrated user definitions, legacy user definitions
and bundled assets in that order. Inbound OTLP JSON is independent of exporter
packages and bounds raw input to 1 MiB/1000 spans, nesting to 32 levels and
10,000 nodes, and normalized expansion to 8 MiB. Fresh Copilot/Claude JSONL
watchers use the standalone decoder with the retained `codeplane-native-v1`
identity seed and explicit version-2 acquisition checkpoints. Physical records,
decoded outputs, emitted events and storage cursors remain separate counts.
Version-1 checkpoints retain their optional legacy decoder; absence of that
implementation reports readiness failure without converting or erasing history.
Complete format, acquisition and checkpoint-conversion qualification remain
separate gates.

### Recipe definitions and publication

Recipes describe configurable intra-Job work. They are distinct from the legacy
TaskLink chains and from StepTracker's transcript turns. `POST /api/recipes`
creates a draft only. Saving, validating, copying and publishing definitions
never creates a Job or dispatches a runner. Draft writes and publication use an
expected version, so a stale editor cannot overwrite another revision.

`POST /api/recipes/validate` returns actionable field paths and error codes.
The `/recipes` editor uses the same APIs for typed runner, capability, workspace,
binding, dependency and outcome-route fields. It can insert pinned published
blocks and copy published revisions into independent drafts. Invalid local JSON
and invalid graphs cannot be published; neither authoring nor publication runs a Job.
Publication rejects missing or mistyped bindings, unknown outcomes, unreachable
nodes, dependency/route cycles, recursive block composition, unsafe parallel
writers and invalid retry bounds. Outcome routes support fanout and explicit
dependency joins. Human gates name the proposed action and explicit decisions.
Retries count total attempts, cannot skip a human gate, and cannot automatically
retry an uncertain external effect.

Published revisions retain stable step IDs, runner settings, instructions,
typed inputs/outputs, workspace policies and capability requirements. Reusable
blocks reference published revision IDs. Publication retains the transitive
block snapshots and a canonical SHA-256 checksum. Later draft edits or block
publications do not rewrite those snapshots. Copying creates a new independent
draft, with no execution, approval or completion identity.

Recipe definition/revision lists are bounded to 100 records per page and use a
storage sequence cursor plus `highWater`. The bound freezes which persisted
revisions are included, not a fabricated historical view of mutable drafts.
Migration 0076 adds Job-owned run, step-instance, immutable attempt/result,
output-receipt, workspace-lease and review storage. Recipe gates reference
existing approval records. Existing Jobs and transcript steps are not backfilled
with invented recipe identities.

Attempt admission persists the exact typed inputs and dispatch identity before
runner start. Retries retain their earlier inputs, results and output checksums
and cannot exceed the published total-attempt bound. Writer and shell attempts
hold a canonical workspace lease. Uncertain effects retain that lease and
remain interrupted rather than entering a blind retry.

`POST /api/jobs` accepts an optional published `recipe` selection. The Job and
its pinned run commit together before background setup starts. Missing or
mistyped launch parameters create neither record. Recipe Jobs use the existing
Job queue and shared runner capacity, not a second scheduler. Their graph owns
Context, planning, verification and review steps, so legacy Scout/planning/
verification paths do not execute a second time. Command and human steps do not
create SDK sessions.

Before governed recipe invocation, trusted composition must configure the
actual available execution contracts. Fresh admission pins the selected
contract to the run; recovery only reads its existing pin and never backdates
one onto unrecorded history. The budget identity remains the Job across new
run IDs. A real agent handle is persisted, bound to its contract and attached
to the same Job policy before its initial prompt is submitted. Admission
receipts report accepted/unconfirmed delivery, not observed execution.

Successful dependency joins require their declared outcomes. An explicitly
degraded input can retain a known failed diagnostic receipt, its producing
attempt, original outcome and reason without treating it as successful output.
Human decisions bind the existing ApprovalService record to the exact input
checksum, action and next steps. Nonzero command status cannot become success
through a runner claim, and missing command capture remains interrupted.
Declared code reconciliation must retain each consumed writer commit in the
result's Git ancestry. Merely declaring `reconciles` on an exit-zero command
does not merge code or satisfy that contract.

Nested block instances are scoped to the exact wrapper attempt, such as
`checks@2/lint`. A block retry creates independent child instances rather than
inheriting completed children. Parameter handoffs retain the caller's exact
source receipt, including explicitly degraded diagnostic provenance, while
typed defaults have no fictional producing attempt.

An unfinished agent can resume within its existing attempt only after an
app-owned confirmed-safe stop receipt. Recovery checks the current execution
token, instance version, owning Job, cancellation state and workspace lease.
Delayed stop callbacks cannot interrupt a subsequently resumed execution.
An unconfirmed stop remains interrupted and retains its writer lease.
Transport teardown alone does not establish completed-effect certainty.
Writer, shell, delegation and external-access interruption stays uncertain
without a provider-owned effect receipt or explicit reconciliation. Read-only
recovery and positively recorded no-dispatch recovery do not mint new attempts.

Input snapshots use private Git refs and an alternate index, preserving HEAD,
the user's index and working files. Command capture can retain immutable,
checksummed artifact revisions instead of putting entire output streams into
the run record. A process that never started records that fact without inventing
an exit status or a passing check.
On Windows, a trusted isolated bootstrap enrolls itself in a per-command Job
Object before starting the exact user argv. Cleanup terminates that owned tree
and checks its active-process count even after the leader exits. It does not
reuse the tunnel service's global Job Object or infer tree termination from
an exited PID. Unknown startup or containment remains interrupted, retains
writer ownership and displays an unknown start status rather than a passing
check or positive no-dispatch receipt.

The compatibility graph builder keeps configured verification and self-review
as agent steps, not command checks. Plan First retains an initial plan and at
most five replans, each with a fresh approval of its exact plan and code inputs.
Agent turn limits are ceilings, not a prescribed number of repeated prompts.
A fulfilled output contract or explicit validated failure ends the attempt
after retaining the completed-turn receipt. Only incomplete output contracts
use remaining turns. Steps with no model-produced outputs, including code-ref-only
steps, do not require an invented empty JSON response instead of a natural-language
summary. Explicit structured failure still ends those steps unsuccessfully.
Recovery qualifies the last registered execution against its app-owned
completed-turn receipt before dispatch. A conclusive retained contract can be
finalized without opening another SDK session. New receipts retain the exact
completion text; older receipts may use only a matching execution token,
native session and epoch before that receipt's event boundary. A later
uncompleted message is never substituted for completed output.
Claude recipe executions additionally require a successful native terminal result,
including on recovery. Initialization/status messages and stream exhaustion do not
qualify as output or completion. A genuine result with no text may still fulfill
a contract with no model-produced outputs, including code-ref-only steps.
`POST /api/recipes/resolve-default` validates and retains a content-addressed
published revision of that actual configuration, without launching a Job.
Repeated resolution reuses the same revision. A configuration change creates
another pin and does not rewrite earlier definitions or runs. New Job selects
that default or an explicit published custom revision, validates typed launch
inputs and previews resolved steps, capabilities and routes before creation.
Custom graphs retain their own planning and gates rather than inheriting a
second legacy Plan First loop.

Migration 0078 retains multiple historical runs on the same Job and permits only
one unresolved run, including canceled runs with uncertain execution effects.
Explicit terminal continuation pins a new run with the instruction and current
code reference, retaining the selected revision unless a replacement was
explicitly selected. Old attempts, results and approvals are not copied. Run
numbers provide a deterministic current-run selector and bounded history cursor;
exact historical run and step identities remain addressable.

Job-owned inspection lives under `/api/jobs/{jobId}/recipe`: `current`, bounded
`runs` history, and exact `runs/{runId}/steps/{stepId}/attempts/{attemptId}` reads.
Each nested identity is checked against its owner, including historical runs.
Reads never recover, resume or dispatch work. Step pages contain at most 100
instances and attempt summaries at most 20 entries; full evidence is fetched
only for the selected attempt. Active step pages are live cursor projections,
not a claim of a frozen historical snapshot. Resolved run history retains its
recorded definitions, outcomes and gate receipts after a successor is admitted.
Keyboard selection preserves focus while attempt evidence refreshes. The
inspector's read-refresh control remains focusable while busy but cannot issue
another read. Job tab swipes remain usable without spatial animation when
reduced motion is requested.

Selected-attempt `/control` reads describe the actual owned message channel and
its disabled reason, not a model's presumed capabilities. `/messages` requires
the expected execution token and checks current Job/run/step/attempt ownership
again before dispatch. It never uses the Job's primary session as a fallback.
Conflict, failed and uncertain delivery remain distinct; accepted means a
durable admission receipt, not observed execution. Read refreshes do not resend,
resume or clear gates. Receipts capture the original canonical recipe scope,
native session and execution fence before provider awaits.

The Run tab retains bounded pending Job decisions and the existing permission
Approve/Reject controls. Positively bound human recipe gates link to their exact
input/output decision inspector instead. Unknown scope is shown as a Job-wide
blocker, never silently excluded or treated as an agent message.

Selected stop targets the exact owned runner task, not the Job or sibling
attempts. A safe read-only/no-dispatch stop requires explicit attempt Resume.
Uncertain command or writer effects retain ownership until an operator records
stopped-effect reconciliation with the exact current code and rationale.
Reconciliation does not mutate prior results, manufacture success, or grant a
retry. Whole-Job cancellation remains a separate action.

The shared inspector lists pinned predecessor outcomes and declared consumers
within the exact block invocation. Deep links carry run, step and optional
attempt identities. Declared consumer bindings are not represented as proof
that a selected historical attempt's output was consumed.
The selected attempt also exposes paged actual consuming input receipts,
including the consuming attempt, original receipt/checksum and both recorded
outcomes. These references prove input admission, not consumer completion.

`POST /api/jobs/{jobId}/reviews/prepare` seals the explicitly selected attempt
and event evidence plus an exact current-code snapshot without executing work.
Selection is bounded to 20 attempts and 100 canonical events. Structured
snapshot metadata is limited to 1 MiB and copied artifact evidence to 256 MiB.
Copies are checksummed before short metadata writes; original mutable artifact
IDs alone are not review evidence. Captures follow ordinary artifact retention,
while the recorded event keeps the selected structured evidence and checksums.
A canonical app-owned preparation receipt binds the snapshot artifact to its
original checksum, so replacing bytes and supplying a replacement checksum
cannot manufacture a new trusted capture.

`POST /api/jobs/{jobId}/reviews` records rationale with a stable request key,
snapshot identity and checksum after revalidating Job/run versions, code and
selected evidence. It is an append-only `review.recorded` event, not an
accept/reject state machine, agent message, approval, merge or tracker command.
An HTTP 409 rejection invalidates the selected capture and unlocks explicit
recapture with a new request key while preserving the operator's rationale.
Uncertain delivery retains the exact request and key for an idempotent retry.
Legacy Jobs have no fabricated recipe correlation. Review history uses bounded
storage cursors and a frozen high-water mark, not a reconstruction of unsaved
Story annotations. Changes and Story remain central, with the selected
generation, command, result and delivery evidence alongside them.

`POST /api/jobs/{jobId}/resume` accepts `expectedRunId` and `startRequestKey`
for an exact continuation receipt, with an optional explicit replacement
`recipe` selection. Replaying the same request cannot start a third run or
change its inputs. A conflicting request key is rejected. A completed older
run without a policy pin may still receive a fresh, explicitly selected
continuation pin; its old history remains unrecorded. Compatible agent
continuations reuse the actual native conversation, otherwise they receive
retained historical context without treating old outcomes as current approvals.

### Existing runtime guarantees

The normal lifespan now selects the local standalone catalogue and AGT policy
authority before recovery, using the existing canonical event store for admission
pins, the durable ApprovalService for decisions, and the separate governance
ledger/audit outbox. SDK permission and pre-tool boundaries carry immutable
session/attempt ownership and actual arguments into that authority. Optional
voice model loading is deferred to an explicit voice request.

This cutover is not full qualification: native-source compatibility paths,
historical budget/trust reconciliation, optional export and complete
interoperability/acceptance gates remain open. Deterministic activity labels
remain authoritative fallbacks. Closed activities may receive one durably claimed
text-only refinement, with one worker, queue 64, at most 12 bounded snippets,
4 KiB input, 512-byte output and an 8-second deadline. Failed or interrupted
claims are never retried during recovery.

Direct command argv is checked for mandatory Git hard gates before assessment.
Known executable wrappers and literal nested shell scripts retain that gate.
The authority also inspects executable AST nodes, including Git global options
and quoted executable paths, independently of model-provided mechanism labels.
Quoted prose in ordinary arguments is not treated as an executable command.
Committed command results join the canonical replay cursor and accrue exactly
once, including executed failures. A known non-start does not accrue; an unknown
start/effect remains explicitly unaccountable. Windows audit keys use user-bound
DPAPI encryption and a protected current-user/SYSTEM directory DACL. Native
Windows qualification is a separate required gate, not inferred from Linux tests.

`start_or_enqueue` admits or queues work without invoking sidecars. Startup is
reserved before the first database await, then `_start_reserved_job` configures
policy before built-in/custom sidecars or Scout can run. The contract-pin hook
belongs immediately before that policy setup. Queued session tokens remain
in-memory only and are removed on startup, cancellation or shutdown. Preflight,
Scout and managed execution share bounded runtime slots.

`runtime.copilot_only` defaults to true. This restricts CodePlane-managed
provider integrations, not model families served by Copilot or manually typed
terminal commands. Disabled integrations retain their code, configuration and
history. Explicit disabled execution fails before side effects, and disabled
in-flight managed jobs fail explicitly during recovery rather than changing SDK.

Native observation uses deterministic source identities and a validated source
checkpoint. CodePlane job identity owns persistence and SSE routing. Replayed
records do not repeat execution, permission decisions or durable projections.
Observation failure does not imply the external agent stopped.

Approval decisions remain durable across waiter races and server restart.
Explicit approval markers cannot be waived by bulk trust. Superseded SDK
permission invocations are retired with a reason, while independent tracker and
task-spawn approvals are retained.

Coordinated dispatch uses an exact live-execution token and refuses stale,
non-running or gated targets. It never implicitly resumes or unpauses a job.
Admission is distinct from execution, and uncertain delivery is not retried.

Project creation and Chat responses are fenced by their exact target and
operation generation. A completed request for an old form or conversation
cannot clear a new draft, change the selected Chat, or navigate a newer view.
Late clone/init results retain repository files and report registration
cleanup separately. Chat drafts remain scoped to their conversation, and
retrying a failed conversation read never resends a Chat or Job command.

Project Boards read persisted Project ownership rather than the browser's Job
page or a Project's current repository list. The four projected columns are
Ready / blocked, In Progress, Needs a decision and Failed. A linked Task and Job
share one work card; recipe steps are nested evidence, not extra work items.
Full-scope totals name task, linked-Job, standalone-Job and approval units.
Column, approval and tracker-delivery reads are bounded and paginated.
Each Board response reads its totals, columns and hydrated items from one
database snapshot. A column page also retains one snapshot while hydrating its
items; later page requests remain live reads, not a frozen multi-page view.
Pending approvals remain visible on completed or archived Jobs. Tracker
approval and delivery states remain distinct from code completion.

Task inspection returns its full stored prompt or explicitly labeled historical
source reference, predecessor delivery conditions and current Start eligibility
from the shared execution service. Inspecting never creates a Job, worktree or
approval. Start rechecks the same conditions rather than trusting a prior read.

Verification results describe completed, failed or interrupted verification
agent turns. Completion is not proof of test success or operator acceptance.
The existing review/completed distinction remains.

Remote mode enforces credentials on HTTP, SSE and WebSockets throughout startup
and steady state, including connector traffic arriving from loopback. Explicit
passwords are preserved. Access-only mode requires an explicit selection and
per-request verified JWT. Preview transport never forwards control-plane
credentials or cookies and enforces the decoded response limit during reading.

Access-only detached restart uses the operator's existing cached Cloudflare
application JWT, privately retrieved before the adoption deadline begins.
Missing or unusable credentials fail before mutation, without initiating login.
A private inherited pipe carries a versioned, length-prefixed credential frame
of at most 64 KiB bound to request ID, server PID/creation time and exact HTTPS
origin. The helper verifies identity and authenticates a protected read on the
old loopback listener before adoption. Only a separate matching parent commit
permits pause/stop; parent death, EOF, invalid input or timeout before commit
aborts. Committed helpers survive parent exit. Credentials are confined to
memory and the pipe, restricted to old-listener job-list/pause requests, and
dropped before stop/replacement. No credential is stored in argv, environment,
files, logs or status. Replacement retains Access-only. This is single-use
transport of an ordinary JWT, not a restart-scoped server credential.

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Technology Architecture](#2-technology-architecture)
3. [Frontend](#3-frontend)
4. [Backend](#4-backend)
5. [Live Update Model (SSE)](#5-live-update-model-sse)
6. [Execution Runtime](#6-execution-runtime)
7. [Deployment Model](#7-deployment-model)
8. [Repository and Workspace Model](#8-repository-and-workspace-model)
9. [Voice Input and Transcription](#9-voice-input-and-transcription)
10. [Configuration Model](#10-configuration-model)
11. [Canonical Internal Event Model](#11-canonical-internal-event-model)
12. [Job States](#12-job-states)
13. [Execution Phases](#13-execution-phases)
14. [User Interface](#14-user-interface)
15. [Data Model](#15-data-model)
16. [Persistence](#16-persistence)
17. [REST API](#17-rest-api)
18. [Approval System](#18-approval-system)
19. [Diff Model](#19-diff-model)
20. [Observability](#20-observability)
21. [Security Model](#21-security-model)
22. [Engineering Constraints and Pitfalls](#22-engineering-constraints-and-pitfalls)
23. [Sequence Diagrams](#23-sequence-diagrams)
24. [Execution Strategy Model](#24-execution-strategy-model)
25. [Ross Review: Open Questions](#25-ross-review-open-questions)
26. [MCP Orchestration Server](#26-mcp-orchestration-server)

---

## 1. Product Overview

CodePlane is a control plane for running and supervising coding agents.

It allows an operator to launch automated coding tasks against real repositories and observe everything the agent does in real time.

The system provides visibility into execution progress, code changes, logs, artifacts, and agent reasoning so work can be reviewed and controlled as it happens.

Operators can intervene at any time by sending instructions, approving risky actions, canceling runs, or rerunning failed tasks.

The interface can be accessed locally or remotely through Dev Tunnels, allowing jobs to be monitored and controlled from another device such as a phone.

CodePlane turns autonomous coding agents into something observable, controllable, and safe to operate.

### Core Capabilities

| Capability | Description |
|---|---|
| Job orchestration | Launch coding tasks against local repositories |
| Live monitoring | Watch agent reasoning, logs, and code changes as they happen |
| Approval gating | Intercept and approve or reject risky actions before they execute |
| Operator intervention | Send messages, cancel, or rerun jobs at any time |
| Workspace isolation | Every job gets its own isolated worktree under `.codeplane-worktrees/` |
| Remote access | Dev Tunnels exposes the UI over HTTPS for phone/remote control |
| PWA & mobile install | Progressive Web App manifest enables install-to-home-screen on phones and tablets |
| Push notifications | Web Push alerts for approvals, completions, and failures — even with the browser closed |
| Job sharing | Generate read-only share links for password-free viewing within the existing access boundary |
| Port preview | Reverse-proxy localhost ports through the tunnel for previewing dev servers remotely |
| Voice input | Speak prompts, operator instructions, and terminal commands into the browser |
| Artifact inspection | Browse files, diffs, and produced outputs from every job |
| Integrated terminal | PTY-backed terminal sessions with optional AI agent assistance |

---

## 2. Technology Architecture

CodePlane is a two-tier application.

```
┌──────────────────────────────────────────────────┐
│                  Operator Browser                │
│          React + TypeScript Frontend             │
│   REST (commands/queries) + SSE (live events)   │
└────────────────────┬─────────────────────────────┘
                     │ HTTP / SSE
┌────────────────────▼─────────────────────────────┐
│             FastAPI Backend (Python)             │
│  REST API · SSE stream · Job orchestration       │
│  Git workspace mgmt · Agent adapter registry    │
│  Approval routing · Persistence coordinator     │
└──────┬──────────────┬──────────────┬─────────────┘
       │              │              │
  ┌────▼────┐   ┌─────▼─────┐  ┌───▼───────┐
  │ SQLite  │   │ Git repos │  │Agent SDKs │
  │   DB    │   │/worktrees │  │(pluggable)│
  └─────────┘   └───────────┘  └───────────┘
```

| Tier | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite |
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Persistence | SQLite (via SQLAlchemy) |
| Agent runtime | Pluggable SDK adapters behind `AgentAdapterInterface`; ships with Copilot SDK and Claude Agent SDK |
| Workspace isolation | Git worktrees |
| Voice transcription | faster-whisper |
| Remote access | Dev Tunnels or Cloudflare Tunnels (HTTPS) |
| State management | Zustand |
| UI primitives | Radix UI (headless) |
| Diff / code viewer | Monaco Editor (`@monaco-editor/react`) |
| Virtualization | `@tanstack/react-virtual` |
| Drag and drop | `@dnd-kit/core` + `@dnd-kit/sortable` |
| Markdown | `react-markdown` + `remark-gfm` |
| Syntax highlighting | Shiki (`@shikijs/rehype`) |
| File tree | `react-arborist` |
| Toasts | Sonner |
| Icons | Lucide React |
| API type generation | openapi-typescript (from FastAPI OpenAPI spec) |
| Package management | uv (Python), npm (Frontend) |

### 2.1 Frontend Serving Model

In production mode, the FastAPI backend serves the built frontend as static files from a `/static` mount point. The Vite dev server (`localhost:5173`) is used only during frontend development.

This means:

- Only one port is exposed in production (`8080`)
- The tunnel exposes a single port
- CORS is not needed in production (same origin)
- During development, CORS allows `http://localhost:5173`

---

## 3. Frontend

### 3.1 Responsibilities

- Operator console UI
- Job dashboards (Kanban / list)
- Job detail views
- Live execution monitoring
- Diff visualization
- Artifact inspection
- Workspace browsing
- Approval and operator controls
- Voice input capture

### 3.2 Communication Model

| Channel | Direction | Purpose |
|---|---|---|
| REST API | Client → Server | Commands and queries |
| SSE (`/api/events`) | Server → Client | Live runtime updates |

The frontend never polls for state. All live updates arrive via SSE. REST calls are used exclusively for actions (create job, send message, approve, cancel, etc.) and one-time data fetches.

### 3.3 Application State

Application state has a single source of truth managed by a Zustand store.

SSE events are processed centrally through a single event dispatcher that updates the store. Components subscribe to the store via selectors and never maintain their own copies of job state.

State slices:

| Slice | Contents |
|---|---|
| `jobs` | All job summaries keyed by job ID |
| `approvals` | Pending approval requests keyed by approval ID |
| `batchApprovals` | Pending action policy batch approvals |
| `logs` | Per-job log lines keyed by job ID |
| `transcript` | Per-job transcript entries keyed by job ID |
| `diffs` | Per-job diff file models keyed by job ID |
| `plans` | Per-job plan steps keyed by job ID |
| `timelines` | Per-job timeline entries keyed by job ID |
| `activityTimelines` | Per-job activity timeline (structured trail) |
| `stories` | Per-job narrative stories |
| `streamingMessages` | In-flight streaming message content |
| `streamingToolOutput` | In-flight streaming tool output |
| `streamingReasoning` | In-flight streaming reasoning content |
| `telemetryVersions` | Hydration version tracking for telemetry |
| `sdks` | Available SDK catalogue with models |
| `terminalSessions` | Active terminal sessions and drawer state |
| `connectionStatus` | SSE connection state (`connected`, `reconnecting`, `disconnected`) |
| `policySettingsVersion` | Policy config version for cache invalidation |
| `jobHeartbeats` | Last heartbeat timestamp per job |

An LRU eviction policy keeps at most 30 jobs' per-job data (logs, transcript, diffs, etc.) in memory. Stale entries are evicted when new jobs are hydrated.

### 3.4 Component Hierarchy

```
App
├── Router
│   ├── DashboardScreen
│   │   ├── KanbanBoard (desktop)
│   │   │   ├── KanbanColumn [Active]
│   │   │   ├── KanbanColumn [Sign-off]
│   │   │   ├── KanbanColumn [Failed]
│   │   │   └── KanbanColumn [History]
│   │   └── JobList (mobile)
│   ├── JobDetailScreen
│   │   ├── JobMetadataHeader
│   │   ├── ApprovalBanner
│   │   ├── TranscriptPanel
│   │   ├── LogsPanel
│   │   ├── DiffViewer
│   │   ├── WorkspaceBrowser
│   │   ├── ArtifactViewer
│   │   ├── ExecutionTimeline
│   │   └── TerminalPanel (inline pane, job-scoped)
│   │       └── AssistPanel (optional sidecar)
│   ├── JobCreationScreen
│   │   ├── RepoSelector
│   │   ├── PromptInput
│   │   ├── VoiceInputButton
│   │   └── AdvancedOptions
│   └── SettingsScreen
│       ├── RepoManager
│       │   ├── AddRepoForm
│       │   └── RepoList
│       ├── RepoDetailView
│       │   ├── RepoHeader
│       │   ├── MCPConfigTable
│       │   ├── RepoConfigPanel
│       │   └── RepoJobList
│       ├── GlobalConfigEditor
│       └── RepoConfigList
├── TerminalDrawer (global, persists across navigation)
│   ├── TerminalPanel (per tab)
│   └── AssistPanel (optional sidecar)
└── SSEProvider (global)
```

### 3.5 SSE Client

The SSE client lives in a singleton provider mounted at the app root.

Behavior:

- Connects to `/api/events` on mount
- Tracks the last received `event_id`
- On disconnect, reconnects automatically with `Last-Event-ID` header
- Dispatches each received event to the central store
- Exposes connection status to UI components

#### Reconnection Strategy

| Parameter | Value |
|---|---|
| Initial delay | 1 second |
| Backoff multiplier | 2x |
| Maximum delay | 30 seconds |
| Jitter | ±500 ms (random) |
| Maximum attempts | 20 |
| Fallback | Show persistent "Disconnected" banner with manual reconnect button |

Connection status is exposed as a Zustand slice with values: `connected`, `reconnecting`, `disconnected`. The UI renders a status indicator in the app header.

### 3.6 TypeScript Domain Models (Generated)

All frontend types are **generated** from the FastAPI OpenAPI schema. The Pydantic models in Section 4.6 are the single source of truth.

#### Code Generation

TypeScript types are generated using `openapi-typescript`:

```bash
npx openapi-typescript http://localhost:8080/openapi.json -o src/api/schema.d.ts
```

This runs as part of the frontend build pipeline:

```json
// package.json scripts
{
  "generate:api": "openapi-typescript http://localhost:8080/openapi.json -o src/api/schema.d.ts",
  "dev": "npm run generate:api && vite",
  "build": "npm run generate:api && tsc && vite build"
}
```

During CI or when the backend isn't running, the committed `schema.d.ts` is used as-is. Developers regenerate it after any Pydantic model change.

#### Convenience Type Aliases

The generated types use path-based access. A thin `src/api/types.ts` file re-exports them as friendly aliases:

```typescript
import type { components } from "./schema";

export type Job = components["schemas"]["JobResponse"];
export type JobState = Job["state"];
export type LogLine = components["schemas"]["LogLinePayload"];
export type TranscriptEntry = components["schemas"]["TranscriptPayload"];
export type ApprovalRequest = components["schemas"]["ApprovalResponse"];
export type DiffFile = components["schemas"]["DiffFileModel"];
export type DiffHunk = components["schemas"]["DiffHunkModel"];
export type DiffLine = components["schemas"]["DiffLineModel"];
export type Artifact = components["schemas"]["ArtifactResponse"];
export type ExecutionPhase = components["schemas"]["ExecutionPhase"];
export type WorkspaceEntry = components["schemas"]["WorkspaceEntry"];
```

All component code imports from `src/api/types.ts`, never from `schema.d.ts` directly.

### 3.7 Performance Guidelines

- Large log and transcript lists must use virtualized rendering (`@tanstack/react-virtual`)
- Diff viewer uses Monaco `DiffEditor`; large files are loaded on demand, not all at once
- Kanban board must not re-render all columns when a single job updates; use memoized selectors per column

---

## 4. Backend

### 4.1 Responsibilities

- REST API endpoints
- SSE event streaming
- Job orchestration
- Agent session lifecycle management
- Git workspace management
- Artifact collection
- Approval routing
- Runtime monitoring
- Persistence coordination
- Voice transcription

### 4.2 Module Structure

```
backend/
├── main.py                    # CLI entry point (Click)
├── app_factory.py             # FastAPI app creation and middleware
├── config.py                  # Configuration loading and dataclasses
├── cli.py                     # CLI command definitions (up, down, restart, setup, doctor, info, version)
├── di.py                      # Dependency injection container
├── lifespan.py                # Startup/shutdown lifecycle hooks
├── logging_config.py          # structlog configuration
├── validators.py              # Shared validation helpers
├── api/
│   ├── jobs.py                # Job CRUD and control endpoints
│   ├── events.py              # SSE streaming endpoint
│   ├── artifacts.py           # Artifact retrieval endpoints
│   ├── job_artifacts.py       # Job-scoped data: logs, diff, transcript, steps, timeline, story
│   ├── job_telemetry.py       # Per-job telemetry endpoint
│   ├── workspace.py           # File browsing endpoints
│   ├── approvals.py           # Approval resolution, messaging, trust, batch resolution
│   ├── voice.py               # Voice transcription endpoint
│   ├── health.py              # Health check and sidecar metrics
│   ├── settings.py            # Settings, repos, SDKs, platforms
│   ├── analytics.py           # Fleet-level cost and telemetry analytics (30+ endpoints)
│   ├── share.py               # Job sharing with read-only tokens
│   ├── preview.py             # Port preview reverse proxy
│   ├── memory.py              # Workspace memory CRUD
│   ├── terminal.py            # Terminal sessions and AI ask
│   ├── trail.py               # Agent audit trail endpoints
│   ├── notifications.py       # Web Push subscription endpoints
│   ├── hooks.py               # External integration webhooks
│   ├── utility_sessions.py    # Warm utility session management
│   ├── sidecar_templates.py   # Sidecar template CRUD
│   └── policy_settings.py     # Governance preset, config, USD ceilings, MCP servers
├── services/
│   ├── job_service.py         # Job lifecycle orchestration
│   ├── runtime/               # Long-running job execution (RuntimeService)
│   ├── git_service.py         # Git worktree and branch operations
│   ├── agent_adapter.py       # Agent adapter interface + shared utilities
│   ├── adapter_registry.py    # Lazy-caching adapter factory
│   ├── base_adapter.py        # Shared adapter base class
│   ├── copilot_adapter/       # Copilot SDK adapter
│   ├── claude_adapter/        # Claude Agent SDK adapter
│   ├── event_bus.py           # Internal async event bus
│   ├── sse_manager.py         # SSE connection management and broadcast
│   ├── approval_service.py    # Approval request persistence and routing
│   ├── artifact_service.py    # Artifact storage and retrieval
│   ├── diff_service.py        # Diff generation and parsing
│   ├── merge_service/         # Merge-back, PR creation, conflict handling
│   ├── action_policy/         # Action policy router over traceforge.governance
│   ├── permission_policy.py   # SPEC §18.2 hard-gate command detection
│   ├── platform_adapter.py    # Per-platform integration (GitHub, etc.)
│   ├── retention_service.py   # Artifact and worktree retention cleanup
│   ├── setup/                 # Interactive dependency setup
│   ├── summarization_service.py # Post-session LLM summarization
│   ├── naming_service.py      # LLM-generated job/branch naming
│   ├── telemetry.py           # OpenTelemetry instrumentation
│   ├── telemetry_query_service.py # Analytics query engine
│   ├── cost_attribution.py    # Per-file and per-dimension cost attribution
│   ├── latency_attribution.py # Latency breakdown analysis
│   ├── analytics_service.py   # Fleet analytics and observations
│   ├── statistical_analysis.py # Statistical anomaly detection
│   ├── ingest_service.py      # Event ingestion and telemetry recording
│   ├── event_processor.py     # SessionEvent processing pipeline
│   ├── event_enricher.py      # Event enrichment (tool classification, etc.)
│   ├── conversation_ledger.py # Transcript deduplication and ordering
│   ├── steps/                 # Plan step tracking and inference
│   ├── trail/                 # Agent audit trail (intent graph)
│   ├── story/                 # Narrative story generation
│   ├── sidecar/               # Sidecar session management (arbiter, planner, enricher)
│   ├── memory/                # Workspace memory extraction and compaction
│   ├── tool_classifier.py     # Tool action classification (activity dimensions)
│   ├── tool_formatters/       # Human-readable tool output formatting
│   ├── auth.py                # Authentication helpers (password, session cookies)
│   ├── cf_access.py           # Cloudflare Access JWT verification
│   ├── share_service.py       # Share token management
│   ├── push_service.py        # Web Push notification delivery
│   ├── vapid_keys.py          # VAPID key generation and persistence
│   ├── voice_service.py       # faster-whisper transcription
│   ├── terminal_service.py    # PTY session management
│   ├── tunnel_service.py      # Dev Tunnels / Cloudflare Tunnels lifecycle
│   ├── retry_tracker.py       # Agent retry detection and tracking
│   ├── snapshot_helpers.py    # Job state snapshot construction
│   ├── copilot_steer.py       # Copilot session steering prompts
│   ├── preflight_curator.py   # Read-only utility-model Scout
│   ├── lightweight_completer.py # Single-turn LLM completions
│   ├── narrator_completer.py  # Narrative generation LLM calls
│   └── parsing_utils.py       # Shared parsing helpers
├── models/
│   ├── db.py                  # SQLAlchemy ORM models
│   ├── domain.py              # Domain dataclasses, enums, state machine
│   ├── events.py              # Canonical event types and typed payloads
│   ├── api_schemas.py         # Pydantic request/response schemas
│   └── schemas/               # Additional schema definitions
├── persistence/
│   ├── database.py            # Database engine and session management
│   ├── repository.py          # Base repository pattern
│   └── ...                    # Domain-specific repositories
├── mcp/                       # MCP orchestration server
├── web/                       # Static file serving and HTML templates
├── templates/                 # HTML templates (login page, etc.)
└── tests/
    ├── unit/
    └── integration/
```

### 4.3 API Routes Must Not Contain Orchestration Logic

API route handlers are thin. They:

1. Validate and parse input
2. Delegate to a service
3. Return the result

No orchestration logic, no direct database access, and no git operations belong in route handlers.

### 4.4 Agent Adapter

The agent runtime is wrapped behind an interface so the system is not tightly coupled to any specific SDK. Multiple SDKs are supported simultaneously via the `AdapterRegistry`.

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum

class AgentSDK(StrEnum):
    """Supported agent SDK backends."""
    copilot = "copilot"
    claude = "claude"

@dataclass
class SessionConfig:
    workspace_path: str
    prompt: str
    mcp_servers: dict[str, MCPServerConfig]  # discovered from repo config files
    protected_paths: list[str]               # from per-repo config; used by permission policy
    preset: Preset = "supervised"             # autonomous | supervised | locked
    sdk: str = "copilot"                     # which SDK adapter to use

@dataclass
class MCPServerConfig:
    command: str
    args: list[str]
    env: dict[str, str] | None = None

from traceforge.types import SessionEvent

# SessionEvent.kind is an open string. CodePlane emits and preserves dotted
# TraceForge kinds such as "message.assistant" and "permission.requested".

class AgentAdapterInterface(ABC):

    @abstractmethod
    async def create_session(self, config: SessionConfig) -> str:
        """Create a session, return session_id."""

    @abstractmethod
    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        """Stream events from a running session."""

    @abstractmethod
    async def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message into a running session."""

    @abstractmethod
    async def abort_session(self, session_id: str) -> None:
        """Abort the current message processing. Session remains valid."""

    async def interrupt_session(self, session_id: str) -> None:
        """Interrupt the session (optional, default no-op)."""

    async def pause_tools(self, session_id: str) -> None:
        """Block tool execution temporarily."""

    async def resume_tools(self, session_id: str) -> None:
        """Resume tool execution after a pause."""

    async def complete(self, prompt: str) -> CompletionResult:
        """Non-agentic single-turn completion for utility tasks."""

    def set_execution_phase(self, job_id: str, phase: ExecutionPhase) -> None:
        """Set the current execution phase for cost analytics tracking."""
```

#### Adapter Registry

`AdapterRegistry` is a lazy-caching factory that creates and returns the appropriate adapter for a given `AgentSDK` value. Adapters are instantiated on first `get_adapter()` call and reused for subsequent requests with the same SDK.

```python
class AdapterRegistry:
    def get_adapter(self, sdk: AgentSDK | str) -> AgentAdapterInterface: ...
    @property
    def default_adapter(self) -> AgentAdapterInterface: ...
```

`RuntimeService` calls `registry.get_adapter(job.sdk)` before starting each job, so different jobs can use different SDKs concurrently.

#### Copilot Adapter

The `CopilotAdapter` wraps the Python Copilot SDK (`pip install github-copilot-sdk`, import as `from copilot import CopilotClient`). A `FakeAgentAdapter` is used in tests.

##### Copilot SDK Method Mapping

| Adapter method | SDK method | Notes |
|---|---|---|
| `create_session()` | `client.create_session(config)` | Returns `CopilotSession` |
| `stream_events()` | `session.on(handler)` | Callback-based; adapter bridges to async iterator (see below) |
| `send_message()` | `session.send(MessageOptions)` | Uses `mode="immediate"` for mid-session injection |
| `abort_session()` | `session.abort()` | Aborts current message; session stays alive |

##### Callback-to-Iterator Bridge

The Copilot SDK uses a callback-based API: `SessionHooks` (`on_pre_tool_use`, `on_post_tool_use`, `on_session_start`, etc.) and `on_permission_request` are registered at session creation. The adapter bridges these into the `AsyncIterator[SessionEvent]` pattern by:

1. Creating an `asyncio.Queue` per session
2. Registering an SDK callback via `session.on(handler)` that pushes `SessionEvent` items onto the queue
3. `stream_events()` yields from the queue until the session completes or the subprocess exits
4. On subprocess crash, the SDK raises `ProcessExitedError` on pending futures; the adapter catches this and emits an error `SessionEvent`

This keeps the rest of the system (strategies, runtime service, event bus) decoupled from SDK callback mechanics.

#### Claude Adapter

The `ClaudeAdapter` wraps the Claude Agent SDK (`pip install claude-code-sdk`, import as `import claude_code_sdk`). The SDK internally spawns the Claude Code CLI as a subprocess and provides a native async iterator API.

##### Claude SDK Method Mapping

| Adapter method | SDK method / pattern | Notes |
|---|---|---|
| `create_session()` | `ClaudeSDKClient(options)` | Creates a client; session state is managed by the client |
| `stream_events()` | `async for message in client.query()` | Native async iterator — no callback bridge needed |
| `send_message()` | `client.query(prompt)` | Starts a new conversational turn |
| `abort_session()` | `client.interrupt()` + `client.disconnect()` | Interrupts current turn, then disconnects |

##### Preset to SDK Mode Mapping

| CodePlane preset | Claude SDK mode | Behavior |
|---|---|---|
| `autonomous` | `bypassPermissions` | All tools auto-approved |
| `supervised` | `default` | `can_use_tool` callback routes to action policy engine |
| `locked` | `default` | `can_use_tool` callback routes to action policy engine (stricter governance profile) |

##### Message Iterator Pattern

Unlike Copilot's callback bridge, the Claude SDK yields messages natively as an async iterator. The adapter consumes messages in a background task that normalizes them into canonical TraceForge `SessionEvent` items:

- `AssistantMessage` with `TextBlock` → `message.assistant`
- `ToolUseBlock` → `tool.call.started` + start time tracking
- `ToolResultBlock` → `tool.call.completed` + telemetry
- `ResultMessage` → telemetry recording + `session.ended` or `session.error`
- `SystemMessage` → log event only

#### SDK-Model Compatibility

Each SDK only supports models from its provider ecosystem. Validation is performed at job creation time before any resources are allocated:

| SDK | Accepted model prefixes | Examples |
|---|---|---|
| `copilot` | _(any)_ | `gpt-4o`, `claude-sonnet-4-20250514`, `o1-preview` |
| `claude` | `claude-` | `claude-sonnet-4-20250514`, `claude-3-opus-20240229` |

If the user requests a model incompatible with the selected SDK (e.g., `gpt-4o` with `claude`), the API returns `400 Bad Request` with a descriptive error message. When no model is specified, the SDK uses its own default.

### 4.5 Session Config Resolution

The `RuntimeService` constructs a `SessionConfig` directly from the `Job` record and resolved config (global + per-repo). There is no intermediate aggregation object. The inputs are:

- `workspace_path` — from `Job.worktree_path` (set by `GitService` during workspace prep)
- `prompt` — from `Job.prompt`
- `mcp_servers` — discovered from the repo's MCP config files (see §10.2.1)
- `protected_paths` — from per-repo config, translated to SDK-native permission rules by the adapter

### 4.6 Pydantic API Schemas

All API request and response bodies are defined as Pydantic models in `models/api_schemas.py`. These models are the **single source of truth** for the API contract. FastAPI auto-generates an OpenAPI schema from them, and TypeScript types are generated from that schema (see Section 3.6).

All response models use camelCase serialization to match frontend conventions:

```python
from pydantic import BaseModel, Field, ConfigDict
from pydantic.alias_generators import to_camel
from typing import Literal
from datetime import datetime
from enum import Enum


class CamelModel(BaseModel):
    """Base model that serializes field names to camelCase."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- Jobs ---

class CreateJobRequest(CamelModel):
    repo: str
    prompt: str
    base_ref: str | None = None
    branch: str | None = None
    title: str | None = None
    description: str | None = None
    worktree_name: str | None = None
    preset: Preset | None = None              # autonomous | supervised | locked
    model: str | None = None                  # LLM model override
    sdk: str | None = None                    # copilot | claude
    verify: bool | None = None                # run verification pass after completion
    self_review: bool | None = None           # run self-review pass after completion
    max_turns: int | None = None              # max verification/review turns (1-10)
    verify_prompt: str | None = None          # custom verification prompt
    self_review_prompt: str | None = None     # custom self-review prompt
    enable_stall_detection: bool | None = None # enable stall detection sidecar
    enable_plan_tracking: bool | None = None  # enable plan step tracking
    session_token: str | None = None          # external session token for CLI mirroring

class CreateJobResponse(CamelModel):
    id: str
    state: JobState
    title: str | None = None
    branch: str | None = None
    worktree_path: str | None = None
    sdk: str = "copilot"
    created_at: datetime

class JobResponse(CamelModel):
    id: str
    repo: str
    prompt: str
    title: str | None = None
    description: str | None = None
    state: JobState
    base_ref: str
    worktree_path: str | None
    branch: str | None
    preset: Preset | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    pr_url: str | None = None
    merge_status: GitMergeOutcome | None = None
    resolution: Resolution | None = None
    archived_at: datetime | None = None
    failure_reason: str | None = None
    progress_headline: str | None = None
    progress_summary: str | None = None
    model: str | None = None
    sdk: str = "copilot"
    worktree_name: str | None = None
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = None
    verify_prompt: str | None = None
    self_review_prompt: str | None = None
    enable_stall_detection: bool | None = None
    enable_plan_tracking: bool | None = None
    parent_job_id: str | None = None
    source: str = "managed"                   # managed | copilot_cli | claude_cli
    external_session_id: str | None = None
    total_cost_usd: float | None = None
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

class JobListResponse(CamelModel):
    items: list[JobResponse]
    cursor: str | None
    has_more: bool


# --- Messages ---

class SendMessageRequest(BaseModel):
    content: str

class SendMessageResponse(CamelModel):
    seq: int
    timestamp: datetime


# --- Approvals ---

class ApprovalResolution(str, Enum):
    approved = "approved"
    rejected = "rejected"

class ResolveApprovalRequest(BaseModel):
    resolution: ApprovalResolution

class ApprovalResponse(CamelModel):
    id: str
    job_id: str
    description: str
    proposed_action: str | None
    requested_at: datetime
    resolved_at: datetime | None
    resolution: ApprovalResolution | None
    requires_explicit_approval: bool | None = None
    batch_id: str | None = None
    tier: str | None = None                  # deprecated — retired tier model, no longer populated
    reversible: bool | None = None           # deprecated — retired classifier field, no longer populated
    contained: bool | None = None            # deprecated — retired classifier field, no longer populated


# --- Artifacts ---

class ArtifactType(str, Enum):
    diff_snapshot = "diff_snapshot"
    agent_summary = "agent_summary"
    session_snapshot = "session_snapshot"
    session_log = "session_log"
    agent_plan = "agent_plan"
    telemetry_report = "telemetry_report"
    approval_history = "approval_history"
    document = "document"
    server_log = "server_log"       # §20.5 — server-side structlog per job session
    cli_log = "cli_log"             # §20.5 — agent process stderr/stdout per session
    custom = "custom"

class ArtifactResponse(CamelModel):
    id: str
    job_id: str
    name: str
    type: ArtifactType
    mime_type: str
    size_bytes: int
    phase: ExecutionPhase
    created_at: datetime
    session_number: int | None = None   # §20.5 — which session produced this artifact

class ArtifactListResponse(CamelModel):
    items: list[ArtifactResponse]


# --- Workspace ---

class WorkspaceEntryType(str, Enum):
    file = "file"
    directory = "directory"

class WorkspaceEntry(CamelModel):
    path: str
    type: WorkspaceEntryType
    size_bytes: int | None = None    # None for directories

class WorkspaceListResponse(CamelModel):
    items: list[WorkspaceEntry]
    cursor: str | None
    has_more: bool


# --- Settings ---

class UpdateSettingsRequest(CamelModel):
    \"\"\"Structured settings update — only include fields to change.\"\"\"
    max_concurrent_jobs: int | None = Field(None, ge=1, le=10)
    auto_push: bool | None = None
    cleanup_worktree: bool | None = None
    delete_branch_after_merge: bool | None = None
    artifact_retention_days: int | None = Field(None, ge=1, le=365)
    max_artifact_size_mb: int | None = Field(None, ge=1, le=10_000)
    auto_archive_days: int | None = Field(None, ge=1, le=365)
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = Field(None, ge=1, le=10)
    verify_prompt: str | None = Field(None, max_length=5000)
    self_review_prompt: str | None = Field(None, max_length=5000)

class SettingsResponse(CamelModel):
    max_concurrent_jobs: int
    auto_push: bool
    cleanup_worktree: bool
    delete_branch_after_merge: bool
    artifact_retention_days: int
    max_artifact_size_mb: int
    auto_archive_days: int
    verify: bool
    self_review: bool
    max_turns: int
    verify_prompt: str
    self_review_prompt: str


# --- Voice ---

class TranscribeResponse(BaseModel):
    text: str


# --- Health ---

class HealthStatus(str, Enum):
    healthy = "healthy"

class HealthResponse(CamelModel):
    status: HealthStatus
    version: str
    uptime_seconds: float
    active_jobs: int
    queued_jobs: int


# --- SSE Payload Models ---
# These models define the shape of SSE event payloads.
# They appear in the OpenAPI schema so TypeScript types
# are generated for them alongside the REST models.

class ExecutionPhase(str, Enum):
    environment_setup = "environment_setup"
    agent_reasoning = "agent_reasoning"
    verification = "verification"
    finalization = "finalization"
    post_completion = "post_completion"

class LogLevel(str, Enum):
    debug = "debug"
    info = "info"
    warn = "warn"
    error = "error"

class LogLinePayload(CamelModel):
    job_id: str
    seq: int
    timestamp: datetime
    level: LogLevel
    message: str
    context: dict | None = None

class TranscriptRole(str, Enum):
    agent = "agent"
    agent_delta = "agent_delta"
    operator = "operator"
    tool_call = "tool_call"
    tool_running = "tool_running"
    tool_output_delta = "tool_output_delta"
    reasoning = "reasoning"
    reasoning_delta = "reasoning_delta"
    divider = "divider"

class TranscriptPayload(CamelModel):
    job_id: str
    seq: int
    timestamp: datetime
    role: TranscriptRole
    content: str

class DiffLineType(str, Enum):
    context = "context"
    addition = "addition"
    deletion = "deletion"

class DiffLineModel(CamelModel):
    type: DiffLineType
    content: str

class DiffHunkModel(CamelModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLineModel]

class DiffFileStatus(str, Enum):
    added = "added"
    modified = "modified"
    deleted = "deleted"
    renamed = "renamed"

class DiffFileModel(CamelModel):
    path: str
    status: DiffFileStatus
    additions: int
    deletions: int
    hunks: list[DiffHunkModel]

class JobStateChangedPayload(CamelModel):
    job_id: str
    previous_state: str | None
    new_state: str
    timestamp: datetime

class DiffUpdatePayload(CamelModel):
    job_id: str
    changed_files: list[DiffFileModel]

class SessionHeartbeatPayload(CamelModel):
    job_id: str
    session_id: str
    timestamp: datetime

class SnapshotPayload(CamelModel):
    jobs: list[JobResponse]
    pending_approvals: list[ApprovalResponse]
```

### 4.7 Internal Event Bus

The `EventBus` is the backbone of the backend. All subsystems communicate through it.

- Services publish domain events to the bus
- Persistence layer subscribes and persists events
- SSE manager subscribes and pushes events to connected clients
- Job state machine subscribes and applies transitions

The event bus is an in-process async pub/sub. It is not a message broker. All subscribers run in the same process.

---

## 5. Live Update Model (SSE)

### 5.1 Endpoint

```
GET /api/events
```

Optional query parameter to scope to a single job:

```
GET /api/events?job_id={job_id}
```

### 5.2 SSE Event Format

Each event follows the standard SSE wire format:

```
id: {event_id}
event: {event_type}
data: {json_payload}

```

### 5.3 Event Types

Runtime events are `traceforge.SessionEvent` objects (see §11). The SSE wire carries them **as-is** — there is no translation to a separate SSE vocabulary:

- **`event:`** — the event's dotted `kind` (e.g. `job.created`, `message.assistant`, `tool.call.completed`, `permission.requested`). This is the same `EventKind` value used on the event bus and in persistence.
- **`data:`** — the `SessionEvent` serialized verbatim (`SessionEvent.model_dump_json()`):

```json
{
  "id": "<event uuid>",
  "session_id": "<job id>",
  "timestamp": "<event timestamp>",
  "kind": "job.created",
  "payload": { "...": "kind-specific fields — see §11.1" },
  "metadata": { "sequence": 12, "turn_id": "...", "...": "TraceForge annotations" }
}
```

- **`id:`** — the SQLite storage cursor, kept separate from the canonical
  event id and producer sequence; drives `Last-Event-ID` replay.

The frontend consumes this shape directly (`useSSE.ts`), reading `payload.*` and deriving job-state transitions from the dotted `kind` — the server does **not** emit the paired synthetic state-change frames the retired model did. The sole state event is the dotted `job.state_changed` kind, emitted on real transitions.

The non-`SessionEvent` frames are transport concerns. `snapshot`, sent when a
replay gap exceeds the bounds in §5.4, omits `id:` and carries a bespoke
`{ jobs: JobResponse[], pending_approvals: ApprovalResponse[] }` payload. The
endpoint also sends `event: session_heartbeat` with `data: {}` immediately and
after each five seconds without queued data. That unnumbered keepalive is not
persisted or replayed and is distinct from the canonical dotted
`session.heartbeat` domain event.

### 5.3.1 Broadcast Rules

Not every kind reaches clients. `SSEManager` gates delivery (`_BROADCAST_KINDS` in `sse_manager.py` is the source of truth):

| Rule | Effect |
|---|---|
| **Broadcast allowlist** | Only allow-listed dotted kinds are sent. Internal-only kinds — `workspace.prepared`, `agent.session_started`, `step.started`, `step.completed`, `step.title_generated`, `step.group_updated`, `plan.updated`, `execution.phase_changed`, `progress.headline`, `sidecar.*`, `monitor.*` — are dropped at the boundary; their effect surfaces via other broadcast events or the job snapshot. `plan.step_updated` and `step.entries_reassigned` are allowlisted. |
| **Selective suppression** | When >20 jobs are active, high-frequency kinds (`log`, `diff.updated`, `session.heartbeat`, and the transcript family) are suppressed for global/dashboard connections. |
| **Job-scoped only** | `telemetry.updated` and `secondary_session.entry` are delivered only to a connection scoped to that job, never to global/dashboard connections. |

A job-scoped connection (a client viewing one job's detail) always receives full streaming for that job.

### 5.4 Reconnection and Replay

- Every SSE event carries a monotonically increasing `id`
- The client sends `Last-Event-ID` on reconnect
- The backend replays all events with `id > Last-Event-ID` from the event log in SQLite
- Replay is bounded: events older than the job's terminal state are not replayed

#### Replay Bounds

To prevent unbounded replay for long-running jobs, the backend enforces:

| Constraint | Value |
|---|---|
| Maximum replay events | 500 per job |
| Maximum replay age | 5 minutes of wall-clock time |

If the client has been disconnected longer than the replay window, the backend sends a `snapshot` event first containing the current state of all active jobs, followed by recent events within the window. The client applies the snapshot to its store, then processes the delta events.

```
event: snapshot
data: { "jobs": [...], "pending_approvals": [...] }

```

The `jobs` array contains full `JobResponse` objects (same shape as `GET /api/jobs/{id}`). The `pending_approvals` array contains full `ApprovalResponse` objects. This gives the client enough data to fully reconstruct its store without additional REST calls.

### 5.5 SSE Manager

The `SSEManager` service:

- Maintains the set of open SSE connections
- Subscribes to the internal event bus
- Serializes events to SSE wire format
- Broadcasts or routes events to appropriate connections
- Handles client disconnection cleanup

### 5.6 SSE Scaling Constraint

Broadcasting all job events to all connected clients does not scale beyond approximately 20 concurrent jobs per operator session. This is acceptable for the single-developer deployment model.

When more than 20 jobs are active concurrently, the SSE manager switches to a **selective streaming** mode:

| Condition | Behavior |
|---|---|
| Job card is open (Job Detail screen) | Full event streaming for that job continues normally |
| Dashboard view with >20 active jobs | Job cards receive only `job.state_changed` events (no logs, transcripts, or diffs) |
| Dashboard with >20 active jobs | A "Refresh page for latest updates" banner is shown above the Kanban board |

This means:

- An operator viewing a specific job always gets the full live experience regardless of total job count
- The dashboard degrades gracefully by showing state transitions only, not the full event firehose
- The operator can click into any job to restore full streaming for that job
- Below the 20-job threshold, behavior is unchanged — all events stream to all views

---

## 6. Execution Runtime

### 6.1 Job Lifecycle

When a job is created:

1. `JobService` validates the request and persists the job in `preparing` state
2. `GitService` creates the worktree and branch
3. `JobService` persists a `job.created` event and a `workspace.prepared` event
4. `RuntimeService` is asked to run the job
5. If at capacity, job transitions to `queued`; otherwise proceeds immediately
6. `RuntimeService` creates an asyncio task and job transitions to `running`
7. The task starts the agent session and consumes yielded events
8. Each canonical TraceForge event is published to the event bus
9. When the session completes, the job transitions to `review`, `failed`, or `canceled`

### 6.2 Runtime Service

The `RuntimeService` manages all active job tasks.

- Tracks running asyncio tasks by `job_id`
- Enforces `max_concurrent_jobs` from global config
- Enqueues jobs if at capacity (state: `queued`)
- Starts queued jobs when capacity opens
- Resolves the appropriate adapter per-job via `AdapterRegistry.get_adapter(job.sdk)`
- Provides a `cancel(job_id)` method that cancels the asyncio task and calls `adapter.abort_session()`

### 6.3 Operator Message Injection

When an operator sends a message to a running job:

1. `POST /api/jobs/{job_id}/messages` received
2. Route delegates to `JobService.send_operator_message()`
3. Service calls `adapter.send_message(session_id, message)`
4. A `message.user` transcript event is published with `role="operator"`

### 6.4 Approval Pause

When the agent SDK raises a permission request (e.g., Copilot SDK calls `on_permission_request`):

1. Adapter translates to `permission.requested` domain event
2. Event bus delivers to `ApprovalService`
3. `ApprovalService` persists the request; adapter holds the SDK callback pending
4. Job transitions to `waiting_for_approval`
5. `permission.requested` SSE event sent to frontend
6. Operator approves or rejects via `POST /api/approvals/{approval_id}/resolve`
7. `ApprovalService` records resolution; adapter returns the decision to the SDK callback

### 6.5 Graceful Shutdown

By default, `cpl down` and the down phase of `cpl restart` request a pause for
each running managed session before stopping the server. Runtime shutdown
interrupts in-memory tasks but deliberately preserves their active database
state so startup recovery can resume them in place. Queued jobs remain queued.

`cpl down --force` and `cpl restart --force` skip the pause requests and stop
immediately. Forced shutdown semantics are unchanged and do not carry the
graceful pause guarantee.

### 6.6 Restart Recovery

On startup, the backend schedules recovery for managed jobs left in `running`
or `waiting_for_approval`. Recoverable jobs are restarted in place using their
existing job, worktree, and prior SDK session when available; the resulting
`session.resumed` event carries `reason: "server_restart"`. Queued jobs are
re-evaluated against capacity.

Recovery is bounded by the current implementation. A job whose worktree cannot
be restored is failed, and a plan-mode job interrupted while awaiting approval
is failed because its in-memory approval context cannot be reconstructed.
Other recovery startup errors retain their existing handling; this is not a
blanket guarantee that every interrupted job resumes.

---

## 7. Deployment Model

CodePlane runs entirely on a single developer machine.

```
Developer Machine
├── CodePlane Backend (FastAPI on localhost:8080, serves frontend static files)
├── SQLite database (~/.codeplane/data.db)
├── Artifact storage (~/.codeplane/artifacts/)
├── Global config (~/.codeplane/config.yaml)
├── Application logs (~/.codeplane/logs/)
├── Local git repositories (/repos/...)
└── Dev Tunnels (HTTPS tunnel for remote access)
```

In production mode, the backend serves the built React frontend as static files. Only one port (`8080`) is exposed. The Vite dev server on port `5173` is used only during frontend development.

### 7.1 Tunnel

CodePlane supports two remote-access tunnel providers: **Dev Tunnels** (default) and **Cloudflare Tunnels**. Both expose the local server over HTTPS for remote access from phones and other devices.

#### Dev Tunnels (default)

- Requires the `devtunnel` CLI installed and authenticated on the machine
- Exposes the local port over HTTPS at `https://{tunnel}-{port}.{region}.devtunnels.ms`
- Tunnel URLs are provisioned by the Dev Tunnels relay
- The relay requires Microsoft account login (tunnel-owner only) — this is an identity gate independent of the CodePlane password
- HTTPS is enforced by the Dev Tunnels relay
- Password auth is always enabled when tunneling — remote access requires the password printed in the startup banner
- The backend enforces CORS to prevent cross-origin abuse from other browser tabs

#### Cloudflare Tunnels

- Requires the `cloudflared` CLI and a pre-created named tunnel with a routed public hostname
- Uses a tunnel token (env var `CPL_CLOUDFLARE_TUNNEL_TOKEN`) — no local config file needed
- Ingress routing must be configured in the Cloudflare dashboard or API before first use
- DNS must point at the tunnel (CNAME to `{tunnel-id}.cfargotunnel.com`, proxied)
- Password auth is always enabled — same as Dev Tunnels
- **Important:** Unlike Dev Tunnels, Cloudflare Tunnels have **no built-in identity gate** at the relay level. CodePlane requires a [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) application on the hostname and refuses to start without one. Email OTP is the simplest identity provider; SSO and mTLS are also supported. See [Security §21.1](#211-authentication) for details.

### 7.2 Startup

The system is started with a single command:

```bash
cpl up
```

This command:

1. Loads and validates global config
2. Initializes the SQLite database (runs migrations)
3. Runs restart recovery (see Section 6.6)
4. Loads the faster-whisper model (if voice is enabled)
5. Starts the FastAPI server
6. Optionally starts Dev Tunnels

### 7.3 CLI

The `cpl` CLI is a Python entry point installed via `uv`:

```bash
uv pip install -e .
```

The entry point is defined in `pyproject.toml`:

```toml
[project.scripts]
cpl = "backend.main:cli"
```

#### CLI Commands

| Command | Description |
|---|---|
| `cpl up` | Start the server |
| `cpl up --port 9090` | Start on a custom port |
| `cpl up --remote` | Start with tunnel for remote access |
| `cpl up --phone` | Shortcut for `--remote` with QR code display |
| `cpl up --dev` | Start in development mode (CORS allows localhost:5173) |
| `cpl up --provider cloudflare` | Use Cloudflare Tunnels instead of Dev Tunnels |
| `cpl down` | Stop a running server |
| `cpl restart` | Restart the server |
| `cpl setup` | Run the interactive setup wizard |
| `cpl doctor` | Run diagnostic checks |
| `cpl info` | Show server connection info and QR code |
| `cpl version` | Print version |

---

## 8. Repository and Workspace Model

### 8.1 Worktree Creation

Every job gets its own isolated worktree. The main worktree (repo root) is never used for job execution.

When a job starts:

1. Backend resolves the repository root from the config
2. `JobService` generates a `worktree_name` using the utility LLM (kebab-case, 3-30 chars, e.g. `fix-null-pointer`). If naming fails, a deterministic fallback `task-{sha256(prompt)[:8]}` is used
3. `GitService.create_worktree()` creates a worktree directory at:
   ```
   {repo_root}/{worktrees_dirname}/{worktree_name}/
   ```
   Default `worktrees_dirname`: `.codeplane-worktrees`
4. A branch is created from `base_ref`:
   - If `branch` was provided in the job creation request, that name is used as-is
   - Otherwise, the LLM-generated branch name is used
5. Before creation, stale worktree registrations are cleaned up (`git worktree prune`, force-remove if directory exists, delete stale branch)

#### Worktree Creation Failure

If `git worktree add` fails (e.g., disk full, permissions error, corrupt repo state), `GitService.create_worktree()` raises `GitError` which `JobService` catches. The job is persisted in `failed` state with a descriptive error message including git stderr output. The operator can resolve the underlying issue and rerun the job.

Example — single job:

```
/repos/service-a/.codeplane-worktrees/fix-null-pointer/   ← job-104 works here
```

Example — two concurrent jobs on the same repo:

```
/repos/service-a/.codeplane-worktrees/fix-null-pointer/   ← job-104
/repos/service-a/.codeplane-worktrees/add-pagination/     ← job-105
```

### 8.2 Branch Naming

Branch names are either explicitly provided by the operator at job creation or chosen by the agent as a preflight step. The agent picks a conventional name based on the prompt.

Examples:

```
fix/null-pointer-in-user-service
feat/add-pagination-to-orders-api
chore/upgrade-react-to-19
```

### 8.3 Workspace Cleanup

On job completion (success, failure, or cancel):

- Worktree directories are retained for artifact inspection and diff browsing
- Worktrees are not automatically deleted; cleanup depends on the completion config (`completion.cleanup_worktree`)
- A batch cleanup command is available via: `POST /api/settings/cleanup-worktrees`

Jobs have a `completion.strategy` config that controls what happens after success:

- **`manual`** (default): the job is left as `unresolved` for operator decision via the Review column
- **`auto_merge`**: the existing escalation runs automatically — fast-forward merge is attempted first, then a regular merge, and if both fail a PR is created as a fallback
- **`pr_only`**: a PR is always created immediately upon success

### 8.4 Protected Paths

If a per-repository config defines `protected_paths`, the adapter translates these into SDK-native permission rules at session creation time. Any write to a protected path triggers the SDK's built-in permission request flow, which the adapter routes to the operator via the approval system (Section 18).

### 8.5 Concurrent Jobs on the Same Repository

Multiple jobs may target the same repository concurrently.

Every job gets its own worktree under `.codeplane-worktrees/`, providing full isolation regardless of concurrency. No job ever uses the main worktree.

Isolation between concurrent jobs is guaranteed by Git worktrees: each job works in its own worktree directory with its own branch and cannot interfere with another job's files. The main branch of the repository is never written to directly.

### 8.6 Repository Safety Enforcement

To prevent the agent from accidentally modifying unrelated files:

1. `GitService` creates the worktree before the agent session starts
2. The adapter sets `workspace_path` in the SDK's session config, which scopes agent operations to the worktree. The SDK's own subprocess inherits this scoping
3. The permission policy evaluates file operations against the worktree boundary (see §18)
4. Pushing to remote and creating PRs are allowed — these are useful agent capabilities. Push protection is not enforced at the git config level

### 8.7 Pull Request Creation After Successful Job

When a job completes successfully, CodePlane instructs the agent to create a pull request as a **post-completion step** if the GitHub CLI (`gh`) or GitHub MCP tools are available.

The flow:

1. Job reaches `review` state
2. During the `post_completion` phase, the agent is instructed to push the branch and open a PR using whichever GitHub tooling is available:
   - **GitHub MCP server** (if configured in `.vscode/mcp.json` or `tools.mcp` global config) — the agent uses the MCP `create_pull_request` tool
   - **`gh` CLI** (if available on `$PATH`) — the agent runs `gh pr create` with the branch name, prompt-derived title, and a summary body
3. If neither `gh` CLI nor GitHub MCP tools are available, the step is skipped silently and the branch remains on disk for the operator to push manually
4. The PR URL (if created) is included in the `job.review` event payload and displayed on the Job Detail screen

This is a best-effort operation — if PR creation fails (e.g., no remote configured, auth issues), the job is still considered successful. The failure is logged as a warning.

Completed branches are **not** auto-deleted after merge. The branch remains on disk until the operator explicitly cleans it up or the retention policy removes the worktree.

### 8.8 Job Resolution

When a job succeeds with `completion.strategy = "manual"`, it enters
the Review column with `resolution = "unresolved"` in the `review` state. The operator resolves it via:

    POST /api/jobs/{id}/resolve
    Body: { "action": "merge" | "create_pr" | "discard" }

**Merge**: Attempts fast-forward merge, then regular merge. On success, resolution
becomes `merged`, job transitions to `completed`, worktree is cleaned up, and branch is deleted. On conflict, the
merge is aborted (worktree stays clean), resolution becomes `conflict`, and the job
stays in `review` with a conflict badge showing the affected files.

**Create PR**: Pushes the branch to origin and creates a PR via `gh pr create`.
Resolution becomes `pr_created`, job transitions to `completed`, worktree is cleaned up (branch kept on remote).

**Discard**: Removes the worktree and deletes the branch. Resolution becomes
`discarded`, job transitions to `completed`.

Jobs with `resolution = "conflict"` can be further resolved with `create_pr` or
`discard` (but not `merge`).

Terminal jobs can be archived to hide them from the Kanban board:

    POST /api/jobs/{id}/archive     → 204
    POST /api/jobs/{id}/unarchive   → 204

Resolved jobs are auto-archived after `retention.auto_archive_days` (default: 7 days).

### 8.9 Repository Registration

Operators manage the set of repositories CodePlane can work with through the web UI or by editing the global config directly.

#### Adding a Local Repository

The operator provides an absolute path on the developer machine (e.g. `/repos/service-a`). The backend validates that:

1. The path exists and is a directory
2. The directory is a valid Git repository (contains `.git`)
3. The path is not already registered

On success the path is appended to the `repos` list in the global config and persisted.

#### Adding a Remote Repository

The operator provides a remote URL (HTTPS or SSH) — for example `https://github.com/org/repo.git`. The backend:

1. Resolves a local clone directory from the `clone_to` field in the request (or derives one from the URL)
2. Runs `git clone <url> <target_dir>` via subprocess
3. Authentication relies entirely on the machine's existing Git credential configuration (SSH keys, credential helpers, `.netrc`, etc.). CodePlane never stores or prompts for credentials
4. On success the cloned path is appended to the global config `repos` list
5. On failure (auth error, network issue, invalid URL) a descriptive error is returned — the `repos` list is not modified

#### Removal

Operators can remove a repository from the `repos` list. This only removes the config entry — it does **not** delete the directory from disk. Active jobs targeting the removed repo continue to completion.

#### Configuration

Registered repositories are stored in the global config:

```yaml
repos:
  - /repos/service-a             # local path
  - /repos/service-b
```

For remote repositories, the clone target is specified per-request via the `clone_to` field in the register API, not as a global config setting.

---

## 9. Voice Input and Transcription

### 9.1 Overview

CodePlane supports voice input for dictating prompts and operator messages. Audio is captured in the browser, uploaded to the local backend, and transcribed locally using `faster-whisper`. No audio data is transmitted to external services.

### 9.2 Privacy Guarantee

All voice transcription runs locally on the developer machine:

- Audio is uploaded only to `localhost` (or via the authenticated tunnel)
- The `faster-whisper` library performs inference locally using downloaded model weights
- `faster-whisper` does not phone home or transmit data to external servers
- The UI displays a "Local transcription" indicator next to the microphone button
- Audio blobs are discarded after transcription; they are not persisted

### 9.3 Workflow

1. Operator presses and holds the microphone button in the browser
2. Browser requests microphone permission (`getUserMedia`)
3. `MediaRecorder` records audio chunks while button is held
4. On release, recording stops
5. Chunks are combined into a single audio blob (WebM/Opus or WAV)
6. Blob uploaded via `POST /api/voice/transcribe` as `multipart/form-data`
7. Backend transcribes and returns `{ text: "..." }`
8. Transcribed text is inserted into the active prompt or message input field

### 9.4 Frontend Implementation

```typescript
async function recordAndTranscribe(): Promise<string> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: Blob[] = [];

  recorder.ondataavailable = (e) => chunks.push(e.data);

  await new Promise<void>((resolve) => {
    recorder.onstop = () => resolve();
    // caller triggers recorder.stop() on button release
  });

  const blob = new Blob(chunks, { type: "audio/webm" });
  const form = new FormData();
  form.append("audio", blob, "recording.webm");

  const res = await fetch("/api/voice/transcribe", { method: "POST", body: form });
  const { text } = await res.json();
  return text;
}
```

### 9.5 Voice Input Contexts

Voice input is available in three contexts:

| Context | Description |
|---|---|
| Job creation prompt | Dictate the initial task prompt |
| Operator message | Dictate a mid-run instruction to send to the agent |
| Terminal session | Dictate a command (pasted into the terminal) or an assist message (inserted into the assist chat input). See §14.6.3 |

### 9.6 Transcription Engine

Local transcription uses `faster-whisper`.

| Model | RAM | Latency (approx.) | Use case |
|---|---|---|---|
| `tiny.en` | ~150 MB | 100–300 ms | Low-latency, English only |
| `base.en` | 300–400 MB | 300–800 ms | Default, better accuracy |

Default model: `base.en`

### 9.7 Configuration

Voice is always enabled with hardcoded defaults. The `max_audio_size_mb` is fixed at 10 MB. These values are not user-configurable.

### 9.8 Backend Endpoint

```
POST /api/voice/transcribe
Content-Type: multipart/form-data

Field: audio (binary, max 10 MB)
```

Response:

```json
{
  "text": "Fix the null pointer exception in the user service."
}
```

The endpoint rejects uploads exceeding `max_audio_size_mb` with `413 Payload Too Large`.

The frontend enforces the same limit client-side: it checks the recording size before uploading and shows a warning toast if the limit is exceeded, without sending the request.

### 9.9 Transcription Service

```python
from faster_whisper import WhisperModel

class VoiceService:
    def __init__(self, model_name: str = "base.en"):
        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, audio_bytes: bytes) -> str:
        segments, _ = self._model.transcribe(audio_bytes)
        return " ".join(seg.text.strip() for seg in segments)
```

The model is loaded once at startup and reused across requests.

---

## 10. Configuration Model

### 10.1 Overview

Configuration exists at three layers:

| Layer | Location | Scope |
|---|---|---|
| Global | `~/.codeplane/config.yaml` | Machine-level runtime behavior |
| Per-repository | `{repo_root}/.codeplane.yml` | Repository-specific overrides |
| Per-job | Job creation payload | Single-job overrides |

### 10.2 Global Configuration

File: `~/.codeplane/config.yaml`

```yaml
server:
  host: 127.0.0.1
  port: 8080

runtime:
  max_concurrent_jobs: 2
  worktrees_dirname: .codeplane-worktrees
  utility_model: claude-haiku-4.5   # default utility model for naming and summaries
  default_sdk: copilot              # copilot | claude — SDK used when not overridden per-job
  suppressed_preflight_agent_prompts: []

retention:
  artifact_retention_days: 30
  max_artifact_size_mb: 100
  cleanup_on_startup: false
  auto_archive_days: 7

completion:
  strategy: manual                  # manual | auto_merge | pr_only
  auto_push: true                   # push branch to remote on success
  cleanup_worktree: true            # remove worktree after resolution
  delete_branch_after_merge: true   # delete branch after merge

verification:
  verify: false                     # run verification pass after job completion
  self_review: false                # run self-review pass after job completion
  max_turns: 2                      # max turns for verification/self-review
  verify_prompt: ""                 # custom verification prompt (empty = use default)
  self_review_prompt: ""            # custom self-review prompt (empty = use default)

terminal:
  enabled: true
  max_sessions: 5
  default_shell: null               # auto-detect if null
  scrollback_size_kb: 500

telemetry:
  otel_exporter_endpoint: ""        # OTLP collector URL (empty = in-process only)
  claude_monthly_budget_usd: 0.0    # budget cap for analytics dashboard
  copilot_premium_entitlement: 0    # premium request quota (auto-detected from SDK)
  daily_spend_limit_usd: 0.0       # personal daily cost limit (0 = no limit)
  instance_id: ""                   # auto-generated UUID on first run

trail:
  enrich_batch_size: 20             # trail enrichment batch size
  enrich_interval_seconds: 10.0     # enrichment interval
  enrich_max_retries: 10            # max retry attempts for enrichment
  enrich_decisions_context: 5       # number of preceding decisions for context

logging:
  level: info
  file: ~/.codeplane/logs/server.log
  max_file_size_mb: 50
  backup_count: 3

rate_limits:
  max_sse_connections: 5

platforms:                          # per-platform auth and repo binding
  github:
    auth: cli                       # cli | token
    repos:
      - /repos/service-a

repos:
  - /repos/service-a
  - /repos/service-b

tools:
  mcp:
    github:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
    postgres:
      command: uvx
      args: ["mcp-postgres"]
      env:
        DATABASE_URL: "${DATABASE_URL}"
```

Missing or blank `runtime.utility_model` uses `claude-haiku-4.5`. An explicit
nonblank model choice is preserved. This default is not a claim of lower billing.

`runtime.utility_sdk` optionally selects a registered text-completion provider.
When omitted, utility calls use the existing enabled-provider/default-SDK policy,
independently of the primary work harness. Model choices are validated against the
actual selected provider and never silently replaced. `copilot_only` applies to
utility calls, including cached text sessions. Unavailable providers fail explicitly.
This override applies to assessment, titles, monitoring, narration and text sidecars,
not tool-using Scout or agentic sidecar execution. Those retain their execution
binding and existing model override precedence.
The enforcement recommendation contract preserves the five existing wire values
without importing TraceForge's title runtime. This removes the import dependency
from monitor/router construction, not the still-pending production governance and
enrichment cutover.

Entries support glob patterns via Python's `glob.glob`. Each pattern is expanded at startup and re-expanded when the config is reloaded. Only directories that are valid git repositories (contain `.git`) are included after expansion.

#### 10.2.1 MCP Server Discovery

CodePlane discovers MCP servers from two sources, merged as a union:

1. `.vscode/mcp.json` in the repo — `"servers"` key (VS Code / Copilot convention)
2. `tools.mcp` block in CodePlane's global config (`~/.codeplane/config.yaml`)

If the same server name appears in both, the repo-level `.vscode/mcp.json` takes precedence.

Entries are normalized into `MCPServerConfig` and passed to the SDK.

Example `.vscode/mcp.json` (already in the repo):

```json
{
  "servers": {
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"]
    },
    "postgres": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-postgres"],
      "env": { "DATABASE_URL": "${DATABASE_URL}" }
    }
  }
}
```

The per-repo `.codeplane.yml` can optionally disable specific discovered servers:

```yaml
tools:
  mcp:
    disabled:
      - postgres
```

This keeps CodePlane out of the business of defining MCP servers and lets developers use the same config they already have.

### 10.3 Per-Repository Configuration

File: `{repo_root}/.codeplane.yml`

```yaml
base_branch: main

protected_paths:
  - infra/
  - .github/workflows/

tools:
  mcp:
    disabled:
      - postgres
```

| Field | Description |
|---|---|
| `base_branch` | Branch to create worktree from |
| `protected_paths` | Paths that require approval before modification |
| `tools.mcp.disabled` | MCP servers discovered in the repo to exclude from sessions |

### 10.4 Per-Job Overrides

Provided in the job creation request body:

```json
{
  "repo": "/repos/service-a",
  "prompt": "Fix the null pointer exception in UserService.java",
  "base_ref": "main",
  "sdk": "claude",
  "model": "claude-sonnet-4-20250514",
  "preset": "supervised",
  "verify": true,
  "self_review": false,
  "description": "NullPointer fix for user service",
  "enable_stall_detection": true,
  "enable_plan_tracking": true
}
```

| Field | Required | Description |
|---|---|---|
| `repo` | Yes | Path to repository (must be in allowlist) |
| `prompt` | Yes | Task description for the agent |
| `base_ref` | No | Override base branch/commit |
| `sdk` | No | Override SDK for this job (`copilot`, `claude`); defaults to `runtime.default_sdk` |
| `model` | No | Override model for this job; must be compatible with the selected SDK (see §4.4) |
| `preset` | No | Override action policy preset (`autonomous`, `supervised`, `locked`); defaults to global preset |
| `verify` | No | Run verification after agent completes |
| `self_review` | No | Run self-review after agent completes |
| `description` | No | Short description shown in the UI job list |
| `enable_stall_detection` | No | Enable stall detection for this job |
| `enable_plan_tracking` | No | Enable step/plan tracking for this job |

---

## 11. Canonical Internal Event Model

All runtime activity is represented as `traceforge.SessionEvent` objects — the single canonical event model, end to end (event bus, persistence, and the SSE wire). CodePlane defines no bespoke event dataclass; it uses TraceForge's `SessionEvent` + `EventMetadata` directly. Event `kind` is an open dotted string in TraceForge's grammar; CodePlane's own control-plane kinds are named constants in the `EventKind` StrEnum (TraceForge-canonical strings are reused where semantics match — e.g. `log`, `message.*`, `tool.call.*`, `permission.*`):

```python
from enum import StrEnum

from traceforge.types import EventMetadata, SessionEvent


class EventKind(StrEnum):
    """CodePlane control-plane kinds as open dotted TraceForge kinds."""

    # Job lifecycle
    job_created = "job.created"
    job_setup_progress = "job.setup_progress"
    workspace_prepared = "workspace.prepared"
    agent_session_started = "agent.session_started"
    # Transcript family — one CP transcript event fans out per agent role
    message_user = "message.user"
    message_assistant = "message.assistant"
    message_delta = "message.delta"
    tool_call_started = "tool.call.started"
    tool_call_completed = "tool.call.completed"
    # Logs / diffs
    log_line_emitted = "log"
    diff_updated = "diff.updated"
    # Approvals (TraceForge-canonical permission.* grammar)
    approval_requested = "permission.requested"
    approval_resolved = "permission.resolved"
    batch_approval_requested = "permission.batch.requested"
    batch_approval_resolved = "permission.batch.resolved"
    # Job state / outcomes
    job_review = "job.review"
    job_completed = "job.completed"
    job_failed = "job.failed"
    job_canceled = "job.canceled"
    job_state_changed = "job.state_changed"
    session_heartbeat = "session.heartbeat"
    merge_completed = "merge.completed"
    merge_conflict = "merge.conflict"
    session_resumed = "session.resumed"
    job_resolved = "job.resolved"
    job_archived = "job.archived"
    job_title_updated = "job.title_updated"
    progress_headline = "progress.headline"
    model_downgraded = "model.downgraded"
    tool_group_summary = "tool.group_summary"
    agent_plan_updated = "plan.updated"
    execution_phase_changed = "execution.phase_changed"
    telemetry_updated = "telemetry.updated"
    # Steps / plan
    step_started = "step.started"
    step_completed = "step.completed"
    step_title_generated = "step.title_generated"
    step_group_updated = "step.group_updated"
    plan_step_updated = "plan.step_updated"
    step_entries_reassigned = "step.entries_reassigned"
    turn_summary = "turn.summary"
    action_classified = "action.classified"
    policy_settings_changed = "policy.settings_changed"
    # Repo index
    stall_detected = "stall.detected"
    # Monitors
    monitor_approved = "monitor.approved"
    monitor_rejected = "monitor.rejected"
    monitor_escalated = "monitor.escalated"
    # Sidecars
    sidecar_transcript = "sidecar.transcript"
    sidecar_agent_message = "sidecar.agent_message"
    sidecar_gate_verdict = "sidecar.gate_verdict"
    sidecar_metadata_update = "sidecar.metadata_update"
    # Secondary sessions (preflight, sidecars, monitors)
    secondary_session_started = "secondary_session.started"
    secondary_session_entry = "secondary_session.entry"
    secondary_session_completed = "secondary_session.completed"
    # Context handoff / mode
    context_handoff = "context.handoff"
    job_mode_changed = "job.mode_changed"
```

The `SessionEvent` envelope (from `traceforge.types`) carries `id`, `session_id`
(the CodePlane job id), `timestamp`, `kind` (an open dotted string), `payload`
(CodePlane's event-specific fields), and `metadata` (including producer
sequence, turn correlation, and TraceForge annotations). Fields that once lived
on a bespoke `DomainEvent` now ride `payload`; `turn_id` lives on
`metadata.turn_id`. Persistence preserves this envelope and `kind` verbatim, as does SSE for kinds
that pass its delivery rules; the constants below are the kinds CodePlane
currently emits, not a closed enumeration of permitted TraceForge kinds.

### 11.1 Event Types

| Event Kind | Trigger | Key Payload Fields |
|---|---|---|
| `job.created` | Job creation request accepted | `repo`, `prompt`, `base_ref` |
| `job.setup_progress` | Workspace setup step progress | `step` |
| `workspace.prepared` | Worktree and branch created | `worktree_path`, `branch` |
| `agent.session_started` | Agent session created | `session_id` |
| `log` | Agent or system log output | `seq`, `level`, `message`, `context` |
| `message.user` / `message.assistant` / `message.delta` / `tool.call.started` / `tool.call.completed` | Agent/operator transcript and tool activity, identified by kind | `seq`, `content`, `tool_name`, `tool_call_id`, `arguments`, `result`, `success` as applicable |
| `diff.updated` | File changes detected in worktree | `changed_files` (list of DiffFile) |
| `permission.requested` | SDK permission request intercepted | `approval_id`, `description`, `proposed_action`, `requires_explicit_approval` |
| `permission.resolved` | Operator approves or rejects | `approval_id`, `resolution` |
| `permission.batch.requested` | Action policy batch awaiting approval | `batch_id`, `actions` |
| `permission.batch.resolved` | Batch approved or rejected | `batch_id`, `resolution` |
| `job.review` | Session completed, awaiting operator review | `pr_url`, `merge_status`, `resolution` |
| `job.completed` | Job resolved to terminal state | |
| `job.failed` | Session terminated with error | `reason` |
| `job.canceled` | Operator canceled the job | `reason` |
| `job.state_changed` | Job transitions between states | `previous_state`, `new_state` |
| `session.heartbeat` | Periodic heartbeat from running session | `session_id` |
| `merge.completed` | Merge-back succeeded | `branch`, `base_ref`, `strategy` |
| `merge.conflict` | Merge-back hit conflicts | `branch`, `conflict_files`, `fallback` |
| `session.resumed` | Agent session resumed, including startup recovery | `session_number`, `instruction`, `timestamp`, `reason` (`server_restart` for startup recovery) |
| `job.resolved` | Operator resolved a completed job | `resolution`, `pr_url`, `conflict_files` |
| `job.archived` | Job moved to archive | _(none)_ |
| `job.title_updated` | LLM generated or updated job title | `title`, `branch`, `description` |
| `progress.headline` | Agent progress summary | `headline` |
| `model.downgraded` | Requested model unavailable, fallback used | `requested_model`, `actual_model` |
| `tool.group_summary` | AI-generated summary for tool group | `turn_id`, `summary` |
| `plan.updated` | Agent plan steps updated | `steps` |
| `execution.phase_changed` | Execution phase transition | `phase` |
| `telemetry.updated` | Cost/token metrics updated | telemetry fields |
| `step.started` | Plan step started | `step_id`, `step_number` |
| `step.completed` | Plan step finished | `step_id` |
| `step.title_generated` | Step title generated by LLM | `step_id`, `title` |
| `step.group_updated` | Step grouping changed | `step_id` |
| `plan.step_updated` | Plan step detail updated | `step_id` |
| `step.entries_reassigned` | Entries moved to different step | `old_step_id`, `new_step_id` |
| `turn.summary` | AI-generated turn summary | `turn_id`, `summary` |
| `action.classified` | Action policy classification | `recommendedAction`, `reasonCode`, `riskScore`, `riskBand`, `effect` |
| `policy.settings_changed` | Policy configuration changed | `preset` |
| `stall.detected` | Agent stall detected by arbiter sidecar | |
| `monitor.approved` | Monitor sidecar auto-approved action | `action` |
| `monitor.rejected` | Monitor sidecar auto-rejected action | `action` |
| `monitor.escalated` | Monitor sidecar escalated to human | `action` |
| `sidecar.transcript` | Sidecar session output | `sidecar_name`, `content` |
| `sidecar.agent_message` | Sidecar agent message | `sidecar_name`, `message` |
| `sidecar.gate_verdict` | Sidecar gate decision | `sidecar_name`, `verdict` |
| `sidecar.metadata_update` | Sidecar metadata changed | `sidecar_name`, `metadata` |

### 11.2 Event Consumers

| Consumer | Events consumed | Action |
|---|---|---|
| `JobStateMachine` | All state-relevant events | Applies state transitions |
| `PersistenceSubscriber` | All events | Persists to SQLite event log |
| `SSEManager` | Allowlisted events | Pushes unchanged canonical envelopes and dotted kinds according to the broadcast, selective-suppression, and job-scope rules in §5.3.1 |
| `ApprovalService` | `permission.requested` | Persists request, awaits operator resolution |
| `DiffService` | `workspace.prepared`, `job.review` | Generates and stores diff snapshots |
| `ArtifactService` | `job.review` | Collects and stores artifacts |
| `TimelineBuilder` | All events | Updates job timeline view |

---

## 12. Job States

### 12.0 Job ID Generation

Job IDs use a sequential integer with a `job-` prefix, backed by SQLite autoincrement. The `jobs` table uses `TEXT` primary key but the value is always `job-{N}` where `N` is the next integer from an internal sequence.

Examples: `job-1`, `job-104`, `job-2057`

Sequential integers are preferred over UUIDs because:
- Branch names remain human-readable (`fix/null-pointer-in-user-service`)
- Job IDs are easy to reference in conversation
- There is only one instance of CodePlane; global uniqueness is not required

### 12.1 States

| State | Description |
|---|---|
| `preparing` | Workspace is being created (worktree, branch setup) |
| `queued` | Job accepted but not yet started (at capacity) |
| `running` | Agent session is active |
| `waiting_for_approval` | Session paused, awaiting operator decision |
| `review` | Agent session exited cleanly; awaiting operator review and resolution |
| `completed` | Operator resolved the job (merged, PR created, or discarded) |
| `failed` | Session terminated with an error |
| `canceled` | Operator canceled the job |

Jobs in `review` carry a **resolution status** that tracks how the job's changes were handled: `unresolved` (awaiting operator decision), `conflict` (merge attempted but conflicts detected). Jobs move to `completed` when the operator resolves them with `merged`, `pr_created`, or `discarded`.

### 12.2 State Transition Table

| From | Event | To |
|---|---|---|
| _(none)_ | `job.created` | `preparing` |
| _(none)_ | `job.created` (external CLI session) | `running` or `queued` |
| `preparing` | `workspace.prepared` + capacity available | `queued` |
| `preparing` | Workspace creation failed | `failed` |
| `preparing` | `job.canceled` | `canceled` |
| `queued` | Capacity opens | `running` |
| `queued` | `job.canceled` | `canceled` |
| `running` | `permission.requested` | `waiting_for_approval` |
| `running` | `job.review` (agent done) | `review` |
| `running` | `job.failed` | `failed` |
| `running` | `job.canceled` | `canceled` |
| `waiting_for_approval` | `permission.resolved` (approved) | `running` |
| `waiting_for_approval` | `permission.resolved` (rejected) | `failed` |
| `waiting_for_approval` | `job.canceled` | `canceled` |
| `review` | Operator resolves (merge/PR/discard) | `completed` |
| `review` | Operator resumes with instructions | `running` |
| `review` | `job.canceled` | `canceled` |

Terminal states (`completed`, `failed`, `canceled`) can transition back to `running` for job resumption.

After reaching `review`, the job enters a resolution lifecycle managed by `POST /api/jobs/{id}/resolve`. A successful resolution (merge, PR, discard) transitions the job to `completed`. Resolution transitions: `unresolved` → `merged|pr_created|discarded|conflict`, `conflict` → `pr_created|discarded`.

### 12.3 Rerun

Rerunning a job creates a new job record. The original job is not mutated. The new job copies the original's `repo`, `prompt`, and `base_ref`.

---

## 13. Execution Phases

| Phase | Description | Example events |
|---|---|---|
| `environment_setup` | Workspace creation, branch, dependency install | `workspace.prepared`, `log` |
| `agent_reasoning` | Agent reads code, thinks, plans, and writes changes | `message.assistant`, `diff.updated`, `permission.requested` |
| `verification` | Post-task verification and self-review passes | `message.assistant`, `diff.updated` |
| `finalization` | Final diff snapshot, artifact collection | `diff.updated`, `job.review` |
| `post_completion` | Operator reviews, approves, or reruns | _(no agent events)_ |

Artifacts and timeline entries carry the phase in which they were produced. The frontend uses phase labels to group the execution timeline.

---

## 14. User Interface

### 14.1 Dashboard

**Desktop layout: Kanban board** (viewport width ≥ 1024px)

Columns:

| Column | States shown |
|---|---|
| Active | `queued`, `running` |
| Review | `waiting_for_approval`, `review` |
| Failed | `failed`, `canceled` |
| History | `completed` (excludes archived) |

Each card displays: job ID, repository name, prompt excerpt, elapsed time, and status badge.

The History column shows the most recent 50 jobs by default. A "Load more" button fetches the next page via `GET /api/jobs?state=completed,canceled&limit=50&cursor={last_id}`. The column uses virtualized rendering for smooth scrolling.

**Mobile layout: Filtered job list** (viewport width < 1024px)

A single scrollable list of jobs. Filter tabs at the top correspond to the Kanban columns. Tapping a job opens the Job Detail screen.

#### Responsive Breakpoints

| Breakpoint | Layout |
|---|---|
| ≥ 1024px | Kanban board (4 columns) |
| 768px – 1023px | Kanban board (2 columns, stacked) |
| < 768px | Mobile job list with filter tabs |

The Job Detail screen uses a single-column stacked layout on viewports below 768px. Panels (Transcript, Logs, Diff, etc.) become collapsible accordion sections.

### 14.2 Job Detail Screen

Sections:

| Section | Contents |
|---|---|
| **Job Metadata Header** | Job ID, repo, branch, state badge, started/completed timestamps |
| **Approval Banner** | Shown only in `waiting_for_approval` state. Displays description, proposed action, and Approve/Reject buttons |
| **Transcript Panel** | Scrolling list of agent reasoning messages and operator injections. Auto-scrolls to bottom on new entries |
| **Logs Panel** | Raw log output with level filtering (debug/info/warn/error). Virtualized list. After session ends, enriched with server-side and CLI log artifacts (see §20.5) |
| **Diff Viewer** | Per-file diffs with syntax highlighting, additions/deletions counts, and hunk navigation |
| **Workspace Browser** | File tree of the worktree. Click a file to view its contents |
| **Artifact Viewer** | List of collected artifacts with type badges and download links. Always visible as a tab; shows an empty state when no artifacts are collected yet |
| **Execution Timeline** | Chronological list of key events grouped by phase |
| **Terminal Pane** | Inline xterm.js terminal scoped to the job's worktree. Only shown when the job has a worktree. See §14.6 |

#### Concurrent Approval Notifications

When multiple jobs are simultaneously in `waiting_for_approval` state:

- The Sign-off column on the dashboard clearly shows each pending approval with its job ID
- On mobile, a persistent badge on the "Sign-off" filter tab shows the count of pending approvals
- If the operator is viewing a different job's detail screen, a toast notification appears for new approval requests with a "View" link

### 14.3 Job Creation Screen

Fields:

| Field | Type | Notes |
|---|---|---|
| Repository | Dropdown | Only repositories in allowlist |
| Prompt | Textarea + voice button | Task description |
| Base reference | Text | Default: repo's `base_branch` |

### 14.4 Settings Screen

Sections:

- **Repository management** — Add repositories from local disk (browse/type path) or from a remote hub (paste URL). List of registered repos with remove action. Clone progress indicator for remote repos
- Global config viewer/editor (YAML text editor with validation)
- Repository config list (per-repo `.codeplane.yml` viewer)
- Worktree cleanup action
- **Action Policy** — Active preset selector and custom rule management (see §18.3)

### 14.5 Repository Detail View

Accessible by clicking a repository in the Settings Screen repo list or from the repo selector anywhere in the UI. Displays the full resolved configuration for a single repository.

Sections:

| Section | Contents |
|---|---|
| **Header** | Repository name (derived from path), absolute path on disk, remote origin URL (if any) |
| **Tool / MCP Configuration** | Table of all MCP servers available to this repo. Each row shows: server name, command, source badge (`local` — from `.vscode/mcp.json`, `global` — from global config, `disabled` — blocked by `.codeplane.yml`). Inherited global servers are shown with a dimmed style if not overridden |
| **Repo Config** | Rendered view of the repo's `.codeplane.yml` — `base_branch`, `protected_paths`, `tools.mcp.disabled`. Shows defaults for fields not explicitly set |
| **Active Jobs** | List of currently active jobs targeting this repo (links to Job Detail) |
| **Recent Jobs** | Last 10 completed/failed/canceled jobs for this repo |

#### MCP / Tool Resolution Display

For each MCP server, the view shows the resolution chain so the operator understands where the config comes from:

```
┌─────────────┬──────────────────┬──────────┐
│ Server      │ Command          │ Source   │
├─────────────┼──────────────────┼──────────┤
│ github      │ npx -y @model... │ local    │  ← .vscode/mcp.json (overrides global)
│ postgres    │ uvx mcp-postgres │ disabled │  ← global, disabled by .codeplane.yml
│ filesystem  │ npx -y @model... │ global   │  ← inherited from global config
└─────────────┴──────────────────┴──────────┘
```

### 14.6 Terminal Sessions

CodePlane provides integrated terminal sessions backed by backend PTY processes connected via WebSocket. There are two distinct contexts with different scoping rules.

#### 14.6.1 Global Terminal (Drawer)

The global terminal is a persistent bottom drawer rendered at the `App` level (outside `<Routes>`), so it survives page navigation. It supports:

- **Multiple session tabs** — each tab is an independent PTY session
- **Resize via drag** — drag handle at the top edge; min 150px, max 70% of viewport
- **Maximize / minimize** — toolbar buttons to expand or collapse
- **Keyboard shortcut** — `Ctrl+`` `` ` toggles the drawer open/closed
- **Create / close sessions** — `+` button creates a new session; `×` closes one

Global terminal sessions have no `jobId`. They default their `cwd` to the user's home directory (or the first registered repository).

Job-scoped terminal sessions (those created from the Job Detail screen) do **not** appear in the global drawer tab bar. The drawer filters its session list to exclude any session with a non-null `jobId`.

#### 14.6.2 Per-Job Terminal (Inline Pane)

When a job has a worktree, the Job Detail screen shows a "Terminal" tab alongside Live, Files, Changes, and Artifacts. Clicking this tab creates a terminal session scoped to the job's worktree directory.

Per-job terminals:

- Render **inline** within the Job Detail tab content area (not as a drawer)
- Are scoped to the job's `worktreePath` as `cwd`
- Carry a `jobId` in their session metadata
- Do **not** open or interact with the global terminal drawer
- Are stored in the global Zustand store for WebSocket lifecycle management, but are filtered out of the drawer's tab list

#### 14.6.3 Agent Assistance

Both global and per-job terminal sessions can optionally activate an **Agent Assist** sidecar — a lightweight conversational AI agent scoped to the terminal's working directory.

**Activation**: A robot icon (🤖) in the terminal toolbar toggles the assist panel. When activated, the terminal area splits horizontally:

```
┌──────────────────────────────────────────────┐
│ [Tab1] [Tab2] [+]              [🎤] [🤖] [−]│  ← toolbar: voice, assist toggle, minimize
├────────────────────────┬─────────────────────┤
│                        │  Agent Assist       │
│   Terminal (xterm)     │  ┌───────────────┐  │
│   $ ls -la             │  │ Chat history   │  │
│   $ npm test           │  │ (streaming)    │  │
│                        │  ├───────────────┤  │
│                        │  │ Ask... [🎤][↵]│  │
└────────────────────────┴─────────────────────┘
```

**Agent Assist behavior**:

- The assist agent is a **separate, lightweight agent instance** — not a full job agent. It does not create worktrees, branches, or jobs.
- Scoped to the terminal session's `cwd` for file context.
- Has **read access** to the terminal's scrollback buffer, which is sent as context with each assist message.
- Responses stream as markdown via SSE.
- The agent uses the SDK and model configured in global settings under `terminal.assist` (see §10.2). Per-job terminals inherit the same configuration.
- Assist chat history is ephemeral — cleared when the terminal session is closed.

**Voice input in assist**: The assist chat input includes a microphone button (🎤) that uses the same voice transcription flow as job creation (§9). Transcribed text is inserted into the assist input field.

**Voice input in terminal toolbar**: A microphone button in the terminal toolbar (outside the assist panel) transcribes speech and pastes the result directly into the terminal as typed input.

#### 14.6.4 Terminal Session Lifecycle

| Event | Behavior |
|---|---|
| Session created | Backend spawns a PTY process, returns session ID. Frontend connects via WebSocket |
| Session closed (tab ×) | Frontend sends `DELETE /api/terminal/sessions/{id}`, backend kills PTY |
| Page navigation (global) | Drawer persists — sessions remain connected |
| Page navigation (per-job) | Session remains in store; reconnects if user returns to job |
| Server restart | All PTY sessions are lost; frontend shows "disconnected" state |

---

## 15. Data Model

### 15.1 SQLite Schema

#### jobs

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    branch TEXT,
    worktree_path TEXT,
    session_id TEXT,
    pr_url TEXT,
    merge_status TEXT,
    resolution TEXT,
    archived_at TEXT,
    title TEXT,
    description TEXT,
    worktree_name TEXT,
    permission_mode TEXT NOT NULL DEFAULT 'full_auto',  -- legacy, retained for migration compatibility
    preset TEXT NOT NULL DEFAULT 'supervised',
    session_count INTEGER NOT NULL DEFAULT 1,
    sdk_session_id TEXT,
    model TEXT,
    sdk TEXT NOT NULL DEFAULT 'copilot',
    failure_reason TEXT,
    verify BOOLEAN DEFAULT FALSE,
    self_review BOOLEAN DEFAULT FALSE,
    max_turns INTEGER DEFAULT 2,
    verify_prompt TEXT,
    self_review_prompt TEXT,
    enable_stall_detection BOOLEAN DEFAULT TRUE,
    enable_plan_tracking BOOLEAN DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 0,
    parent_job_id TEXT,
    source TEXT NOT NULL DEFAULT 'managed',
    external_session_id TEXT,
    tail_offset INTEGER NOT NULL DEFAULT 0,
    story_text TEXT,
    trail_state_snapshot TEXT,         -- JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
```

#### events

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,        -- JSON
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX idx_events_job_id ON events(job_id);
CREATE INDEX idx_events_id ON events(id);
```

#### approvals

```sql
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    description TEXT NOT NULL,
    proposed_action TEXT,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    requires_explicit_approval BOOLEAN DEFAULT FALSE,
    batch_id TEXT,
    tier TEXT,                        -- deprecated — retired tier model, no longer populated
    reversible BOOLEAN,               -- deprecated — retired classifier field, no longer populated
    contained BOOLEAN,                -- deprecated — retired classifier field, no longer populated
    checkpoint_ref TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

#### artifacts

```sql
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,              -- see Artifact Types table below
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    disk_path TEXT NOT NULL,
    phase TEXT NOT NULL,
    step_id TEXT,                     -- which plan step produced this artifact
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

#### Artifact Types

| Type | Description | Collection trigger | MIME | Upserts? |
|---|---|---|---|---|
| `diff_snapshot` | Final unified diff of all file changes, serialized as JSON | `store_diff_snapshot()` (manual / test) | `application/json` | No |
| `agent_summary` | LLM-generated session summary (task, accomplishments, decisions, blockers, resume instructions) | Session end → `summarize()` | `application/json` | No (one per session) |
| `session_snapshot` | Raw session state: deduplicated transcript turns + changed files _(legacy — superseded by `session_log`)_ | Session end → `store_session_snapshot()` | `application/json` | No |
| `session_log` | Unified log across all handoff sessions — aggregated transcript turns + changed files per session | Session end → `upsert_session_log()` | `application/json` | Yes — subsequent sessions append |
| `agent_plan` | Agent's internal plan steps with status (`completed`, `in_progress`, `pending`) | Job completion → `_store_post_completion_artifacts()` | `application/json` | Yes — latest plan replaces previous |
| `telemetry_report` | Cost, token, and model-call metrics from the telemetry subsystem | Job completion → `_store_post_completion_artifacts()` | `application/json` | Yes |
| `approval_history` | Chronological record of all approval requests and resolutions for the job | Job completion → `_store_post_completion_artifacts()` | `application/json` | Yes — accumulates across sessions |
| `document` | Human-readable text files: markdown from agent session state (`~/.copilot/session-state/`), and `agent.log` (formatted log lines). Also includes `.md`/`.txt`/`.html`/`.csv`/`.log` files from `.codeplane/artifacts/` | Session end + job completion | `text/markdown` or `text/plain` | Yes — same-named docs updated in place |
| `server_log` | Server-side structlog output scoped to a single job session (JSON Lines) | Session end (see §20.5) | `text/plain` | No (one per session) |
| `cli_log` | Raw stderr/stdout from the agent SDK process for a session | Session end (see §20.5) | `text/plain` | No (one per session) |
| `custom` | Any non-document file placed by the agent in `.codeplane/artifacts/` inside the worktree | Session end → `collect_from_workspace()` | Guessed from extension | No |

##### Collection Sources

**Workspace scan** — At session end, `collect_from_workspace()` scans
`{worktree_path}/.codeplane/artifacts/` for all files (max 50 MB each).
Files are classified by extension:

| Extensions | Type |
|---|---|
| `.md`, `.txt`, `.html`, `.csv`, `.log` | `document` |
| Everything else | `custom` |

**Session storage scan** — `collect_from_session_storage()` scans
`~/.copilot/session-state/{sdk_session_id}/` for `*.md` files (top-level
and `files/` subdirectory). These become `document` artifacts with
`text/markdown` MIME type. Same-named documents are upserted across handoffs.

**Post-completion artifacts** — After a job reaches a terminal state,
`_store_post_completion_artifacts()` creates `telemetry_report`, `agent_plan`,
`approval_history`, and `agent.log` (as `document`). These are only created
when there is data to store (e.g. no `approval_history` if there were zero
approvals).

**Session-scoped log artifacts** — `server_log` and `cli_log` are collected
per §20.5. One pair per session for multi-session jobs.

#### diff_snapshots

```sql
CREATE TABLE diff_snapshots (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    diff_json TEXT NOT NULL,      -- serialized list of DiffFile
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX idx_diff_snapshots_job_id ON diff_snapshots(job_id);
```

#### steps

```sql
CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    turn_id TEXT,
    intent TEXT,
    title TEXT,
    status TEXT,                  -- completed | in_progress | pending
    trigger TEXT,
    tool_count INTEGER,
    agent_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_ms INTEGER,
    start_sha TEXT,
    end_sha TEXT,
    files_read TEXT,              -- JSON array
    files_written TEXT,           -- JSON array
    preceding_context TEXT,       -- JSON
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX ix_steps_job_number ON steps(job_id, step_number);
```

#### job_telemetry_summary

```sql
CREATE TABLE job_telemetry_summary (
    job_id TEXT NOT NULL,
    session_kind TEXT NOT NULL DEFAULT 'job',
    -- ~45 columns tracking cost, tokens, timing, retry stats, approval metrics
    PRIMARY KEY (job_id, session_kind)
);
```

#### job_telemetry_spans

```sql
CREATE TABLE job_telemetry_spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    span_type TEXT,               -- "tool" | "llm"
    name TEXT,
    started_at REAL,
    duration_ms REAL,
    attrs_json TEXT,
    created_at TEXT,
    tool_category TEXT,
    tool_target TEXT,
    turn_number INTEGER,
    execution_phase TEXT,
    is_retry BOOLEAN,
    retries_span_id INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    cost_usd REAL,
    tool_args_json TEXT,
    result_size_bytes INTEGER,
    error_kind TEXT,
    turn_id TEXT,
    preceding_context TEXT,
    motivation_summary TEXT,
    edit_motivations TEXT,         -- JSON
    session_kind TEXT DEFAULT 'job',
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX idx_spans_job ON job_telemetry_spans(job_id);
CREATE INDEX idx_spans_turn_id ON job_telemetry_spans(turn_id);
```

#### job_file_cost

```sql
CREATE TABLE job_file_cost (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    cost_usd REAL,
    read_cost REAL,
    write_cost REAL,
    turn_count INTEGER,
    created_at TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX idx_file_cost_job ON job_file_cost(job_id);
```

#### job_file_access_log

```sql
CREATE TABLE job_file_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    access_type TEXT,             -- read | write
    turn_number INTEGER,
    span_id INTEGER,
    byte_count INTEGER,
    created_at TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

#### job_cost_attribution

```sql
CREATE TABLE job_cost_attribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    bucket TEXT NOT NULL,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    call_count INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    model TEXT,
    created_at TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

#### job_latency_attribution

```sql
CREATE TABLE job_latency_attribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    bucket TEXT NOT NULL,
    wall_clock_ms REAL,
    sum_duration_ms REAL,
    span_count INTEGER,
    p50_ms REAL,
    p95_ms REAL,
    max_ms REAL,
    pct_of_total REAL,
    created_at TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

#### cost_observations

```sql
CREATE TABLE cost_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    severity TEXT,
    title TEXT,
    detail TEXT,
    evidence_json TEXT,
    job_count INTEGER,
    total_waste_usd REAL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    dismissed BOOLEAN DEFAULT FALSE
);
```

#### trail_nodes

```sql
CREATE TABLE trail_nodes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    seq INTEGER,
    anchor_seq INTEGER,
    parent_id TEXT,
    kind TEXT,                    -- goal | turn | reasoning | decision | modify | write | verify | investigate | backtrack
    deterministic_kind TEXT,
    phase TEXT,
    timestamp TEXT,
    enrichment TEXT,
    intent TEXT,
    rationale TEXT,
    outcome TEXT,
    step_id TEXT,
    span_ids TEXT,                -- JSON array
    turn_id TEXT,
    files TEXT,                   -- JSON array
    start_sha TEXT,
    end_sha TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

---

## 16. Persistence

### 16.1 Storage Layout

```
~/.codeplane/
├── config.yaml
├── data.db            # SQLite database
└── artifacts/
    └── {job_id}/
        └── {artifact_id}-{name}
```

### 16.2 Migrations

Schema migrations are managed with Alembic. The backend runs `alembic upgrade head` at startup before accepting requests.

### 16.3 Persistence Layer Design

All database access is mediated through repository classes. No SQLAlchemy sessions are used directly in services or route handlers.

```python
class JobRepository:
    def create(self, job: Job) -> Job: ...
    def get(self, job_id: str) -> Job | None: ...
    def list(self, state: str | None = None) -> list[Job]: ...
    def update_state(self, job_id: str, new_state: str, updated_at: datetime) -> None: ...

class EventRepository:
    def append(self, event: SessionEvent) -> None: ...
    def list_after(self, after_id: int, job_id: str | None = None) -> list[SessionEvent]: ...

class ArtifactRepository:
    def create(self, artifact: Artifact) -> Artifact: ...
    def list_for_job(self, job_id: str) -> list[Artifact]: ...
    def get(self, artifact_id: str) -> Artifact | None: ...
```

### 16.4 Retention Policy

Artifacts and associated data accumulate over time. The retention policy prevents unbounded growth:

- **Artifact retention**: Artifacts older than `retention.artifact_retention_days` (default: 30 days) are deleted from disk and database
- **Maximum artifact size**: Individual artifacts exceeding `retention.max_artifact_size_mb` (default: 100 MB) are rejected at collection time
- **Cleanup schedule**: Retention cleanup runs once daily as a background task. It can also be triggered manually via `POST /api/settings/cleanup-worktrees`
- **Cleanup on startup**: If `retention.cleanup_on_startup` is `true`, cleanup runs during startup after recovery

Retention cleanup removes:
1. Artifact files from `~/.codeplane/artifacts/{job_id}/`
2. Artifact metadata from the `artifacts` table
3. Diff snapshots from the `diff_snapshots` table
4. Worktree directories for jobs in terminal states older than the retention period

Job records and events are never deleted. They serve as an audit log.

### 16.5 Recoverable Task Starts and Tracker Delivery

`TaskExecutionService` is application-scoped and shared by REST, MCP, and completion subscribers. It performs read-only Job preflight before a short serialized transaction that revalidates eligibility, creates the `preparing` Job, and attaches it to its TaskLink. Git discovery and naming do not commit a jobless reservation. Cancellation before commit leaves a retryable task. After commit, the existing runtime recovers the linked Job and prepares its workspace.

Completion projections use conditional state updates. Replayed completion events cannot overwrite a live start. A dependency is satisfied only by a persisted completed Job resolved as `merged` or `pr_created`. Chat gating remains chain-scoped. The task transaction commits and closes before requesting an approval through `ApprovalService`. Structured spawn actions retain the predecessor's Job attempt, and legacy `spawn_task:<id>` approvals remain readable. Startup reconciles abandoned legacy claims and approved starts before admitting new commands, without starting the same Job twice.

Tracker approval and delivery are separate durable facts. Each `tracker_operations` row holds an immutable request identity, exact target and content, canonical approval association, dispatch phase, and outcome. Repeated recipe completion for the same Job attempt reuses the same source identity. The managed worker survives a canceled request caller and reconstructs approval associations after restart. Tracker output failure never changes a successfully completed task into a failed task.

Operations progress through `awaiting_approval`, `approved`, and `dispatching`. An acknowledged write becomes `applied`, a denial becomes `rejected`, and a definite nondelivery becomes `failed`. A lost response or interrupted dispatch becomes `uncertain`. Neither restart nor retry automatically repeats an uncertain comment. The operator must record evidence of delivery or nondelivery. Confirmed nondelivery becomes `not_applied`; a subsequent retry is a distinct linked operation requiring fresh explicit approval. Reconciliation and retry use an expected-version compare-and-set.

Live Job, Project, TaskLink, TrackerLink, and Approval correlations use nullable `ON DELETE SET NULL` foreign keys. Immutable references and approval/receipt/reconciliation evidence remain available for history. Missing live dependencies prohibit dispatch; snapshots do not authorize a write or silently bind a replacement target. Existing archive and artifact/worktree retention behavior remains unchanged. Legacy approved tracker requests without dispatch evidence are imported as uncertain, never assumed delivered or replayed.

`tracker.operation_updated` is an allowlisted SSE event delivered after the operation commit. It reaches global connections and the associated Job connection. An operation with a deleted Job uses an unassociated global event rather than inserting an invalid Job-FK event. The frontend stores these records centrally, hydrates them after reconnect, and protects newer versions and deleted correlations from stale replay.

GitHub tracker reads display Projects v2 **Project Status**. The existing GitHub `transition` command changes the underlying issue's **Issue state** (`open` or `closed`), including the existing `Done` alias for closing an issue. Approval and outcome labels name that operation explicitly. It does not mutate Project Status.

Project-scoped history and operator commands are available at:

- `GET /api/projects/{project_id}/tracker-links/operations`, optionally filtered by `job_ref` or `tracker_link_ref`.
- `GET /api/projects/{project_id}/tracker-links/operations/{operation_id}`.
- `POST .../{operation_id}/reconcile` with `expectedVersion`, `resolution`, and delivery `evidence`.
- `POST .../{operation_id}/retry` with `expectedVersion` and a stable caller `requestId`.

The existing tracker panel shows pending approval, precise targets, receipts, failures, and uncertain delivery. Evidence is retained after a recoverable error. An uncertain operation has no retry action until nondelivery is established.

---

## 17. REST API

All endpoints are prefixed with `/api`.

Authentication is handled by password-based session cookies when accessed remotely via tunnel (see §21.1). The backend binds to `127.0.0.1` by default, so direct access is limited to the local machine. Localhost requests bypass authentication.

### 17.1 Health Check

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Service health and status |

The health endpoint does **not** require authentication. It returns:

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "uptime_seconds": 3621.5,
  "active_jobs": 1,
  "queued_jobs": 0
}
```

### 17.2 Jobs

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs` | Create a new job |
| `GET` | `/api/jobs` | List jobs (filterable, paginated) |
| `GET` | `/api/jobs/{job_id}` | Get full job detail |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a running or queued job |
| `POST` | `/api/jobs/{job_id}/rerun` | Create a new job from this job's config |
| `POST` | `/api/jobs/{job_id}/messages` | Send an operator message to a running job |
| `POST` | `/api/jobs/{job_id}/continue` | Continue a job with an instruction |
| `POST` | `/api/jobs/{job_id}/resume` | Resume a job from review state |
| `PUT` | `/api/jobs/{job_id}` | Update job metadata (title, description) |
| `POST` | `/api/jobs/{job_id}/resolve` | Resolve a review job (merge/PR/discard) |
| `POST` | `/api/jobs/{job_id}/archive` | Archive a terminal job |
| `POST` | `/api/jobs/{job_id}/unarchive` | Unarchive a job |
| `POST` | `/api/jobs/{job_id}/restore` | Restore an archived job |
| `POST` | `/api/jobs/suggest-names` | Suggest title/branch/worktree names from prompt |

#### Job Data Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/logs` | Get job log lines |
| `GET` | `/api/jobs/{job_id}/diff` | Get current diff files |
| `GET` | `/api/jobs/{job_id}/transcript` | Get agent transcript |
| `GET` | `/api/jobs/{job_id}/transcript/search` | Full-text search transcript |
| `GET` | `/api/jobs/{job_id}/steps` | Get plan steps |
| `GET` | `/api/jobs/{job_id}/steps/{step_id}/diff` | Get diff for a single step |
| `GET` | `/api/jobs/{job_id}/timeline` | Get progress headline timeline |
| `GET` | `/api/jobs/{job_id}/snapshot` | Full state hydration (for reconnect/refresh) |
| `GET` | `/api/jobs/{job_id}/telemetry` | Get job telemetry data |
| `GET` | `/api/jobs/{job_id}/story` | Get narrative story of job execution |
| `GET` | `/api/jobs/{job_id}/trail` | Get structured intent graph (audit trail) |
| `GET` | `/api/jobs/{job_id}/trail/summary` | Get lightweight trail summary |

#### Pagination

List endpoints support cursor-based pagination:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | 50 | Maximum items to return (max: 200) |
| `cursor` | string | _(none)_ | Opaque cursor from previous response |
| `state` | string | _(none)_ | Comma-separated state filter |

Response envelope for list endpoints:

```json
{
  "items": [...],
  "cursor": "eyJpZCI6ICJqb2ItNTAifQ",
  "has_more": true
}
```

#### Create Job — Request

```json
POST /api/jobs
{
  "repo": "/repos/service-a",
  "prompt": "Fix the null pointer in UserService",
  "base_ref": "main",
  "sdk": "claude",
  "model": "claude-sonnet-4-20250514"
}
```

The `sdk` field selects the agent SDK adapter (default: `runtime.default_sdk` from config). The `model` field overrides the LLM model; it must be compatible with the selected SDK (see §4.4). Incompatible combinations return `400 Bad Request`.

#### Create Job — Response

```json
201 Created
{
  "id": "job-104",
  "state": "running",
  "branch": "fix/null-pointer-in-userservice",
  "worktreePath": "/repos/service-a",
  "sdk": "claude",
  "createdAt": "2025-01-01T12:00:00Z"
}
```

#### Send Operator Message — Request

```json
POST /api/jobs/{job_id}/messages
{
  "content": "Also add unit tests for the fix."
}
```

#### Send Operator Message — Response

```json
200 OK
{
  "seq": 5,
  "timestamp": "2025-01-01T12:10:00Z"
}
```

The endpoint returns `409 Conflict` if the job is not in `running` state.

### 17.3 Events (SSE)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/events` | SSE stream for all jobs |
| `GET` | `/api/events?job_id={id}` | SSE stream scoped to one job |

### 17.4 Approvals

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/approvals` | List approvals for a job |
| `POST` | `/api/approvals/{approval_id}/resolve` | Approve or reject |
| `POST` | `/api/jobs/{job_id}/approvals/trust` | Auto-approve all current and future requests for this session |
| `POST` | `/api/jobs/{job_id}/batches/resolve` | Resolve an action policy batch |

#### Resolve Approval — Request

```json
POST /api/approvals/{approval_id}/resolve
{
  "resolution": "approved"    // or "rejected"
}
```

### 17.5 Artifacts

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/artifacts` | List artifacts for a job |
| `GET` | `/api/artifacts/{artifact_id}` | Download artifact file |

### 17.6 Workspace

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/workspace` | List files in job's worktree (paginated, max 200 entries) |
| `GET` | `/api/jobs/{job_id}/workspace/file` | Get file contents (`?path=relative/path`) |

### 17.7 Voice

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/voice/transcribe` | Upload audio, receive transcript |

### 17.8 Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/settings` | Get current settings |
| `PUT` | `/api/settings` | Update settings (structured, partial) |
| `GET` | `/api/settings/repos` | List registered repos |
| `GET` | `/api/settings/repos/{repo_path}` | Get detailed repo config with resolved MCP servers |
| `GET` | `/api/settings/repos/{repo_path}/summary` | Get aggregated repo overview |
| `GET` | `/api/settings/repos/{repo_path}/jobs` | List jobs for a repo |
| `GET` | `/api/settings/repos/{repo_path}/memory` | Get repo workspace memory |
| `POST` | `/api/settings/repos/{repo_path}/register` | Register a repository (local path or remote URL) |
| `POST` | `/api/settings/repos/{repo_path}/create` | Create a new repository |
| `POST` | `/api/settings/repos/{repo_path}/browse` | Browse directory structure |
| `POST` | `/api/settings/cleanup-worktrees` | Clean up completed job worktrees |
| `GET` | `/api/settings/sdks` | List available agent SDKs and their status |
| `GET` | `/api/settings/platforms` | List platform auth status |

#### `POST /api/settings/repos` — Register Repository

Request body:

```json
{
  "source": "/repos/service-c"
}
```

`source` is either an absolute local path or a remote Git URL (HTTPS/SSH). The backend auto-detects the type:

- **Local path** (starts with `/` or `~`): validated as an existing Git repository
- **Remote URL** (contains `://` or matches `git@`): cloned to the target specified by `clone_to` (or derived from URL) via `git clone` subprocess

Response (`201 Created`):

```json
{
  "path": "/home/user/codeplane-repos/org/repo",
  "source": "https://github.com/org/repo.git",
  "cloned": true
}
```

Errors: `400` if path doesn't exist or isn't a git repo, `409` if already registered, `502` if clone fails (auth/network).

#### `GET /api/settings/repos/{repo_path}` — Repository Detail

Returns the fully resolved configuration for a single repository, including the MCP server resolution chain.

Response (`200 OK`):

```json
{
  "path": "/repos/service-a",
  "origin_url": "https://github.com/org/service-a.git",
  "base_branch": "main",
  "protected_paths": ["infra/", ".github/workflows/"],
  "mcp_servers": [
    {
      "name": "github",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "source": "local",
      "overrides_global": true
    },
    {
      "name": "postgres",
      "command": "uvx",
      "args": ["mcp-postgres"],
      "source": "disabled",
      "disabled_by": ".codeplane.yml"
    },
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem"],
      "source": "global",
      "overrides_global": false
    }
  ],
  "active_job_count": 1,
  "recent_jobs": [{"id": "job-42", "state": "completed", "prompt": "..."}]
}
```

`source` is one of: `local` (from `.vscode/mcp.json`), `global` (inherited from global config), `disabled` (present but blocked by `.codeplane.yml`).

### 17.9 SDKs

The SDK listing endpoint moved to `GET /api/settings/sdks` (see §17.8).

Returns all registered SDKs, their installation/configuration status, and which is the default.

```json
{
  "default": "copilot",
  "sdks": [
    {
      "id": "copilot",
      "name": "GitHub Copilot",
      "enabled": true,
      "status": "ready"
    },
    {
      "id": "claude",
      "name": "Claude Agent SDK",
      "enabled": true,
      "status": "ready"
    }
  ]
}
```

`status` is one of: `ready` (installed and configured), `not_installed` (SDK package missing), `not_configured` (package installed but credentials missing).

### 17.10 Terminal Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/terminal/sessions` | Create a new PTY session |
| `GET` | `/api/terminal/sessions` | List active sessions |
| `DELETE` | `/api/terminal/sessions/{id}` | Kill a PTY session and clean up |
| `WebSocket` | `/api/terminal/ws` | WebSocket connection for terminal I/O |
| `POST` | `/api/terminal/ask` | Translate natural language to a shell command |

#### `POST /api/terminal/sessions` — Create Session

Request body:

```json
{
  "cwd": "/repos/service-a",
  "jobId": null
}
```

`cwd` defaults to the first registered repository when `null`. `jobId` is set when creating a job-scoped terminal.

Response (`201 Created`):

```json
{
  "id": "term-abc123",
  "cwd": "/repos/service-a",
  "jobId": null
}
```

#### `POST /api/terminal/ask` — Natural Language to Shell Command

Request body:

```json
{
  "message": "How do I find all Python files modified in the last week?",
  "cwd": "/repos/service-a"
}
```

Response (`200 OK`):

```json
{
  "command": "find . -name '*.py' -mtime -7"
}
```

### 17.11 Connection Limits

SSE connections are limited to `max_sse_connections` concurrent connections (default: 5).

Voice transcription uploads are limited to `max_audio_size_mb` (default: 10 MB).

No per-request rate limiting is applied. CodePlane runs on a single developer machine accessed by one browser and optionally one phone — throttling REST requests adds complexity without value.

### 17.13 Error Responses

All errors return a consistent envelope:

```json
{
  "error": {
    "code": "JOB_NOT_FOUND",
    "message": "Job job-999 does not exist."
  }
}
```

| HTTP Status | Condition |
|---|---|
| 400 | Validation error in request |
| 403 | Repository not in allowlist, or protected path violation |
| 404 | Resource not found |
| 409 | State conflict (e.g., cancel an already-completed job) |
| 413 | Payload too large (voice upload exceeds `max_audio_size_mb`) |
| 500 | Internal server error |

#### Error Codes

| Code | Used by | Description |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Request body failed Pydantic validation |
| `REPO_NOT_ALLOWED` | 403 | Repository path not in allowlist |
| `PROTECTED_PATH` | 403 | Operation targets a protected path |
| `JOB_NOT_FOUND` | 404 | Job ID does not exist |
| `APPROVAL_NOT_FOUND` | 404 | Approval ID does not exist |
| `ARTIFACT_NOT_FOUND` | 404 | Artifact ID does not exist |
| `FILE_NOT_FOUND` | 404 | Workspace file path does not exist |
| `SHARE_TOKEN_INVALID` | 404 | Share token does not exist or has expired |
| `STATE_CONFLICT` | 409 | Action invalid for current job state |
| `PAYLOAD_TOO_LARGE` | 413 | Upload exceeds size limit |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

### 17.12 Job Sharing

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs/{job_id}/share` | Generate a read-only share token (24 h TTL) |
| `GET` | `/api/share/{token}/job` | Job detail via share token (bypasses password auth) |
| `GET` | `/api/share/{token}/events` | SSE stream for the shared job (bypasses password auth) |

#### Create Share Link — Response

```json
201 Created
{
  "token": "dGhpcyBpcyBhIHRva2Vu...",
  "jobId": "job-104",
  "url": "https://tunnel-url/shared/dGhpcyBpcyBhIHRva2Vu..."
}
```

Share tokens are ephemeral (in-memory, 24-hour TTL). If the server restarts, all tokens are invalidated. Share endpoints bypass CodePlane's password authentication but provide strictly read-only access — no approval, cancel, or message operations are possible.

> **Scope:** Share tokens bypass CodePlane's password gate only. They do **not** bypass tunnel-level identity gates (Dev Tunnels requires Microsoft login; Cloudflare Tunnels requires the Access policy). Share links are useful for giving read-only access to team members who can already reach the server — e.g. via the same tunnel, LAN, or localhost.

### 17.13 Push Notifications

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/notifications/vapid-key` | Fetch the VAPID application server key |
| `POST` | `/api/notifications/subscribe` | Register a browser push subscription |
| `POST` | `/api/notifications/unsubscribe` | Remove a push subscription |

Push notification delivery is triggered automatically by the event bus when
`permission.requested`, `job.completed`, or `job.failed` events are published.
Subscriptions are stored in-memory; clients re-subscribe via the service worker
on reconnect.

### 17.14 Port Preview Proxy

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/preview/{port}/{path}` | Reverse-proxy to `127.0.0.1:{port}` |

Ports are restricted to 1024–65535. The proxy only forwards to `127.0.0.1` (no SSRF). Returns `502` if the target port is not listening.

### 17.15 Analytics

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/analytics/overview` | High-level fleet metrics |
| `GET` | `/api/analytics/models` | Model usage and cost |
| `GET` | `/api/analytics/tools` | Tool execution stats |
| `GET` | `/api/analytics/repos` | Per-repo analytics |
| `GET` | `/api/analytics/jobs` | Job-level analytics |
| `GET` | `/api/analytics/pricing` | Model pricing data |
| `GET` | `/api/analytics/cost-drivers/{job_id}` | Job cost breakdown |
| `GET` | `/api/analytics/cost-drivers` | Fleet cost drivers |
| `GET` | `/api/analytics/latency-drivers` | Latency analysis |
| `GET` | `/api/analytics/file-access/{job_id}` | File access patterns |
| `GET` | `/api/analytics/turn-economics/{job_id}` | Per-turn cost |
| `GET` | `/api/analytics/scorecard` | Executive scorecard |
| `GET` | `/api/analytics/model-comparison` | Model performance comparison |
| `GET` | `/api/analytics/observations` | Cost observations and anomalies |
| `POST` | `/api/analytics/observations/{id}/dismiss` | Dismiss an observation |
| `POST` | `/api/analytics/analyse` | Trigger analysis |
| `GET` | `/api/analytics/shell-commands` | Shell command analysis |
| `GET` | `/api/analytics/retry-cost` | Retry cost analysis |
| `GET` | `/api/analytics/edit-efficiency` | Edit efficiency metrics |
| `GET` | `/api/analytics/yield` | Yield analysis |
| `GET` | `/api/analytics/model-efficiency` | Model efficiency metrics |
| `GET` | `/api/analytics/cache-efficiency` | Prompt cache efficiency |
| `GET` | `/api/analytics/file-cost` | Per-file cost attribution |
| `GET` | `/api/analytics/outcome-matrix` | Outcome matrix |
| `GET` | `/api/analytics/executive-summary` | High-level summary |
| `GET` | `/api/analytics/export` | Export analytics data |
| `GET` | `/api/analytics/sidecar-costs` | Sidecar cost breakdown |

### 17.16 Action Policy

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/settings/policy` | Full policy config (preset, governance config, USD ceilings) |
| `PUT` | `/api/settings/policy/preset` | Update active preset |
| `PUT` | `/api/settings/policy/config` | Update policy config |
| `GET` | `/api/settings/policy/usd-ceilings` | Per-preset USD spend ceilings |
| `PUT` | `/api/settings/policy/usd-ceilings` | Update per-preset USD ceilings |
| `GET` | `/api/settings/policy/mcp-servers` | List MCP server policies |
| `POST` | `/api/settings/policy/mcp-servers` | Add an MCP server policy |
| `PUT` | `/api/settings/policy/mcp-servers/{name}` | Update an MCP server policy |
| `DELETE` | `/api/settings/policy/mcp-servers/{name}` | Remove an MCP server policy |
| `GET` | `/api/settings/policy/export` | Export policy config |
| `POST` | `/api/settings/policy/import` | Import policy config |

### 17.17 Workspace Memory

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/repos/{repo_path}/memory` | Read workspace memory |
| `GET` | `/api/repos/{repo_path}/memory/detail` | Read memory with sections |
| `PUT` | `/api/repos/{repo_path}/memory` | Update memory |
| `POST` | `/api/repos/{repo_path}/memory/compact` | Compact memory using LLM |

### 17.18 Sidecar Templates

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sidecar-templates` | List templates |
| `GET` | `/api/sidecar-templates/{template_id}` | Get template |
| `POST` | `/api/sidecar-templates` | Create template |
| `PUT` | `/api/sidecar-templates/{template_id}` | Update template |
| `DELETE` | `/api/sidecar-templates/{template_id}` | Delete template |
| `POST` | `/api/sidecar-templates/generate` | Generate sidecar definition |

### 17.19 Utility Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/utility-sessions/warm` | Create warm utility session |
| `DELETE` | `/api/utility-sessions/{token}` | Delete session |

### 17.20 Hooks

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/hooks/claude` | Claude integration webhook |

---

## 18. Approval System

### 18.1 Purpose

Approval gates intercept risky operations before they execute. This ensures the operator maintains control over destructive or irreversible actions.

### 18.2 Hard-Gated Commands

Certain shell commands are **always** routed to the operator for approval, regardless of the active preset — even in `autonomous` mode. These commands are irreversible or bypass CodePlane's managed workflows:

| Pattern | Reason |
|---------|--------|
| `git merge …` | Bypasses CodePlane merge controls |
| `git pull …` | Implicitly merges remote changes |
| `git rebase …` | Rewrites history / merges changes |
| `git cherry-pick …` | Merges a specific commit |
| `git reset --hard …` | Destructive — discards uncommitted work |

Hard-gated commands return `ask` from the permission policy. The agent system prompt also instructs the agent not to run these commands; the hard gate is a defence-in-depth backstop.

### 18.3 Action Policy Presets

CodePlane delegates the permission decision to **`traceforge.governance`**. Three presets control posture; each preset *selects a governance profile* — a recommendation-rules file, a count/effect budget, protected-path and cost-pressure primitives, and CodePlane's per-preset USD spend ceiling — rather than mapping actions to a fixed tier. The profiles live in `action_policy/preset_profiles.py`.

| Preset | Governance profile |
|------|----------|
| `autonomous` | Generous budget; protected-path writes escalate; no USD ceiling. Escalations handled by the monitor sidecar. |
| `supervised` (default) | Moderate budget and USD ceiling; cost pressure and protected-path writes escalate; monitor handles escalations. |
| `locked` | Tight budget and USD ceiling; protected-path writes denied; hard tool-call ceiling; monitor disabled — escalations go directly to the human operator. |

#### Recommendation → enforcement

For each action the governance substrate returns a `RecommendedAction` plus a risk score/band and a reason code. The router maps the recommendation to a concrete outcome:

| RecommendedAction | Enforcement |
|------|----------|
| `ALLOW` | Proceed, no interruption |
| `WARN` | Git savepoint, proceed, notify (operator can intervene) |
| `TRANSFORM` | Treated as `WARN` (advisory; CodePlane applies no auto-edit) |
| `ESCALATE` | LLM monitor (if enabled and not security-critical) → human approval gate |
| `DENY` | Human approval gate (the monitor is skipped — a `DENY` always reaches a human) |

A `None` recommendation (no rule fired, risk below every threshold) is an implicit `ALLOW`; any internal error fails **closed** to `ESCALATE`.

#### Hard-Gated Commands

Hard-gated commands (§18.2) are pre-checked **before** governance scoring and always route to the operator regardless of preset, trust grant, or recommendation.

#### Configuration

Preset is resolved with this priority chain (first match wins):

1. **Per-job** — `preset` field in `POST /api/jobs` request body
2. **Global** — via the policy settings API

Default: `supervised`

### 18.4 Delegation to the Agent Runtime

The underlying agent SDK (Copilot SDK, Claude Code, etc.) decides **which** actions surface a permission request. CodePlane's action policy router then evaluates the request through `traceforge.governance` and enforces the resulting `RecommendedAction`.

CodePlane's role is to:

1. **Evaluate** the SDK's permission request against the active preset's governance profile
2. **Proceed** on `ALLOW` (and on `WARN`/`TRANSFORM`, with a git savepoint + notification)
3. **Route** `ESCALATE` to the monitor sidecar (if enabled) or the operator, and `DENY` straight to the operator
4. **Relay** the resulting decision back to the SDK
5. **Persist** approval requests and resolutions for auditability
6. **Select** the per-preset governance profile (budget, protected paths, USD ceiling) at session creation time

### 18.5 Approval Request Object

```json
{
  "id": "appr-88",
  "job_id": "job-104",
  "description": "Agent wants to delete all files in /tmp/build",
  "proposed_action": "rm -rf /tmp/build",
  "requested_at": "2025-01-01T12:05:00Z"
}
```

The adapter normalizes whichever fields the SDK provides into this common shape. Fields the SDK doesn't supply are omitted.

### 18.6 Approval Flow

1. SDK raises a permission request (e.g., Copilot SDK calls `on_permission_request`)
2. The action policy router evaluates the request:
   - **Hard-gated commands (§18.2):** pre-checked before governance — always route to the operator regardless of preset
   - **Governance decision:** `traceforge.governance` returns a `RecommendedAction` (plus risk score/band and reason code) from the active preset's profile
3. Based on the recommendation:
   - **`ALLOW`:** action proceeds immediately
   - **`WARN` / `TRANSFORM`:** a git savepoint is taken, the action proceeds, and the operator is notified
   - **`ESCALATE` / `DENY`:** the action is blocked pending resolution
4. If `ESCALATE`, the monitor sidecar is enabled (autonomous/supervised presets), and the verdict is not security-critical:
   a. Monitor sidecar evaluates the action
   b. Monitor may auto-approve, auto-reject, or escalate to human
5. If human approval required:
   a. `ApprovalService` persists the request
   b. `approval_request` event emitted
   c. Job transitions to `waiting_for_approval`
   d. `permission.requested` SSE event sent to frontend
   e. Frontend renders approval banner on Job Detail screen
   f. Operator clicks Approve or Reject
   g. `POST /api/approvals/{id}/resolve` called
   h. `ApprovalService` persists the resolution and unblocks the adapter's Future
   i. Adapter returns the decision to the SDK
   j. `permission.resolved` event published
   k. Job transitions back to `running`

The SDK's `on_permission_request` callback is **async** — it blocks the SDK at the callback level while waiting for the operator's response. This ensures the action does not proceed until approved.

#### Trust Grant

Operators can issue a **trust grant** via `POST /api/jobs/{job_id}/approvals/trust`. Trust is applied inside governance as a time-boxed (24h), reason-code-scoped grant: a later matching recommendation comes back `ALLOW` until the grant expires. Security-critical verdicts (§18.2 / hard gates) are never waived by a trust grant.

### 18.7 Approval Timeout

Approval requests do not expire automatically. The job remains in `waiting_for_approval` state indefinitely until the operator responds. This is intentional for a single-operator tool.

The frontend provides visibility:

- Pending approvals older than 30 minutes show an "Aging" warning badge
- The dashboard sorts approvals by age (oldest first)
- A persistent toast notification remains visible if the operator is on a different screen

---

## 19. Diff Model

### 19.1 Diff Generation

Diffs are generated relative to the base branch using git:

```bash
git diff {base_ref}...HEAD
```

This command is run inside the job's worktree. The output is standard unified diff format.

### 19.2 Hunk Parsing

The `DiffService` parses raw unified diff output into structured `DiffFileModel` and `DiffHunkModel` Pydantic objects (see Section 4.6).

Parser responsibilities:

- Extract changed file paths (old and new)
- Detect file status (added, modified, deleted, renamed)
- Parse hunk headers (`@@ -a,b +c,d @@`)
- Classify each line as context, addition, or deletion
- Count additions and deletions per file

### 19.3 Diff Updates

`diff.updated` events are emitted:

- When the agent writes a file (debounced)
- When a validation phase completes
- When the job reaches a terminal state

#### Debounce Behavior

Diff recalculation uses **per-job throttling** (not trailing-edge debounce):

- After a file-change event from the agent adapter, the `DiffService` schedules a diff recalculation
- If a diff was calculated for this job within the last 5 seconds, the request is skipped
- At most one diff recalculation runs per job per 5-second window
- The final diff at job completion always runs regardless of throttle state

Diff recalculation is triggered by the adapter's `file_changed` events (emitted when the agent uses file-write or file-edit tools), not by filesystem watching. This avoids race conditions with partial file writes.

### 19.4 Diff Snapshots

At job completion, a final diff snapshot is stored in the `diff_snapshots` table. This snapshot represents the full set of changes produced by the job.

Diff snapshots are not exposed via a dedicated REST endpoint. The frontend receives live diffs via SSE `diff.updated` events during execution, and the final diff is available as a `diff_snapshot` artifact via `GET /api/jobs/{job_id}/artifacts`. Historical intermediate snapshots are stored for future features (e.g., diff timeline playback) but are not served in v1.

### 19.5 Frontend Diff Rendering

The `DiffViewer` component:

- Lists all changed files with status icons and line count badges
- Expands a file to show its hunks
- Renders each line with syntax highlighting and color coding (green for additions, red for deletions, gray for context)
- Supports collapsing unchanged context blocks
- Shows a summary bar: total files changed, total additions, total deletions

---

## 20. Observability

### 20.1 Job Health

The Job Detail screen exposes:

- Current state with color-coded badge
- Session heartbeat timestamp (updated by canonical `session.heartbeat` events every 30 seconds)
- If no heartbeat received in 90 seconds, a "Session unresponsive" warning is shown

### 20.2 Runtime Logs

Logs are streamed in real time via SSE `log` events.

Backend logging uses human-readable format via Python's `structlog` library with console renderer:

```python
log.info("job_started", job_id=job_id, repo=repo)
# Output: 2025-01-01 12:00:00 [info] job_started  job_id=job-104 repo=/repos/service-a
```

Every log line carries:

- `timestamp`
- `level`
- `message`
- `job_id` (when applicable)
- Additional structured context fields

#### Log Rotation

Backend logs are written to `~/.codeplane/logs/server.log` using Python's `RotatingFileHandler`:

| Parameter | Default |
|---|---|
| Max file size | 50 MB (`logging.max_file_size_mb`) |
| Backup count | 3 (`logging.backup_count`) |
| Format | Human-readable (`structlog` console renderer) |

This produces files: `server.log`, `server.log.1`, `server.log.2`, `server.log.3`.

#### Terminal Output

Stdout does not show raw log lines. Instead, `cpl up` renders a live status display using `rich` showing:

- Server URL and tunnel URL (if active)
- Active jobs table: job ID, repo, state, elapsed time
- Aggregate metrics: jobs running, queued, completed, failed
- Last few significant events (job created, completed, failed, approval requested)

This provides an at-a-glance operational view without overwhelming the terminal. Full logs remain in `~/.codeplane/logs/server.log`.

### 20.3 Failure Diagnostics

When a job enters `failed` state, the Job Detail screen shows:

- The error message from the `job.failed` event payload
- The traceback (if available)
- The last log lines before failure
- The last transcript entries

### 20.4 Session Heartbeat

The runtime generates a canonical `session.heartbeat` domain event every 30
seconds for each running session. The SDK itself may not provide periodic
heartbeats, so the runtime maintains its own timer per active session. The
frontend uses these to display session health status. This is distinct from the
transport-only SSE `session_heartbeat` keepalive frame described in
`docs/reference/sse-events.md`.

#### Heartbeat Watchdog

If no heartbeat is received for a running session within 90 seconds:

1. The frontend shows a "Session unresponsive" warning badge
2. The backend logs a warning: `log.warning("session_unresponsive", job_id=job_id, last_heartbeat=...)`
3. After 5 minutes without a heartbeat, the backend auto-cancels the job with `reason: "heartbeat_timeout"` and transitions it to `failed`
4. The operator can rerun the job if desired

### 20.5 Session-Scoped Log Artifacts

Live `log` SSE events provide real-time log streaming during a running session.
Once a session ends, those events remain queryable via `GET /api/jobs/{job_id}/logs`,
but the result is limited to the domain events that were published to the internal
event bus — essentially the "application layer" view.

Many operationally useful logs never reach the event bus:

- **Server-side request logs** — full structlog output for a job's lifecycle
  (adapter calls, git operations, workspace setup, teardown, error traces).
- **CLI / agent SDK stderr** — raw output from the agent process, including
  SDK debug messages, retry traces, and crash diagnostics.

Session-scoped log artifacts capture these richer log streams and persist them
as downloadable artifacts attached to the job.

#### 20.5.1 New Artifact Types

Two new `ArtifactType` values:

| Type | Description |
|---|---|
| `server_log` | Server-side structlog output scoped to a single job session |
| `cli_log` | Raw stderr/stdout captured from the agent SDK process for a session |

Both are plain-text files (`text/plain`) stored under the standard artifact
path `~/.codeplane/artifacts/{job_id}/`.

#### 20.5.2 Server Log Collection

The backend already writes all log output to `~/.codeplane/logs/server.log`.
Per-job server log artifacts add **job-scoped extraction** on top of that:

1. **During execution**, every structlog record that carries a `job_id` context
   field is _also_ appended to an in-memory ring buffer keyed by `(job_id, session_number)`.
   The buffer is bounded at **10 MB** per session to prevent runaway memory use.
2. **On session end** (any terminal state transition — `completed`, `failed`, `canceled`),
   the buffer is flushed to disk as a `server_log` artifact.
3. **Format**: One JSON object per line (JSON Lines / `.jsonl`), preserving all
   structlog fields. This keeps the artifact machine-parseable while remaining
   human-readable.
4. **Fallback**: If the in-memory buffer was evicted (e.g. after an OOM restart),
   the collector falls back to scanning `server.log` for lines matching the
   `job_id`. This is best-effort and may be incomplete if log rotation has
   already discarded the relevant segment.

Ring buffer configuration:

| Parameter | Config key | Default |
|---|---|---|
| Max buffer per session | `logging.job_log_buffer_mb` | 10 |
| Enable job log artifact | `logging.collect_job_logs` | `true` |

#### 20.5.3 CLI / Agent Process Log Collection

When the adapter spawns or communicates with an agent SDK process, stderr and
stdout output that is _not_ parsed into structured domain events (transcript,
tool calls, etc.) is captured as the **CLI log**:

1. **During execution**, the adapter tees process stderr/stdout into a
   per-session temporary file under `~/.codeplane/tmp/{job_id}/`.
2. **On session end**, the temp file is promoted to a `cli_log` artifact.
   If the file is empty (agent produced no unstructured output), no artifact
   is created.
3. **Format**: Raw text, preserving ANSI escape codes. The frontend renders
   these with a monospace viewer and optional ANSI-stripping toggle.
4. **Size cap**: The temp file is truncated (tail-kept) at **50 MB**.

#### 20.5.4 Multi-Session Jobs

Jobs that are resumed or handed off produce multiple sessions. Each session
generates its own `server_log` and `cli_log` artifact, named with the session
number suffix:

- `server-log-session-1.jsonl`
- `server-log-session-2.jsonl`
- `cli-log-session-1.txt`
- `cli-log-session-2.txt`

The `session_number` is stored in the artifact's metadata (a new optional field
on `ArtifactResponse`) so the frontend can group and label them.

#### 20.5.5 REST API

No new endpoints. Log artifacts are served through the existing artifact
endpoints:

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/artifacts` | Returns all artifacts; filter client-side by `type` |
| `GET` | `/api/artifacts/{artifact_id}` | Download the log file |

A new optional query parameter on the list endpoint:

```
GET /api/jobs/{job_id}/artifacts?type=server_log,cli_log
```

Accepts a comma-separated list of `ArtifactType` values. When provided, only
artifacts matching one of the listed types are returned. Omit to return all
types (backward-compatible).

#### 20.5.6 Schema Changes

**`ArtifactType` enum** — add two values:

```python
class ArtifactType(StrEnum):
    # ... existing ...
    server_log = "server_log"
    cli_log = "cli_log"
```

**`ArtifactResponse`** — add optional session metadata:

```python
class ArtifactResponse(CamelModel):
    # ... existing fields ...
    session_number: int | None = None   # which session produced this artifact
```

**`LogLinePayload`** — add optional `source` discriminator:

```python
class LogLinePayload(CamelModel):
    # ... existing fields ...
    source: Literal["event_bus", "server_log", "cli_log"] | None = None
```

This allows the Logs Panel to merge live SSE log lines with lines loaded from
log artifacts and show the provenance of each entry.

#### 20.5.7 Frontend: Enhanced Logs Panel

The Logs Panel (§14.2) gains the following capabilities:

| Feature | Description |
|---|---|
| **Source tabs** | Three sub-tabs: _Live_ (existing SSE log lines), _Server Log_ (from `server_log` artifact), _CLI Output_ (from `cli_log` artifact). Default: Live while job is running; Server Log once job completes |
| **Session selector** | Dropdown to pick a session number when multiple sessions exist. Defaults to the latest session |
| **Artifact availability** | Server Log and CLI Output tabs are disabled with a "Collecting…" badge while the job is still running. They become available once the session ends and artifacts are persisted |
| **Full-text search** | A search input filters visible log lines by substring match (case-insensitive). Applied client-side over the loaded log content |
| **Download button** | Each artifact tab includes a download button that fetches the raw file via `GET /api/artifacts/{id}` |
| **ANSI rendering** | CLI Output tab renders ANSI escape codes for colored output. A toggle strips codes for plain-text viewing |
| **Level filtering** | Existing level filter (debug/info/warn/error) continues to work on the Live tab. Server Log tab applies the same filter over the parsed JSON Lines. CLI Output tab has no level filter (raw text) |

The Live tab behavior is unchanged from the current implementation — SSE
`log` events streamed in real time, merged with historical fetch on
reconnect.

#### 20.5.8 Retention

Log artifacts follow the same retention policy as all other artifacts
(§16.4): deleted after `retention.artifact_retention_days` (default 30 days).
The global `server.log` file rotation (§20.2) is independent and unaffected.

---

## 21. Security Model

### 21.1 Authentication

CodePlane uses **password-based authentication** for remote access and **localhost trust** for local access:

1. **Localhost binding**: The backend binds to `127.0.0.1` by default, making it accessible only from the local machine. Localhost requests bypass password auth entirely.
2. **Password auth for remote access**: When `--remote` is used, a password is required. It is set explicitly (`--password`, `CPL_PASSWORD` env var / `.env`) or auto-generated. Remote clients must authenticate via the login page; sessions use httpOnly cookies with 24h expiry.
3. **Relay-level identity gate (provider-dependent)**: The security posture of the relay layer differs by provider:

| Provider | Relay Identity Gate | Effect |
|----------|-------------------|--------|
| **Dev Tunnels** | Microsoft account login (tunnel owner only) | Two-factor by default: MS identity + CodePlane password |
| **Cloudflare Tunnels** | Email OTP, SSO, or mTLS via Cloudflare Zero Trust (required) | Two-factor: Cloudflare identity + CodePlane password |

This means:

- Local access requires no credentials
- Remote via Dev Tunnels requires Microsoft login **and** the CodePlane password — two independent auth layers out of the box
- Remote via Cloudflare requires Cloudflare Access on the hostname — the server refuses to start without a detected Access gate, ensuring two-factor auth
- Rate limiting (5 attempts/min/IP) protects the login endpoint against brute-force
- If the server is intentionally bound to `0.0.0.0` (e.g., for LAN access), a startup warning is emitted noting that no authentication is enforced

### 21.2 Repository Allowlist

The `repos` list in global config is the authoritative allowlist.

- All job creation requests are validated against this list
- Any path traversal attempts (e.g., `../../etc/passwd`) are rejected
- The backend resolves all paths to their canonical absolute form before comparison

### 21.3 Filesystem Protections

- The backend never serves files outside of whitelisted repository paths or the artifact storage directory
- All file-read endpoints validate the requested path against the job's worktree root
- Directory traversal attacks are prevented by canonicalizing paths and asserting prefix membership

### 21.4 Approval Gating

Risky operations require explicit operator approval before execution. This prevents autonomous destructive actions, especially when the agent misunderstands intent.

### 21.5 CORS

The backend enforces CORS:

- In production mode (single port): CORS is not needed (same origin)
- In development mode (`--dev`): `http://localhost:5173` is allowed
- When tunnel is active: the tunnel origin is dynamically added to the allowed origins list

### 21.6 Connection Limits

SSE connections are capped at `max_sse_connections` (default: 5) to prevent resource exhaustion from too many open connections. No per-request rate limiting is applied.

### 21.7 Share Token Security

Share tokens provide **read-only** access to a single job. Security properties:

- Tokens are 256-bit cryptographically random values (`secrets.token_urlsafe(32)`)
- 24-hour TTL, stored in-memory only (lost on server restart)
- Share endpoints bypass CodePlane's password authentication but expose only: job metadata, SSE event stream, and progress state
- No mutation operations are possible via share tokens — approvals, cancellation, messages, and resolution are all blocked
- The port preview proxy is **not** accessible via share tokens
- **Share tokens do not bypass tunnel-level identity gates.** Dev Tunnels still requires Microsoft login; Cloudflare Tunnels still requires the Access policy. The viewer must be able to reach the server before the share token matters
- No audit trail is kept for token creation or access — treat share URLs as sensitive

### 21.8 Push Notification Security

Web Push uses the VAPID (Voluntary Application Server Identification) protocol:

- VAPID EC P-256 key pair generated at first use and persisted in `~/.codeplane/vapid.json` (chmod 600)
- Browsers verify the VAPID signature on every push message — forged messages are rejected
- Subscriptions are stored in-memory only; stale subscriptions (HTTP 410/404) are automatically pruned
- Notification payloads contain only: title, body text, a tag for deduplication, and a relative URL path — no secrets or sensitive data

### 21.9 Port Preview Proxy Security

The reverse proxy enforces strict boundaries:

- Only proxies to `127.0.0.1` — prevents SSRF against internal networks
- Only ports 1024–65535 — system/privileged ports are blocked
- Subject to normal password authentication (not accessible via share tokens)
- Returns `502 Bad Gateway` when the target port is not listening

### 21.10 Tunnel Security

Both tunnel providers enforce HTTPS and require password authentication. The key difference is the relay-level identity gate:

**Dev Tunnels:**

- Remote clients connect through the `devtunnels.ms` relay URL
- The relay requires the tunnel owner's Microsoft account login before the request reaches CodePlane
- Tunnel URLs should be treated as sensitive operational details even though they are gated by MS auth + password
- HTTPS is enforced by the Dev Tunnels relay

**Cloudflare Tunnels:**

- Remote clients connect through the operator's custom hostname (e.g., `codeplane.example.com`)
- The tunnel relay itself has no built-in identity gate, so CodePlane **requires** a Cloudflare Access application on the hostname
- The server probes the hostname at startup and refuses to serve if no Access redirect is detected
- Configure a [Cloudflare Access application](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-app/) with an identity policy (email OTP, SSO, or mTLS) before starting
- HTTPS is enforced by Cloudflare's edge

**Common to both:**

- Password auth is mandatory
- Session cookies are set with `Secure; HttpOnly; SameSite=Lax`
- Rate limiting prevents brute-force attacks on the login endpoint
- Localhost requests bypass auth entirely (same-machine access is trusted)

---

## 22. Engineering Constraints and Pitfalls

### 22.1 Backend Rules

| Rule | Rationale |
|---|---|
| API routes must not contain orchestration logic | Routes should delegate to services; mixing concerns makes testing and refactoring difficult |
| Agent SDK must be wrapped behind an adapter | Prevents tight coupling to SDK types; enables testing with fakes |
| Git operations must be isolated behind `GitService` | Prevents git logic from spreading across the codebase; enables mocking |
| Long-running jobs must be managed by `RuntimeService` | Central task management enables cancellation, capacity enforcement, and recovery |
| Job state transitions must be explicit | Prevents invalid state changes and makes the state machine auditable |
| Important state must be persisted | Enables restart recovery and event replay |
| Logging must include structured context | Enables filtering and correlation |

### 22.2 Frontend Rules

| Rule | Rationale |
|---|---|
| Application state must have a single source of truth | Prevents inconsistency between components |
| SSE events must be processed centrally | Prevents duplicate state updates and race conditions |
| Components must not duplicate job state | Components should read from the store, not maintain local copies |
| Large lists must avoid excessive re-renders | Virtualization required for log and transcript panels |
| Domain models must be strongly typed | TypeScript interfaces prevent entire classes of runtime errors |

### 22.3 Testing Requirements

| Test type | Coverage target |
|---|---|
| Unit tests | Job state machine transitions |
| Unit tests | Approval service logic |
| Unit tests | Diff parser (hunk parsing, line classification) |
| Unit tests | Config loading and validation |
| Integration tests | Git service: worktree creation, branch creation, cleanup |
| Integration tests | Concurrent jobs on same repository |
| Integration tests | Approval flow end-to-end |
| Integration tests | Job restart recovery (simulate process restart) |
| Integration tests | SSE reconnection and event replay |

### 22.4 Style Requirements

**Backend:**

- Python 3.11+
- `mypy` with strict mode for type checking
- `ruff` for linting and formatting
- `pytest` for testing

**Frontend:**

- TypeScript strict mode
- ESLint with React and TypeScript rules
- Prettier for formatting
- Vitest for unit tests
- Playwright for end-to-end tests

---

## 23. Sequence Diagrams

All diagrams use participants:

- **Operator** — human at the UI
- **React UI** — frontend application
- **FastAPI** — backend application
- **JobRuntime** — RuntimeService + asyncio task
- **AgentSDK** — adapter-wrapped agent runtime
- **GitWorkspace** — GitService + worktree
- **Persistence** — SQLite via repositories
- **SSEStream** — SSEManager + client connection

---

### 23.1 Job Creation and Workspace Initialization

```
Operator -> React UI: Fill job form, click Create
React UI -> FastAPI: POST /api/jobs
FastAPI -> FastAPI: Validate repo in allowlist
FastAPI -> Persistence: persist job (state=preparing)
FastAPI -> GitWorkspace: create_worktree(repo, base_ref, job_id)
GitWorkspace --> FastAPI: worktree_path, branch
FastAPI -> Persistence: persist JobCreated event
FastAPI -> Persistence: persist WorkspacePrepared event
FastAPI -> JobRuntime: enqueue(job)
JobRuntime -> FastAPI: job accepted (state=running or queued)
FastAPI --> React UI: 201 { job_id, state, branch, worktree_path }
React UI -> SSEStream: subscribe /api/events?job_id={id}
JobRuntime -> AgentSDK: create_session(workspace_path, prompt)
AgentSDK --> JobRuntime: session_id
JobRuntime -> Persistence: persist AgentSessionStarted
JobRuntime -> SSEStream: job.state_changed (queued->running)
SSEStream --> React UI: job.state_changed event
React UI -> React UI: Update job state badge
```

---

### 23.2 Agent Execution Lifecycle

```
JobRuntime -> AgentSDK: stream_events(session_id)
loop [SDK emits events]
    AgentSDK --> JobRuntime: SessionEvent(kind="message.assistant", ...)
    JobRuntime -> Persistence: persist SessionEvent
    JobRuntime -> SSEStream: message.assistant
    SSEStream --> React UI: message.assistant
    React UI -> React UI: Append to TranscriptPanel

    AgentSDK --> JobRuntime: SessionEvent(kind="log", ...)
    JobRuntime -> Persistence: persist LogLineEmitted
    JobRuntime -> SSEStream: log
    SSEStream --> React UI: log
    React UI -> React UI: Append to LogsPanel

    AgentSDK --> JobRuntime: SessionEvent(kind="file.edited", ...)
    JobRuntime -> GitWorkspace: generate_diff(worktree, base_ref)
    GitWorkspace --> JobRuntime: DiffFile[]
    JobRuntime -> Persistence: persist DiffUpdated
    JobRuntime -> SSEStream: diff.updated
    SSEStream --> React UI: diff.updated
    React UI -> React UI: Refresh DiffViewer
end

AgentSDK --> JobRuntime: SessionEvent(kind="session.ended")
JobRuntime -> GitWorkspace: final_diff(worktree, base_ref)
JobRuntime -> Persistence: persist DiffUpdated (final)
JobRuntime -> Persistence: persist JobReview
JobRuntime -> SSEStream: job.state_changed (running->review)
SSEStream --> React UI: job.state_changed
React UI -> React UI: Show review badge, enable rerun
```

---

### 23.3 Approval Pause and Resolution

```
AgentSDK --> JobRuntime: on_permission_request callback invoked
JobRuntime -> FastAPI: ApprovalRequested domain event
FastAPI -> Persistence: persist ApprovalRequested
FastAPI -> Persistence: update job state = waiting_for_approval
FastAPI -> SSEStream: permission.requested event
FastAPI -> SSEStream: job.state_changed (running->waiting_for_approval)
SSEStream --> React UI: permission.requested
SSEStream --> React UI: job.state_changed
React UI -> React UI: Show ApprovalBanner with proposed action

Operator -> React UI: Click "Approve"
React UI -> FastAPI: POST /api/approvals/{id}/resolve { resolution: "approved" }
FastAPI -> Persistence: persist ApprovalResolved
FastAPI -> Persistence: update job state = running
FastAPI -> AgentSDK: Return PermissionRequestResult(approved) to pending callback
FastAPI -> SSEStream: job.state_changed (waiting_for_approval->running)
SSEStream --> React UI: job.state_changed
React UI -> React UI: Hide ApprovalBanner, show running state
AgentSDK -> AgentSDK: Resume execution
```

---

### 23.4 Job Cancellation

```
Operator -> React UI: Click "Cancel Job"
React UI -> FastAPI: POST /api/jobs/{job_id}/cancel
FastAPI -> JobRuntime: cancel(job_id)
JobRuntime -> AgentSDK: abort_session(session_id)
AgentSDK --> JobRuntime: session aborted
JobRuntime -> Persistence: persist JobCanceled
JobRuntime -> Persistence: update job state = canceled
JobRuntime -> SSEStream: job.state_changed (running->canceled)
SSEStream --> React UI: job.state_changed
React UI -> React UI: Show canceled badge
FastAPI --> React UI: 200 OK
```

If job is `waiting_for_approval` at cancel time:

```
FastAPI -> Persistence: persist ApprovalResolved (resolution=rejected, reason=canceled)
FastAPI -> JobRuntime: cancel(job_id)
JobRuntime -> AgentSDK: abort_session(session_id)
[continues as above]
```

---

### 23.5 Job Rerun

```
Operator -> React UI: Click "Rerun"
React UI -> FastAPI: POST /api/jobs/{job_id}/rerun
FastAPI -> Persistence: get original job config
FastAPI -> FastAPI: Create new job (same repo, prompt, base_ref)
FastAPI -> GitWorkspace: create_worktree(repo, base_ref, new_job_id)
FastAPI -> Persistence: persist JobCreated (new job)
FastAPI -> Persistence: persist WorkspacePrepared (new job)
FastAPI -> JobRuntime: enqueue(new_job)
FastAPI --> React UI: 201 { new_job_id, state, branch }
React UI -> React UI: Navigate to new job detail
[continues as Job Creation flow]
```

---

### 23.6 SSE Reconnection and Event Replay

```
React UI -> SSEStream: GET /api/events?job_id={id}
SSEStream --> React UI: [stream opens]
React UI -> React UI: Track last_event_id from each "id:" field

note: Network interruption
React UI -> React UI: SSE connection closed
React UI -> React UI: Wait 1s (exponential backoff)
React UI -> SSEStream: GET /api/events?job_id={id}
                       Header: Last-Event-ID: {last_event_id}
SSEStream -> Persistence: EventRepository.list_after(last_event_id, job_id)
Persistence --> SSEStream: [missed events]
SSEStream --> React UI: [replay missed events in order]
SSEStream --> React UI: [continue live stream]
React UI -> React UI: Apply replayed events to store (idempotent)
React UI -> React UI: UI updated to current state
```

---

### 23.7 Voice Input Transcription Flow

```
Operator -> React UI: Press and hold microphone button
React UI -> Browser: getUserMedia({ audio: true })
Browser --> React UI: MediaStream
React UI -> Browser: new MediaRecorder(stream)
React UI -> Browser: recorder.start()

loop [while button held]
    Browser --> React UI: ondataavailable(chunk)
    React UI -> React UI: chunks.push(chunk)
end

Operator -> React UI: Release microphone button
React UI -> Browser: recorder.stop()
React UI -> React UI: blob = new Blob(chunks, { type: "audio/webm" })
React UI -> FastAPI: POST /api/voice/transcribe (multipart: audio=blob)
FastAPI -> VoiceService: transcribe(audio_bytes)
VoiceService -> faster-whisper: model.transcribe(audio_bytes)
faster-whisper --> VoiceService: segments
VoiceService --> FastAPI: "Fix the null pointer in UserService"
FastAPI --> React UI: 200 { text: "Fix the null pointer in UserService" }
React UI -> React UI: Insert text into prompt/message input
```

---

## 24. Execution Strategy Model

Jobs delegate execution to the `RuntimeService`, which manages the agent session lifecycle. The execution model uses a single-agent approach: one job maps to one agent session.

### 24.1 Single-Agent Execution

Each job runs as a single agent session:

1. `RuntimeService` constructs a `SessionConfig` from the job record and resolved config
2. Calls `adapter.create_session(config)` to start the agent
3. Consumes `adapter.stream_events(session_id)` and translates each `SessionEvent` into a domain event
4. Publishes domain events to the event bus
5. When the session completes, the job transitions to `review`, `failed`, or `canceled`

Operator messages are forwarded via `adapter.send_message()`. Cancellation calls `adapter.abort_session()`.

### 24.2 How the RuntimeService Manages Jobs

The `RuntimeService`:

1. Tracks running asyncio tasks by `job_id`
2. Enforces `max_concurrent_jobs` from global config
3. Enqueues jobs if at capacity (state: `queued`)
4. Starts queued jobs when capacity opens
5. Provides a `cancel(job_id)` method that cancels the asyncio task and calls `adapter.abort_session()`

### 24.3 Future Strategy Examples

The following strategies are **not implemented** but the architecture is designed to support them:

| Strategy | Description |
|---|---|
| `planner_executor` | A planner agent decomposes the task, then an executor agent implements each step |
| `executor_reviewer` | An executor agent produces changes, then a reviewer agent validates them |
| `parallel_executors` | Multiple executor agents work on independent subtasks concurrently |
| `human_in_the_loop` | The agent pauses after each step for operator review before continuing |

---

## 25. Ross Review: Open Questions

This section documents genuinely open questions that require further investigation or decision-making. Items that had clear suggested approaches have been promoted into the main specification as design decisions.

---

### 25.1 Agent SDK Integration

**Context from research:** The Python Copilot SDK (`github/copilot-sdk`) wraps the Copilot CLI via JSON-RPC over a subprocess. Sessions are created with a `SessionConfig` that accepts `available_tools`, `excluded_tools`, `mcp_servers`, `agent_mode`, and callback hooks. The SDK provides:

- **Event interception** via `SessionHooks` (`on_pre_tool_use`, `on_post_tool_use`, `on_session_start`, `on_session_end`, `on_error_occurred`)
- **Permission handling** via `on_permission_request` (blocking callback, returns `PermissionRequestResult`)
- **Tool filtering** via `available_tools` / `excluded_tools` lists
- **MCP server config** with per-server tool filtering
- **Agent modes**: `autopilot`, `interactive`, `plan`, `shell`

**Resolved:** The approval-pause mechanism uses the SDK's blocking `on_permission_request` callback. CodePlane holds the pending callback and resolves it when the operator responds.

**Resolved:** Session cancellation uses `session.abort()` which aborts the current message processing. The session remains valid after abort. Operator message injection uses `session.send(MessageOptions)` with `mode="immediate"` to send a follow-up message while the session is active.

**Resolved:** Subprocess crash detection relies on EOF/broken pipe on the JSON-RPC stdout stream. The SDK's background read loop detects the closed stream, polls the process exit code, captures stderr, and raises `ProcessExitedError` on all pending futures. The adapter catches this exception and emits a `job.failed` event with the error details. This process-crash path does not depend on the runtime's separate `session.heartbeat` mechanism; detection is immediate via the broken pipe.

---

### 25.2 Runtime Failure Scenarios

**Resolved:** `GitService.create_worktree()` catches exceptions from `git worktree add` (disk full, permissions, corrupt state) and transitions the job to `failed` with a descriptive error including git stderr output. The operator resolves the underlying issue and reruns. See Section 8.1.

---

### 25.3 SSE Scalability Constraint

**Resolved:** Documented as a known constraint. Beyond ~20 concurrent jobs, the SSE manager switches to selective streaming: only `job.state_changed` events are broadcast to the dashboard, while the currently open Job Detail screen continues to receive full event streaming. A "Refresh page for latest updates" banner is shown on the dashboard. See Section 5.6.

---

### 25.4 Branch Cleanup and PR Integration

**Resolved:** CodePlane offers to create a pull request after a successful job using the GitHub MCP server or `gh` CLI, whichever is available. If neither is available, the step is skipped and the branch remains on disk for manual push. Completed branches are **not** auto-deleted after merge. See Section 8.7.

---

## 26. MCP Orchestration Server

CodePlane exposes an **MCP (Model Context Protocol) server** that mirrors the full UI functionality, enabling external agents to use CodePlane as an orchestration layer. An outer agent connects to CodePlane's MCP server and drives job lifecycle, approval workflows, workspace inspection, and configuration — all through MCP tool calls.

---

### 26.1 Purpose

The MCP orchestration server turns CodePlane from a human-operated dashboard into a programmable control plane. An orchestrating agent can:

- Create and monitor coding jobs across multiple repositories
- Handle approval requests on behalf of a human or policy engine
- Inspect diffs, workspace files, and artifacts produced by inner agents
- Inject operator messages to steer running agents
- Manage repository registration and global settings

This enables hierarchical agent architectures where a planning agent delegates implementation tasks to CodePlane-managed coding agents and reacts to their progress in real time.

---

### 26.2 Transport

The MCP server uses **Streamable HTTP** transport, served from the same FastAPI process on a dedicated path:

- **Endpoint**: `POST /mcp` (message endpoint), `GET /mcp` (SSE stream for server-initiated notifications)
- **Authentication**: None for local connections; remote access is secured via tunnel authentication (see §7, §21)
- **Discovery**: Standard MCP capabilities negotiation on `initialize`

Running in-process avoids a separate deployment unit and shares the service layer, database connections, and event bus with the REST API.

---

### 26.3 Tool Surface

Every REST API capability is exposed as an MCP tool. Related operations are collapsed into a single tool with an `action` parameter, keeping the tool count low for LLM clients. Tool names use the `codeplane_` prefix. Each tool carries an `annotations.title` for human-readable display.

#### Job Management

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_job` | Manage Coding Jobs | create, list, get, cancel, rerun, message | `POST/GET /api/jobs`, `POST /api/jobs/{id}/cancel`, etc. |

#### Approvals

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_approval` | Manage Approvals | list, resolve | `GET /api/jobs/{id}/approvals`, `POST /api/approvals/{id}/resolve` |

#### Workspace & Artifacts

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_workspace` | Browse Job Worktree | list, read | `GET /api/jobs/{id}/workspace`, `GET /api/jobs/{id}/workspace/file` |
| `codeplane_artifact` | Access Job Artifacts | list, get | `GET /api/jobs/{id}/artifacts`, `GET /api/artifacts/{id}` |

#### Tracker Delivery

`codeplane_tracker` exposes `comment`, `transition`, `status`, and `list` for a Job's explicit tracker pairing. Writes require an explicit CodePlane approval and return an operation identity plus a truthful durable outcome. Supplying the same `request_id` deduplicates a retried MCP call. `status` and `list` recover results after disconnection. Approval alone is never reported as applied, and uncertain delivery must not be blindly repeated.

#### Configuration

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_settings` | Global Settings | get, update | `GET/PUT /api/settings/global` |
| `codeplane_repo` | Manage Repositories | list, get, register, remove | `GET/POST/DELETE /api/settings/repos` |

#### Observability

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_health` | Health & Maintenance | check, cleanup | `GET /api/health`, `POST /api/settings/cleanup-worktrees` |

---

### 26.4 Notifications (Server → Client)

The MCP server pushes **notifications** to connected clients for key domain events:

| Notification | Trigger |
|---|---|
| `cpl/job_state_changed` | Job transitions to a new state |
| `cpl/approval_requested` | A running job requests operator approval |
| `cpl/job_completed` | Job reaches `completed` or `failed` terminal state |
| `cpl/agent_message` | Agent produces a transcript message |

Notifications are sourced from the internal event bus — the same events that drive the SSE stream and the frontend. The orchestrating agent can subscribe to these to react without polling.

---

### 26.5 Implementation Approach

1. **Thin MCP handler layer**: Each MCP tool handler validates input, calls the corresponding service method, and returns the result. Same principle as the REST route handlers.
2. **Shared service layer**: MCP handlers call the same `JobService`, `ApprovalService`, `GitService`, etc. used by the REST API. No duplication of business logic.
3. **Schema reuse**: Tool input/output schemas are derived from the existing Pydantic models in `api_schemas.py`.
4. **Event bus integration**: Notifications are powered by subscribing to the internal event bus, same as `SSEManager`.

---

### 26.6 Configuration

The MCP server is always enabled and mounted at `/mcp`. These are hardcoded constants, not user-configurable.

---

### 26.7 Security Considerations

- Local connections (localhost) require no authentication — same trust model as the REST API.
- Remote access is secured via tunnel authentication with mandatory password auth (see §7, §21).
- Tool calls are subject to the same validation as REST requests (repository allowlist, state machine rules).
- Rate limiting and capacity enforcement from `RuntimeService` apply equally to MCP-initiated jobs.
- The MCP server does **not** expose raw database access, shell execution, or filesystem paths outside registered repositories.