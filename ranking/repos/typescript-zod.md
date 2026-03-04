# colinhacks/zod

| Field | Value |
|-------|-------|
| **URL** | https://github.com/colinhacks/zod |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Small (focused library) |
| **Category** | Schema validation library |

## Why this repo

- **Single-purpose**: TypeScript-first schema validation with static type
  inference. Core functionality is self-contained and graspable.
- **Well-structured**: Source under `src/` with clear type/validator split.
  Each schema type (string, number, object, array, union, etc.) is a distinct
  class with shared base patterns.
- **Rich history**: 3K+ commits, active PRs, widely adopted (30K+ GitHub stars).
  Real development patterns visible in commit/PR history.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── ZodError.ts          # Error types and formatting
├── types.ts             # Core schema types (ZodString, ZodNumber, ZodObject, etc.)
├── helpers/             # Utility types and functions
│   ├── parseUtil.ts     # Parse context and issue handling
│   ├── typeAliases.ts   # Shared type aliases
│   └── util.ts          # General utilities
├── locales/             # Error message localization
├── external.ts          # Public API re-exports
└── index.ts             # Entry point
```

## Scale indicators

- ~30 TypeScript source files
- ~12K lines of code
- Flat module structure, single conceptual domain
- Zero runtime dependencies

---

## Tasks

8 tasks (3 narrow, 3 medium, 2 wide) for the TypeScript schema validation library.

## Narrow

### N1: Fix `.transform()` losing optional status in `.partial()`

When an object schema has an optional field with a `.transform()`, calling
`.partial()` on the parent object incorrectly marks the transformed field
as required. The transform wrapper loses the `isOptional()` flag during
the partial conversion. Fix `.partial()` to preserve optionality through
transform chains.

### N2: Add `.readonly()` modifier for object schemas

Add a `.readonly()` method to `ZodObject` that produces a TypeScript type
with all properties marked as `readonly`. The runtime behavior should be
identical (no Object.freeze), but the inferred type should use
`Readonly<T>`. Support nesting: `.readonly()` on an object with nested
objects should make all levels readonly.

### N3: Fix error path for discriminated union with nested objects

When validation fails inside a nested object within a discriminated union,
the error path does not include the discriminator key's value. The error
message says "Invalid input" without indicating which branch of the union
failed. Include the matched discriminator value in the error path so
users can identify which variant had the validation failure.

## Medium

### M1: Implement async refinements with `.superRefine()`

The current `.refine()` and `.superRefine()` methods only support synchronous
validation functions. Implement async variants (`.refineAsync()` already
exists but `.superRefine()` doesn't have an async path). Async refinements
should run in parallel where possible, collect all errors before returning,
and work with `.safeParseAsync()`. Update the parse pipeline to detect
when async refinements are present and require `.parseAsync()`.

### M2: Add JSON Schema generation

Implement a `.toJsonSchema()` method on all Zod schema types that produces
a valid JSON Schema (draft-2020-12) representation. Handle string formats,
numeric constraints, object properties, arrays (tuple and variable-length),
unions (including discriminated unions), enums, and recursive schemas
(using `$ref`). Add configuration for custom format mappings and
additional keywords.

### M3: Implement branded types with runtime validation

Add a `.brand<B>()` method that creates a branded type — a type that is
assignable only from validated values, not from plain literals. The
branded type should carry a phantom brand in the TypeScript type system.
Add `.isBranded()` type guard and `.unbrand()` to strip the brand.
Document the difference between `.brand()` (type-level only) and
`.refine()` (runtime checks).

## Wide

### W1: Add schema versioning and migration

Implement a system for evolving schemas over time. Add `.version(n)` to
tag a schema version, and a `migrate(oldSchema, newSchema, transformer)`
function that converts data validated against an older schema version to
the new format. Support chaining migrations across multiple versions
(v1 → v2 → v3). Include a `parseWithMigration()` method that automatically
applies the migration chain.

### W2: Implement form integration layer

Add a `@zod/forms` subpackage that bridges Zod schemas to HTML form
handling. Generate form field metadata (labels, placeholders, input types,
validation rules) from Zod schemas. Support client-side validation that
mirrors server-side Zod validation. Provide adapters for React Hook Form
and Formik. Include support for multi-step forms where each step
validates a subset of the schema.
