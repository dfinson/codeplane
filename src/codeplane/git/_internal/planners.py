"""Decision planners that separate "what to do" from "how to do it"."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import pygit2

from codeplane.git._internal.access import RepoAccess
from codeplane.git.errors import GitError, RefNotFoundError


class DiffType(Enum):
    """Types of diff operations."""

    WORKING_TREE = auto()
    STAGED_UNBORN = auto()
    STAGED_NORMAL = auto()
    REF_TO_WORKING = auto()
    REF_TO_REF = auto()


@dataclass(frozen=True, slots=True)
class DiffPlan:
    """Plan for executing a diff operation."""

    diff_type: DiffType
    base_oid: pygit2.Oid | None = None
    target_oid: pygit2.Oid | None = None


class DiffPlanner:
    """Plans and executes diff operations."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def plan(
        self,
        base: str | None,
        target: str | None,
        staged: bool,
    ) -> DiffPlan:
        """Determine the type of diff to perform and resolve refs upfront."""
        if staged:
            if self._access.is_unborn:
                return DiffPlan(DiffType.STAGED_UNBORN)
            return DiffPlan(DiffType.STAGED_NORMAL)

        if base is None and target is None:
            return DiffPlan(DiffType.WORKING_TREE)

        # Resolve refs at plan time - fail early with clear errors
        if base is None and self._access.is_unborn:
            raise RefNotFoundError("HEAD (unborn)")

        base_oid = (
            self._access.resolve_commit(base).id if base else self._access.must_head_commit().id
        )

        if target is None:
            return DiffPlan(DiffType.REF_TO_WORKING, base_oid=base_oid)

        target_oid = self._access.resolve_commit(target).id
        return DiffPlan(DiffType.REF_TO_REF, base_oid=base_oid, target_oid=target_oid)

    def execute(self, plan: DiffPlan) -> pygit2.Diff:
        """Execute a diff plan. All validation done at plan time."""
        if plan.diff_type == DiffType.WORKING_TREE:
            return self._access.diff_working_tree()

        if plan.diff_type == DiffType.STAGED_UNBORN:
            # For unborn repos, diff staged against empty tree
            empty_tree = self._access.get_empty_tree()
            return empty_tree.diff_to_index(self._access.index)

        if plan.diff_type == DiffType.STAGED_NORMAL:
            return self._access.index.diff_to_tree(self._access.must_head_tree())

        if plan.diff_type == DiffType.REF_TO_WORKING:
            return self._access.diff_refs(plan.base_oid)  # type: ignore[arg-type]

        # REF_TO_REF
        return self._access.diff_refs(plan.base_oid, plan.target_oid)  # type: ignore[arg-type]


class CheckoutType(Enum):
    """Types of checkout operations."""

    LOCAL_BRANCH = auto()
    REMOTE_BRANCH_NEW_LOCAL = auto()
    REMOTE_BRANCH_EXISTING_LOCAL = auto()
    DETACHED = auto()


@dataclass(frozen=True, slots=True)
class CheckoutPlan:
    """Plan for executing a checkout operation."""

    checkout_type: CheckoutType
    ref: str
    local_name: str | None = None


class CheckoutPlanner:
    """Plans and executes checkout operations."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def plan(self, ref: str) -> CheckoutPlan:
        """Determine the type of checkout to perform."""
        if self._access.has_local_branch(ref):
            return CheckoutPlan(CheckoutType.LOCAL_BRANCH, ref)

        if self._access.has_remote_branch(ref):
            local_name = ref.split("/", 1)[-1]
            if self._access.has_local_branch(local_name):
                return CheckoutPlan(CheckoutType.REMOTE_BRANCH_EXISTING_LOCAL, ref, local_name)
            return CheckoutPlan(CheckoutType.REMOTE_BRANCH_NEW_LOCAL, ref, local_name)

        return CheckoutPlan(CheckoutType.DETACHED, ref)

    def execute(self, plan: CheckoutPlan) -> None:
        """Execute a checkout plan linearly - no recursion."""
        if plan.checkout_type == CheckoutType.LOCAL_BRANCH:
            branch = self._access.must_local_branch(plan.ref)
            self._access.checkout_branch(branch)

        elif plan.checkout_type == CheckoutType.REMOTE_BRANCH_NEW_LOCAL:
            remote = self._access.must_remote_branch(plan.ref)
            # Remote branches are direct refs, but handle symbolic just in case
            remote_target = remote.target
            if isinstance(remote_target, str):
                remote_target = self._access.resolve_ref_oid(remote_target)
            obj = self._access.get_object(remote_target)
            target = obj.peel(pygit2.Commit) if obj else None
            if not isinstance(target, pygit2.Commit):
                raise GitError(f"Remote branch {plan.ref!r} does not point to a commit")
            assert plan.local_name is not None  # CheckoutType guarantees this
            self._access.create_local_branch(plan.local_name, target)
            local = self._access.must_local_branch(plan.local_name)
            self._access.checkout_branch(local)

        elif plan.checkout_type == CheckoutType.REMOTE_BRANCH_EXISTING_LOCAL:
            assert plan.local_name is not None  # CheckoutType guarantees this
            local = self._access.must_local_branch(plan.local_name)
            self._access.checkout_branch(local)

        else:  # DETACHED
            oid = self._access.resolve_ref_oid(plan.ref)
            self._access.checkout_detached(oid)
