# SnapKit/SnapKit

| Field | Value |
|-------|-------|
| **URL** | https://github.com/SnapKit/SnapKit |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Small (focused DSL library) |
| **Category** | UI layout / Auto Layout DSL |
| **Set** | Cutoff |

## Why this repo

- **Extremely focused**: A single-purpose DSL for Apple Auto Layout with
  ~15 source files. The entire API surface revolves around the `snp`
  property on views and the chainable `ConstraintMaker` builder.
  Navigation requires understanding protocol extensions, operator
  overloading, and generic constraint chaining in a compact codebase.
- **Well-structured**: Core code under `Sources/SnapKit/` with
  `ConstraintMaker.swift` (make/remake/update entry points),
  `Constraint.swift` (the constraint model), `ConstraintDescription.swift`
  (chainable builder), `ConstraintAttributes.swift` (left/right/top/bottom
  attribute flags), `ConstraintView+Extensions.swift` (UIView/NSView
  extensions exposing `snp`), `ConstraintItem.swift`,
  `ConstraintLayoutGuide.swift`, and `Debugging.swift`.
- **Rich history**: 20K+ stars, the de facto Auto Layout DSL for
  iOS/macOS. Issues span cross-platform compatibility (iOS vs macOS),
  constraint lifecycle bugs, and API ergonomics.
- **Permissive**: MIT license.

## Structure overview

```
Sources/SnapKit/
├── ConstraintMaker.swift           # DSL entry point — make, remake, update closures
├── ConstraintMakerRelatable.swift  # equalTo, greaterThanOrEqualTo, lessThanOrEqualTo
├── ConstraintMakerEditable.swift   # priority, multipliedBy, dividedBy, offset
├── ConstraintMakerPriortizable.swift # priority chaining
├── ConstraintMakerFinalizable.swift  # labeled() terminal
├── ConstraintMakerExtendable.swift # left, right, top, bottom, etc. chainable props
├── Constraint.swift                # Core constraint model — activate, deactivate, update
├── ConstraintDescription.swift     # Intermediate chainable constraint description builder
├── ConstraintAttributes.swift      # OptionSet of layout attributes (left, right, width, etc.)
├── ConstraintItem.swift            # Wraps a layout anchor target (view or layout guide)
├── ConstraintView+Extensions.swift # UIView/NSView extension providing .snp accessor
├── ConstraintLayoutGuide.swift     # UILayoutGuide support
├── ConstraintLayoutGuide+Extensions.swift # Layout guide .snp accessor
├── ConstraintLayoutSupport.swift   # topLayoutGuide / bottomLayoutGuide support
├── ConstraintInsets.swift          # Platform-agnostic EdgeInsets typedef
├── ConstraintConfig.swift          # Global configuration (e.g. debug identifiers)
├── ConstraintRelatableTarget.swift # Protocol for relatable constraint targets
├── ConstraintConstantTarget.swift  # Protocol for constant/insets constraint targets
├── ConstraintMultiplierTarget.swift# Protocol for multiplier targets
├── ConstraintPriorityTarget.swift  # Protocol for priority targets
├── ConstraintDSL.swift             # Base DSL proxy class for .snp
├── ConstraintViewDSL.swift         # View-specific DSL properties (edges, size, center)
├── ConstraintLayoutGuideDSL.swift  # Layout-guide-specific DSL properties
├── Debugging.swift                 # Constraint description for debugging/logging
├── Typealiases.swift               # Cross-platform type aliases (ConstraintView, etc.)
└── UILayoutSupport+Extensions.swift# Legacy layout support extensions
```

## Scale indicators

- ~25 Swift source files
- ~2.5K lines of code
- Flat structure (single `Sources/SnapKit/` directory)
- Zero external dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix ConstraintMaker.updateConstraints not matching by attribute

When `snp.updateConstraints` is called, the maker attempts to match
existing constraints by attribute type to update their constants. If a
view has multiple constraints on the same attribute (e.g., two `.top`
constraints to different targets), the update matches the wrong one
because it only compares the attribute, not the target. Fix the
matching logic in `ConstraintMaker.swift` to also compare the second
item (target view or layout guide) when resolving which constraint
to update.

### N2: Fix Constraint.deactivate leaking references to removed views

When a view is removed from the hierarchy and its constraints are
deactivated via `snp.removeConstraints()`, the `Constraint` objects
retain strong references to the `ConstraintItem`'s target view,
preventing deallocation. Fix `Constraint.swift` to nil out the
internal layout constraint's first and second items upon deactivation
so the view can be released.

### N3: Fix ConstraintAttributes.edges not including safeArea on iOS 11+

The `.edges` composite attribute on `ConstraintAttributes.swift`
always resolves to `.top + .bottom + .left + .right` relative to the
superview bounds. When the target is the safe area layout guide, the
resolved attributes should reference the guide's anchors instead. Fix
`ConstraintAttributes` so that `.edges` correctly respects the safe
area when the constraint target is a `UILayoutGuide`.

### N4: Fix priority chaining dropping previous constraint in maker

When chaining `.priority(.required)` onto a constraint inside
`snp.makeConstraints`, the `ConstraintMakerPriortizable` returns a
finalizable object but discards the constraint description's link to
the maker's internal list, causing it to not be activated. Fix the
return chain in `ConstraintMakerPriortizable.swift` to preserve the
constraint's registration with the `ConstraintMaker`.

### N5: Fix labeled() not propagating identifier to underlying NSLayoutConstraint

Calling `.labeled("myLabel")` on a constraint description sets the
label on the `ConstraintDescription` but does not transfer it to the
underlying `NSLayoutConstraint.identifier` when the constraint is
activated. Fix `Constraint.swift` to assign the description's label
to the `NSLayoutConstraint.identifier` property during activation.

### N6: Fix snp.contentHuggingHorizontalPriority mapping to wrong axis

The `ConstraintViewDSL.swift` exposes `contentHuggingHorizontalPriority`
and `contentCompressionResistanceHorizontalPriority` convenience setters.
The horizontal hugging priority setter incorrectly passes `.vertical`
as the axis argument to `setContentHuggingPriority(_:for:)`. Fix the
axis parameter in `ConstraintViewDSL.swift`.

### N7: Fix Debugging.swift not including multiplier in constraint description

The `Debugging.swift` description method prints the constraint's
first item, attribute, relation, second item, and constant, but omits
the multiplier when it is not 1.0. Fix the description builder in
`Debugging.swift` to include the multiplier value in the formatted
output when it differs from the default.

### N8: Fix ConstraintInsets.init ignoring directional insets on RTL

`ConstraintInsets` typedef maps to `UIEdgeInsets` which uses
`left`/`right`, but when the constraint target uses leading/trailing
attributes, the insets should map directionally. Fix
`ConstraintConstantTarget` conformance for `ConstraintInsets` so that
leading/trailing constraints apply the correct inset values based on
the user interface layout direction.

### N9: Fix remake not deactivating constraints when superview changes

`snp.remakeConstraints` deactivates all existing constraints before
creating new ones. If the view has been moved to a different superview
between the original `makeConstraints` and the `remakeConstraints`
call, the deactivation fails silently because the old constraints
reference a stale view hierarchy. Fix `ConstraintMaker.swift` to
handle stale constraints by removing them from the stored list when
deactivation fails instead of leaving them as dangling entries.

### N10: Fix equalToSuperview() crashing when called on root view

Calling `.equalToSuperview()` on a view with no superview triggers a
force-unwrap crash because `ConstraintMakerRelatable` accesses
`view.superview!` without a guard. Fix `ConstraintMakerRelatable.swift`
to throw a descriptive precondition failure or assertion instead of
force-unwrapping, providing the view's description in the error message.

## Medium

### M1: Add support for UILayoutGuide-to-UILayoutGuide constraints

Currently constraints can only be made between views and layout guides,
or between two views. Add support for constraining one
`UILayoutGuide` directly to another `UILayoutGuide`. Requires extending
`ConstraintItem.swift` to accept layout guides as both first and second
items, updating `ConstraintMakerRelatable.swift` for the new target
type, and adjusting `Constraint.swift` to resolve the common ancestor
for constraint installation.

### M2: Implement constraint animation helpers on ConstraintViewDSL

Add `snp.animateConstraints { make in ... }` that wraps constraint
updates in a `UIView.animate(withDuration:)` block, calling
`layoutIfNeeded()` automatically. Support configurable duration,
delay, and spring damping. Requires changes to `ConstraintViewDSL.swift`
for the new API, `ConstraintMaker.swift` to batch constant updates
without deactivating/reactivating constraints, and
`ConstraintView+Extensions.swift` to wire the animation call.

### M3: Add constraint group support for batch activation/deactivation

Implement a `ConstraintGroup` type that collects constraints created
inside a `snp.makeConstraints` block via an optional group parameter.
Support `group.activate()`, `group.deactivate()`, and
`group.toggle()`. Requires a new `ConstraintGroup.swift` class, changes
to `ConstraintMaker.swift` to register constraints with a group, and
updates to `Constraint.swift` for group membership tracking and
batch NSLayoutConstraint activation.

### M4: Implement keyboard-aware bottom constraint adjustment

Add `snp.bottomToKeyboard` that automatically adjusts a bottom
constraint's constant when the keyboard appears or disappears.
Subscribe to `UIKeyboardWillShow`/`WillHide` notifications and
animate the constant change. Requires a new
`ConstraintKeyboardTracker.swift` for notification handling, changes
to `ConstraintViewDSL.swift` for the API, and updates to
`Constraint.swift` to support external constant mutation with
animation.

### M5: Add typed constraint references for later modification

Implement a `ConstraintRef<Attribute>` wrapper returned from
`snp.makeConstraints` that provides type-safe access to a specific
constraint's constant, multiplier, and priority. Support subscript
access by attribute type (e.g., `refs[.top].constant = 20`). Requires
a new `ConstraintRef.swift`, changes to `ConstraintMaker.swift` to
return the ref collection, and updates to `Constraint.swift` to
support indexed lookup by attribute.

### M6: Implement cross-hierarchy constraint support with coordinate conversion

Add `snp.makeConstraints(relativeTo: otherView)` that allows
constraining a view to a view not in the same direct superview chain
by using coordinate-space conversion. Requires changes to
`ConstraintMaker.swift` for the new entry point,
`ConstraintItem.swift` to handle coordinate conversion during
constraint resolution, and `Constraint.swift` to install the
constraint on the correct common ancestor view.

### M7: Add SwiftUI interop for hosting SnapKit-constrained views

Implement a `SnapKitView` SwiftUI wrapper (`UIViewRepresentable`) that
accepts a `snp.makeConstraints` closure for its underlying UIView.
Support dynamic constraint updates via SwiftUI state bindings.
Requires a new `SnapKitRepresentable.swift`, integration with
`ConstraintMaker.swift` for constraint rebuilding on state changes,
and updates to `ConstraintViewDSL.swift` to support the representable
lifecycle.

### M8: Implement constraint conflict detection and reporting

Add `snp.diagnoseConstraints()` that analyzes all constraints on a view
and its subviews, detecting conflicting or redundant constraints before
they hit the Auto Layout engine. Report conflicts with human-readable
descriptions using the labels from `.labeled()`. Requires a new
`ConstraintDiagnostics.swift`, reading constraint data from
`Constraint.swift`, attribute comparison logic using
`ConstraintAttributes.swift`, and formatted output via
`Debugging.swift`.

### M9: Add percentage-based constraint support

Implement `.equalTo(superview).percent(50)` syntax that sets a
constraint's multiplier to 0.5 relative to the target's dimension.
Support percentages for width, height, centerX, and centerY
attributes. Requires adding a `percent()` method to
`ConstraintMakerEditable.swift`, updating `ConstraintDescription.swift`
to store percentage values, and modifying `Constraint.swift` to
compute the correct multiplier during activation.

### M10: Implement conditional constraint builder with platform branching

Add `snp.makeConstraints { make in make.top.if(.iOS, equalTo: safeArea)
.else(equalTo: superview) }` syntax for platform-conditional
constraints. Support `#available` checks and trait-collection-based
conditions. Requires a new `ConstraintCondition.swift` for condition
evaluation, changes to `ConstraintMakerRelatable.swift` for the
branching API, and updates to `ConstraintMaker.swift` to evaluate
conditions and install only the active branch.

## Wide

### W1: Implement full Auto Layout DSL for SwiftUI-style declarative syntax

Redesign the public API to support a declarative, SwiftUI-inspired
syntax: `view.snp.layout { $0.edges.equalToSuperview().inset(16) }`
using result builders. Implement a `@ConstraintBuilder` result builder
that collects constraint descriptions. Requires a new
`ConstraintBuilder.swift` for the result builder, rewriting
`ConstraintMaker.swift` to accept builder closures, updating
`ConstraintMakerExtendable.swift` for chainable syntax compatibility,
modifying `ConstraintDescription.swift` for builder integration, and
adding result-builder overloads to `ConstraintView+Extensions.swift`
and `ConstraintLayoutGuide+Extensions.swift`.

### W2: Add full constraint lifecycle management with state machine

Implement a constraint state machine (inactive → active → updating →
deactivated → removed) with transition hooks and validation at each
stage. Track constraint history for debugging. Requires rewriting
`Constraint.swift` with a state enum and transition methods, updating
`ConstraintMaker.swift` for state-aware make/remake/update, adding
lifecycle hooks to `ConstraintDescription.swift`, extending
`Debugging.swift` for state history logging, and modifying
`ConstraintView+Extensions.swift` to query constraint states.

### W3: Implement constraint-based responsive layout system

Add a responsive layout system that automatically adjusts constraints
based on trait collection changes (size classes, dynamic type, and
accessibility settings). Support named layout configurations that
activate different constraint sets per trait. Requires a new
`ConstraintResponsiveLayout.swift` for configuration storage, changes
to `ConstraintMaker.swift` for multi-configuration constraint creation,
trait-change observation in `ConstraintView+Extensions.swift`,
batch activation logic in `Constraint.swift`, and integration with
`ConstraintAttributes.swift` for per-trait attribute resolution.

### W4: Add complete macOS AppKit support with unified API

Extend SnapKit to fully support AppKit's `NSView` constraint model
alongside UIKit, handling differences in coordinate systems (flipped
vs non-flipped), `NSLayoutGuide` vs `UILayoutGuide`,
`translatesAutoresizingMaskIntoConstraints` defaults, and safe area
insets (absent on macOS). Requires changes to `Typealiases.swift` for
platform-conditional types, `ConstraintView+Extensions.swift` for
AppKit-specific extensions, `ConstraintLayoutGuide.swift` for
NSLayoutGuide support, `ConstraintAttributes.swift` for macOS-specific
attributes, `ConstraintInsets.swift` for `NSEdgeInsets`, and
`Debugging.swift` for AppKit-aware descriptions.

### W5: Implement constraint serialization and deserialization system

Add the ability to serialize a view's constraint set to JSON/Plist and
restore it later, supporting view identity resolution by tag or
accessibility identifier. Requires a new `ConstraintCodable.swift`
with Codable conformances, changes to `Constraint.swift` for
serialization of all constraint parameters, `ConstraintItem.swift`
for view identity encoding, `ConstraintAttributes.swift` for
attribute serialization, `ConstraintDescription.swift` for
description round-tripping, and a new `ConstraintRestorer.swift` for
deserializing and re-applying constraints to a view hierarchy.

### W6: Add constraint performance profiling and optimization engine

Implement `snp.profile()` that instruments constraint activation,
deactivation, and layout pass timing. Detect performance anti-patterns
(excessive constraint churn, redundant constraints, overly complex
hierarchies) and suggest optimizations. Requires a new
`ConstraintProfiler.swift` for timing instrumentation, integration
with `Constraint.swift` for activation/deactivation hooks,
`ConstraintMaker.swift` for churn detection across make/remake cycles,
`Debugging.swift` for profiling report output, and
`ConstraintView+Extensions.swift` for the profiling entry point.

### W7: Implement visual constraint debugging overlay

Add `snp.showDebugOverlay()` that renders a translucent overlay on the
view hierarchy showing all constraints as colored lines, with conflict
highlighting, constraint identifiers, and interactive tap-to-inspect.
Requires a new `ConstraintDebugOverlay.swift` for the overlay view,
`ConstraintDebugRenderer.swift` for Core Graphics drawing of constraint
lines, integration with `Constraint.swift` to extract geometry,
`ConstraintAttributes.swift` for attribute-to-anchor mapping,
`Debugging.swift` for textual descriptions in the overlay, and a new
`ConstraintInspectorViewController.swift` for the detail view.

### W8: Add Combine-based reactive constraint bindings

Implement reactive constraint updates via Combine publishers:
`snp.reactive.top.bind(to: viewModel.$topOffset)` that automatically
updates constraint constants when the publisher emits. Support
animated transitions and cancellation. Requires a new
`ConstraintReactive.swift` for the reactive proxy, publisher
integration in `Constraint.swift` for constant observation, changes
to `ConstraintViewDSL.swift` for the `.reactive` accessor, subscription
lifecycle management in `ConstraintView+Extensions.swift`, and
animation support in `ConstraintMaker.swift` for batched reactive
updates.

### W9: Implement stack-view-like distribution DSL on top of constraints

Add `snp.distribute(.horizontally, views: [...], spacing: 8)` that
creates the full set of constraints to arrange views in a line with
equal or weighted distribution, alignment, and padding — without using
`UIStackView`. Support fill, fillEqually, fillProportionally, and
equalSpacing distributions. Requires a new
`ConstraintDistribution.swift` for the distribution engine, changes to
`ConstraintMaker.swift` for multi-view constraint generation,
`ConstraintMakerExtendable.swift` for distribution-specific attributes,
`ConstraintAttributes.swift` for inter-view spacing attributes,
`ConstraintView+Extensions.swift` for the distribution entry point,
and `Constraint.swift` for managing the generated constraint set as
a unit.

### W10: Add accessibility-driven automatic constraint adjustment

Implement a system that monitors accessibility settings (Dynamic Type
size, Bold Text, Reduce Motion, etc.) and automatically adjusts
constraints — increasing spacing for larger text sizes, simplifying
animations, and reflowing layouts for accessibility zoom. Requires a
new `ConstraintAccessibility.swift` for settings observation, an
accessibility-aware constraint factory in `ConstraintMaker.swift`,
trait-change handling in `ConstraintView+Extensions.swift`, dynamic
constant computation in `Constraint.swift` based on content size
category, adaptive attribute resolution in `ConstraintAttributes.swift`,
and integration with `ConstraintConfig.swift` for global
accessibility policy settings.
