# sindresorhus/execa

| Field | Value |
|-------|-------|
| **URL** | https://github.com/sindresorhus/execa |
| **License** | MIT |
| **Language** | TypeScript / JavaScript |
| **Scale** | Medium |
| **Category** | Process execution |
| **Set** | Cutoff |
| **Commit** | `f3a2e8481a1e9138de3895827895c834078b9456` |

## Why this repo

- **Well-structured**: Clean modular layout with isolated concerns in
  dedicated directories — `methods/` (factory, sync/async entry, script
  mode, template strings), `pipe/` (subprocess piping and abort),
  `stdio/` (file-descriptor routing and native streams), `ipc/`
  (structured inter-process communication), `terminate/` (kill, graceful
  shutdown, timeout), `transform/` (user-supplied stream transforms and
  line splitting), `convert/` (readable/writable/duplex/iterable stream
  adapters), `resolve/` (exit handling and stream draining), `return/`
  (result/error construction and messaging), and `verbose/` (logging and
  debug output). Each concern is self-contained with minimal coupling.
- **Rich history**: 7K+ stars, 1.5K+ commits. The de-facto Node.js
  process execution library (successor to `child_process` wrappers),
  used widely across the JS ecosystem. Issues span IPC edge cases, pipe
  buffering, signal handling, and Windows compatibility.
- **Permissive**: MIT license.

## Structure overview

```
execa/
├── index.js                      # Public API — execa, execaSync, execaCommand, execaNode, $
├── index.d.ts                    # TypeScript type re-exports
├── lib/
│   ├── arguments/
│   │   ├── command.js            # Command string parsing (parseCommandString)
│   │   ├── cwd.js                # Working directory resolution and validation
│   │   ├── encoding-option.js    # Encoding option normalization
│   │   ├── escape.js             # Shell argument escaping
│   │   ├── fd-options.js         # Per-fd option mapping (stdin/stdout/stderr/fd3+)
│   │   ├── file-url.js           # file:// URL to path conversion
│   │   ├── options.js            # Options normalization and defaults
│   │   ├── shell.js              # Shell mode wrapping (cmd /c, /bin/sh -c)
│   │   └── specific.js           # Method-specific argument handling
│   ├── convert/
│   │   ├── add.js                # Attaches .readable()/.writable()/.duplex() to subprocess
│   │   ├── concurrent.js         # Handles concurrent stream conversion subscribers
│   │   ├── duplex.js             # Duplex stream adapter for subprocess
│   │   ├── iterable.js           # Async iterable adapter for subprocess output
│   │   ├── readable.js           # Readable stream adapter for subprocess stdout
│   │   ├── shared.js             # Shared conversion utilities
│   │   └── writable.js           # Writable stream adapter for subprocess stdin
│   ├── io/
│   │   ├── contents.js           # Buffered output collection (getStreamContents)
│   │   ├── input-sync.js         # Synchronous stdin writing
│   │   ├── iterate.js            # Line-by-line async iteration over output
│   │   ├── max-buffer.js         # maxBuffer enforcement and error handling
│   │   ├── output-async.js       # Async output stream wiring
│   │   ├── output-sync.js        # Sync output collection
│   │   ├── pipeline.js           # Stream pipeline setup (source → transform → dest)
│   │   └── strip-newline.js      # Final newline stripping
│   ├── ipc/
│   │   ├── array.js              # IPC message array handling
│   │   ├── buffer-messages.js    # Message buffering before IPC channel ready
│   │   ├── forward.js            # IPC message forwarding between processes
│   │   ├── get-each.js           # getEachMessage() — async iterable over IPC messages
│   │   ├── get-one.js            # getOneMessage() — single IPC message receive
│   │   ├── graceful.js           # Graceful IPC disconnect
│   │   ├── incoming.js           # Incoming IPC message processing
│   │   ├── ipc-input.js          # IPC input option handling
│   │   ├── methods.js            # IPC method exports (sendMessage, getOneMessage, etc.)
│   │   ├── outgoing.js           # Outgoing IPC message queueing
│   │   ├── reference.js          # IPC channel ref/unref management
│   │   ├── send.js               # sendMessage() implementation with serialization
│   │   ├── strict.js             # Strict IPC mode validation
│   │   └── validation.js         # IPC message validation and type checking
│   ├── methods/
│   │   ├── bind.js               # Options merging for bound instances
│   │   ├── command.js            # execaCommand — parse a full command string
│   │   ├── create.js             # createExeca — factory for all method variants
│   │   ├── main-async.js         # execaCoreAsync — async subprocess lifecycle
│   │   ├── main-sync.js          # execaCoreSync — sync subprocess execution
│   │   ├── node.js               # execaNode — spawn Node.js child with IPC
│   │   ├── parameters.js         # Argument normalization (file, args, options)
│   │   ├── promise.js            # Subprocess-as-promise wrapping
│   │   ├── script.js             # $ tagged template — script mode helpers
│   │   └── template.js           # Template string parsing ($`cmd ${arg}`)
│   ├── pipe/
│   │   ├── abort.js              # AbortSignal handling for piped subprocesses
│   │   ├── pipe-arguments.js     # Pipe destination argument parsing
│   │   ├── sequence.js           # Sequential pipe chaining
│   │   ├── setup.js              # .pipe() method setup on subprocess
│   │   ├── streaming.js          # Stream-level pipe wiring between subprocesses
│   │   └── throw.js              # Error propagation through pipe chains
│   ├── resolve/
│   │   ├── all-async.js          # Async promise-all for subprocess streams
│   │   ├── all-sync.js           # Sync result collection
│   │   ├── exit-async.js         # Async exit event handling
│   │   ├── exit-sync.js          # Sync exit code capture
│   │   ├── stdio.js              # Stdio result assembly
│   │   ├── wait-stream.js        # Wait for individual streams to close
│   │   └── wait-subprocess.js    # Wait for full subprocess termination
│   ├── return/
│   │   ├── duration.js           # Execution duration measurement
│   │   ├── early-error.js        # Pre-spawn error handling (e.g., ENOENT)
│   │   ├── final-error.js        # ExecaError / ExecaSyncError construction
│   │   ├── message.js            # Error message formatting with command/output
│   │   ├── reject.js             # Rejection policy (reject: false support)
│   │   └── result.js             # Result object construction (stdout, stderr, etc.)
│   ├── stdio/
│   │   ├── direction.js          # Fd direction classification (input vs output)
│   │   ├── duplicate.js          # Duplicate fd handling
│   │   ├── handle-async.js       # Async stdio option resolution
│   │   ├── handle-sync.js        # Sync stdio option resolution
│   │   ├── handle.js             # Common stdio handling entry point
│   │   ├── input-option.js       # input/inputFile option processing
│   │   ├── native.js             # Native fd passthrough ('inherit', 'pipe', etc.)
│   │   ├── stdio-option.js       # Per-fd option normalization
│   │   └── type.js               # Stdio type detection and classification
│   ├── terminate/
│   │   ├── cancel.js             # AbortController/cancelSignal integration
│   │   ├── cleanup.js            # Process cleanup on parent exit (signal-exit)
│   │   ├── graceful.js           # Graceful termination with configurable grace period
│   │   ├── kill.js               # .kill() method — signal dispatch with forceKill
│   │   ├── signal.js             # Signal name/number normalization
│   │   └── timeout.js            # timeout option — auto-kill after duration
│   ├── transform/
│   │   ├── encoding-transform.js # Binary ↔ string encoding transforms
│   │   ├── generator.js          # User generator/transform wrapping as Node streams
│   │   ├── normalize.js          # Transform option normalization
│   │   ├── object-mode.js        # Object-mode stream handling
│   │   ├── run-async.js          # Async transform execution
│   │   ├── run-sync.js           # Sync transform execution
│   │   ├── split.js              # Line splitting transform
│   │   └── validate.js           # Transform function validation
│   ├── utils/
│   │   ├── abort-signal.js       # AbortSignal utilities
│   │   ├── deferred.js           # Deferred promise helper
│   │   ├── max-listeners.js      # EventEmitter maxListeners management
│   │   ├── standard-stream.js    # Standard stream (process.stdin/out/err) detection
│   │   └── uint-array.js         # Uint8Array ↔ string conversion
│   └── verbose/
│       ├── complete.js           # Verbose logging on subprocess completion
│       ├── custom.js             # Custom verbose function support
│       ├── default.js            # Default verbose log formatter (stderr)
│       ├── error.js              # Verbose error output
│       ├── info.js               # Verbose info-level logging
│       ├── ipc.js                # Verbose IPC message logging
│       ├── log.js                # Core verbose log dispatch
│       ├── output.js             # Verbose stdout/stderr output logging
│       ├── start.js              # Verbose command-start logging
│       └── values.js             # Verbose value formatting
├── types/
│   ├── arguments/                # TypeScript type definitions for arguments module
│   ├── ipc.ts                    # TypeScript types for IPC methods
│   ├── methods/                  # TypeScript types for method variants
│   ├── pipe.ts                   # TypeScript types for pipe API
│   ├── return/                   # TypeScript types for result and error objects
│   ├── stdio/                    # TypeScript types for stdio options
│   ├── subprocess/               # TypeScript types for subprocess instance
│   ├── transform.ts              # TypeScript types for transforms
│   └── verbose.ts                # TypeScript types for verbose options
└── test/                         # Comprehensive test suite
```

## Scale indicators

- ~100 JavaScript source files in `lib/`
- ~6,700 lines of library code
- Modular directory structure (14 subdirectories under `lib/`)
- Dependencies: `cross-spawn`, `get-stream`, `human-signals`,
  `npm-run-path`, `signal-exit`, `strip-final-newline`, `is-stream`,
  `is-plain-obj`, `figures`, `pretty-ms`, `@sindresorhus/merge-streams`

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix graceful termination not sending forceKill signal when grace period expires on Windows

When `gracefulCancel` is used with a `forceKillAfterDelay` on Windows,
the graceful termination flow in `lib/terminate/graceful.js` sends the
initial signal but never escalates to SIGKILL (or `taskkill /F`) after
the grace period. The `forceKillAfterDelay` timer set in
`lib/terminate/kill.js` does not fire because the subprocess ref is
already cleared by `lib/ipc/reference.js` during graceful disconnect.
Fix the ref management so the forceKill timer runs if the subprocess
has not exited after the grace period.

### N2: Fix maxBuffer error message not including the truncated output

When `maxBuffer` is exceeded, the error thrown by
`lib/io/max-buffer.js` reports "maxBuffer exceeded" with the fd name
and limit, but does not include the truncated output in the result
object. Downstream code in `lib/return/result.js` has the buffered
content available but `lib/io/max-buffer.js` discards it before
building the result. Fix the flow so `error.stdout` / `error.stderr`
contains the data collected up to the buffer limit.

### N3: Fix strip-newline not stripping final newline from last line when using lines and preserveNewlines

When `stripFinalNewline: true` is used together with `lines: true` and
`preserveNewlines: true`, the last element of the output array retains
its trailing newline character. The `stripNewline` function in
`lib/io/strip-newline.js` short-circuits for array values via the
`!Array.isArray(value)` guard, so no stripping is applied to the
individual elements. Fix `stripNewline` to strip the trailing newline
from the last element of the array when both `lines` and
`stripFinalNewline` are active for the given file descriptor.

### N4: Fix parseCommandString not handling escaped quotes inside arguments

`parseCommandString('echo "hello \\"world\\""')` in
`lib/methods/command.js` fails to recognize the double-quoted group
and splits the string by the space inside the quotes, producing
`["echo", '"hello', '\\"world\\""']` instead of
`["echo", 'hello \\"world\\"']`. Fix the parser to support
double-quoted argument groups so spaces inside quotes are not treated
as delimiters and backslash-escaped quotes inside a quoted group are
preserved.

### N5: Fix VerboseFunction type hiding result from custom verbose functions

When a custom `verbose` function is provided, it is typed as
`VerboseFunction` in `types/verbose.d.ts`, whose `verboseObject`
parameter is `MinimalVerboseObject`. That type sets `result?: never`,
which means TypeScript reports `result` as inaccessible even though the
property is present at runtime. Custom functions therefore cannot type-
safely read `verboseObject.result.durationMs`, `verboseObject.result.failed`,
or any other result field. Fix `MinimalVerboseObject` in
`types/verbose.d.ts` to expose `result` as `VerboseObject['result']`
so that the `result` property (including `durationMs` on both success
and error events) is accessible to custom verbose functions.

### N6: Fix IPC getOneMessage disconnecting the channel when the filter function throws

When `getOneMessage({filter})` is called with a `filter` function that
throws an exception, the exception propagates out of the `for await...of`
loop in `lib/ipc/get-one.js` and is caught by the outer `try/catch`,
which calls `disconnect(anyProcess)`. This forcibly terminates the IPC
channel because of a user-level error in the filter function, preventing
any further IPC communication for the lifetime of the subprocess. Fix
`getOneMessageAsync` to isolate filter-function exceptions from genuine
IPC errors so that `disconnect` is only called when the failure is an
IPC-level disconnect or strict-mode error, not a filter callback error.

### N7: Fix template literal escaping not preserving backslashes before non-special characters

When using the `$` tagged template (e.g., `` $`echo foo\\bar` ``),
the template parser in `lib/methods/template.js` strips all backslashes,
treating `\\b` as an escape sequence rather than a literal `\b`. Only
backslashes before special shell characters should be consumed as
escapes. Fix the parser to preserve backslashes that do not precede
a recognized escape character.

### N8: Fix validateSyncOptions not rejecting gracefulCancel in synchronous methods

When `gracefulCancel: true` is passed to `execaSync`, the sync entry
path in `lib/methods/main-sync.js` does not check for `gracefulCancel`
in `validateSyncOptions`. Instead, `normalizeOptions` calls
`validateGracefulCancel` from `lib/terminate/graceful.js`, which may
throw misleading errors about `cancelSignal` or `ipc` being missing
rather than a clear "cannot be used with synchronous methods" message.
Fix `validateSyncOptions` to explicitly reject `gracefulCancel: true`
with the same `throwInvalidSyncOption` helper used for other sync-
incompatible options, ensuring users receive a consistent and
actionable error.

### N9: Fix verbose IPC logging providing no activity indication when `verbose: 'short'` is set

When `verbose` is set to `'short'`, the verbose system logs the command
start and completion but gives no indication that IPC messages were
exchanged during the subprocess run. By contrast, `verbose: 'full'`
logs the full IPC message contents via `lib/verbose/ipc.js`. The gap
means debugging IPC-heavy subprocesses with `'short'` mode is harder
than necessary. Fix `lib/verbose/ipc.js` so that when the fd-specific
verbose level is `'short'`, it logs a redacted placeholder such as
`[ipc message]` without the message content, consistent with how
`'short'` suppresses output content but still indicates activity.

### N10: Fix file URL conversion not handling percent-encoded spaces on Windows

When a `file://` URL with encoded spaces (e.g.,
`file:///C:/My%20Dir/cmd.exe`) is passed as the command,
`lib/arguments/file-url.js` converts it using `fileURLToPath` but then
the resulting path is not properly quoted for `cross-spawn` on Windows.
Fix the conversion to ensure paths with spaces are passed correctly to
the underlying spawn call.

## Medium

### M1: Implement subprocess resource usage reporting

Add a `resourceUsage` property to the result object that includes
`maxRSS`, `userCPUTime`, `systemCPUTime`, and `voluntaryContextSwitches`
from Node.js `process.resourceUsage()` captured via IPC from the child
process. Requires changes to `lib/return/result.js` for the result
schema, `lib/methods/node.js` for automatic resource collection in
Node.js subprocesses, `lib/ipc/incoming.js` for internal resource
messages, and `types/return/result.d.ts` for TypeScript types. Document
the new `resourceUsage` property with examples in `readme.md` under the
Result section, and add a `"resourceUsage"` keyword to `package.json`.

### M2: Add subprocess retry with configurable backoff

Implement a `retry` option (`{retries, delay, backoff, shouldRetry}`)
that automatically re-executes a failed subprocess. Requires a retry
loop wrapper in `lib/methods/main-async.js`, backoff delay computation,
integration with `lib/return/reject.js` for retry-aware rejection,
result aggregation across attempts in `lib/return/result.js`, verbose
logging of retry attempts in `lib/verbose/start.js`, and TypeScript
types in `types/methods/`. Add a retry-specific test job to
`.github/workflows/main.yml` that runs retry tests in a separate
matrix entry, since retry tests are inherently slower and may need
extended timeouts.

### M3: Implement per-line transform with line number context

Extend the transform system to provide a `lineNumber` (0-indexed)
and `isLast` boolean to user-supplied transform generators. Requires
changes to the line splitter in `lib/transform/split.js` to track
line count, the transform runner in `lib/transform/run-async.js` and
`lib/transform/run-sync.js` to pass the context object to generators,
`lib/transform/generator.js` for context propagation, and TypeScript
type updates in `types/transform/normalize.d.ts`.

### M4: Add subprocess output tee to file

Implement a `tee` option that writes subprocess output to both the
result buffer and a file simultaneously. Requires a tee stream in
`lib/io/pipeline.js`, file descriptor management in
`lib/stdio/handle-async.js`, integration with `lib/io/max-buffer.js`
so the tee file is not subject to buffer limits, cleanup on error in
`lib/resolve/wait-stream.js`, and TypeScript types in `types/stdio/`.

### M5: Implement subprocess progress events for long-running commands

Add an `onProgress` callback option that receives periodic updates
containing bytes read/written, elapsed time, and estimated ETA (if
`expectedSize` is provided). Requires a progress tracker in
`lib/io/output-async.js`, integration with the pipeline in
`lib/io/pipeline.js`, timer-based interval emission, backpressure-
aware byte counting in `lib/convert/readable.js`, and TypeScript types.

### M6: Add structured JSON output mode

Implement `{json: true}` that automatically parses each line of stdout
as JSON and collects results into a `jsonOutput` array on the result.
Invalid JSON lines should be collected separately in a `jsonErrors`
array with line numbers. Requires a JSON parsing transform in
`lib/transform/`, integration with `lib/return/result.js`, error
handling for malformed lines, interaction with `lib/io/max-buffer.js`
for object counting, and TypeScript generics for typed JSON output.

### M7: Implement subprocess sandboxing with environment isolation

Add an `isolate` option that strips the inherited environment,
restricts the PATH to explicit entries, and optionally sets
`cwd` to a temporary directory. Requires environment filtering in
`lib/arguments/options.js`, PATH restriction logic in
`lib/arguments/cwd.js`, temporary directory creation and cleanup,
interaction with `lib/methods/node.js` for Node.js-specific isolation
(clearing `NODE_PATH`), and TypeScript types.

### M8: Add pipe fan-out to multiple destinations

Extend `.pipe()` to accept an array of destinations, fanning out
a single subprocess's output to multiple downstream subprocesses
simultaneously. Requires changes to `lib/pipe/pipe-arguments.js` for
array parsing, `lib/pipe/streaming.js` for multi-destination tee
wiring, `lib/pipe/throw.js` for aggregated error handling across
destinations, `lib/pipe/abort.js` for coordinated abort, and TypeScript
types in `types/pipe.d.ts`.

### M9: Implement command dry-run mode

Add a `dryRun: true` option that resolves options and constructs the
full command line (including shell wrapping, PATH resolution, and
environment) but does not spawn a subprocess. Return a result-like
object with `command`, `escapedCommand`, `env`, and `cwd`. Requires
early returns in `lib/methods/main-async.js` and `main-sync.js`,
option resolution without spawn in `lib/arguments/options.js`,
shell expansion in `lib/arguments/shell.js`, verbose logging changes,
and TypeScript types.

### M10: Add subprocess execution history and statistics

Implement an `execa.history` API that tracks the last N subprocess
executions with their command, duration, exit code, and signal.
Expose `execa.stats()` returning aggregate metrics (total runs,
failure rate, average duration). Requires a history ring buffer
module, integration with `lib/methods/create.js` factory, recording
in `lib/return/result.js` and `lib/return/final-error.js`, verbose
integration, and TypeScript types.

## Wide

### W1: Implement a subprocess pool with concurrency control

Add `execa.pool({concurrency, timeout, onDrain})` that manages a pool
of subprocess slots, queuing commands when the pool is full and
draining on shutdown. Support priority queuing, per-slot resource
limits, graceful pool shutdown with `pool.close()`, and abort-all via
`pool.abort()`. Changes span `lib/methods/create.js` for pool factory,
a new `lib/pool/` module for queue and slot management, integration
with `lib/terminate/` for coordinated shutdown, `lib/pipe/` for
pool-aware piping, `lib/verbose/` for pool activity logging, and
TypeScript types.

### W2: Implement remote subprocess execution over SSH

Add `execa.ssh({host, user, key, port})` that transparently wraps
commands for remote execution via SSH, preserving the full execa API
(piping, IPC, transforms, verbose). Support host-key verification,
connection multiplexing, SCP-based file transfer for `inputFile`/
`tee`, and remote process cleanup on abort. Changes span
`lib/methods/create.js` for SSH factory, a new `lib/remote/` module
for SSH connection management, `lib/arguments/shell.js` for remote
quoting, `lib/terminate/kill.js` for remote signal dispatch,
`lib/ipc/` adaptation for tunneled IPC, `lib/verbose/` for host-
prefixed logging, and TypeScript types.

### W3: Implement a workflow engine for multi-step subprocess pipelines

Add `execa.workflow()` that defines a DAG of subprocess steps with
dependencies, conditional branches, and parallel lanes. Support
output forwarding between steps, step-level retry, partial failure
handling (continue-on-error per step), workflow-level timeout, and
a dry-run mode showing the execution plan. Changes span a new
`lib/workflow/` module for DAG definition and execution, integration
with `lib/pipe/` for inter-step streaming, `lib/methods/` for step
factories, `lib/return/` for workflow-level results, `lib/verbose/`
for step-annotated logging, and TypeScript types with builder pattern.

### W4: Add comprehensive Windows compatibility layer

Implement a Windows-native execution layer that handles: cmd.exe and
PowerShell quoting differences, PATHEXT-aware command resolution,
UNC path support, Windows job objects for process tree cleanup, and
ConPTY for pseudo-terminal support. Changes span
`lib/arguments/shell.js` for per-shell quoting, `lib/arguments/escape.js`
for Windows-specific escaping, `lib/terminate/kill.js` for job-object
based tree kill, `lib/terminate/cleanup.js` for Windows cleanup,
`lib/stdio/native.js` for ConPTY integration, `lib/arguments/cwd.js`
for UNC paths, and TypeScript platform-specific types.

### W5: Implement subprocess observability with OpenTelemetry integration

Add optional OpenTelemetry instrumentation that creates spans for each
subprocess execution, records IPC messages as span events, tracks pipe
chains as linked spans, and emits metrics for duration, exit codes, and
resource usage. Support configurable exporters and context propagation
to child processes via environment variables. Changes span a new
`lib/telemetry/` module, integration with `lib/methods/main-async.js`
for span lifecycle, `lib/ipc/` for event recording, `lib/pipe/` for
span linking, `lib/verbose/` for trace-id annotated logs, and
TypeScript types.

### W6: Implement interactive subprocess control with PTY support

Add `execa.pty()` that spawns subprocesses in a pseudo-terminal,
enabling interactive programs (e.g., `ssh`, `vim`, terminal prompts).
Support terminal size configuration, raw-mode input forwarding,
ANSI escape parsing for structured output, expect-style pattern
matching (`subprocess.expect('Password:')`), and automatic response.
Changes span a new `lib/pty/` module, `lib/stdio/` for PTY fd setup,
`lib/io/` for raw-mode stream handling, `lib/convert/` for PTY-aware
stream adapters, `lib/terminate/` for PTY session cleanup, and
TypeScript types.

### W7: Implement a subprocess mock/replay framework for testing

Add `execa.mock()` that intercepts subprocess creation and returns
pre-recorded results, enabling deterministic testing without spawning
real processes. Support recording mode (capture real executions),
replay mode (match commands by pattern/regex/exact), fixture file
I/O for recorded sessions, IPC message replay, and streaming output
simulation with configurable timing. Changes span `lib/methods/create.js`
for mock injection, a new `lib/mock/` module for recording and
matching, `lib/return/result.js` for synthetic results, `lib/pipe/`
for mocked pipe chains, `lib/ipc/` for replayed messages, and
TypeScript types.

### W8: Add multi-process coordinator with shared state

Implement `execa.cluster({workers, init})` that spawns a set of worker
subprocesses sharing state via IPC. Support broadcast messaging,
request/response patterns between workers, worker health monitoring
with automatic restart, graceful scaling (add/remove workers), and a
coordinator API for work distribution. Changes span a new
`lib/cluster/` module, `lib/ipc/` extension for multiplexed channels,
`lib/terminate/` for worker lifecycle, `lib/methods/node.js` for
worker-mode Node.js processes, `lib/verbose/` for per-worker logging,
and TypeScript types.

### W9: Implement cross-platform process tree management

Add process tree awareness: `.kill()` terminates the entire process
tree (not just the direct child), `.tree()` returns the live process
tree as a hierarchical object, and orphan detection warns when
grandchild processes outlive the child. Support Linux (`/proc`),
macOS (`sysctl`), and Windows (`wmic`/`Get-CimInstance`). Changes span
`lib/terminate/kill.js` for tree-kill, a new `lib/tree/` module for
process enumeration, `lib/terminate/cleanup.js` for orphan handling,
`lib/resolve/exit-async.js` for tree-exit waiting, `lib/verbose/` for
tree-annotated logging, and TypeScript types.

### W10: Implement subprocess output streaming with backpressure-aware web integration

Add first-class Web Streams API support: `.readableStream()`,
`.writableStream()`, `.transformStream()` returning WHATWG streams
with proper backpressure propagation. Support Server-Sent Events
streaming from subprocess output, WebSocket bridging for bidirectional
IPC, and `ReadableStream.from(subprocess)` compatibility. Changes span
`lib/convert/` for Web Stream adapters, `lib/io/pipeline.js` for
backpressure signaling, `lib/pipe/streaming.js` for Web Stream piping,
a new `lib/web/` module for SSE and WebSocket bridges, `lib/ipc/` for
WebSocket-tunneled IPC, and TypeScript types.

### N11: Fix .editorconfig missing explicit settings for TypeScript definition files

The `.editorconfig` in the repo root defines formatting rules for
JavaScript files but does not include a section for `*.d.ts` files
under `types/`. Type definition files may use inconsistent indentation
compared to their corresponding `.js` source files. Add an `[*.d.ts]`
section to `.editorconfig` matching the project's tab-based indentation
style, and verify the `tsconfig.json` `include` paths cover all type
definition directories under `types/`.

### M11: Restructure documentation with a migration guide and API reference

Add a `docs/migration-v8-to-v9.md` covering breaking changes between
execa v8 and v9: the IPC API restructuring in `lib/ipc/`, verbose
option changes in `lib/verbose/`, and transform system updates in
`lib/transform/`. Update `readme.md` to link to the migration guide
and restructure the API overview with categorized function listings
(execution methods, IPC, pipe, transform, convert, verbose). Add a
`docs/api-reference.md` providing a structured index of all exported
functions organized by category with links to the relevant `types/`
definition files. Update `package.json` to add a `"homepage"` field
pointing to the documentation and ensure the `"files"` array includes
the `docs/` directory for npm distribution.

### W11: Overhaul CI pipeline and project configuration files

Expand `.github/workflows/main.yml` to split the single `npm test`
command into separate jobs for type checking (`tsc` and `tsd`),
linting (`xo`), and unit tests (`c8 ava`), allowing independent
failure reporting and parallelism. Add a `.github/workflows/release.yml`
workflow for automated npm publishing with provenance attestation.
Update the existing `.github/codecov.yml` to add per-module coverage
targets for `lib/ipc/`, `lib/pipe/`, `lib/terminate/`, and
`lib/convert/`. Update the existing `.github/security.md` vulnerability
disclosure template to include a response timeline. Update
`package.json` scripts to add `test:types`, `test:lint`, and
`test:coverage` entries with `c8` threshold enforcement. Update
`tsconfig.json` to enable `exactOptionalPropertyTypes` for stricter
type checking across the `types/` directory. Add `provenance=true` to
`.npmrc` for supply chain security.
