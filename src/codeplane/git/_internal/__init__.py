"""Internal components for git operations - not part of public API."""

from codeplane.git._internal.access import RepoAccess
from codeplane.git._internal.flows import WriteFlows
from codeplane.git._internal.parsing import (
    extract_local_branch_from_remote,
    extract_tag_name,
    first_line,
    make_tag_ref,
)
from codeplane.git._internal.planners import CheckoutPlanner, DiffPlanner
from codeplane.git._internal.preconditions import (
    check_nothing_to_commit,
    require_branch_exists,
    require_current_branch,
    require_not_current_branch,
    require_not_unborn,
)
from codeplane.git._internal.rebase import RebaseFlow, RebasePlanner

__all__ = [
    "CheckoutPlanner",
    "DiffPlanner",
    "RebaseFlow",
    "RebasePlanner",
    "RepoAccess",
    "WriteFlows",
    "check_nothing_to_commit",
    "extract_local_branch_from_remote",
    "extract_tag_name",
    "first_line",
    "make_tag_ref",
    "require_branch_exists",
    "require_current_branch",
    "require_not_current_branch",
    "require_not_unborn",
]
