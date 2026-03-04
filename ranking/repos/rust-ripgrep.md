# BurntSushi/ripgrep

| Field | Value |
|-------|-------|
| **URL** | https://github.com/BurntSushi/ripgrep |
| **License** | MIT OR UNLICENSE |
| **Language** | Rust |
| **Scale** | Medium (multi-module project) |
| **Category** | Recursive search tool |

## Why this repo

- **Multi-module with clear boundaries**: Regex engine integration, glob
  matching, directory traversal, printer/output formatting, CLI argument
  handling — each is a separate crate in the workspace with well-defined
  interfaces between them.
- **Well-structured**: Workspace of focused crates (`grep-regex`, `grep-searcher`,
  `grep-matcher`, `grep-printer`, `grep-cli`, `ignore`). Each crate has a
  clear single responsibility.
- **Rich history**: 2K+ commits by BurntSushi with extremely high code quality.
  PRs and issues show real decisions about performance, correctness, and API
  design.
- **Permissive**: Dual-licensed MIT OR UNLICENSE.

## Structure overview

```
.
├── crates/
│   ├── matcher/         # Trait for regex matching backends
│   ├── regex/           # Regex matcher implementation (wraps regex crate)
│   ├── searcher/        # Core search logic (line-by-line, multiline)
│   ├── printer/         # Output formatting (standard, JSON, summary)
│   ├── cli/             # CLI utilities (colors, human-readable output)
│   ├── ignore/          # .gitignore-style file filtering + directory walking
│   └── globset/         # Glob pattern matching
├── src/
│   ├── main.rs          # CLI entry point
│   ├── app.rs           # Argument parsing and config
│   ├── args.rs          # Argument processing
│   └── search.rs        # Search coordinator
└── tests/               # Integration tests
```

## Scale indicators

- ~100 Rust source files across crates
- ~40K lines of code
- Clear crate boundaries with trait-based interfaces
- 2-3 levels of module nesting within crates

---

## Tasks

8 tasks (3 narrow, 3 medium, 2 wide) for the Rust recursive search tool.

## Narrow

### N1: Fix `--count-matches` incorrect with `--multiline`

When using `--count-matches` together with `--multiline`, the count is
wrong for patterns that match across line boundaries. The counter
increments per line touched by the match instead of per match occurrence.
Fix the multiline match counter in the searcher crate to count actual
match occurrences.

### N2: Add `--max-columns-preview` to show truncated line prefix

When `--max-columns` truncates a long line, the output shows
`[Omitted long matching line]` with no content preview. Add a
`--max-columns-preview` flag that shows the first N characters of the
truncated line followed by `...` instead of the generic omission
message.

### N3: Fix `.rgignore` not loaded from `$HOME` on Windows

On Windows, ripgrep looks for the global ignore file at
`$HOME/.rgignore` but `$HOME` is not standard on Windows. The lookup
should also check `%USERPROFILE%\.rgignore` and
`%APPDATA%\ripgrep\ignore`. Fix the global ignore file discovery in
the `ignore` crate to use platform-appropriate paths.

## Medium

### M1: Implement search-and-replace with `--replace` improvements

Expand the `--replace` functionality to support capture group
transformations: `\U$1` (uppercase), `\L$1` (lowercase), `\E` (end
case change). Currently `--replace` only supports literal substitution
and numbered capture groups. This requires changes to the regex crate
integration, the printer crate (for preview output), and CLI argument
validation.

### M2: Add JSON output for structured consumption

Implement a `--json` output format that emits one JSON object per
match with fields: file path, line number, column range, matched text,
context lines (before/after), and byte offset. The JSON printer should
handle binary file detection, encoding issues, and streaming output
(one JSON object per line, not a single array). Add tests for
round-tripping JSON output.

### M3: Implement parallel directory traversal with work stealing

The current directory traversal uses a thread pool but the walk is
single-threaded with a channel to workers. Implement a work-stealing
directory walker where each thread independently walks its portion
of the directory tree and steals work from other threads' queues when
idle. This should improve performance on network filesystems and
repositories with many directories at the same depth.

## Wide

### W1: Add Language-aware search

Implement `--lang` filtering that goes beyond file extension matching
to understand language-specific structures. Support searching only
within comments (`--lang-scope comment`), string literals
(`--lang-scope string`), or code (`--lang-scope code`) for supported
languages. This requires integrating a fast tokenizer (not full
parser) for major languages. The tokenizer should run in the searcher
crate before regex matching.

### W2: Implement search indexing for repeated searches

Add an optional `--index` mode that builds a trigram index of the
file content on first run and uses it for subsequent searches to
skip files that cannot contain the search pattern. The index should
be stored alongside the directory, updated incrementally on file
changes (using mtime checks), and ignored when the user's pattern
uses features that can't be trigram-filtered (e.g., `.` or `\w`).
This affects the ignore crate (index storage), searcher crate
(index lookup), and CLI (index management commands).
