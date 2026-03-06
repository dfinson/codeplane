# briannesbitt/Carbon

| Field | Value |
|-------|-------|
| **URL** | https://github.com/briannesbitt/Carbon |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Large |
| **Category** | Date/time library |
| **Set** | Cutoff |
| **Commit** | `72ee09e5ada27bd82d668ba30e877722251d8322` |

## Why this repo

- **Well-structured**: Core classes (`Carbon`, `CarbonImmutable`,
  `CarbonInterval`, `CarbonPeriod`) compose behaviour from fine-grained
  traits in `Traits/` — `Creator`, `Difference`, `Comparison`,
  `Converter`, `Modifiers`, `Localization`, `Units`, `Boundaries`,
  `Serialization`, and more. The interface `CarbonInterface` defines
  the comprehensive API contract.
- **Rich history**: 5K+ commits, 17K+ stars. The standard PHP date/time
  library, extending PHP's `DateTime`. Issues cover timezone edge cases,
  DST transitions, locale formatting, and interval arithmetic.
- **Permissive**: MIT license.

## Structure overview

```
src/Carbon/
├── Carbon.php                 # Mutable DateTime subclass — uses all traits
├── CarbonImmutable.php        # Immutable variant — returns new instances
├── CarbonInterface.php        # Comprehensive interface — hundreds of methods
├── CarbonInterval.php         # DateInterval extension — fluent interval creation
├── CarbonPeriod.php           # Date period iteration — start, end, step
├── CarbonPeriodImmutable.php  # Immutable period variant
├── CarbonTimeZone.php         # Timezone wrapper with region/offset resolution
├── Factory.php                # Configurable factory for Carbon instances
├── FactoryImmutable.php       # Factory for CarbonImmutable
├── Translator.php             # Translation backend — message catalogues
├── TranslatorImmutable.php    # Immutable translator variant
├── AbstractTranslator.php     # Base translator with catalogue loading
├── Language.php               # Language metadata — names, regions
├── Month.php                  # Month enum
├── WeekDay.php                # WeekDay enum
├── Unit.php                   # Time unit enum — year, month, day, hour, etc.
├── WrapperClock.php           # PSR-20 Clock wrapper
├── Callback.php               # Macro callback wrapper
├── Traits/
│   ├── Date.php               # Master trait — composes all other traits (~3K lines)
│   ├── Creator.php            # Static constructors — parse, create, createFromFormat
│   ├── Comparison.php         # Comparison methods — eq, gt, lt, between, isSameDay
│   ├── Difference.php         # Diff methods — diffInDays, diffForHumans
│   ├── Converter.php          # Format conversion — toDateString, toISOString, toArray
│   ├── Modifiers.php          # Date modification — addDays, subMonths, next, previous
│   ├── Units.php              # Unit arithmetic — add/sub with overflow handling
│   ├── Boundaries.php         # Start/end of — startOfDay, endOfMonth, startOfWeek
│   ├── Localization.php       # Locale management — setLocale, getTranslatedDayName
│   ├── Serialization.php      # JSON/serialisation — toJSON, jsonSerialize
│   ├── Rounding.php           # Rounding — roundHour, floorMinute, ceilDay
│   ├── Week.php               # ISO week — weekOfYear, weekYear, daysInYear
│   ├── Timestamp.php          # Unix timestamp handling
│   ├── Options.php            # Global/local settings — strictMode, monthsOverflow
│   ├── StaticOptions.php      # Static configuration — useStrictMode, setHumanDiffOptions
│   ├── Macro.php              # Macro registration — dynamic method extension
│   ├── Mixin.php              # Mixin support — external trait composition
│   ├── Cast.php               # Type casting between Carbon/DateTime
│   ├── Mutability.php         # Mutable/immutable conversion helpers
│   ├── MagicParameter.php     # Named parameter parsing for magic methods
│   ├── IntervalRounding.php   # Interval-specific rounding
│   ├── IntervalStep.php       # Custom step for periods
│   ├── LocalFactory.php       # Instance-level factory
│   ├── ObjectInitialisation.php # Constructor helpers
│   ├── StaticLocalization.php # Static locale helpers
│   ├── Test.php               # Time mocking — setTestNow, withTestNow
│   └── ToStringFormat.php     # Default __toString format
├── Exceptions/
│   ├── InvalidDateException.php
│   ├── InvalidFormatException.php
│   ├── InvalidTimeZoneException.php
│   ├── OutOfRangeException.php
│   └── ...
├── Lang/                      # ~200 locale translation files
├── Cli/                       # CLI tool for project utilities
├── Constants/                 # Enums for diff options, formats, units
├── Laravel/                   # Laravel service provider integration
├── PHPStan/                   # PHPStan type extensions
└── MessageFormatter/          # ICU message formatting support
```

## Scale indicators

- ~900+ PHP source files (including ~200 locale files)
- ~59K lines of PHP code
- Moderate depth (2–3 levels: Traits/, Exceptions/, Lang/)
- Depends on `symfony/translation` for i18n

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix diffForHumans not handling micro-second precision for sub-second diffs

The `diffForHumans()` method in `Traits/Difference.php` reports diffs
down to seconds but does not include microsecond-level precision. When
two Carbon instances differ by less than one second, the output is
"0 seconds ago" rather than a microsecond-accurate representation. Add
a `microsecond` resolution option so `diffForHumans()` can report
"500 milliseconds ago" or "250 microseconds ago" when configured.

### N2: Add `toRelativeCalendarString()` for colloquial date references

The `Converter` trait in `Traits/Converter.php` provides various
`toString` formats but lacks a relative calendar method that returns
"today", "yesterday", "tomorrow", "last Monday", or "next Friday"
depending on the date relative to now. Add `toRelativeCalendarString()`
using the existing locale translations from `Lang/` for the day and
relative terms.

### N3: Fix CarbonPeriod iteration skipping dates at DST boundaries

The `CarbonPeriod` class in `CarbonPeriod.php` iterates by adding
intervals to a start date. When crossing a DST boundary with a 1-hour
interval, the iteration can skip or double-count the ambiguous hour.
Fix the iteration logic to use wall-clock arithmetic that preserves the
intended step size across DST transitions, using `CarbonTimeZone`
offset detection.

### N4: Add `closestWeekday()` modifier to Modifiers trait

The `Modifiers` trait in `Traits/Modifiers.php` provides `nextWeekday()`
and `previousWeekday()` but not a `closestWeekday()` that returns the
nearest weekday (Mon–Fri), preferring the same day if it is already a
weekday. Add `closestWeekday()` that returns Saturday→Friday and
Sunday→Monday, using the existing `isWeekday()` check in the
`Comparison` trait.

### N5: Fix `startOfQuarter()` not respecting custom fiscal year start month

The `Boundaries` trait in `Traits/Boundaries.php` calculates quarters
based on calendar months (Q1 = Jan–Mar). When a fiscal year starts in
a non-January month (e.g., April for UK financial year), `startOfQuarter()`
returns incorrect boundaries. Add a `fiscalYearStartMonth` option to
`Options` trait and use it in the quarter boundary calculations.

### N6: Add `diffInBusinessDays()` to Difference trait

The `Difference` trait in `Traits/Difference.php` provides `diffInDays()`,
`diffInWeekdays()`, and similar methods but not a business-day diff
that excludes weekends and configurable public holidays. Add
`diffInBusinessDays()` that accepts an optional array of holiday dates
and counts only Mon–Fri non-holiday days between two dates.

### N7: Fix `createFromTimestamp()` losing microsecond precision on 32-bit systems

The `Creator` trait in `Traits/Creator.php` implements
`createFromTimestamp()` using integer casting for the timestamp. On
32-bit systems, float timestamps lose microsecond precision due to
floating-point representation. Fix the method to use string-based
timestamp parsing via `createFromFormat('U.u', ...)` to preserve
microsecond accuracy regardless of platform.

### N8: Add `toHumanDuration()` for CarbonInterval

The `CarbonInterval` class in `CarbonInterval.php` can be cast to
strings but lacks a human-friendly duration formatter that respects the
current locale. Add `toHumanDuration()` that formats intervals like
"2 hours, 30 minutes" (English) or "2 heures, 30 minutes" (French),
using the translation files in `Lang/` and the `Localization` trait.

### N9: Fix `isSameAs()` format comparison not handling timezone-aware formats

The `isSameAs()` method in `Traits/Comparison.php` compares two dates
by formatting both with a given format string (e.g., `'Y-m-d'`). When
the format includes timezone-sensitive components (e.g., `'H'` for
hour), two dates in different timezones can incorrectly compare as
different even when they represent the same instant. Fix `isSameAs()`
to normalise both dates to the same timezone before formatting when
the format includes time components.

### N10: Add `age()` method returning years, months, and days decomposition

The `Difference` trait in `Traits/Difference.php` provides `age` as an
integer number of years, but there is no method that returns a full
decomposition (years, months, days) suitable for displaying a person's
exact age. Add `exactAge()` returning a `CarbonInterval` with precise
year/month/day components, handling leap years correctly.

## Medium

### M1: Implement recurring schedule support in CarbonPeriod

Extend `CarbonPeriod` to support recurring schedules: "every Tuesday
and Thursday from 9:00 to 17:00" or "first Monday of every month".
Add a `schedule()` factory that accepts a cron-like expression or a
fluent DSL, iteration logic in `CarbonPeriod`, conflict detection for
overlapping schedules, and `Comparison` traits for testing schedule
membership.

### M2: Add timezone-aware range operations to Comparison trait

Implement `overlaps()`, `contains()`, `gap()`, and `intersection()`
for date ranges represented as pairs of Carbon instances. Handle
timezone differences by normalising to UTC internally. Support
open-ended ranges (null start/end). Requires new methods in
`Traits/Comparison.php`, helper types for range representation, and
integration with `CarbonPeriod` for iteration over intersections.

### M3: Implement a date expression parser for natural language input

Add `Carbon::parseNaturalLanguage('next business day after Christmas')`,
supporting relative expressions ("3 weeks from now"), named dates
("Christmas", "Easter"), ordinal references ("third Friday in November"),
and combinations. Requires a tokeniser and recursive-descent parser in a
new `Parser/` namespace, integration with `Creator` trait's `parse()`
method, and locale-aware date name resolution via `Localization`.

### M4: Add calendar system abstraction for non-Gregorian calendars

Implement support for Jalali (Persian), Hijri (Islamic), and Hebrew
calendars via a `CalendarSystem` abstraction. Add `toJalali()`,
`toHijri()`, and `fromJalali()` conversion methods. Requires a calendar
converter interface, conversion algorithms in a new `Calendar/`
namespace, integration with the `Converter` and `Creator` traits, and
locale-specific month/day names in `Lang/`.

### M5: Implement a date testing DSL with fluent assertions

Add `Carbon::assertThat($date)->isWeekday()->isAfter('2024-01-01')
->hasTimezone('UTC')` for expressive test assertions. Support
negation (`isNot()`), chaining, custom failure messages, and
integration with PHPUnit custom assertions. Requires a new
`Testing/DateAssert` class, method delegation to `Comparison` trait
methods, PHPUnit assertion integration, and a `Traits/Test.php`
extension.

### M6: Add work-schedule-aware date arithmetic

Implement `addBusinessDays(5)`, `subBusinessHours(3)`, and
`nextBusinessHour()` that respect configurable work schedules (e.g.,
Mon–Fri, 09:00–17:00) and holiday calendars. Requires a `Schedule`
configuration class, modifications to `Traits/Modifiers.php` and
`Traits/Units.php` for schedule-aware arithmetic, holiday storage, and
integration with `Factory` for per-instance schedule binding.

### M7: Implement a lightweight cron expression parser for CarbonPeriod

Add `CarbonPeriod::fromCron('0 9 * * MON-FRI')` that generates dates
matching a cron schedule. Support standard 5-field cron and extensions
(seconds, years). Requires a cron parser in a new `Cron/` namespace,
integration with `CarbonPeriod` for iteration, timezone handling for
cron evaluation, and `Comparison` trait support for `matchesCron()`.

### M8: Add ISO 8601 duration and interval serialisation round-tripping

The `Serialization` trait in `Traits/Serialization.php` handles JSON
serialisation but does not fully round-trip ISO 8601 duration strings
(e.g., `P1Y2M3DT4H5M6S`) through `CarbonInterval`. Fix parsing of
the full ISO 8601 duration spec including weeks, fractional values,
and negative durations. Add `toISO8601Duration()` and ensure
`CarbonInterval::fromString()` handles all valid ISO formats.

### M9: Implement relative date formatting with granularity control

Extend `diffForHumans()` in `Traits/Difference.php` to support
multi-unit output: "2 years, 3 months ago" instead of "2 years ago".
Add a `parts` option to control the number of units shown, a
`threshold` option for when to roll up to the next unit, and
`join` and `separator` options for output formatting. Propagate
locale-specific conjunctions from `Lang/`.

### M10: Add clock abstraction with PSR-20 compliance across all factories

The `WrapperClock` class provides basic PSR-20 support, but the
`Factory` and `FactoryImmutable` classes do not consistently use it
for all time-dependent operations. Refactor all `now()` calls,
`setTestNow()`, and timestamp generation in `Creator`, `Comparison`,
and `Difference` traits to delegate to a configurable `ClockInterface`,
enabling dependency-injected time in testing without global state.

## Wide

### W1: Implement a comprehensive date/time validation framework

Add `Carbon::validate('2024-02-30', 'Y-m-d')` with rich validation:
date existence checking (Feb 30 doesn't exist), format strictness,
range constraints, timezone validity, and business rule validation
(e.g., "must be a business day"). Return structured validation results
with error codes. Changes span `Creator` parsing, a new `Validation/`
namespace, format analysis, timezone validation in `CarbonTimeZone`,
and locale-aware error messages.

### W2: Add event sourcing support with immutable date timeline

Implement a `Timeline` class that tracks date/time state changes as an
immutable event log: "created at X, modified at Y, timezone changed to
Z". Support replaying timelines to reconstruct state, diffing timelines,
serialisation, and undo/redo. Changes span `CarbonImmutable`, a new
`Timeline/` namespace, `Serialization` trait, `Factory`, and integration
with `CarbonPeriod` for timeline queries.

### W3: Implement a timezone intelligence layer

Add timezone-aware features beyond basic conversion: business-hours
overlap detection between timezones, optimal meeting time finder across
multiple timezones, timezone abbreviation disambiguation (CST = Central
vs. China), DST transition prediction, and timezone change history.
Changes span `CarbonTimeZone`, `Comparison` trait, a new `Timezone/`
namespace, IANA timezone database parsing, and `Localization` for
timezone name translations.

### W4: Add comprehensive date arithmetic with overflow strategies

Implement configurable overflow strategies for all date arithmetic:
`addMonths(1)` on Jan 31 could overflow to Mar 3 (current default),
clamp to Feb 28/29, or throw. Support per-operation strategy override,
global defaults in `Options`, and strategy composition for chained
operations. Changes span `Traits/Units.php`, `Traits/Modifiers.php`,
`Traits/Options.php`, `CarbonInterval`, `CarbonPeriod`, and
`Factory` configuration.

### W5: Implement a date formatting template engine

Add `Carbon::format('{relative} ({iso})')` with template syntax
supporting conditionals (`{if:past}ago{/if}`), pluralisation
(`{count:year|years}`), locale switching (`{locale:fr}{month}{/locale}`),
and nested expressions. Requires a template parser, an expression
evaluator, integration with `Converter` trait methods, `Localization`
for locale switching, and a template cache for performance.

### W6: Add a migration toolkit for DateTime to Carbon conversion

Implement static analysis tools and runtime helpers for migrating
legacy code from `DateTime` to `Carbon`: a PHP-CS-Fixer rule for
automated conversion, runtime deprecation notices for `DateTime` usage,
a compatibility layer that logs Carbon-specific methods called on
plain `DateTime` objects, and migration guides. Changes span `Cast`
trait, a new `Migration/` namespace, `Factory`, `PHPStan/` extensions,
and Laravel integration.

### W7: Implement a calendar UI data provider

Add `Carbon::calendarMonth(2024, 3)` that returns structured data for
rendering calendar UIs: weeks with day cells including day number,
events, today marker, selection state, and disabled status. Support
month/week/day/agenda views, locale-aware week starts, and event
overlay from a configurable event source. Changes span `Boundaries`
trait, `Week` trait, a new `Calendar/` namespace, `CarbonPeriod` for
date generation, and `Localization` for day/month names.

### W8: Add duration parsing and formatting for human-readable intervals

Implement bidirectional parsing between human strings ("2h 30m",
"1 day, 3 hours", "1.5 weeks") and `CarbonInterval`. Support multiple
input formats (colon-notation "2:30:00", abbreviated "2h30m", verbose
"two hours and thirty minutes"), locale-aware parsing, and ambiguity
resolution. Changes span `CarbonInterval`, a new `Duration/` parser
namespace, `Localization` for unit names, `Creator` trait integration,
and `Converter` trait for output formatting.

### W9: Implement date-aware data structure utilities

Add `CarbonCollection` — a sorted, timezone-aware collection of Carbon
instances with methods: `groupByDay()`, `gaps()`, `overlaps()`,
`clusterByProximity()`, `histogram()`, and `statistics()` (mean, median,
mode date). Support lazy evaluation and streaming. Changes span a new
`Collection/` namespace, `Comparison` trait for sorting, `Difference`
trait for clustering, `CarbonPeriod` integration, and `Serialization`
for collection export.

### W10: Add comprehensive audit logging for date manipulation

Implement `Carbon::withAudit()` that wraps a Carbon instance with an
audit trail recording every mutation: method called, arguments, result,
timestamp, calling code location. Support serialisation of audit logs,
diff-based change reports, and integration with logging frameworks.
Changes span a new `Audit/` namespace, proxy generation for
`CarbonInterface` methods, `Traits/Modifiers.php` instrumentation,
`Traits/Units.php`, `Serialization`, and `Factory` wiring.
