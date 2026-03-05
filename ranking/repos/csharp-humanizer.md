# Humanizr/Humanizer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Humanizr/Humanizer |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Small (focused library) |
| **Category** | String manipulation and formatting library |

## Why this repo

- **Single-purpose**: Manipulates and displays strings, enums, dates, times,
  quantities, and numbers in human-friendly formats. One developer can hold
  the entire codebase in their head.
- **Well-structured**: Source under `src/Humanizer/` with clear per-concern
  directories (inflections, truncation, date/time humanization, bytes, etc.).
  Consistent patterns across all humanization types.
- **Rich history**: 1.5K+ commits, community contributors for localization
  (30+ languages). Varied PR types from new features to localization to
  performance.
- **Permissive**: MIT license.

## Structure overview

```
src/Humanizer/
├── StringHumanizeExtensions.cs    # Core string humanization
├── EnumHumanizeExtensions.cs      # Enum display names
├── NumberToWordsExtension.cs      # Number-to-words conversion
├── DateHumanizeExtensions.cs      # "2 hours ago" style
├── TimeSpanHumanizeExtensions.cs  # Duration formatting
├── Inflections/                   # Pluralize, singularize
├── Truncation/                    # String truncation strategies
├── Bytes/                         # Byte size formatting
├── Localisation/                  # Localization resources
│   ├── NumberToWords/             # Per-language number-to-words
│   └── DateToOrdinalWords/        # Per-language ordinal dates
└── FluentDate/                    # Fluent date construction
```

## Scale indicators

- ~100 C# source files
- ~15K lines of code (excluding localization resources)
- Flat structure (1-2 levels)
- Zero dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `ToQuantity` formatting negative numbers incorrectly

`(-3).ToWords()` returns "minus three" correctly, but
`"item".ToQuantity(-3, ShowQuantityAs.Words)` returns "minus three item"
(singular) instead of "minus three items" (plural). The pluralization
logic does not handle negative quantities. Fix `ToQuantity` to use the
absolute value for pluralization decisions.

### N2: Add ordinal word support for Portuguese

The Portuguese localization supports `ToWords()` for cardinal numbers
but not `ToOrdinalWords()`. Implement the ordinal number-to-words
converter for Portuguese, handling gender agreement (primeiro/primeira)
and irregular ordinals through 10th.

### N3: Fix `Truncate` with `TruncateFrom.Left` adding ellipsis at wrong end

When truncating from the left with `"long string".Truncate(5, Truncator.FixedLength, TruncateFrom.Left)`, the ellipsis characters appear
at the end instead of the beginning. The output is `"tring…"` but
should be `"…tring"`. Fix the left-truncation to place the truncation
indicator at the left.

### N4: Fix `Singularize` incorrectly singularizing uncountable nouns

Calling `"sheep".Singularize()` returns "shep" instead of "sheep". The singularization engine does not consult the uncountable-words list before applying suffix-stripping rules. Fix the inflection pipeline to check uncountables first, matching the guard already present in `Pluralize`.

### N5: Fix `Humanize` on `TimeSpan` dropping sub-second precision

`TimeSpan.FromMilliseconds(750).Humanize()` returns "no time" instead of "750 milliseconds". The humanizer considers only whole-second granularity and discards the millisecond component. Fix the duration humanization to include milliseconds when the total span is less than one second.

### N6: Fix `ToRoman` returning empty string for zero

`(0).ToRoman()` silently returns an empty string. Roman numerals have no zero, but the method should throw an `ArgumentOutOfRangeException` or return a documented sentinel rather than silently producing invalid output. Add an explicit guard with a clear error message.

### N7: Fix `Titleize` breaking on acronyms

`"use the NASA api".Titleize()` produces "Use The N A S A Api" because the titleization logic inserts spaces before each uppercase letter in a contiguous uppercase run. Adjust the word-boundary detection to preserve fully-uppercase sequences as single tokens.

### N8: Fix `Kebaberize` not handling consecutive uppercase letters

`"XMLParser".Kebaberize()` yields `"x-m-l-parser"` rather than `"xml-parser"`. The casing-boundary detection splits every uppercase-to-uppercase transition. Update the kebab-case converter to group runs of uppercase letters as one segment, splitting only before the final uppercase letter when followed by lowercase.

### N9: Add ordinal word support for Czech

The Czech localization has `ToWords()` for cardinal numbers but lacks `ToOrdinalWords()`. Implement the Czech ordinal converter, handling gender and case agreement (první, druhý/druhá/druhé) and the irregular forms through 10th.

### N10: Fix `Humanize` on enum flags showing raw integer for combined values

When a `[Flags]` enum has a value that is a combination of defined members, `Humanize()` shows the underlying integer instead of a comma-separated list of flag names. Fix the enum humanizer to decompose combined flag values into their named constituents before formatting.

## Medium

### M1: Add relative time formatting with granularity control

The current `Humanize()` on `DateTime` shows "2 hours ago" but with no
control over granularity. Implement a `Humanize(precision)` overload
that shows multiple units: "2 hours and 15 minutes ago" (precision=2),
"2 hours, 15 minutes, and 30 seconds ago" (precision=3). Support both
past and future directions. Add `maxUnit` and `minUnit` parameters
to cap the range.

### M2: Implement file size formatting with IEC/SI units

Add `ByteSize` formatting that supports both IEC binary units (KiB, MiB,
GiB — powers of 1024) and SI decimal units (KB, MB, GB — powers of 1000).
Include parsing from formatted strings back to byte counts. Support
custom decimal precision, automatic unit selection, and explicit unit
specification. Add localization for unit names in supported languages.

### M3: Add collection humanization

Implement `IEnumerable<T>.Humanize()` that formats collections as
English lists: `["a", "b", "c"].Humanize()` → `"a, b, and c"`.
Support conjunctions (`and`, `or`), Oxford comma configuration,
custom separators, custom element formatters, and truncation for
long lists (`"a, b, and 8 more"`). Add localization for conjunctions
and list formatting conventions.

### M4: Add metric prefix formatting for numbers

Implement `ToMetric()` that formats large and small numbers with SI metric prefixes: `1_500.ToMetric()` → `"1.5k"`, `0.003.ToMetric()` → `"3m"`. Support the full SI prefix range from yocto to yotta, custom decimal precision, and both symbol and full-word output modes ("kilo" vs "k"). Include a corresponding `FromMetric()` parser.

### M5: Implement heading/slug generation

Add `Slugify()` that converts arbitrary text to URL-safe slugs: `"What's New in C# 12?".Slugify()` → `"whats-new-in-c-12"`. Handle diacritics via Unicode normalization and decomposition, collapse consecutive separators, strip non-alphanumeric characters, and support configurable separator character. Add transliteration mappings for common non-Latin scripts (Cyrillic, Greek) to produce ASCII-only slugs.

### M6: Add Roman numeral parsing and validation

Implement `FromRoman()` that converts Roman numeral strings back to integers: `"XLII".FromRoman()` → `42`. Validate strict Roman numeral rules (no more than three consecutive identical symbols, correct subtractive notation). Provide clear error messages for malformed input. Support both uppercase and lowercase input and the overline notation for large numbers.

### M7: Add a noun-countability API for inflections

Extend the inflection engine with `AddUncountable()`, `AddIrregular()`, and `AddPluralizationRule()` methods that let consumers register domain-specific vocabulary at runtime. Ensure thread safety for concurrent registration and lookup, and provide a scoped mechanism to add rules for one call without polluting the global rule set.

### M8: Implement duration formatting with clock-style output

Add a `ToClockNotation()` method on `TimeSpan` that formats durations the way humans write them on clocks and stopwatches: `TimeSpan.FromSeconds(3661).ToClockNotation()` → `"1:01:01"`. Support optional display of days, configurable zero-padding, and fractional seconds for sub-second precision. Include localization for the separator character used in different cultures.

### M9: Add text-to-number parsing

Implement `FromWords()` that parses written-out numbers back to integers: `"forty-two".FromWords()` → `42`. Handle compound numbers ("one hundred and twenty-three"), ordinals ("third" → 3), and negative values ("minus seven" → −7). Support the same set of languages that `ToWords()` already handles, delegating to per-locale parsers.

### M10: Implement sentence-level truncation

Add a `TruncateAtSentence()` strategy that truncates text at the nearest sentence boundary rather than mid-word: `"First sentence. Second sentence. Third.".Truncate(30, Truncator.AtSentence)` → `"First sentence. Second sentence."`. Detect sentence boundaries using common punctuation patterns (period, exclamation, question mark followed by whitespace or end-of-string). Support configurable ellipsis placement and minimum-length thresholds.

## Wide

### W1: Add comprehensive date/time humanization

Implement a unified date/time humanization system covering: relative
time ("2 days ago"), calendar time ("last Tuesday"), approximate
duration ("about a month"), date ranges ("March 3–7"), recurring
patterns ("every other Tuesday"), and time-of-day descriptions
("this morning", "tonight"). Support timezone-aware formatting and
cultural conventions (24h vs 12h, date order). Add localization for
all time expressions.

### W2: Implement a fluent builder API for complex formatting

Add a `Formatter` builder that chains humanization operations:
`Formatter.For(value).Truncate(50).Titleize().ToQuantity(3)`.
The builder should be composable, reusable (save a formatter
configuration and apply to many values), and type-safe (only show
methods applicable to the current value type). Include predefined
formatters for common patterns (slug generation, display name
formatting, search-friendly text). Add serialization of formatter
configurations for use in configuration files.

### W3: Add comprehensive number formatting across cultures

Extend number humanization to cover ordinal suffixes ("1st", "2nd"), approximate descriptions ("about a dozen", "nearly a hundred"), digit grouping with culture-specific separators, fraction-to-words ("three quarters"), and percentage humanization ("42%" → "forty-two percent"). Each feature must respect the localization system, with per-language overrides for ordinal suffixes, approximate-quantity thresholds, and fraction vocabulary. Add formatters for at least ten languages.

### W4: Implement a string difference humanizer

Add `"old text".HumanizeDifferenceTo("new text")` that produces a human-readable summary of text changes: insertions, deletions, and replacements described in natural language. Support word-level and character-level granularity, configurable context size around changes, and a summary mode ("3 words changed, 1 added, 2 removed"). Include localization for the change descriptors and integrate with the existing extension-method conventions.

### W5: Add cron expression humanization

Implement `"0 30 9 ? * MON-FRI".HumanizeCron()` → `"At 9:30 AM, Monday through Friday"`. Parse standard five- and six-field cron expressions, translate each field independently, and combine them into fluent natural-language descriptions. Handle special characters (ranges, lists, steps, wildcards), named days and months, and non-standard extensions like `L`, `W`, and `#`. Add localization for day names, month names, and time-of-day phrasing across supported languages.

### W6: Implement comprehensive enum humanization with metadata

Extend enum humanization to read from multiple attribute sources — `[Display]`, `[Description]`, `[DisplayName]`, and a new `[HumanizedValue]` attribute — with a configurable priority order. Add support for grouped enums (rendering as optgroup-style structures), enum-to-color mappings, enum-to-icon mappings, and enum value ordering annotations. Provide a discovery API that returns all humanized values for an enum type as a structured list. Localize display values via satellite resource files matched by culture.

### W7: Add a natural-language list-differencing formatter

Implement `oldList.HumanizeDifferenceTo(newList)` for `IEnumerable<T>` that summarizes changes: `"Added 'banana' and 'cherry'; removed 'fig'; moved 'apple' from position 1 to 3"`. Detect additions, removals, and moves using a longest-common-subsequence algorithm. Support custom equality comparers, configurable verbosity (terse vs. detailed), and grouping of similar changes. Localize the change verbs and conjunctions for all supported languages.

### W8: Implement a human-friendly regex describer

Add `"^[A-Z]{2}\\d{4}$".HumanizeRegex()` → `"Starts with exactly 2 uppercase letters followed by exactly 4 digits"`. Parse the regex AST and translate character classes, quantifiers, anchors, groups, alternations, and lookaheads into plain English. Support named groups, backreferences, and common shorthand classes. Localize the output descriptions and handle nested groups with proper natural-language nesting ("either … or …").

### W9: Add a data-table humanizer

Implement `DataTable.Humanize()` that formats tabular data for human consumption: column alignment, header separators, automatic column-width calculation, number and date formatting within cells, row-count summaries, and truncation of wide or tall tables. Support multiple output formats (plain text, Markdown, simple HTML). Integrate with existing `Humanize()` methods for individual cell values. Add localization for summary text and table-chrome characters.

### W10: Implement a units-of-measurement humanization system

Add `Measure.From(1500, Unit.Gram).Humanize()` → `"1.5 kilograms"`. Support length, mass, volume, temperature, area, speed, and data-transfer-rate dimensions. Implement automatic unit scaling (grams → kilograms → tonnes), configurable precision, and both full-word and abbreviated output. Include bidirectional conversion within a dimension and parsing from formatted strings back to values. Localize unit names, symbols, and decimal formatting for all supported cultures.
