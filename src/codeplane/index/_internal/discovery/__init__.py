"""Context discovery pipeline."""

from codeplane.index._internal.discovery.authority import (
    AuthorityResult,
    Tier1AuthorityFilter,
)
from codeplane.index._internal.discovery.membership import (
    MembershipResolver,
    MembershipResult,
    is_inside,
)
from codeplane.index._internal.discovery.probe import (
    BatchProbeResult,
    ContextProbe,
    ProbeConfig,
    ProbeResult,
    validate_contexts,
)
from codeplane.index._internal.discovery.router import (
    ContextRouter,
    FileRoute,
    RoutingResult,
    route_single_file,
)
from codeplane.index._internal.discovery.scanner import (
    AMBIENT_FAMILIES,
    INCLUDE_SPECS,
    MARKER_DEFINITIONS,
    UNIVERSAL_EXCLUDES,
    ContextDiscovery,
    DiscoveredMarker,
    DiscoveryResult,
)

__all__ = [
    # Scanner
    "ContextDiscovery",
    "DiscoveredMarker",
    "DiscoveryResult",
    "MARKER_DEFINITIONS",
    "AMBIENT_FAMILIES",
    "INCLUDE_SPECS",
    "UNIVERSAL_EXCLUDES",
    # Authority
    "Tier1AuthorityFilter",
    "AuthorityResult",
    # Membership
    "MembershipResolver",
    "MembershipResult",
    "is_inside",
    # Probe
    "ContextProbe",
    "ProbeConfig",
    "ProbeResult",
    "BatchProbeResult",
    "validate_contexts",
    # Router
    "ContextRouter",
    "FileRoute",
    "RoutingResult",
    "route_single_file",
]
