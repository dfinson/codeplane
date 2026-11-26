"""Developer self-restart runtime support.

Shared, dependency-light modules used by ``backend/cli.py`` (launch-profile
publication) and ``tools/dev_restart.py`` (parent/helper restart flow):

- ``restart_protocol``: paths, timeouts, phase logging, atomic JSON I/O,
  process-identity checks, and the restart lock.
- ``launch_profile``: the active launch profile (``~/.codeplane/run.json``)
  schema, atomic persistence, and fail-closed validation.

See ``internal-docs/design/developer-restart.md``
for the governing architecture and solution design.
"""
