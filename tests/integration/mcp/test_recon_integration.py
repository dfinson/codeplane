"""Integration tests for the recon pipeline.

Tests the full recon pipeline end-to-end against a real indexed repository.
Verifies that after the SOLID decomposition refactoring (merge.py, rrf.py,
assembly.py split, OutputTier redesign), the pipeline still produces
correct, complete results.

Each test builds a real git repo, indexes it with tree-sitter + tantivy,
and runs the pipeline with a real AppContext.
"""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest

from codeplane.index.ops import IndexCoordinatorEngine
from codeplane.mcp.context import AppContext
from codeplane.mcp.tools.recon.assembly import (
    _build_failure_actions,
    build_agentic_hint,
    build_gate_hint,
)
from codeplane.mcp.tools.recon.merge import (
    _merge_candidates,
    _select_graph_seeds,
)
from codeplane.mcp.tools.recon.models import (
    FileCandidate,
    HarvestCandidate,
    OutputTier,
    ParsedTask,
)
from codeplane.mcp.tools.recon.parsing import parse_task
from codeplane.mcp.tools.recon.pipeline import (
    _file_centric_pipeline,
    _find_unindexed_files,
)
from codeplane.mcp.tools.recon.rrf import _enrich_file_candidates
from codeplane.mcp.tools.recon.scoring import (
    assign_tiers,
    compute_noise_metric,
)

pytestmark = pytest.mark.integration


# ===================================================================
# Fixtures
# ===================================================================


def _noop_progress(indexed: int, total: int, by_ext: dict[str, int], phase: str = "") -> None:
    pass


def _make_coordinator(repo_path: Path) -> IndexCoordinatorEngine:
    """Create an IndexCoordinatorEngine with proper paths."""
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir(exist_ok=True)
    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "tantivy"
    return IndexCoordinatorEngine(repo_path, db_path, tantivy_path)


@pytest.fixture
def recon_repo(tmp_path: Path) -> Path:
    """Create a multi-file Python repo designed to exercise all recon pipeline stages.

    Structure:
        src/
            __init__.py
            service.py      — main business logic (imports utils, models)
            utils.py         — helper functions (hub: called by service + tests)
            models.py        — data models (imported by service)
            config.py        — configuration
        tests/
            __init__.py
            test_service.py  — tests for service (imports service)
            test_utils.py    — tests for utils
        docs/
            README.md        — unindexed file (non-Python)
        pyproject.toml       — unindexed config file
    """
    repo_path = tmp_path / "recon_test_repo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path))

    repo = pygit2.Repository(str(repo_path))
    repo.config["user.name"] = "Recon Test"
    repo.config["user.email"] = "recon@test.com"

    # --- Source files ---
    (repo_path / "src").mkdir()
    (repo_path / "src" / "__init__.py").write_text("")

    (repo_path / "src" / "models.py").write_text('''"""Data models for the application."""

from dataclasses import dataclass


@dataclass
class User:
    """Represents a user in the system."""
    name: str
    email: str
    active: bool = True

    def display_name(self) -> str:
        """Return formatted display name."""
        return f"{self.name} <{self.email}>"


@dataclass
class Message:
    """A message sent between users."""
    sender: User
    recipient: User
    body: str

    def is_valid(self) -> bool:
        """Check if message is valid."""
        return bool(self.body.strip()) and self.sender.active
''')

    (
        repo_path / "src" / "utils.py"
    ).write_text('''"""Utility functions used across the application."""

import hashlib


def compute_hash(data: str) -> str:
    """Compute SHA256 hash of a string."""
    return hashlib.sha256(data.encode()).hexdigest()


def sanitize_input(text: str) -> str:
    """Sanitize user input by stripping and lowering."""
    return text.strip().lower()


def format_greeting(name: str) -> str:
    """Format a greeting message."""
    return f"Hello, {name}!"


def validate_email(email: str) -> bool:
    """Basic email validation."""
    return "@" in email and "." in email.split("@")[-1]
''')

    (repo_path / "src" / "service.py").write_text('''"""Main service module — business logic."""

from src.models import Message, User
from src.utils import compute_hash, sanitize_input, validate_email


class UserService:
    """Service for managing users."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def create_user(self, name: str, email: str) -> User:
        """Create a new user after validation."""
        clean_name = sanitize_input(name)
        if not validate_email(email):
            raise ValueError(f"Invalid email: {email}")
        user = User(name=clean_name, email=email)
        key = compute_hash(email)
        self._users[key] = user
        return user

    def get_user(self, email: str) -> User | None:
        """Look up user by email."""
        key = compute_hash(email)
        return self._users.get(key)

    def send_message(self, sender_email: str, recipient_email: str, body: str) -> Message:
        """Send a message between users."""
        sender = self.get_user(sender_email)
        recipient = self.get_user(recipient_email)
        if sender is None or recipient is None:
            raise ValueError("Both sender and recipient must exist")
        msg = Message(sender=sender, recipient=recipient, body=body)
        if not msg.is_valid():
            raise ValueError("Invalid message")
        return msg
''')

    (repo_path / "src" / "config.py").write_text('''"""Application configuration."""

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
APP_NAME = "recon-test-app"


def get_config() -> dict[str, object]:
    """Return application configuration."""
    return {
        "timeout": DEFAULT_TIMEOUT,
        "max_retries": MAX_RETRIES,
        "app_name": APP_NAME,
    }
''')

    # --- Test files ---
    (repo_path / "tests").mkdir()
    (repo_path / "tests" / "__init__.py").write_text("")

    (repo_path / "tests" / "test_service.py").write_text('''"""Tests for the UserService."""

from src.service import UserService


def test_create_user() -> None:
    """Test user creation."""
    svc = UserService()
    user = svc.create_user("Alice", "alice@example.com")
    assert user.name == "alice"
    assert user.email == "alice@example.com"


def test_get_user() -> None:
    """Test user lookup."""
    svc = UserService()
    svc.create_user("Bob", "bob@example.com")
    found = svc.get_user("bob@example.com")
    assert found is not None
    assert found.name == "bob"
''')

    (repo_path / "tests" / "test_utils.py").write_text('''"""Tests for utility functions."""

from src.utils import compute_hash, format_greeting, sanitize_input, validate_email


def test_compute_hash() -> None:
    assert len(compute_hash("hello")) == 64


def test_sanitize_input() -> None:
    assert sanitize_input("  Hello  ") == "hello"


def test_format_greeting() -> None:
    assert format_greeting("World") == "Hello, World!"


def test_validate_email() -> None:
    assert validate_email("user@example.com")
    assert not validate_email("invalid")
''')

    # --- Unindexed files ---
    (repo_path / "docs").mkdir()
    (repo_path / "docs" / "README.md").write_text("# Recon Test App\n\nA test application.\n")

    (repo_path / "pyproject.toml").write_text("""[project]
name = "recon-test-app"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
""")

    # --- .codeplane init ---
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir()

    # --- Git commit ---
    repo.index.add_all()
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Recon Test", "recon@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial multi-module repo", tree, [])

    return repo_path


@pytest.fixture
async def indexed_app_ctx(recon_repo: Path) -> AppContext:
    """Create an AppContext with a fully indexed repo."""
    coordinator = _make_coordinator(recon_repo)
    await coordinator.initialize(_noop_progress)

    app_ctx = AppContext.create(
        repo_root=recon_repo,
        db_path=recon_repo / ".codeplane" / "index.db",
        tantivy_path=recon_repo / ".codeplane" / "tantivy",
        coordinator=coordinator,
    )
    return app_ctx


# ===================================================================
# Pipeline integration tests
# ===================================================================


class TestPipelineEndToEnd:
    """Full pipeline tests — parse → harvest → merge → score → tier."""

    @pytest.mark.asyncio
    async def test_pipeline_returns_candidates(self, indexed_app_ctx: AppContext) -> None:
        """Pipeline produces file candidates for a valid task."""
        file_candidates, parsed, diag, session_info = await _file_centric_pipeline(
            indexed_app_ctx,
            "Implement user creation in the service module",
        )
        assert len(file_candidates) > 0
        assert parsed.intent.value in ("implement", "unknown")
        assert "total_ms" in diag

    @pytest.mark.asyncio
    async def test_pipeline_finds_relevant_files(self, indexed_app_ctx: AppContext) -> None:
        """Pipeline finds files relevant to a specific task."""
        file_candidates, parsed, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Fix the UserService.create_user method in src/service.py",
        )
        paths = {fc.path for fc in file_candidates}
        assert "src/service.py" in paths, f"service.py missing from {paths}"

    @pytest.mark.asyncio
    async def test_pipeline_with_seeds(self, indexed_app_ctx: AppContext) -> None:
        """Explicit seeds boost relevant definitions."""
        file_candidates, _, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Understand the UserService class",
            explicit_seeds=["UserService"],
        )
        paths = {fc.path for fc in file_candidates}
        # UserService is in service.py — should be found
        assert "src/service.py" in paths

    @pytest.mark.asyncio
    async def test_pipeline_with_pinned_paths(self, indexed_app_ctx: AppContext) -> None:
        """Pinned paths are guaranteed to appear in results."""
        file_candidates, _, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Review configuration",
            pinned_paths=["src/config.py"],
        )
        paths = {fc.path for fc in file_candidates}
        assert "src/config.py" in paths

    @pytest.mark.asyncio
    async def test_pipeline_tiers_assigned(self, indexed_app_ctx: AppContext) -> None:
        """All candidates get a valid tier."""
        file_candidates, _, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Understand the application architecture",
        )
        for fc in file_candidates:
            assert isinstance(fc.tier, OutputTier)
            assert fc.tier.rank in (0, 1, 2)

    @pytest.mark.asyncio
    async def test_pipeline_scaffold_above_lite(self, indexed_app_ctx: AppContext) -> None:
        """Scaffold-tier files have higher combined_score than lite-tier."""
        file_candidates, _, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "How does the user service work?",
        )
        if len(file_candidates) < 2:
            pytest.skip("Need at least 2 candidates for tier comparison")

        scaffold_scores = [fc.combined_score for fc in file_candidates if fc.tier.is_scaffold]
        lite_scores = [fc.combined_score for fc in file_candidates if not fc.tier.is_scaffold]

        if scaffold_scores and lite_scores:
            assert min(scaffold_scores) >= min(lite_scores)

    @pytest.mark.asyncio
    async def test_pipeline_diagnostics_complete(self, indexed_app_ctx: AppContext) -> None:
        """Diagnostics dict has all expected keys."""
        _, _, diag, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Find the email validation logic",
        )
        expected_keys = {"intent", "file_embed_ms", "file_embed_count", "total_ms"}
        assert expected_keys.issubset(diag.keys())

    @pytest.mark.asyncio
    async def test_pipeline_noise_metric(self, indexed_app_ctx: AppContext) -> None:
        """Session info includes noise metric in [0, 1]."""
        _, _, _, session_info = await _file_centric_pipeline(
            indexed_app_ctx,
            "What does compute_hash do?",
        )
        assert "noise_metric" in session_info
        assert 0.0 <= session_info["noise_metric"] <= 1.0


class TestPipelineExplicitPaths:
    """Tests for explicit path handling in the pipeline."""

    @pytest.mark.asyncio
    async def test_explicit_path_in_task(self, indexed_app_ctx: AppContext) -> None:
        """File paths mentioned in the task text are found."""
        file_candidates, parsed, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Fix a bug in src/utils.py related to email validation",
        )
        assert "src/utils.py" in parsed.explicit_paths
        paths = {fc.path for fc in file_candidates}
        assert "src/utils.py" in paths

    @pytest.mark.asyncio
    async def test_explicit_path_gets_scaffold_tier(self, indexed_app_ctx: AppContext) -> None:
        """Files explicitly mentioned always get promoted to scaffold tier."""
        file_candidates, _, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Review src/config.py for security issues",
            pinned_paths=["src/config.py"],
        )
        config_fc = next((fc for fc in file_candidates if fc.path == "src/config.py"), None)
        assert config_fc is not None
        assert config_fc.tier.is_scaffold


class TestPipelineCoRetrieval:
    """Tests for test co-retrieval in the pipeline."""

    @pytest.mark.asyncio
    async def test_test_file_found_for_source(self, indexed_app_ctx: AppContext) -> None:
        """When source files are found, their test files are co-retrieved."""
        file_candidates, _, _, _ = await _file_centric_pipeline(
            indexed_app_ctx,
            "Refactor the UserService in src/service.py",
            pinned_paths=["src/service.py"],
        )
        paths = {fc.path for fc in file_candidates}
        # test_service.py should be co-retrieved (imports service.py)
        assert "tests/test_service.py" in paths, f"test_service.py missing from {paths}"


# ===================================================================
# Parsing integration tests
# ===================================================================


class TestParsingIntegration:
    """Verify parse_task produces correct structured output for real queries."""

    def test_debug_task(self) -> None:
        parsed = parse_task("Fix the broken email validation in src/utils.py")
        assert parsed.intent.value == "debug"
        assert "src/utils.py" in parsed.explicit_paths
        assert any("email" in t or "validation" in t for t in parsed.primary_terms)

    def test_implement_task(self) -> None:
        parsed = parse_task("Add a delete_user method to UserService")
        assert parsed.intent.value == "implement"
        assert "UserService" in parsed.explicit_symbols

    def test_refactor_task(self) -> None:
        parsed = parse_task("Rename compute_hash to generate_hash across the codebase")
        assert parsed.intent.value == "refactor"
        assert any("compute_hash" in s or "generate_hash" in s for s in parsed.explicit_symbols)

    def test_understand_task(self) -> None:
        parsed = parse_task("How does the message sending flow work?")
        assert parsed.intent.value == "understand"

    def test_negative_mentions(self) -> None:
        parsed = parse_task("Fix everything except test files")
        assert len(parsed.negative_mentions) > 0

    def test_stacktrace_driven(self) -> None:
        parsed = parse_task(
            "Fix the TypeError exception in service.py traceback:\n"
            "  File 'service.py', line 20, in create_user\n"
            "TypeError: invalid email"
        )
        assert parsed.is_stacktrace_driven


# ===================================================================
# OutputTier integration tests
# ===================================================================


class TestOutputTierIntegration:
    """Verify OutputTier properties work correctly in context."""

    def test_rank_ordering(self) -> None:
        assert OutputTier.FULL_FILE.rank < OutputTier.MIN_SCAFFOLD.rank
        assert OutputTier.MIN_SCAFFOLD.rank < OutputTier.SUMMARY_ONLY.rank

    def test_api_value_mapping(self) -> None:
        assert OutputTier.FULL_FILE.api_value == "scaffold"
        assert OutputTier.MIN_SCAFFOLD.api_value == "scaffold"
        assert OutputTier.SCAFFOLD.api_value == "scaffold"
        assert OutputTier.SUMMARY_ONLY.api_value == "lite"
        assert OutputTier.LITE.api_value == "lite"

    def test_is_scaffold_property(self) -> None:
        assert OutputTier.FULL_FILE.is_scaffold
        assert OutputTier.SCAFFOLD.is_scaffold
        assert not OutputTier.SUMMARY_ONLY.is_scaffold
        assert not OutputTier.LITE.is_scaffold

    def test_tier_comparison_via_rank(self) -> None:
        """Rank-based comparisons replace inline tier_rank dicts."""
        fc1 = FileCandidate(path="a.py", combined_score=0.9)
        fc1.tier = OutputTier.FULL_FILE
        fc2 = FileCandidate(path="b.py", combined_score=0.5)
        fc2.tier = OutputTier.SUMMARY_ONLY

        assert fc1.tier.rank < fc2.tier.rank  # FULL_FILE has higher priority


# ===================================================================
# Scoring integration tests
# ===================================================================


class TestScoringIntegration:
    """Verify scoring functions work correctly on realistic data."""

    def test_assign_tiers_realistic(self) -> None:
        """assign_tiers works on a realistic score distribution."""
        candidates = [
            FileCandidate(path=f"src/f{i}.py", combined_score=score)
            for i, score in enumerate([0.9, 0.85, 0.7, 0.5, 0.3, 0.2, 0.1, 0.05])
        ]
        result = assign_tiers(candidates)

        # Should have some scaffold and some lite
        scaffold_count = sum(1 for fc in result if fc.tier.is_scaffold)
        lite_count = sum(1 for fc in result if not fc.tier.is_scaffold)
        assert scaffold_count > 0
        assert lite_count > 0 or len(result) <= 3  # small lists may all be scaffold
        # Sorted descending by score
        scores = [fc.combined_score for fc in result]
        assert scores == sorted(scores, reverse=True)

    def test_noise_metric_realistic(self) -> None:
        """Noise metric produces sensible values for typical distributions."""
        clear = compute_noise_metric([0.95, 0.9, 0.85, 0.1, 0.05])
        noisy = compute_noise_metric([0.5, 0.49, 0.48, 0.47, 0.46])
        assert clear < noisy  # clear signal has lower noise


# ===================================================================
# Merge integration tests
# ===================================================================


class TestMergeIntegration:
    """Verify merge functions work correctly."""

    def test_merge_accumulates_evidence(self) -> None:
        """Merging two harvests for the same uid accumulates evidence."""
        h1 = {"uid1": HarvestCandidate(def_uid="uid1", from_term_match=True, matched_terms={"foo"})}
        h2 = {"uid1": HarvestCandidate(def_uid="uid1", from_lexical=True, lexical_hit_count=3)}
        merged = _merge_candidates(h1, h2)
        assert merged["uid1"].from_term_match
        assert merged["uid1"].from_lexical
        assert merged["uid1"].lexical_hit_count == 3
        assert "foo" in merged["uid1"].matched_terms

    def test_merge_preserves_distinct_uids(self) -> None:
        """Merging doesn't lose candidates."""
        h1 = {"uid1": HarvestCandidate(def_uid="uid1")}
        h2 = {"uid2": HarvestCandidate(def_uid="uid2")}
        merged = _merge_candidates(h1, h2)
        assert "uid1" in merged
        assert "uid2" in merged

    def test_select_graph_seeds_prioritizes_evidence(self) -> None:
        """Candidates with more evidence axes are preferred as graph seeds."""
        merged = {
            "uid1": HarvestCandidate(
                def_uid="uid1", from_term_match=True, from_lexical=True, from_explicit=True
            ),
            "uid2": HarvestCandidate(def_uid="uid2", from_term_match=True),
        }
        seeds = _select_graph_seeds(merged)
        # uid1 should be preferred (3 axes + explicit bonus vs 1 axis)
        assert seeds[0] == "uid1"


# ===================================================================
# RRF integration tests
# ===================================================================


class TestRRFIntegration:
    """Verify RRF scoring produces correct rankings."""

    def test_multi_source_beats_single_source(self) -> None:
        """A file found by multiple sources scores higher than single-source."""
        parsed = ParsedTask(raw="test task", primary_terms=["test"])
        candidates = [
            FileCandidate(path="multi.py", similarity=0.5, combined_score=0.5),
            FileCandidate(path="single.py", similarity=0.5, combined_score=0.5),
        ]
        defs = {
            "uid1": HarvestCandidate(
                def_uid="uid1",
                from_term_match=True,
                from_lexical=True,
                matched_terms={"test"},
                lexical_hit_count=5,
            ),
        }
        defs["uid1"].file_path = "multi.py"
        defs["uid1"].term_idf_score = 1.0

        result = _enrich_file_candidates(candidates, defs, parsed)
        multi = next(fc for fc in result if fc.path == "multi.py")
        single = next(fc for fc in result if fc.path == "single.py")
        assert multi.combined_score > single.combined_score


# ===================================================================
# Assembly integration tests
# ===================================================================


class TestAssemblyIntegration:
    """Verify assembly functions produce correct output."""

    def test_failure_actions_no_dead_tools(self) -> None:
        """Failure actions only reference current tools."""
        actions = _build_failure_actions(["search", "handler"], ["src/handler.py"])
        action_names = {a["action"] for a in actions}
        # Should NOT reference dead tools
        assert "search" not in action_names
        assert "map_repo" not in action_names
        assert "read_source" not in action_names
        # Should reference current tools
        assert "recon" in action_names

    def test_gate_hint_hard_block(self) -> None:
        hint = build_gate_hint("hard_block", min_chars=500)
        assert "RECON HARD GATE" in hint
        assert "500" in hint

    def test_gate_hint_excessive(self) -> None:
        hint = build_gate_hint("excessive", call_num=3, min_chars=500)
        assert "#3" in hint

    def test_agentic_hint_has_scaffold_instructions(self) -> None:
        hint = build_agentic_hint(
            n_files=5,
            intent_value="implement",
            scaffold_files=[{"path": "src/main.py"}],
            lite_count=3,
            read_only=False,
            convention_test_paths=set(),
            tracked_paths=set(),
            pinned_paths=None,
            explicit_paths=None,
        )
        assert "HOW TO READ SCAFFOLDS" in hint
        assert "src/main.py" in hint

    def test_agentic_hint_missing_paths_warning(self) -> None:
        hint = build_agentic_hint(
            n_files=1,
            intent_value="debug",
            scaffold_files=[],
            lite_count=0,
            read_only=True,
            convention_test_paths=set(),
            tracked_paths={"src/main.py", "src/utils.py"},
            pinned_paths=["src/nonexistent.py"],
            explicit_paths=None,
        )
        assert "WARNING" in hint
        assert "nonexistent.py" in hint


# ===================================================================
# Unindexed file discovery integration
# ===================================================================


class TestUnindexedDiscovery:
    """Test unindexed file discovery against a real repo."""

    @pytest.mark.asyncio
    async def test_finds_unindexed_files(self, indexed_app_ctx: AppContext) -> None:
        """Files added after indexing are discovered via path matching."""
        repo_root = indexed_app_ctx.repo_root

        # Add files AFTER indexing (they won't be in the structural index)
        (repo_root / "Makefile").write_text("all:\n\techo hello\n")
        (repo_root / "deploy.yaml").write_text("name: deploy\n")

        # Git-track them
        import pygit2

        git_repo = pygit2.Repository(str(repo_root))
        git_repo.index.add("Makefile")
        git_repo.index.add("deploy.yaml")
        git_repo.index.write()

        # Collect indexed paths from DB
        from codeplane.index._internal.indexing.graph import FactQueries

        indexed_paths: set[str] = set()
        with indexed_app_ctx.coordinator.db.session() as session:
            fq = FactQueries(session)
            for frec in fq.list_files(limit=50000):
                indexed_paths.add(frec.path)

        # These new files should NOT be in the index
        assert "Makefile" not in indexed_paths
        assert "deploy.yaml" not in indexed_paths

        # But _find_unindexed_files should discover them
        parsed = parse_task("Fix the deploy pipeline and Makefile targets")
        found = _find_unindexed_files(indexed_app_ctx, parsed, indexed_paths)
        paths = [p for p, _ in found]
        assert any("deploy" in p for p in paths) or any("Makefile" in p for p in paths), (
            f"Expected deploy.yaml or Makefile in results, got: {paths}"
        )
