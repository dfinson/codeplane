# Humanizr/Humanizer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/Humanizr/Humanizer |
| **License** | MIT |
| **Language** | C# |
| **Scale** | Small (focused library) |
| **Category** | String manipulation and formatting library |
| **Set** | ranker-gate |
| **Commit** | `5054735ad364a56d7c51345cc322ec8fbc65af99` |

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

### N1: Add ordinal word support for Slovenian

The Slovenian number-to-words converter in `Localisation/NumberToWords/SlovenianNumberToWordsConverter.cs` has a `ConvertToOrdinal()` method that simply returns the digit string via `number.ToString(culture)` instead of actual Slovenian ordinal words. Implement Slovenian ordinal words (prvi/prva/prvo for 1st, drugi/druga/drugo for 2nd, etc.) with grammatical gender agreement through `GrammaticalGender`. Handle irregular forms through at least 10th and regular suffix-based forms beyond that.

### N2: Add ordinal word support for Polish

The Polish number-to-words converter in `Localisation/NumberToWords/PolishNumberToWordsConverter.cs` has a `ConvertToOrdinal()` method that returns only the digit string via `number.ToString(culture)`. Implement actual Polish ordinal words (pierwszy/pierwsza/pierwsze for 1st, drugi/druga/drugie for 2nd, etc.) with grammatical gender agreement through the `GrammaticalGender` enum. Handle the irregular ordinal forms through 10th and regular derivation patterns beyond that.

### N3: Implement Croatian `ConvertToOrdinal` from its TODO stub

`CroatianNumberToWordsConverter.cs` in `Localisation/NumberToWords/` has a `ConvertToOrdinal()` method marked with `//TODO: In progress` that returns a numeric string fallback. Implement actual Croatian ordinal word generation (prvi, drugi, treći, etc.) with appropriate gender agreement, matching the pattern used by other Slavic converters like the Russian and Ukrainian implementations.

### N4: Implement Serbian Cyrillic `ConvertToOrdinal` from its TODO stub

`SerbianCyrlNumberToWordsConverter.cs` in `Localisation/NumberToWords/` has a `ConvertToOrdinal()` method marked `//TODO: In progress` that returns a numeric fallback. Implement Serbian Cyrillic ordinal words (први, други, трећи, etc.) with gender agreement, following the same patterns used by the existing Serbian Latin cardinal number conversion already in the file.

### N5: Implement Serbian Latin `ConvertToOrdinal` from its TODO stub

`SerbianNumberToWordsConverter.cs` in `Localisation/NumberToWords/` has a `ConvertToOrdinal()` method marked `//TODO: In progress` returning a numeric fallback. Implement Serbian Latin ordinal words (prvi, drugi, treći, etc.) with gender forms, mirroring the cardinal logic already present and following the same approach used by the Cyrillic Serbian variant.

### N6: Add German date-to-ordinal-words converter

The `Localisation/DateToOrdinalWords/` directory has converters for Catalan, Spanish, French, Lithuanian, and US English, but no German converter. German expresses ordinal dates as "1. Januar 2023" rather than "January 1st, 2023". Add `DeDateToOrdinalWordsConverter.cs` implementing `IDateToOrdinalWordConverter` with the German date format convention, and register it for the `"de"` locale in `Configuration/DateToOrdinalWordsConverterRegistry.cs`.

### N7: Add Italian date-to-ordinal-words converter

The `Localisation/DateToOrdinalWords/` directory lacks an Italian converter. Italian expresses ordinal dates as "1° gennaio 2023" rather than "January 1st, 2023", using the masculine ordinal indicator. Add `ItDateToOrdinalWordsConverter.cs` implementing `IDateToOrdinalWordConverter` with Italian date formatting conventions, and register it for the `"it"` locale in `Configuration/DateToOrdinalWordsConverterRegistry.cs`.

### N8: Add `FromWords` support for Spanish

The `Localisation/WordsToNumber/` directory contains only a `DefaultWordsToNumberConverter` and `EnglishWordsToNumberConverter`. The `WordsToNumberConverterRegistry` falls back to a default for non-English locales, which cannot parse Spanish number words. Add `SpanishWordsToNumberConverter.cs` implementing `IWordsToNumberConverter` to parse Spanish number words ("cuarenta y dos" → 42, "cien" → 100, "mil quinientos" → 1500) and register it for the `"es"` locale in the registry.

### N9: Add ordinal word support for Czech

The Czech localization has `ToWords()` for cardinal numbers but lacks `ToOrdinalWords()`. Implement the Czech ordinal converter, handling gender and case agreement (první, druhý/druhá/druhé) and the irregular forms through 10th.

### N10: Add `FromWords` support for French

The `Localisation/WordsToNumber/` directory has no French converter. The `WordsToNumberConverterRegistry` falls back to a default that cannot parse French number words. Add `FrenchWordsToNumberConverter.cs` implementing `IWordsToNumberConverter` to parse French number words ("quarante-deux" → 42, "soixante-dix" → 70, "quatre-vingts" → 80) handling French-specific vigesimal patterns. Register it for the `"fr"` locale in `WordsToNumberConverterRegistry.cs`.

## Medium

### M1: Add precision-based date humanization strategy with configurable thresholds

The `DateTimeHumanizeStrategy/` directory has `DefaultDateTimeHumanizeStrategy` and `PrecisionDateTimeHumanizeStrategy`, both producing single-unit output like "2 hours ago". Add a new `MultiUnitDateTimeHumanizeStrategy` that returns compound descriptions like "2 hours and 15 minutes ago" with a configurable number of output units. Implement `IDateTimeHumanizeStrategy` and wire it through `Configurator.DateTimeHumanizeStrategy`. Also add matching `MultiUnitDateOnlyHumanizeStrategy` and `MultiUnitDateTimeOffsetHumanizeStrategy` variants to maintain parity across the `DateTimeHumanizeStrategy/` interfaces. Update `DateHumanizeExtensions.cs` to expose an overload that accepts the precision parameter.

### M2: Add SI decimal unit support to `ByteSize`

The `ByteSize` struct in `Bytes/ByteSize.cs` uses 1024-based binary units (KB, MB, GB) but labels them with SI-style names, conflating IEC binary and SI decimal conventions. Add proper dual-mode support: IEC binary units (KiB, MiB, GiB — powers of 1024) and SI decimal units (kB, MB, GB — powers of 1000). The `Humanize()` and `ToString()` methods on `ByteSize` should accept a format flag to select the unit system. Update `ByteSizeExtensions.cs` to provide `.Humanize(ByteSizeUnit.SI)` and `.Humanize(ByteSizeUnit.IEC)` overloads. Extend `IFormatter.DataUnitHumanize` in the `Localisation/Formatters/` module to include the new unit symbols.

### M3: Add configurable Oxford comma and conjunction support to collection humanization

`CollectionHumanizeExtensions.cs` delegates to `ICollectionFormatter` implementations, but the current formatters (`DefaultCollectionFormatter` and `OxfordStyleCollectionFormatter` in `Localisation/CollectionFormatters/`) are limited: `OxfordStyleCollectionFormatter` is English-only, and `DefaultCollectionFormatter` takes only a conjunction string. Add a `ConfigurableCollectionFormatter` that supports: Oxford comma toggle, custom separator (not just comma), truncation with "and N more" (e.g., `["a", "b", "c", "d", "e"].Humanize(maxItems: 3)` → `"a, b, c, and 2 more"`), and custom element-to-string formatters. Register it as an option through `CollectionFormatterRegistry.cs` and expose the new parameters via overloads in `CollectionHumanizeExtensions.cs`.

### M4: Add `ToWords` support for ordinal numbers in `MetricNumeralExtensions`

`MetricNumeralExtensions.cs` provides `ToMetric()` and `FromMetric()` for SI prefix formatting but only handles the symbol forms ("k", "M", "G"). Add a `MetricNumeralFormats.Words` option so that `1500.ToMetric(MetricNumeralFormats.Words)` → `"1.5 kilo"` using the full prefix word instead of just the symbol. Extend the `MetricNumeralFormats` flags enum in `MetricNumeralFormats.cs` to include the new mode. Add localization support by extending `IFormatter` with a `MetricPrefixHumanize(string prefix)` method and implementing it in `Localisation/Formatters/DefaultFormatter.cs` and language-specific formatters.

### M5: Implement heading/slug generation

Add `Slugify()` that converts arbitrary text to URL-safe slugs: `"What's New in C# 12?".Slugify()` → `"whats-new-in-c-12"`. Handle diacritics via Unicode normalization and decomposition, collapse consecutive separators, strip non-alphanumeric characters, and support configurable separator character. Add transliteration mappings for common non-Latin scripts (Cyrillic, Greek) to produce ASCII-only slugs.

### M6: Add `ToRoman` support for large numbers with overline notation

`RomanNumeralExtensions.cs` limits `ToRoman()` to the range 1–3999. Extend the `ToRoman()` method to support numbers up to 3,999,999 using vinculum (overline) notation where a bar above a numeral multiplies its value by 1000. Since overline characters have no direct Unicode representation, use a configurable output format: parenthetical `(V)` = 5000, or Unicode combining overline `V̅`. Update `FromRoman()` to parse both notations back to integers. Add a `RomanNumeralStyle` enum to select between classic (1–3999) and extended modes, and update the guard in `ToRoman()` accordingly.

### M7: Add scoped vocabulary support for inflections

`Inflections/Vocabulary.cs` provides global `AddPlural()`, `AddSingular()`, `AddIrregular()`, and `AddUncountable()` methods via `Vocabularies.Default`, but all registrations modify a shared singleton. Add a scoped vocabulary mechanism: `Vocabulary.CreateScope()` returns a `VocabularyScope` that layers domain-specific rules over the default without polluting it. The scope should be passable to `Pluralize()` and `Singularize()` via new overloads in `InflectorExtensions.cs`. Implement `IDisposable` on `VocabularyScope` for block-scoped usage patterns. Ensure the default `Vocabulary` rule-set remains immutable once the `LocaliserRegistry` has frozen (matching the existing freeze behavior in `LocaliserRegistry.cs`).

### M8: Implement duration formatting with clock-style output

Add a `ToClockNotation()` method on `TimeSpan` that formats durations the way humans write them on clocks and stopwatches: `TimeSpan.FromSeconds(3661).ToClockNotation()` → `"1:01:01"`. Support optional display of days, configurable zero-padding, and fractional seconds for sub-second precision. Include localization for the separator character used in different cultures.

### M9: Add `ToAge` support across date types

`TimeSpanHumanizeExtensions.cs` provides `ToAge()` only on `TimeSpan`, returning strings like "40 years old". Extend age formatting to `DateTime` and `DateTimeOffset` via `DateHumanizeExtensions.cs` so that `birthDate.ToAge()` calculates the elapsed time from the given date and formats it as an age expression. Also add `DateOnly.ToAge()` to `DateToOrdinalWordsExtensions.cs` (under `#if NET6_0_OR_GREATER`). Ensure all variants delegate to `IFormatter.TimeSpanHumanize_Age()` for localization. Add `maxUnit` and `toWords` parameters matching the existing `TimeSpan.ToAge()` overload. Update `Localisation/Formatters/DefaultFormatter.cs` to support a gendered age format for languages that require it.

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
