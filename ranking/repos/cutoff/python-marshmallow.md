# marshmallow-code/marshmallow

| Field | Value |
|-------|-------|
| **URL** | https://github.com/marshmallow-code/marshmallow |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Small–Medium |
| **Category** | Object serialization |
| **Set** | Cutoff |
| **Commit** | `7324d8f3567c0a29a93b0a8f98bdf90307aefb19` |

## Why this repo

- **Well-layered design**: Clean separation across schema metaclass and
  lifecycle (`schema.py`, 1.2K lines), 30+ field types (`fields.py`,
  2K lines), 13 validators (`validate.py`), decorators for
  pre/post-processing hooks (`decorators.py`), a class registry
  (`class_registry.py`), and error aggregation (`error_store.py`). The
  dump/load pipeline flows through clearly defined stages.
- **Rich type surface**: Fields range from primitives (`String`,
  `Integer`, `Boolean`) through complex types (`Nested`, `List`,
  `Dict`, `Mapping`, `Tuple`, `Enum`) to specialized types (`IP`,
  `URL`, `Email`, `DateTime`, `TimeDelta`), all sharing a common
  `Field` base class with `_serialize`/`_deserialize` hooks.
- **Permissive**: MIT license.

## Structure overview

```
src/marshmallow/
├── __init__.py            # Public API re-exports (Schema, fields, decorators)
├── class_registry.py      # Global schema registry for string-based Nested refs
├── constants.py           # Sentinel values — missing, RAISE, INCLUDE, EXCLUDE
├── decorators.py          # @pre_load, @post_load, @pre_dump, @post_dump, @validates
├── error_store.py         # ErrorStore — accumulates field and schema errors
├── exceptions.py          # ValidationError, StringNotCollectionError
├── fields.py              # 30+ field types — Field, Nested, List, String, etc.
├── orderedset.py          # OrderedSet utility for field ordering
├── schema.py              # Schema class, SchemaMeta metaclass, SchemaOpts
├── types.py               # Type aliases — StrSequenceOrSet, Tag
├── utils.py               # get_value, set_value, is_collection, timestamp utils
├── validate.py            # 13 validator classes — Range, Length, OneOf, Regexp, etc.
└── experimental/
    ├── __init__.py
    └── context.py         # Experimental context variable support
```

## Scale indicators

- ~14 Python source files (including experimental)
- ~5K lines of code
- Flat structure (single package with one sub-package)
- No required dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add distinct error message for `Integer` strict mode rejections

The `Integer` field in `fields.py` has a `strict` parameter that
rejects non-integer types via `isinstance(value, numbers.Integral)`.
However, when strict mode rejects a value, the same generic
`"Not a valid integer."` error message is raised as for all other
invalid inputs, giving no indication that the value was rejected
specifically because of a type mismatch in strict mode. Add a
dedicated `"invalid_strict"` error key to `Integer.default_error_messages`
with a message like `"Not a valid integer type."`, and raise it
instead of `"invalid"` when `strict=True` and the
`isinstance(value, numbers.Integral)` check fails.

### N2: Fix `Nested` field dropping dot-notation `only` entries for schema instances

The `Nested` field in `fields.py` handles `only` with dot-notation
inconsistently depending on whether a schema class or schema instance
is passed. When a schema class is used (e.g.,
`Nested(UserSchema, only=('address.city',))`), dot-notation works
because the newly created schema handles it internally. However, when
a schema instance is passed, the `schema` property computes
`set_class(self.only) & set_class(schema.fields.keys())`, which
silently drops `'address.city'` because that string does not match
the top-level field name `'address'`, leaving `only` as an empty set
and excluding all fields. Fix the `Nested.schema` property to
correctly handle dot-notation entries in `only` and `exclude` when a
schema instance is passed, by extracting the top-level field name
from each dot-notation path before computing the intersection.

### N3: Add `format` parameter to `TimeDelta` field for human-readable output

The `TimeDelta` field in `fields.py` serializes timedeltas as integer
values (seconds, milliseconds, etc.) but does not support human-readable
string formats like `"2h 30m"` or ISO 8601 duration format
(`"PT2H30M"`). Add a `format` parameter to `TimeDelta` that accepts
`"iso8601"` or a custom format string pattern, implementing the
serialization in `_serialize` and deserialization in `_deserialize`. Also add a changelog entry in `CHANGELOG.rst` documenting the new format parameter with usage examples.

### N4: Fix `ValidationError` messages not including field path for nested errors

When a `Nested` field raises a `ValidationError`, the exception's
`messages` dict contains the nested field's errors but does not include
the dotted path to the field (e.g., `"address.city"`). While the
`Schema._invoke_field_validators` correctly nests errors, external
consumers of `ValidationError` must manually reconstruct the path. Add
a `field_path` property to `ValidationError` in `exceptions.py` that
returns the dot-separated path by walking the nested `messages` dict.

### N5: Add `NoneField` type for explicit null handling

The `fields.py` module has no dedicated field type for values that must
always be `None`/`null`. The `Constant` field can achieve this but is
semantically different. Add a `NoneField` class to `fields.py` that
validates input is `None` (or missing) and always serializes to `None`,
raising `ValidationError` for any non-null input value.

### N6: Add `update`, `intersection_update`, and `difference_update` to `OrderedSet`

The `OrderedSet` class in `orderedset.py` inherits `__or__`, `__and__`,
and `__sub__` operators from `MutableSet`, but does not implement the
named equivalents `update()`, `intersection_update()`, and
`difference_update()` that users familiar with Python's built-in `set`
API expect. Code that calls `ordered_set.update([1, 2, 3])` or
`ordered_set.difference_update(other)` raises `AttributeError`. Add
`update`, `intersection_update`, and `difference_update` methods that
accept an iterable and modify the `OrderedSet` in place, preserving
insertion order and returning `None` (matching built-in `set` semantics).

### N7: Add per-bound `min_error` and `max_error` parameters to `Range` validator

The `Range` validator in `validate.py` accepts an `error` parameter
that overrides the error message for all violation types (min violated,
max violated, or both violated) with a single string. There is no way
to provide separate custom messages for min-only violations versus
max-only violations while keeping the default message for the
other case. Add `min_error` and `max_error` keyword parameters to
`Range.__init__` that, when provided, override only the min-bound
and max-bound error messages respectively (including `{input}`,
`{min}`, `{max}` format placeholders), falling back to the existing
`message_min`, `message_max`, and `message_all` class-level templates
when not specified. The existing `error` parameter should continue to
override all messages when set.

### N8: Fix `Email` validator allowing hyphens at start of TLD

The `Email` validator in `validate.py` validates domain labels to
ensure each label starts and ends with an alphanumeric character.
However, the final TLD portion of `DOMAIN_REGEX` uses the pattern
`[A-Z0-9-]{2,}` which permits a hyphen as the first character,
allowing invalid addresses like `user@example.-com` to pass
validation. Fix `Email.DOMAIN_REGEX` so the TLD alternative
`[A-Z0-9-]{2,}` is replaced with a pattern that requires the TLD
to start and end with an alphanumeric character (e.g.,
`[A-Z0-9][A-Z0-9-]*[A-Z0-9]|[A-Z0-9]`), while still accepting
single-character TLDs and TLDs of any valid length.

### N9: Add `ErrorStore.merge` method for combining error stores

The `ErrorStore` class in `error_store.py` accumulates errors via
`store_error` but has no method to merge two error stores together.
When implementing custom schema validation that runs multiple
independent validation passes, there is no clean way to combine their
errors. Add a `merge(other: ErrorStore)` method that combines the
errors from another `ErrorStore` instance, preserving field paths and
avoiding duplicate messages.

### N10: Condense verbose `Field.__repr__` to show only non-default values

The `Field.__repr__` in `fields.py` always outputs all nine
parameters (`dump_default`, `attribute`, `validate`, `required`,
`load_only`, `dump_only`, `load_default`, `allow_none`,
`error_messages`), making repr output extremely verbose for the
common case where most parameters hold their default values. For
example, `fields.String(required=True)` shows a multi-attribute
repr even though only `required` is non-default. Refactor
`Field.__repr__` to omit parameters that hold their default/sentinel
values: skip `attribute` when `None`, skip `validate` when empty,
skip `dump_default` and `load_default` when equal to `missing_`,
skip `load_only`, `dump_only`, `required`, and `allow_none` when
`False`, and skip `error_messages` when it equals the class-level
`default_error_messages`, so that the repr only shows explicitly
configured options.

## Medium

### M1: Implement conditional field inclusion based on context

Add a `when` parameter to `Field` in `fields.py` that accepts a
callable receiving the serialization/deserialization context and returns
`True`/`False` to determine whether the field should be included.
Requires changes to `Field.__init__` (parameter storage), `Schema._serialize`
and `Schema._deserialize` in `schema.py` (conditional field filtering),
and `SchemaOpts` (context propagation). The `when` callable should
receive the full object being processed and the current context.

### M2: Add schema versioning with migration support

Implement a versioning system where schemas can declare a version
number and define migration functions between versions. When
deserializing data, detect the version field and apply migration
transforms before validation. Requires a `version` field on
`SchemaOpts`, a `@migrates(from_version, to_version)` decorator in
`decorators.py`, migration chain resolution in `schema.py`, and
integration with `pre_load` hooks.

### M3: Implement field-level caching for expensive transformations

Add a caching mechanism for field serialization/deserialization results.
When a field's `_serialize` or `_deserialize` method is computationally
expensive (e.g., `Nested` with large schemas), cache the result keyed
by input identity. Requires a cache layer in `Field` base class in
`fields.py`, cache invalidation in `Schema.load`/`Schema.dump` in
`schema.py`, and configurable cache strategies (LRU, per-call) in
`schema.py`.

### M4: Add schema diff and compatibility checking

Implement a `Schema.diff(other_schema)` method that compares two
schema classes and returns a structured report of differences: added
fields, removed fields, type changes, validator changes, and metadata
changes. Include a `Schema.is_compatible(other_schema)` method that
checks backward compatibility (no required field additions, no type
narrowing). Changes touch `schema.py` (diff logic), `fields.py`
(field comparison), and `validate.py` (validator comparison).

### M5: Implement bulk validation with error limit

Add a `max_errors` parameter to `Schema.load` in `schema.py` that
stops validation after accumulating the specified number of errors,
improving performance when validating large invalid payloads. Requires
changes to the `ErrorStore` in `error_store.py` (error counting and
limit checking), the field-level validation loop in `schema.py`
(early termination), and `@validates_schema` hooks in `decorators.py`
(respecting the limit). Also update `RELEASING.md` to document performance testing steps for bulk validation changes.

### M6: Add schema-to-JSON-Schema generation

Implement `Schema.to_json_schema()` that generates a JSON Schema
(draft 2020-12) from a marshmallow schema definition. Map field types
to JSON Schema types, validators to JSON Schema constraints (e.g.,
`Range` to `minimum`/`maximum`, `Length` to `minLength`/`maxLength`,
`OneOf` to `enum`), and `Nested` fields to `$ref` definitions. Changes
touch `schema.py` (generation entry point), `fields.py` (type-to-
JSON-Schema mapping per field class), `validate.py` (constraint
mapping), and a new `jsonschema.py` module.

### M7: Implement partial schema updates with PATCH semantics

Add a `Schema.patch(data, original)` method that deserializes only the
provided fields and merges them with the original object, supporting
HTTP PATCH semantics. Fields not present in the input should retain
their original values. Handle `Nested` fields recursively — a partial
update to a nested object should merge rather than replace. Changes
touch `schema.py` (patch logic), `fields.py` (`Nested` merge
behavior), and `constants.py` (new `PATCH` sentinel).

### M8: Add async serialization and deserialization support

Implement `Schema.async_load` and `Schema.async_dump` methods that
support fields with async `_serialize`/`_deserialize` methods. This
enables fields that need to perform I/O during serialization (e.g.,
resolving URLs, fetching related objects). Requires async versions of
schema lifecycle methods in `schema.py`, async-aware hook dispatching
in `decorators.py`, and async field base methods in `fields.py`.

### M9: Implement schema inheritance conflict detection

When a schema subclass redefines a field from a parent schema with a
different type or validators, no warning or error is raised — the child
silently shadows the parent field. Add conflict detection to
`SchemaMeta.__new__` in `schema.py` that detects type changes,
validator changes, and required-to-optional changes in inherited
fields. Emit warnings by default and raise errors when
`strict_inheritance=True` is set in `class Meta`. Changes touch
`schema.py` (metaclass validation), `fields.py` (field comparison),
and `exceptions.py` (new `SchemaInheritanceWarning`).

### M10: Add field dependency resolution for cross-field defaults

Implement support for fields whose `load_default` depends on the value
of another field. Add a `depends_on` parameter to `Field.__init__` in
`fields.py` that accepts field names, and a `default_factory` that
receives the values of dependent fields. Requires topological sorting
of field processing order in `Schema._deserialize` in `schema.py`
and cycle detection to prevent circular dependencies.

## Wide

### W1: Implement a data migration framework for schema evolution

Add a framework for migrating serialized data across schema versions.
Support defining migration functions that transform data between
consecutive versions, automatic migration chain resolution (v1 → v2 →
v3), rollback migrations, migration testing utilities, and a migration
registry. Changes span `schema.py` (version detection and migration
dispatch), `decorators.py` (migration decorators), `class_registry.py`
(versioned schema lookup), `fields.py` (version-aware serialization),
`exceptions.py` (migration errors), and a new `migrations.py` module.

### W2: Add a full-featured schema-based form generation system

Implement `Schema.to_form()` that generates HTML form definitions
from schema fields. Map field types to form widgets (text input, select,
checkbox, date picker, etc.), validators to client-side validation
attributes, `Nested` fields to fieldsets, `List` fields to repeatable
groups, and include CSRF token handling. Changes span `schema.py` (form
generation entry point), `fields.py` (widget mapping per field type),
`validate.py` (HTML attribute mapping), a new `forms.py` module for
form rendering, and a new `widgets.py` module for widget definitions.

### W3: Implement a query DSL for filtering serialized data

Add a query language that operates on schema-typed data. Support field
comparisons (`age__gt=18`), nested field access
(`address__city="NYC"`), logical operators (AND, OR, NOT), and ordering.
The query should validate field names and value types against the
schema definition. Changes span `schema.py` (query entry point),
`fields.py` (field-to-operator mapping), `validate.py` (query value
validation), a new `query.py` module for query parsing and evaluation,
a new `operators.py` module for comparison operators, and
`exceptions.py` (query errors).

### W4: Implement automatic API documentation generation

Add `Schema.to_openapi()` that generates OpenAPI 3.1 components from
schema definitions. Map schemas to OpenAPI schema objects, fields to
properties with type/format, validators to constraints, `Nested` to
`$ref`, `List` to arrays, and `Enum` to enum values. Support request
body and response schema generation, pagination wrappers, and error
response schemas. Changes span `schema.py` (OpenAPI generation),
`fields.py` (OpenAPI type mapping), `validate.py` (constraint
mapping), `class_registry.py` (component registry), `types.py`
(OpenAPI type aliases), and a new `openapi.py` module.

### W5: Add multi-format serialization support

Extend marshmallow to support serialization targets beyond Python dicts:
MessagePack, Protocol Buffers (proto3), Avro, and CBOR. Each format
requires a format-specific serializer that maps field types to the
target format's type system, handles schema evolution differently, and
produces format-native output. Changes span `schema.py` (pluggable
format backends), `fields.py` (format-specific serialization per field
type), a new `formats/` sub-package with modules for each format
(msgpack, protobuf, avro, cbor), and `exceptions.py` (format errors).

### W6: Implement a schema-based data validation pipeline

Add a pipeline system where schemas can be composed into multi-stage
validation and transformation workflows. Support pipeline stages:
deserialize → validate → transform → enrich → serialize, with each
stage configurable per-field. Include pipeline error handling with
stage-specific error reports, rollback on failure, and pipeline
metrics. Changes span `schema.py` (pipeline orchestration), `fields.py`
(pipeline stage hooks), `decorators.py` (stage decorators),
`error_store.py` (stage-aware error tracking), `exceptions.py`
(pipeline errors), and a new `pipeline.py` module.

### W7: Implement a type-safe schema builder with runtime validation

Add a fluent builder API as an alternative to declarative schema
classes: `SchemaBuilder().field("name", String(required=True)).field("age",
Integer()).validator(validate.Range(min=0)).build()`. Support all
features of declarative schemas: nested schemas, hooks, meta options,
and class registry integration. Include runtime type checking for
builder method arguments. Changes span `schema.py` (builder-to-schema
conversion), `fields.py` (dynamic field creation), `decorators.py`
(builder hook registration), `class_registry.py` (builder schema
registration), `validate.py` (builder validator attachment), and a new
`builder.py` module.

### W8: Add comprehensive schema testing utilities

Implement a testing framework for marshmallow schemas: factory-based
test data generation from schemas, property-based testing integration
with Hypothesis (strategy generation from field types and validators),
snapshot testing for serialized output, round-trip testing
(dump → load → dump equality), and edge case generators (boundary
values for Range, empty strings for Length, etc.). Changes span
`schema.py` (test data entry point), `fields.py` (strategy per field
type), `validate.py` (boundary value generation per validator), a new
`testing.py` module for test utilities, and a new `hypothesis.py`
module for Hypothesis strategy generation.

### W9: Implement a real-time streaming deserialization system

Add `Schema.stream_load()` that deserializes objects from a streaming
source (file, network socket, async iterator) one record at a time,
yielding validated objects as they arrive. Support JSON Lines, CSV, and
newline-delimited JSON formats. Handle partial records across chunk
boundaries, per-record error handling (skip vs abort), and
backpressure for async streams. Changes span `schema.py` (streaming
entry points), `fields.py` (incremental deserialization), a new
`streaming.py` module for format-specific stream parsers, `exceptions.py`
(streaming errors with record indices), `error_store.py` (per-record
error tracking), and `utils.py` (chunk boundary handling).

### W10: Implement a schema-driven database ORM integration layer

Add a bidirectional mapping between marshmallow schemas and database
models. Support automatic schema generation from SQLAlchemy models
(field type mapping, relationship traversal for `Nested`), query
generation from deserialized filter parameters, bulk
serialization/deserialization with eager loading hints, and change
detection (diff between original model and loaded data). Changes span
`schema.py` (model introspection and schema generation), `fields.py`
(ORM-aware field types), `class_registry.py` (model-to-schema mapping),
`validate.py` (database-aware validators like unique checks), a new
`orm.py` module for ORM integration, and `exceptions.py` (ORM errors).

### N11: Add maintainer role descriptions to `AUTHORS.rst` Leads section

The `AUTHORS.rst` file has 'Leads' and 'Contributors (chronological)'
sections with GitHub handles for all entries. However, the Leads
section does not include descriptions of each lead's role or area
of responsibility, making it difficult for new contributors to know
whom to contact for specific topics. Add a brief one-line
responsibility description to each entry in the 'Leads' section
(e.g., 'Core development, schema design' or 'Release management,
API review') and rename the 'Leads' section heading to
'Core Maintainers' to align with standard open source project
conventions.

### M11: Expand `RELEASING.md` release checklist and add coverage configuration

The `RELEASING.md` is a minimal 4-step document that does not mention
running the existing `lint`, `mypy`, or `docs` tox environments before
release, nor does it include a post-release checklist. Separately,
`pyproject.toml` has no `[tool.coverage.run]` or
`[tool.coverage.report]` sections, so running `pytest --cov` produces
unconfigured coverage output with no source pinning or pass/fail
threshold. Expand `RELEASING.md` to include: a pre-release checklist
section (run `tox -e lint,mypy`, build docs with `tox -e docs`,
run `tox -e py313`, verify `CHANGELOG.rst` entries), a version bumping
procedure, and post-release steps. Add `[tool.coverage.run]` and
`[tool.coverage.report]` sections to `pyproject.toml` with
`source = ["marshmallow"]`, `branch = true`, and a `fail_under`
threshold. Changes touch `RELEASING.md` and `pyproject.toml`.

### W11: Full project configuration and documentation overhaul

Perform a comprehensive non-code refresh: (1) update `pyproject.toml`
to use PEP 639 `license-expression = "MIT"` and
`license-files = ["LICENSE"]` fields instead of the current
`license = { file = "LICENSE" }` format, add `[tool.coverage.run]`
and `[tool.coverage.report]` sections with `source = ["marshmallow"]`,
`branch = true`, and a `fail_under` threshold, and restructure
optional dependency groups so `tests` and `docs` extras are clearly
separated from `dev`; (2) revise `CONTRIBUTING.rst` to add sections
on schema development guidelines (field naming, metaclass behaviour),
test writing conventions (pytest fixture patterns, parametrize usage),
and documentation contribution workflow (Sphinx rst conventions,
building docs locally); (3) update `AUTHORS.rst` to rename 'Leads'
to 'Core Maintainers' and add role descriptions; (4) expand
`RELEASING.md` into a detailed release checklist including
pre-release validation (`tox -e lint,mypy,docs`), version bump
procedure, CHANGELOG verification, tagging, and post-release
announcement steps; (5) update `SECURITY.md` to document supported
version ranges, expected response timeline, and disclosure process;
(6) update `.pre-commit-config.yaml` to add `check-yaml`, `check-toml`,
and `end-of-file-fixer` hooks from the `pre-commit-hooks` package.
