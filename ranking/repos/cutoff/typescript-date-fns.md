# date-fns/date-fns

| Field | Value |
|-------|-------|
| **URL** | https://github.com/date-fns/date-fns |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large |
| **Category** | Date utility library |
| **Set** | Cutoff |
| **Commit** | `dd66398305c2b015fba3c1b3d31ccff42ee8d4cf` |

## Why this repo

- **Well-structured**: Each date function lives in its own file under
  `src/` (e.g., `addDays/`, `format/`, `parse/`, `differenceInDays/`),
  making the codebase highly modular. Locale definitions under `src/locale/`
  follow a uniform structure with `formatDistance`, `formatRelative`,
  `localize`, and `formatLong` entries. Functional programming variants
  under `src/fp/` wrap every function with curried signatures.
- **Rich history**: 5K+ commits, 34K+ stars. The most popular modular
  date library for JavaScript/TypeScript. PRs cover edge cases around
  DST transitions, locale additions, and time zone handling.
- **Comprehensive types**: The library exports strong TypeScript types
  for intervals, durations, locale objects, and format tokens via
  `types.ts` and per-function type exports.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── addDays/              # addDays(date, amount) — add days to a date
├── addMonths/            # addMonths(date, amount) — add months
├── addWeeks/             # addWeeks(date, amount) — add weeks
├── addYears/             # addYears(date, amount) — add years
├── differenceInDays/     # differenceInDays(dateLeft, dateRight)
├── differenceInHours/    # differenceInHours(dateLeft, dateRight)
├── differenceInMonths/   # differenceInMonths(dateLeft, dateRight)
├── eachDayOfInterval/    # eachDayOfInterval(interval) — iterate days
├── endOfDay/             # endOfDay(date) — set to 23:59:59.999
├── endOfMonth/           # endOfMonth(date) — last moment of month
├── format/               # format(date, formatStr, options) — format dates
├── formatDistance/        # formatDistance(date, baseDate) — "3 days ago"
├── formatRelative/       # formatRelative(date, baseDate) — relative words
├── getDay/               # getDay(date) — day of week (0-6)
├── getMonth/             # getMonth(date) — month (0-11)
├── getYear/              # getYear(date) — full year
├── isAfter/              # isAfter(date, dateToCompare) — comparison
├── isBefore/             # isBefore(date, dateToCompare) — comparison
├── isEqual/              # isEqual(dateLeft, dateRight) — exact equality
├── isSameDay/            # isSameDay(dateLeft, dateRight)
├── isValid/              # isValid(date) — check for Invalid Date
├── isWithinInterval/     # isWithinInterval(date, interval) — range check
├── parse/                # parse(dateString, formatString, referenceDate)
├── parseISO/             # parseISO(argument) — parse ISO 8601 strings
├── startOfDay/           # startOfDay(date) — set to 00:00:00.000
├── startOfMonth/         # startOfMonth(date) — first moment of month
├── startOfWeek/          # startOfWeek(date, options) — week start
├── sub/                  # sub(date, duration) — subtract a duration
├── fp/                   # Functional programming curried variants
│   ├── addDays/          # addDays(amount)(date) — curried
│   ├── format/           # format(formatStr)(date) — curried
│   └── ...               # One curried wrapper per function
├── locale/               # Locale definitions (50+ locales)
│   ├── en-US/            # English (US) — default locale
│   ├── de/               # German
│   ├── fr/               # French
│   ├── ja/               # Japanese
│   ├── zh-CN/            # Chinese (Simplified)
│   └── ...               # Each with formatDistance, localize, formatLong
├── constants.ts          # millisecondsInSecond, daysInWeek, etc.
├── types.ts              # Interval, Duration, Locale, Day, Month types
├── _lib/                 # Internal helpers
│   ├── toDate/           # Normalize input to Date object
│   ├── requiredArgs/     # Argument count validation
│   ├── getTimezoneOffsetInMilliseconds/
│   └── defaultLocale/    # Default locale (en-US) reference
└── index.ts              # Public barrel export of all functions
```

## Scale indicators

- ~220 function modules under `src/`
- ~50 locale definitions under `src/locale/`
- ~200 curried wrappers under `src/fp/`
- ~30K lines of TypeScript source (excluding tests)

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix format() not handling escaped single quotes inside format strings

When `format()` receives a format string containing an escaped single
quote within a quoted literal (e.g., `"h 'o''clock'"`), the token
parser in `format/` incorrectly splits the literal at the escaped
quote, producing garbled output. Fix the tokenizer to treat two
consecutive single quotes inside a quoted section as a literal quote
character.

### N2: Fix differenceInMonths returning wrong result across DST boundaries

`differenceInMonths()` returns an off-by-one result when the date range
crosses a DST transition because the comparison uses raw timestamps
without compensating for the timezone offset change. Fix the month
difference calculation in `differenceInMonths/` to normalize both dates
to UTC-equivalent values before comparison.

### N3: Fix startOfWeek not validating weekStartsOn option range

When `startOfWeek()` receives an out-of-range `weekStartsOn` value
(e.g., `7` or `-1`), it silently produces an incorrect result because
the day-of-week arithmetic in `startOfWeek/index.ts` wraps without
bounds checking. Fix the options validation to throw a `RangeError`
when `weekStartsOn` is not an integer between 0 and 6.

### N4: Fix parseISO silently returning Invalid Date for time-only strings

`parseISO("T14:30:00")` returns an Invalid Date without any indication
of what went wrong. The ISO parser in `parseISO/` should recognize
time-only ISO 8601 strings (starting with `T`) and parse them relative
to the epoch date, or throw a `RangeError` with a clear message
explaining that a date component is required.

### N5: Fix isWithinInterval not throwing when interval is inverted

When `isWithinInterval()` receives an interval where `start > end`, it
silently sorts the boundaries internally. This hides a likely caller
bug where the interval was constructed backwards. Add a `strict` option
(defaulting to `false` for backward compatibility) to
`isWithinInterval/index.ts` that throws a `RangeError` when
`start > end` and `strict` is `true`.

### N6: Fix formatDistance rounding error for durations near threshold boundaries

`formatDistance()` shows "about 1 hour" for a difference of 44 minutes
and 30 seconds because `Math.round((seconds - offsetInSeconds) / 60)`
rounds the computed minute value up to 45, crossing the `minutes < 45`
hour threshold in `formatDistance/index.ts`. Fix the rounding in
`formatDistance/` to use `Math.floor` instead of `Math.round` when
computing `minutes`, so that durations below 45 whole minutes remain
"44 minutes" and the hour label only triggers at a full 45 minutes.

### N7: Fix addMonths not preserving time-of-day across DST transitions

When `addMonths()` crosses a DST transition, the resulting date can
have its hours shifted because `setFullYear()` and `setMonth()` in
`addMonths/index.ts` operate in local time. Adding 1 month to
`2024-03-10T02:30` (spring-forward) can yield `2024-04-10T03:30`
instead of preserving the original `02:30` time. Fix the month addition
to detect and compensate for DST-induced hour shifts after setting the
new month.

### N8: Fix toDate not rejecting non-finite numeric inputs

The `toDate()` function in `toDate/index.ts` accepts `Infinity` and
`-Infinity` as numeric inputs and silently converts them to an Invalid
Date via `constructFrom`. Other functions that call `toDate()` then
propagate the Invalid Date without a clear error. Fix `toDate()` to
throw a `RangeError` for non-finite numeric inputs before passing them
to `constructFrom`.

### N9: Fix eachDayOfInterval not respecting step values greater than the interval span

When `eachDayOfInterval()` is called with a `step` larger than the
number of days in the interval, it still returns the start date even
though no full step fits within the range. The loop condition in
`eachDayOfInterval/index.ts` (`+date <= endTime`) always includes the
first date regardless of step. Fix the function to return an empty
array when the step exceeds the interval span, or document that the
start date is always included.

### N10: Fix formatRelative not accounting for timezone offset in day boundary comparison

The `formatRelative()` function in `formatRelative/index.ts` uses
`differenceInCalendarDays()` to classify the date relative to the base
date, but the calendar-day difference can be off by one when the two
dates are in different timezone offsets (e.g., one during DST and one
not). Fix the comparison to normalize both dates to the same offset
before computing the calendar-day difference.

## Medium

### M1: Implement shiftDates() for bulk date shifting with overflow policy

Add `shiftDates(dates, duration, options)` that applies a `Duration`
shift to an array of dates and returns the shifted array. Unlike calling
`add()` in a loop, `shiftDates` should accept an `overflow` policy
(`"clamp"` or `"reject"`) for month-end overflow cases. Requires a new
`shiftDates/` module under `src/`, integration with `add/` for the
underlying arithmetic, a new `OverflowPolicy` type in `types.ts`, and
a corresponding `fp/` curried variant. Add a `./shiftDates` export
entry to `package.json`'s `exports` map with CJS and ESM sub-paths,
and document the function with usage examples in `README.md` under the
"Date Helpers" section.

### M2: Add ISO 8601 duration parsing function

Implement `parseISODuration("P1Y2M3DT4H5M6S")` that returns a
`Duration` object. The existing `formatISODuration()` serializes
durations but there is no parser for the reverse direction. Requires a
new `parseISODuration/` module under `src/`, a parser for the ISO 8601
duration grammar handling both period and time components, validation
for negative or fractional values, proper type integration with
`Duration` from `types.ts`, and an `fp/` curried variant. Update
`CONTRIBUTING.md` to document the module creation pattern for new
parser functions, and add a `parseISODuration` entry to the function
index in `docs/`.

### M3: Implement scoped default options via createContext

Add `createContext(defaults)` that returns a scoped set of date-fns
functions pre-bound with the given default options (locale,
weekStartsOn, firstWeekContainsDate). Unlike the global
`setDefaultOptions()`, `createContext` produces an isolated instance
that does not affect other consumers. Requires a new `createContext/`
module that wraps each exported function with options merging, updates
to `_lib/defaultOptions/` to support context-level resolution, and
updates to `types.ts` for the context type.

### M4: Add recurring interval generation with configurable anchor

Implement `eachIntervalOfType(interval, type, options)` where `type` is
`"hour"` | `"day"` | `"week"` | `"month"` | `"year"` that generates
dates at the specified cadence. Unlike the existing `eachDayOfInterval`
and siblings, this function should accept an `anchor` date that
determines alignment (e.g., anchor to the 15th of each month).
Requires a new module under `src/`, integration with existing
`startOf*` functions for normalization, and a corresponding `fp/`
curried variant.

### M5: Implement formatDistanceStrict with custom unit thresholds

The existing `formatDistanceStrict()` uses fixed unit thresholds for
choosing between seconds, minutes, hours, etc. Add a
`unitThresholds` option to `formatDistanceStrict/index.ts` that lets
callers customize when the display unit changes (e.g., show minutes
up to 120 instead of switching to hours at 60). Requires changes to
the unit selection logic in `formatDistanceStrict/`, a new
`UnitThresholds` type in `types.ts`, and updates to the locale
`formatDistance` callbacks to receive the custom thresholds.

### M6: Add fiscal year date functions with configurable start month

Implement `startOfFiscalYear(date, { fiscalYearStartMonth })`,
`endOfFiscalYear()`, `getFiscalYear()`, `getFiscalQuarter()`, and
`isSameFiscalYear()` for organizations whose fiscal year does not align
with the calendar year (e.g., starts in April or October). Requires
five new function modules under `src/`, a `FiscalYearOptions` type in
`types.ts`, `fp/` curried wrappers for each, and updates to `index.ts`
barrel exports.

### M7: Implement a date range type with iteration and containment checks

Add an `Interval` class or factory `interval(start, end)` that provides
`contains(date)`, `overlaps(other)`, `intersection(other)`,
`union(other)`, and `[Symbol.iterator]()` for daily iteration. Requires
a new module, integration with the existing `Interval` type in
`types.ts`, validation that `start <= end`, and `fp/` curried variants
for each method.

### M8: Add custom format token registration for format()

Implement `format.addToken(token, formatter)` that allows users to
register custom format tokens (e.g., `'Q'` for quarter) that the
`format()` function recognizes alongside built-in tokens. Requires
changes to the token parser in `format/`, a token registry with conflict
detection for built-in tokens, and documentation of the formatter
callback signature.

### M9: Implement locale validation and completeness checker

Add a `validateLocale(locale)` function under `src/_lib/` that checks
a locale definition for completeness: all required formatters
(`formatDistance`, `formatRelative`, `localize`, `formatLong`, `match`)
are present, `ordinalNumber` returns strings for all tested inputs,
and `era`/`quarter`/`month`/`day` localizers cover all required values.
Requires a validation module under `src/_lib/validateLocale/`, type
integration with the `Locale` type from `types.ts`, and test coverage
for at least the `en-US` locale.

### M10: Add typesVersions compatibility field for older TypeScript consumers

The `package.json` `exports` map provides full sub-path exports but
lacks a `typesVersions` field. TypeScript projects using
`"moduleResolution": "node"` (TypeScript < 4.7) cannot use the
`exports` field for type resolution and receive "Cannot find module
'date-fns/addDays'" errors. Extend `scripts/build/indices.ts` (the
script that auto-generates the `exports` map) to also emit a
`typesVersions` entry mapping `"*"` to glob patterns covering all
public functions, locales, and `fp/` variants. Update `package.json`
with the generated field, add a verification step using `pnpm attw`
in `attw_tests.yaml`, and ensure the `src/fp/index.ts` and
`src/locale/index.ts` barrel exports continue to resolve under the new
mapping.

## Wide

### W1: Implement time zone support via a companion module

Add a `date-fns-tz` companion module under `src/tz/` that provides
`zonedTimeToUtc()`, `utcToZonedTime()`, `formatInTimeZone()`, and
`getTimezoneOffset()`. Requires a time zone database lookup (via
`Intl.DateTimeFormat` or bundled IANA data), integration with the
`format/` token system for zone-aware formatting, updates to
`types.ts` for zone-aware types, `fp/` curried variants, and changes
to `_lib/getTimezoneOffsetInMilliseconds/` for offset calculations.

### W2: Implement a fluent date builder API

Add a chainable `dateFns(date).addDays(3).startOfMonth().format('yyyy-MM-dd')`
API that wraps date-fns functions in a fluent interface. Requires a new
`DateFns` wrapper class with methods mirroring every public function,
lazy evaluation so chained operations compose without intermediate Date
allocations, locale and options propagation through the chain, and
TypeScript overloads that preserve return types through the chain.

### W3: Add comprehensive locale validation and generation tooling

Build a locale development toolkit: a validator that checks every locale
definition for completeness (all required formatters, correct token
coverage, proper pluralization rules), a generator scaffold that creates
a new locale from a template, and a test harness that runs format/parse
round-trip tests against every locale. Requires traversing all 50+
locale directories under `src/locale/`, defining a locale schema,
building a CLI tool, and generating test fixtures per locale.

### W4: Implement a date expression DSL for complex date calculations

Add `evaluate("now + 3 business days - 1 month @ startOfDay")` that
parses a string expression into a chain of date-fns operations.
Support arithmetic (`+`, `-`), snap operations (`@startOfMonth`),
business day logic (skipping weekends and configurable holidays), and
variable references. Requires a tokenizer, parser, and evaluator under
a new `src/expression/` module, integration with existing function
modules for each operation, and type-safe return types.

### W5: Add comprehensive parse format pattern support matching format()

Extend `parse()` to support all format tokens that `format()` supports,
achieving round-trip fidelity. Currently `parse()` handles a subset of
tokens. Requires adding parsers for era (`G`), quarter (`Q`), week-
numbering year (`Y`), local day of week (`e`, `c`), day period (`a`,
`b`, `B`), timezone (`z`, `x`, `X`), and timestamp (`t`, `T`) tokens.
Changes span the parse token registry, locale-aware parsing for day/
month names, and the `_lib/` helpers for week-numbering calculations.

### W6: Implement a streaming/observable date utility layer

Add reactive wrappers that emit date values over time: `interval$(period)`
emitting dates at intervals, `timer$(date)` emitting when a target date
is reached, `countdown$(target)` emitting remaining durations, and
`dateChange$()` emitting on calendar date transitions. Requires a new
`src/observable/` module, integration with the existing duration and
interval types, support for both RxJS-compatible and native async
iterator interfaces, and cleanup/disposal handling.

### W7: Implement holiday calendar system with regional holiday packs

Add a `HolidayCalendar` type and a holiday-aware layer that integrates
with the existing `addBusinessDays()` and `differenceInBusinessDays()`
functions. Implement `createHolidayCalendar({ holidays, workWeek })`,
`isHoliday(date, calendar)`, `nextWorkday(date, calendar)`, and
`previousWorkday(date, calendar)`. Include regional holiday packs
(US-federal, EU-common) as importable presets. Requires new modules
under `src/holidays/`, a `HolidayCalendar` type in `types.ts`,
modifications to `addBusinessDays/` and `differenceInBusinessDays/` to
accept a calendar option, `fp/` curried variants, and `index.ts` export
updates.

### W8: Add a migration CLI for upgrading from date-fns v2 to v3

Build a codemod tool that automatically migrates user code from v2 to v3
API: rename changed function names, update import paths from
`date-fns/esm/` to `date-fns/`, convert removed `locale` re-exports,
update option parameter names, and flag deprecated usage patterns with
inline comments. Requires an AST parser (using TypeScript compiler API),
a transformation rule engine, a CLI entry point, and a dry-run mode that
reports changes without writing.

### W9: Implement a date constraint query builder

Add a `findDate({ after, before, dayOfWeek, monthDay, not })` constraint
solver that returns the nearest date satisfying all constraints, plus
a `snap(date, unit, direction)` function for rounding dates to unit
boundaries (`'ceil'` | `'floor'` | `'round'`). The existing
`closestTo()`, `clamp()`, and `roundToNearestMinutes` /
`roundToNearestHours` cover individual operations but there is no
composed constraint system. Requires a new `findDate/` module, a
`snap/` module, a `DateConstraint` type in `types.ts`, integration with
existing comparison and rounding functions for each constraint, `fp/`
curried variants, and comprehensive edge-case handling for month/year
boundaries.

### W10: Implement a date schema validation library for runtime type checking

Add a `src/schema/` module providing runtime validators that complement
the compile-time types: `dateSchema()`, `intervalSchema()`,
`durationSchema()`, and `localeSchema()`. Each schema validates input
shape, value ranges (e.g., month 0–11, day 1–31), and cross-field
consistency (e.g., interval start ≤ end). Support composability with
Zod-style `.refine()` for custom rules. Requires a schema builder
module, integration with `types.ts` types for inference, validators for
each major type, error message formatting using locale-aware date
rendering via `format/`, and `fp/` curried variants.

### N11: Fix scripts/build/indices.ts generating wrong extension for root import default

The `generatePackageJSON` function in `scripts/build/indices.ts`
hard-codes `"./index.ts"` as the `import.default` value for the root
`"."` export entry, while the `mapExports` helper correctly uses
`"./index.js"` for every other entry. Re-running the script would
overwrite `package.json` and set the root import path to the TypeScript
source file instead of the compiled JavaScript output, breaking ESM
consumers of the published package. Fix `generatePackageJSON` to use
`"./index.js"` (and `"./fp.js"`, `"./locale.js"` for the other
top-level entries) consistent with what `mapExports` generates.

### M11: Add CI workflow for bundle size tracking and API diff reporting

The `.github/workflows/` directory has seven workflow files
(`node_tests.yaml`, `browser_tests.yaml`, `code_quality.yaml`,
`coverage.yaml`, `smoke_tests.yaml`, `tz_tests.yaml`,
`attw_tests.yaml`) but none track bundle size changes or API surface
changes across PRs. Add a new `.github/workflows/bundle_size.yaml`
workflow that builds the package, measures the gzip size of
`cdn.min.js` (referenced as the `jsdelivr` entry in `package.json`),
compares it with the base branch, and posts a PR comment with the size
delta. Also add API surface diffing by running `typedoc` (configured in
`typedoc.json`) against both branches and reporting added/removed
exports. Add `size-limit` configuration to `package.json` for local
bundle size checking, and update `pnpm-workspace.yaml` if a new scripts
workspace is needed for the tooling.

### W11: Overhaul documentation and developer tooling configuration

Rewrite `CONTRIBUTING.md` to include a locale development guide with
step-by-step instructions for creating a new locale directory under
`src/locale/`, referencing the existing locale structure
(`formatDistance`, `formatRelative`, `localize`, `formatLong`). Add a
`docs/migration-guide-v3-v4.md` covering API changes, updated import
paths, and `package.json` export map differences between v3 and v4.
Update `typedoc.json` to generate per-module documentation pages with
cross-references between related functions (e.g., linking `addDays` to
`subDays`, `differenceInDays`). Add a `docs/architecture.md` describing
the module structure, `fp/` curried variant generation pattern, and
`_lib/` internal helper conventions. Update `tsconfig.json` references
to include a new documentation build path. Add missing entries to
`.prettierrc` for consistent markdown formatting in `docs/` and locale
`README.md` files. Update `.oxlintrc.json` to enable documentation
linting rules. Update `babel.config.json` to add a `docs` environment
for documentation build tooling.
