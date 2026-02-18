"""Tool definitions - register all supported lint tools."""

from codeplane.lint import parsers
from codeplane.lint.models import ToolCategory
from codeplane.lint.tools import LintTool, registry

# =============================================================================
# Python Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="python.ruff",
        name="Ruff",
        languages=frozenset({"python"}),
        category=ToolCategory.LINT,
        executable="ruff",
        config_files=["pyproject.toml:tool.ruff", "ruff.toml", ".ruff.toml"],
        check_args=["check", "--output-format=json"],
        fix_args=["check", "--fix", "--output-format=json"],
        dry_run_args=["check", "--diff", "--output-format=json"],
        output_format="json",
    ),
    parser=parsers.parse_ruff,
)

registry.register(
    LintTool(
        tool_id="python.ruff-format",
        name="Ruff Format",
        languages=frozenset({"python"}),
        category=ToolCategory.FORMAT,
        executable="ruff",
        config_files=["pyproject.toml:tool.ruff", "ruff.toml", ".ruff.toml"],
        check_args=["format", "--check", "--diff"],
        fix_args=["format"],
        dry_run_args=["format", "--check", "--diff"],
        output_format="custom",
    ),
    parser=parsers.parse_ruff_format,
)
registry.register(
    LintTool(
        tool_id="python.mypy",
        name="mypy",
        languages=frozenset({"python"}),
        category=ToolCategory.TYPE_CHECK,
        executable="mypy",
        config_files=["pyproject.toml:tool.mypy", "mypy.ini", ".mypy.ini", "setup.cfg"],
        check_args=["--output=json"],
        fix_args=["--output=json"],  # mypy doesn't fix
        dry_run_args=["--output=json"],
        output_format="json",
    ),
    parser=parsers.parse_mypy,
)

registry.register(
    LintTool(
        tool_id="python.pyright",
        name="Pyright",
        languages=frozenset({"python"}),
        category=ToolCategory.TYPE_CHECK,
        executable="pyright",
        config_files=["pyrightconfig.json", "pyproject.toml:tool.pyright"],
        check_args=["--outputjson"],
        fix_args=["--outputjson"],  # pyright doesn't fix
        dry_run_args=["--outputjson"],
        output_format="json",
    ),
    parser=parsers.parse_pyright,
)

registry.register(
    LintTool(
        tool_id="python.bandit",
        name="Bandit",
        languages=frozenset({"python"}),
        category=ToolCategory.SECURITY,
        executable="bandit",
        config_files=["pyproject.toml:tool.bandit", ".bandit", "bandit.yaml"],
        check_args=["-f", "json", "-r"],
        fix_args=["-f", "json", "-r"],  # bandit doesn't fix
        dry_run_args=["-f", "json", "-r"],
        output_format="json",
    ),
    parser=parsers.parse_bandit,
)

registry.register(
    LintTool(
        tool_id="python.black",
        name="Black",
        languages=frozenset({"python"}),
        category=ToolCategory.FORMAT,
        executable="black",
        config_files=["pyproject.toml:tool.black"],
        check_args=["--check", "--diff"],
        fix_args=[],
        dry_run_args=["--check", "--diff"],
        output_format="custom",
        stderr_has_output=True,
    ),
    parser=parsers.parse_black_check,
)

registry.register(
    LintTool(
        tool_id="python.isort",
        name="isort",
        languages=frozenset({"python"}),
        category=ToolCategory.FORMAT,
        executable="isort",
        config_files=["pyproject.toml:tool.isort", ".isort.cfg", "setup.cfg"],
        check_args=["--check", "--diff"],
        fix_args=[],
        dry_run_args=["--check", "--diff"],
        output_format="custom",
    ),
    parser=parsers.parse_gofmt,  # Lists files
)


# =============================================================================
# JavaScript/TypeScript Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="js.eslint",
        name="ESLint",
        languages=frozenset({"javascript", "typescript"}),
        category=ToolCategory.LINT,
        executable="eslint",
        config_files=[
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yml",
            ".eslintrc.yaml",
            "eslint.config.js",
            "eslint.config.mjs",
        ],
        check_args=["--format=json"],
        fix_args=["--fix", "--format=json"],
        dry_run_args=["--format=json"],
        output_format="json",
    ),
    parser=parsers.parse_eslint,
)

registry.register(
    LintTool(
        tool_id="js.tsc",
        name="TypeScript Compiler",
        languages=frozenset({"typescript"}),
        category=ToolCategory.TYPE_CHECK,
        executable="tsc",
        config_files=["tsconfig.json"],
        check_args=["--noEmit"],
        fix_args=["--noEmit"],  # tsc doesn't fix
        dry_run_args=["--noEmit"],
        output_format="custom",
        paths_position="none",  # tsc uses tsconfig
    ),
    parser=parsers.parse_tsc,
)

registry.register(
    LintTool(
        tool_id="js.prettier",
        name="Prettier",
        languages=frozenset(
            {"javascript", "typescript", "json", "css", "html", "markdown", "yaml"}
        ),
        category=ToolCategory.FORMAT,
        executable="prettier",
        config_files=[
            ".prettierrc",
            ".prettierrc.js",
            ".prettierrc.json",
            ".prettierrc.yml",
            ".prettierrc.yaml",
            "prettier.config.js",
            "prettier.config.mjs",
        ],
        check_args=["--check"],
        fix_args=["--write"],
        dry_run_args=["--check"],
        output_format="custom",
    ),
    parser=parsers.parse_prettier_check,
)

registry.register(
    LintTool(
        tool_id="js.biome",
        name="Biome",
        languages=frozenset({"javascript", "typescript", "json"}),
        category=ToolCategory.LINT,
        executable="biome",
        config_files=["biome.json", "biome.jsonc"],
        check_args=["lint", "--reporter=json"],
        fix_args=["lint", "--write", "--reporter=json"],
        dry_run_args=["lint", "--reporter=json"],
        output_format="json",
    ),
    parser=parsers.parse_biome,
)


# =============================================================================
# Go Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="go.vet",
        name="go vet",
        languages=frozenset({"go"}),
        category=ToolCategory.LINT,
        executable="go",
        config_files=["go.mod"],
        check_args=["vet", "./..."],
        fix_args=["vet", "./..."],  # go vet doesn't fix
        dry_run_args=["vet", "./..."],
        output_format="custom",
        paths_position="none",
    ),
    parser=parsers.parse_go_vet,
)

registry.register(
    LintTool(
        tool_id="go.staticcheck",
        name="staticcheck",
        languages=frozenset({"go"}),
        category=ToolCategory.LINT,
        executable="staticcheck",
        # Only detect if explicitly configured (not just any Go project)
        config_files=["staticcheck.conf"],
        check_args=["-f", "json", "./..."],
        fix_args=["-f", "json", "./..."],  # staticcheck doesn't fix
        dry_run_args=["-f", "json", "./..."],
        output_format="json",
        paths_position="none",
    ),
    parser=parsers.parse_staticcheck,
)

registry.register(
    LintTool(
        tool_id="go.golangci-lint",
        name="golangci-lint",
        languages=frozenset({"go"}),
        category=ToolCategory.LINT,
        executable="golangci-lint",
        config_files=[".golangci.yml", ".golangci.yaml", ".golangci.json", ".golangci.toml"],
        check_args=["run", "--out-format=json"],
        fix_args=["run", "--fix", "--out-format=json"],
        dry_run_args=["run", "--out-format=json"],
        output_format="json",
        paths_position="none",
    ),
    parser=parsers.parse_golangci_lint,
)

registry.register(
    LintTool(
        tool_id="go.gofmt",
        name="gofmt",
        languages=frozenset({"go"}),
        category=ToolCategory.FORMAT,
        executable="gofmt",
        config_files=["go.mod"],
        check_args=["-l"],
        fix_args=["-w"],
        dry_run_args=["-l"],
        output_format="custom",
    ),
    parser=parsers.parse_gofmt,
)

registry.register(
    LintTool(
        tool_id="go.goimports",
        name="goimports",
        languages=frozenset({"go"}),
        category=ToolCategory.FORMAT,
        executable="goimports",
        config_files=["go.mod"],
        check_args=["-l"],
        fix_args=["-w"],
        dry_run_args=["-l"],
        output_format="custom",
    ),
    parser=parsers.parse_gofmt,
)


# =============================================================================
# Rust Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="rust.clippy",
        name="Clippy",
        languages=frozenset({"rust"}),
        category=ToolCategory.LINT,
        executable="cargo",
        config_files=["Cargo.toml", "clippy.toml", ".clippy.toml"],
        check_args=["clippy", "--message-format=json"],
        fix_args=["clippy", "--fix", "--allow-dirty", "--message-format=json"],
        dry_run_args=["clippy", "--message-format=json"],
        output_format="json",
        paths_position="none",
    ),
    parser=parsers.parse_clippy,
)

registry.register(
    LintTool(
        tool_id="rust.rustfmt",
        name="rustfmt",
        languages=frozenset({"rust"}),
        category=ToolCategory.FORMAT,
        executable="cargo",
        config_files=["Cargo.toml", "rustfmt.toml", ".rustfmt.toml"],
        check_args=["fmt", "--check"],
        fix_args=["fmt"],
        dry_run_args=["fmt", "--check"],
        output_format="custom",
        paths_position="none",
    ),
    parser=parsers.parse_rustfmt_check,
)

registry.register(
    LintTool(
        tool_id="rust.cargo-audit",
        name="cargo-audit",
        languages=frozenset({"rust"}),
        category=ToolCategory.SECURITY,
        executable="cargo",
        # Only detect if Cargo.lock exists (indicates dependencies to audit)
        config_files=["Cargo.lock"],
        check_args=["audit", "--json"],
        fix_args=["audit", "--json"],  # audit doesn't fix
        dry_run_args=["audit", "--json"],
        output_format="json",
        paths_position="none",
    ),
    parser=parsers.parse_cargo_audit,
)


# =============================================================================
# Java/Kotlin Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="java.checkstyle",
        name="Checkstyle",
        languages=frozenset({"java"}),
        category=ToolCategory.LINT,
        executable="checkstyle",
        config_files=["checkstyle.xml", ".checkstyle"],
        check_args=["-f", "xml", "-c", "checkstyle.xml"],
        fix_args=["-f", "xml", "-c", "checkstyle.xml"],  # checkstyle doesn't fix
        dry_run_args=["-f", "xml", "-c", "checkstyle.xml"],
        output_format="custom",
    ),
    parser=parsers.parse_checkstyle,
)

registry.register(
    LintTool(
        tool_id="kotlin.ktlint",
        name="ktlint",
        languages=frozenset({"kotlin"}),
        category=ToolCategory.LINT,
        executable="ktlint",
        config_files=[".ktlint"],
        check_args=["--reporter=json"],
        fix_args=["-F", "--reporter=json"],
        dry_run_args=["--reporter=json"],
        output_format="json",
    ),
    parser=parsers.parse_ktlint,
)


# =============================================================================
# C#/.NET Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="dotnet.format",
        name="dotnet format",
        languages=frozenset({"csharp", "vb"}),
        category=ToolCategory.FORMAT,
        executable="dotnet",
        # Detect via .NET-specific files, not generic .editorconfig
        config_files=["Directory.Build.props", "global.json"],
        check_args=["format", "--verify-no-changes"],
        fix_args=["format"],
        dry_run_args=["format", "--verify-no-changes"],
        output_format="custom",
        paths_position="none",
    ),
    parser=parsers.parse_dotnet_format,
)


# =============================================================================
# Ruby Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="ruby.rubocop",
        name="RuboCop",
        languages=frozenset({"ruby"}),
        category=ToolCategory.LINT,
        executable="rubocop",
        config_files=[".rubocop.yml", ".rubocop.yaml"],
        check_args=["--format", "json"],
        fix_args=["--autocorrect", "--format", "json"],
        dry_run_args=["--format", "json"],
        output_format="json",
    ),
    parser=parsers.parse_rubocop,
)


# =============================================================================
# PHP Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="php.phpcs",
        name="PHP_CodeSniffer",
        languages=frozenset({"php"}),
        category=ToolCategory.LINT,
        executable="phpcs",
        config_files=["phpcs.xml", "phpcs.xml.dist", ".phpcs.xml"],
        check_args=["--report=json"],
        fix_args=["--report=json"],  # Use phpcbf for fixes
        dry_run_args=["--report=json"],
        output_format="json",
    ),
    parser=parsers.parse_phpcs,
)

registry.register(
    LintTool(
        tool_id="php.phpcbf",
        name="PHP Code Beautifier",
        languages=frozenset({"php"}),
        category=ToolCategory.FORMAT,
        executable="phpcbf",
        config_files=["phpcs.xml", "phpcs.xml.dist", ".phpcs.xml"],
        check_args=[],  # phpcbf always fixes
        fix_args=[],
        dry_run_args=["--dry-run"],
        output_format="custom",
    ),
    parser=parsers.parse_phpcs,
)

registry.register(
    LintTool(
        tool_id="php.phpstan",
        name="PHPStan",
        languages=frozenset({"php"}),
        category=ToolCategory.TYPE_CHECK,
        executable="phpstan",
        config_files=["phpstan.neon", "phpstan.neon.dist"],
        check_args=["analyse", "--error-format=json"],
        fix_args=["analyse", "--error-format=json"],  # phpstan doesn't fix
        dry_run_args=["analyse", "--error-format=json"],
        output_format="json",
    ),
    parser=parsers.parse_phpstan,
)


# =============================================================================
# Shell Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="shell.shellcheck",
        name="ShellCheck",
        languages=frozenset({"bash", "sh", "shell"}),
        category=ToolCategory.LINT,
        executable="shellcheck",
        config_files=[".shellcheckrc"],
        check_args=["-f", "json"],
        fix_args=["-f", "json"],  # shellcheck doesn't fix
        dry_run_args=["-f", "json"],
        output_format="json",
    ),
    parser=parsers.parse_shellcheck,
)

registry.register(
    LintTool(
        tool_id="shell.shfmt",
        name="shfmt",
        languages=frozenset({"bash", "sh", "shell"}),
        category=ToolCategory.FORMAT,
        executable="shfmt",
        # No dedicated config file - detect via shellcheck config as proxy
        config_files=[".shellcheckrc"],
        check_args=["-l"],
        fix_args=["-w"],
        dry_run_args=["-l", "-d"],
        output_format="custom",
    ),
    parser=parsers.parse_shfmt,
)


# =============================================================================
# Miscellaneous Tools
# =============================================================================

registry.register(
    LintTool(
        tool_id="docker.hadolint",
        name="Hadolint",
        languages=frozenset({"dockerfile"}),
        category=ToolCategory.LINT,
        executable="hadolint",
        config_files=[".hadolint.yaml", ".hadolint.yml", "hadolint.yaml"],
        check_args=["--format", "json"],
        fix_args=["--format", "json"],  # hadolint doesn't fix
        dry_run_args=["--format", "json"],
        output_format="json",
    ),
    parser=parsers.parse_hadolint,
)

registry.register(
    LintTool(
        tool_id="yaml.yamllint",
        name="yamllint",
        languages=frozenset({"yaml"}),
        category=ToolCategory.LINT,
        executable="yamllint",
        config_files=[".yamllint", ".yamllint.yaml", ".yamllint.yml"],
        check_args=["-f", "parsable"],
        fix_args=["-f", "parsable"],  # yamllint doesn't fix
        dry_run_args=["-f", "parsable"],
        output_format="custom",
    ),
    parser=parsers.parse_yamllint,
)

registry.register(
    LintTool(
        tool_id="markdown.markdownlint",
        name="markdownlint",
        languages=frozenset({"markdown"}),
        category=ToolCategory.LINT,
        executable="markdownlint",
        config_files=[".markdownlint.json", ".markdownlint.yaml", ".markdownlint.yml"],
        check_args=["--json"],
        fix_args=["--fix", "--json"],
        dry_run_args=["--json"],
        output_format="json",
    ),
    parser=parsers.parse_markdownlint,
)

registry.register(
    LintTool(
        tool_id="sql.sqlfluff",
        name="SQLFluff",
        languages=frozenset({"sql"}),
        category=ToolCategory.LINT,
        executable="sqlfluff",
        config_files=[".sqlfluff", "setup.cfg:sqlfluff", "pyproject.toml:tool.sqlfluff"],
        check_args=["lint", "--format", "json"],
        fix_args=["fix", "--force"],
        dry_run_args=["lint", "--format", "json"],
        output_format="json",
    ),
    parser=parsers.parse_sqlfluff,
)
