# pydantic/pydantic

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pydantic/pydantic |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium-large |
| **Category** | Data validation library |
| **Set** | eval |
| **Commit** | `fd9bfc8aefe91bf2e16c3464d2e3efba9df83fce` |

## Why this repo

- **Multi-subsystem**: Core validation, JSON Schema generation, serialization, type adapter, dataclasses, plugin system, experimental pipeline API
- **Well-structured**: Clean separation between public API (`main.py`, `fields.py`, `types.py`) and internals (`_internal/`)
- **Rich history**: 10K+ commits, foundational library for FastAPI and hundreds of Python projects

## Structure overview

```
pydantic/
├── main.py                  # BaseModel definition
├── fields.py                # Field(), FieldInfo
├── types.py                 # Constrained types, special types
├── config.py                # ConfigDict, model configuration
├── json_schema.py           # JSON Schema generation
├── type_adapter.py          # TypeAdapter for non-model validation
├── root_model.py            # RootModel for single-value models
├── dataclasses.py           # Pydantic-enhanced dataclasses
├── functional_validators.py # @field_validator, AfterValidator, etc.
├── functional_serializers.py# @field_serializer, PlainSerializer, etc.
├── aliases.py               # AliasPath, AliasChoices
├── networks.py              # URL, email types
├── validate_call_decorator.py # @validate_call
├── mypy.py                  # MyPy plugin
├── plugin/                  # Plugin system hooks
├── experimental/            # Pipeline API, arguments schema
├── deprecated/              # V1 compatibility shims
└── _internal/               # Schema generation, model construction internals
    ├── _generate_schema.py  # Core schema generation (~2900 LOC)
    ├── _model_construction.py # Metaclass, model building
    ├── _fields.py           # Field resolution logic
    ├── _discriminated_union.py # Tagged union handling
    ├── _decorators.py       # Validator/serializer decorator processing
    ├── _typing_extra.py     # Typing utilities
    └── _validate_call.py    # validate_call internals
```

## Scale indicators

- ~80 Python source files (excluding v1 compat)
- ~32K lines of source code
- Complex schema generation pipeline with pydantic-core Rust backend
- Extensive type system integration (mypy, pyright plugins)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `model_copy(update=...)` not triggering validators on updated fields

When calling `model.model_copy(update={'field': value})`, the updated values bypass field validators. The `model_copy` method in `main.py` uses `__dict__` manipulation directly without re-validating the updated fields through the core schema.

### N2: Fix `TypeAdapter.validate_json()` not respecting `strict` mode for nested models

When `TypeAdapter` is configured with `strict=True`, top-level type coercion is rejected but nested model fields still allow coercion. The `_init_core_attrs` method in `type_adapter.py` does not propagate the strict flag into nested core schemas.

### N3: Fix `AliasChoices` not working with `model_fields_set` tracking

When a field uses `AliasChoices` and data is provided via one of the alias paths, the field name doesn't appear in `model_fields_set`. The alias resolution in `_fields.py` doesn't update the fields-set tracking for choice-based aliases.

### N4: Add `deprecated` parameter to `Field()` for marking deprecated fields

Fields lack a mechanism to emit deprecation warnings when accessed. Add a `deprecated` parameter to `Field()` in `fields.py` that emits a `DeprecationWarning` when the field is set during validation, and includes the deprecation notice in JSON Schema output.

### N5: Fix JSON Schema `$ref` generation for recursive models with `by_alias=True`

When generating JSON Schema with `mode='serialization'` and `by_alias=True` for recursive models, the `$ref` paths use the original field names instead of aliases. The `GenerateJsonSchema` class in `json_schema.py` doesn't apply alias transformation to `$defs` keys.

### N6: Fix `@field_validator` with `mode='wrap'` not receiving the correct handler type

When a wrap-mode field validator's handler is called, it receives the raw core validator function instead of a properly typed handler. The decorator processing in `_decorators.py` wraps the handler but doesn't match the `ValidatorFunctionWrapHandler` signature.

### N7: Fix `RootModel.model_validate()` losing type discrimination for union root types

When a `RootModel[Union[A, B]]` validates data, the discriminator field is checked but the error message doesn't indicate which union member failed. The root model validation in `root_model.py` delegates to `__pydantic_validator__` without enriching the `ValidationError` context.

### N8: Add `transform` parameter to `PlainSerializer` for pre-serialization data transformation

`PlainSerializer` in `functional_serializers.py` replaces the entire serialization but lacks a way to transform data before the default serializer runs. Add a `transform` parameter that applies a function before default serialization.

### N9: Fix `model_json_schema()` not including `examples` from field metadata in output

When `Field(examples=[...])` is set, the examples are stored in field metadata but the JSON Schema generator in `json_schema.py` doesn't include them in the generated schema output for individual properties.

### N10: Fix `validate_call` not preserving `functools.wraps` metadata on decorated functions

When `@validate_call` decorates a function, the resulting wrapper in `validate_call_decorator.py` doesn't preserve `__module__`, `__qualname__`, and `__annotations__` from the original function, breaking introspection tools.

### N11: Update `docs/concepts/fields.md` to document the `deprecated` parameter for `Field()`

The `deprecated` parameter added to `Field()` is not reflected in the documentation. Add a section to `docs/concepts/fields.md` covering usage examples, deprecation warning behavior, and JSON Schema output. Update `mkdocs.yml` navigation if needed and cross-reference from `docs/migration.md` for users migrating from manual deprecation patterns.

## Medium

### M1: Implement computed fields with dependency tracking

Add the ability for `@computed_field` to declare which model fields it depends on, so revalidation only recomputes affected computed fields when `model_copy(update=...)` is called. Changes span `main.py`, `_fields.py`, `_model_construction.py`, and `_generate_schema.py`.

### M2: Add model inheritance conflict detection and resolution

When a child model redefines a parent field with incompatible validators or types, pydantic silently overwrites. Implement conflict detection in `_model_construction.py` that warns on type narrowing violations and validator conflicts, with an explicit `override=True` parameter in `Field()` to suppress.

### M3: Implement partial model validation for PATCH-style updates

Add a `model_validate_partial()` class method that accepts incomplete data and returns a model with only the provided fields set, leaving others at their defaults. Changes span `main.py`, `_model_construction.py`, and `type_adapter.py` to generate a partial core schema variant.

### M4: Add JSON Schema generation for discriminated unions using `oneOf` with `discriminator`

The JSON Schema output for discriminated unions uses `anyOf` without the OpenAPI `discriminator` mapping object. Implement proper `discriminator` object generation in `json_schema.py` with property name and mapping, coordinating with `_discriminated_union.py` for tag extraction.

### M5: Implement custom error message templates for validation errors

Add configurable error message templates per field or per model via `Field(error_messages={...})` and `model_config`. The template system should support variable interpolation (`{input}`, `{field_name}`, `{constraint}`). Changes span `fields.py`, `config.py`, `_generate_schema.py`. Update `docs/concepts/validators.md` with error message template documentation and add usage examples to `docs/concepts/fields.md`.

### M6: Add `TypeAdapter` caching and reuse for repeated type patterns

Creating `TypeAdapter` instances is expensive due to schema generation. Implement a cache keyed by type annotation that reuses core schemas and validators. Add cache invalidation when `ConfigDict` differs. Changes span `type_adapter.py` and `_internal/_generate_schema.py`.

### M7: Implement field-level serialization context propagation

Add the ability to pass per-field context during serialization via `model.model_dump(context={'field_name': value})` that's accessible in `@field_serializer` functions. Changes span `main.py`, `functional_serializers.py`, and `_internal/_generate_schema.py`.

### M8: Add validation performance profiling to the plugin system

Extend the plugin interface in `plugin/` to support validation timing hooks: `on_validate_start`, `on_validate_end` with field-level granularity. Expose profiling data through a `ValidationProfile` object. Changes span `plugin/`, `_internal/_generate_schema.py`, and `main.py`.

### M9: Implement `TypeAdapter.validate_many()` for batch validation with error collection

Add a `validate_many(items: list)` method to `TypeAdapter` that validates all items and returns a result object containing both valid items and per-item errors, rather than failing on the first error. Changes span `type_adapter.py` and add batch validation logic.

### M10: Add model schema versioning for backward-compatible deserialization

Implement schema version tracking via `model_config['schema_version']` with migration functions between versions. When validating data with an older version tag, apply the migration chain before validation. Changes span `config.py`, `main.py`, and `_model_construction.py`.

### M11: Add migration guide and changelog for partial model validation

Create a new section in `docs/migration.md` covering `model_validate_partial()` usage patterns and migration from workaround approaches. Update `docs/concepts/models.md` with partial validation examples and caveats. Add a changelog entry to `HISTORY.md` describing the feature. Update `mkdocs.yml` navigation to surface the partial validation docs.

## Wide

### W1: Implement async validation support for I/O-bound validators

Add `@async_field_validator` and `@async_model_validator` decorators that support `async` validator functions, with `await model.async_validate()`. Changes span `functional_validators.py`, `_decorators.py`, `_generate_schema.py`, `_model_construction.py`, `main.py`, and `type_adapter.py`.

### W2: Add OpenAPI 3.1 schema generation mode

Implement a dedicated OpenAPI schema generator that produces spec-compliant output: nullable handling, `readOnly`/`writeOnly` from field metadata, `discriminator` objects, response schema variants, and proper `$ref` handling. Changes span `json_schema.py`, `fields.py`, `config.py`, `types.py`, `functional_serializers.py`, and add an `openapi/` module.

### W3: Implement model diffing and change tracking

Add `model.model_changes()` that tracks which fields changed since construction, `model.model_diff(other)` for comparing two model instances, and `model.model_changelog()` for full mutation history. Changes span `main.py`, `_model_construction.py`, `_fields.py`, `root_model.py`, `dataclasses.py`, and add change tracking infrastructure.

### W4: Add multi-format serialization framework

Implement `model.model_dump_yaml()`, `model.model_dump_toml()`, `model.model_dump_msgpack()` with custom serializer registration via `model_config`. Changes span `main.py`, `config.py`, `functional_serializers.py`, `type_adapter.py`, `_internal/_generate_schema.py`, and add format-specific serialization modules. Update `docs/concepts/serialization.md` with YAML, TOML, and MsgPack serialization examples and add changelog entries to `HISTORY.md`.

### W5: Implement pydantic settings with hierarchical configuration sources

Add a configuration management system where models can declare multiple config sources (env vars, files, remote) with priority ordering, type-safe overrides, and hot-reloading. Changes span `config.py`, `fields.py`, `main.py`, `_model_construction.py`, `type_adapter.py`, and add a `settings/` module.

### W6: Add GraphQL schema generation from pydantic models

Implement automatic GraphQL type generation from pydantic models: types, inputs, enums, unions with discriminators, and nested resolution. Changes span `json_schema.py`, `main.py`, `fields.py`, `types.py`, `_internal/_generate_schema.py`, `_discriminated_union.py`, and add a `graphql/` module.

### W7: Implement model migration framework for schema evolution

Add a migration system for evolving model schemas across versions: field renames, type changes, computed defaults, and data transformations. Include forward/backward migration with validation. Changes span `main.py`, `config.py`, `fields.py`, `_model_construction.py`, `_internal/_fields.py`, `_migration.py`, and add migration infrastructure.

### W8: Add comprehensive model testing utilities

Implement `pydantic.testing` with `ModelFactory` for generating valid model instances, `assert_validates`/`assert_rejects` helpers, schema compatibility checking between model versions, and fuzzing support. Changes span `main.py`, `fields.py`, `types.py`, `type_adapter.py`, `json_schema.py`, and add a `testing/` module.

### W9: Implement cross-model validation and referential integrity

Add the ability to define cross-model validation rules where one model's field references another model's instance, with referential integrity checking, cascading updates, and lazy loading. Changes span `main.py`, `fields.py`, `_model_construction.py`, `_generate_schema.py`, `_fields.py`, `root_model.py`, and add a registry module.

### W10: Add real-time validation event streaming

Implement an event system that emits structured validation events (field validated, coercion applied, default used, error raised) to configurable sinks for monitoring and debugging. Changes span `main.py`, `plugin/`, `_generate_schema.py`, `_model_construction.py`, `type_adapter.py`, `config.py`, and add an events module.

### W11: Overhaul CI and documentation for async validation support

Update `.github/workflows/ci.yml` to add an async test matrix covering `asyncio` and `trio` backends. Update `docs/concepts/validators.md` with async validator documentation and usage examples. Add performance comparison notes to `docs/concepts/performance.md`. Update `HISTORY.md` with async validation changelog entries. Update `docs/contributing.md` with async testing guidelines and update `pyproject.toml` with new async test dependencies.
