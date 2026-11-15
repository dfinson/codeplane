# Developer restart contract

The developer restart tool preserves the identity and launch configuration of
the server it replaces. It does not infer a process from its executable name or
kill unrelated listeners.

## Active launch profile

`backend/services/dev_restart/launch_profile.py` owns the versioned schema,
validation, serialization, and atomic persistence of `CODEPLANE_HOME/run.json`.
The server publishes the profile only after its listener binds. A valid profile
records the executable, working directory, host, actual port, development and
remote modes, provider and tunnel ownership, process ID and creation time, and
write time.

Credentials are represented by a closed secret-source classification:
`not_required`, `resolvable` with provider/reference, or `unreplayable`.
The profile never contains the secret value. A replacement can reuse only a
resolvable source. Missing, stale, malformed, or unsupported profiles fail
before disruptive action.

Process identity includes PID and process creation time to prevent PID reuse
from selecting another process. Listener ownership is checked separately.
An ephemeral requested port must be resolved to the bound port before publishing
the profile.

## Restart protocol

`restart_protocol.py` supplies shared paths, timeouts, atomic JSON operations,
phase logging, and the restart lock. The protocol uses pending, claimed, started,
and ready request files. A separate native helper can survive termination of
the original server. Windows uses native process handling, not a Linux shell.

The parent prepares the request and waits for helper adoption before releasing
control. The helper validates identity, pauses eligible work, stops only the
identified server, launches the replacement from the recorded environment, and
checks readiness. Failure at a stage remains a failure, not a successful restart
with missing verification.

Remote restarts preserve provider and ownership distinctions. Externally owned
tunnels must not be stopped as if they were managed children. A remote readiness
check supplements local listener readiness when applicable.

## Evidence and failure behavior

Requests and profiles use same-directory temporary files, flush/fsync, and atomic
replacement. Logs are secret-free and identify the request and phase. The restart
log rotates at 5 MiB with one backup. Replacement stdout/stderr uses a separate
per-request log so an inherited handle cannot block log rotation.

Timeouts bound helper adoption, pause, stop, readiness, and remote checks.
Incomplete requests, invalid credentials, stale identity, and occupied ports
must produce actionable diagnostics. The integration tests for launch profiles,
restart protocol, native helper, and remote behavior define the maintained
executable contract. This document does not authorize restarting a shared server.
