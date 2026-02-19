"""Tests for data format (Markdown, TOML, YAML, JSON) symbol extraction.

Verifies that LanguageQueryConfig entries for data/config file formats
correctly extract structural symbols via tree-sitter queries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.parsing.treesitter import SyntacticSymbol, TreeSitterParser


@pytest.fixture
def parser() -> TreeSitterParser:
    return TreeSitterParser()


def _parse(
    parser: TreeSitterParser, content: bytes, ext: str, tmp_path: Path
) -> list[SyntacticSymbol]:
    """Parse content via a temp file with the right extension."""
    f = tmp_path / f"test{ext}"
    f.write_bytes(content)
    result = parser.parse(f)
    return parser.extract_symbols(result)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


class TestMarkdownExtraction:
    """Verify heading extraction from markdown sections."""

    MARKDOWN_SRC = b"""# Title
Some text
## Section One
Content here
### Subsection
More content
## Section Two
Another section
"""

    def test_heading_count(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.MARKDOWN_SRC, ".md", tmp_path)
        assert len(symbols) == 4  # Title, Section One, Subsection, Section Two

    def test_heading_names(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.MARKDOWN_SRC, ".md", tmp_path)
        names = [s.name for s in symbols]
        assert names == ["Title", "Section One", "Subsection", "Section Two"]

    def test_heading_kind(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.MARKDOWN_SRC, ".md", tmp_path)
        assert all(s.kind == "heading" for s in symbols)

    def test_section_spans_for_nesting(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Section spans should allow line-range containment nesting."""
        symbols = _parse(parser, self.MARKDOWN_SRC, ".md", tmp_path)
        title = next(s for s in symbols if s.name == "Title")
        sec1 = next(s for s in symbols if s.name == "Section One")
        subsec = next(s for s in symbols if s.name == "Subsection")
        # Title section spans the whole document
        assert title.line == 1
        # Section One should be contained within Title
        assert sec1.line >= title.line and sec1.end_line <= title.end_line
        # Subsection should be contained within Section One
        assert subsec.line >= sec1.line and subsec.end_line <= sec1.end_line


# ---------------------------------------------------------------------------
# TOML
# ---------------------------------------------------------------------------


class TestTomlExtraction:
    """Verify table and pair extraction from TOML."""

    TOML_SRC = b"""[package]
name = "my-project"
version = "0.1.0"

[dependencies]
serde = "1.0"

[tool.ruff]
target-version = "py312"
"""

    def test_table_count(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.TOML_SRC, ".toml", tmp_path)
        tables = [s for s in symbols if s.kind == "table"]
        assert len(tables) == 3  # package, dependencies, tool.ruff

    def test_pair_count(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.TOML_SRC, ".toml", tmp_path)
        pairs = [s for s in symbols if s.kind == "pair"]
        assert len(pairs) == 4  # name, version, serde, target-version

    def test_table_names(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.TOML_SRC, ".toml", tmp_path)
        table_names = [s.name for s in symbols if s.kind == "table"]
        assert "package" in table_names
        assert "dependencies" in table_names
        assert "tool.ruff" in table_names

    def test_dotted_key_table(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Dotted key tables like [tool.ruff] should use full dotted name."""
        symbols = _parse(parser, self.TOML_SRC, ".toml", tmp_path)
        dotted = next(s for s in symbols if s.name == "tool.ruff")
        assert dotted.kind == "table"

    def test_pair_inside_table_span(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Pairs should fall within their parent table's line range."""
        symbols = _parse(parser, self.TOML_SRC, ".toml", tmp_path)
        pkg_table = next(s for s in symbols if s.name == "package")
        name_pair = next(s for s in symbols if s.name == "name")
        assert name_pair.line >= pkg_table.line
        assert name_pair.end_line <= pkg_table.end_line


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------


class TestYamlExtraction:
    """Verify key extraction from YAML."""

    YAML_SRC = b"""name: test
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
  deploy:
    runs-on: ubuntu-latest
"""

    def test_key_extraction(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.YAML_SRC, ".yaml", tmp_path)
        names = [s.name for s in symbols]
        assert "name" in names
        assert "jobs" in names
        assert "build" in names

    def test_all_kind_is_key(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.YAML_SRC, ".yaml", tmp_path)
        assert all(s.kind == "key" for s in symbols)

    def test_nested_spans(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Nested YAML keys should have containment-compatible spans."""
        symbols = _parse(parser, self.YAML_SRC, ".yaml", tmp_path)
        jobs = next(s for s in symbols if s.name == "jobs")
        build = next(s for s in symbols if s.name == "build")
        # build should be within jobs span
        assert build.line >= jobs.line
        assert build.end_line <= jobs.end_line


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


class TestJsonExtraction:
    """Verify pair extraction from JSON."""

    JSON_SRC = b"""{
  "name": "test",
  "version": "1.0",
  "scripts": {
    "build": "tsc",
    "test": "jest"
  }
}
"""

    def test_pair_extraction(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.JSON_SRC, ".json", tmp_path)
        assert len(symbols) == 5  # name, version, scripts, build, test

    def test_all_kind_is_pair(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        symbols = _parse(parser, self.JSON_SRC, ".json", tmp_path)
        assert all(s.kind == "pair" for s in symbols)

    def test_nested_pairs_contained(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Nested JSON pairs should fall within parent pair span."""
        symbols = _parse(parser, self.JSON_SRC, ".json", tmp_path)
        scripts = next(s for s in symbols if "scripts" in s.name)
        build = next(s for s in symbols if "build" in s.name)
        assert build.line >= scripts.line
        assert build.end_line <= scripts.end_line
