"""Optional, caller-owned native gates, separate from execution and observation.

The eight native boundaries retain the contracts inspected at
dfinson/traceforge@46e0340b7bb3fd4740f8dba02a5e7586fa26608f, governance/pipeline.py.
No TraceForge runtime, models, budget engine or policy implementation is used.
See backend/compat/traceforge.LICENSE for the upstream contract provenance.

These are not execution adapters. Trusted runtime composition must supply an
admitted ExecutionIdentity, the existing Job SessionPolicy, and a live ownership
fence. Installing a gate neither creates admission nor enables an integration.
Sync SDK calls must run in the runtime's worker, not on its policy event loop.
Native observation remains separate. Only app-owned invocation result receipts
feed the existing ledger, never raw observer callbacks. Gates make no
postflight-filter claim.
"""

from .binding import NativeBinding
from .installation import GateAttachment, NativeGates
from .readiness import dependency_readiness

__all__ = ["GateAttachment", "NativeBinding", "NativeGates", "dependency_readiness"]
