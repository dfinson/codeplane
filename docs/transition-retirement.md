# Transition release and final-runtime recovery

The final package has no TraceForge/title dependency, compatibility extra,
retained-policy executor or old parser. Application-owned canonical history,
24 native formats, profile identities, raw records, conversion receipts,
ledger counters, scoped grants and MIT provenance remain.

The rollback-capable transition implementation is commit
`287483a7cd931589ce67140e915116896fa4f39a`. Preserve its exact source, `uv.lock`,
built wheel and corresponding acceptance evidence. The delivery lane retains
75 passing transition checks plus populated same-database rollout/rollback and
checkpoint fixtures, including read-only original governance backups. Those
fixtures are not backups of an operator's production data.

## Before activating the final runtime

1. Retain the tested transition artifacts and the actual home's verified,
   read-only original governance backup. Preserve the current application
   database and every later outcome, not just the original snapshot.
2. Quiesce admissions, drain or explicitly reconcile active executions, and
   stop the recorded runtime. Complete prepared retained accounting and
   version-1 checkpoint conversion in the transition release. Conflicting
   locked prefixes remain conflicts until reconciled.
3. Use exclusive stopped-runtime ownership and the returned admission
   generation to select the final standalone implementation for new Jobs.
   Existing Job pins and grants are not rewritten. An unavailable old pin
   cannot resume in the final package.

`cpl governance-transition import-legacy` remains an application-owned,
explicit data import and integrity comparison. It does not execute TF or
change provider selection. `select-provider standalone` can activate only
the current final implementation. Final-runtime `reconcile-retained` and
old-parser conversion refuse execution and identify the preserved release.
No error asks the operator to install TF into the final package.

## Rollback and historical access

Stop and quiesce the current runtime before using the preserved transition
release in its own locked environment. Use the same current databases and
explicit per-Job reconciliation. Never restore an old database over new
events, counters, approvals or artifacts. A Job whose exact implementation is
unavailable remains refused, even in the retained release.

Canonical event/history APIs remain available without the old parser.
Reconciled version-2 acquisition checkpoints retain their original conversion
evidence. Historical re-enrichment rebuilds structural tool display and duration
with application-owned pairing. It does not rewrite raw payloads, reclassify
risk, regenerate titles or invoke a model.

Preset and MCP settings remain current controls. Old batching, blanket TF
trust and configurable TF spend overrides are not final-runtime permissions.
Stored historical values remain exportable for transition recovery. The
settings UI shows the unchanged standalone thresholds and distinguishes
verified USD from native usage units and unavailable currency.

Final-wheel absence, registered-profile, history/checkpoint/readback, policy,
latency and actual enabled-provider evidence must be bound to the final source.
Passing transition tests or earlier native workflows is not a final-head pass.
