# SnapKit/SnapKit

| Field | Value |
|-------|-------|
| **URL** | https://github.com/SnapKit/SnapKit |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Small (focused DSL library) |
| **Category** | UI layout / Auto Layout DSL |
| **Set** | Cutoff |
| **Commit** | `19f59a63f0faac287f4e59986959859d81ec851c` |

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

### N1: Fix Constraint.activateIfNeeded matching wrong constraint during update

When `snp.updateConstraints` is called, `Constraint.swift`'s
`activateIfNeeded(updatingExisting:)` prepares a fresh `LayoutConstraint`
and searches for a matching existing one using the `==` operator defined
in `LayoutConstraint.swift`. That operator compares `firstAttribute`,
`secondAttribute`, `firstItem`, and `secondItem`. When a view has two
constraints on the same attribute pointing to different targets (e.g.,
two `.top` constraints to different views), the freshly-created
comparison constraint can have its `firstItem`/`secondItem` ordering
differ from the stored constraint because `NSLayoutConstraint`
normalizes item order internally, causing `existingLayoutConstraints.first
{ $0 == layoutConstraint }` to fail or match the wrong entry and update
the wrong constant. Fix `Constraint.swift`'s `activateIfNeeded` to
match existing constraints by looking up `self.from` and `self.to`
`ConstraintItem` target identities directly (before NSLayoutConstraint
normalization) rather than comparing fully-constructed `NSLayoutConstraint`
objects.

### N2: Fix Constraint.deactivate retaining NSLayoutConstraint objects after deactivation

When a view is removed from the hierarchy and its constraints are
deactivated via `snp.removeConstraints()`, each `Constraint` object
retains its `layoutConstraints: [LayoutConstraint]` array even after
the underlying `NSLayoutConstraint` objects are deactivated. Because
`NSLayoutConstraint` holds strong references to its `firstItem` and
`secondItem`, the deactivated `Constraint` entries stored in the item's
`constraintsSet` keep the removed view and its layout targets alive,
preventing deallocation. Fix `Constraint.swift`'s `deactivateIfNeeded`
to clear the `layoutConstraints` array after calling
`NSLayoutConstraint.deactivate`, releasing the `NSLayoutConstraint`
objects and their strong item references so the view hierarchy can be
deallocated normally.

### N3: Add safeArea convenience accessor to ConstraintViewDSL

There is no shorthand in the SnapKit DSL to access a view's safe area
layout guide. Developers must write `view.safeAreaLayoutGuide.snp.
makeConstraints { ... }` or pass `view.safeAreaLayoutGuide.snp.top`
as a target, which is verbose and inconsistent with the `view.snp`
pattern. Add a `safeArea` property to `ConstraintViewDSL.swift`
(gated `@available(iOS 11.0, tvOS 11.0, *)`) that returns a
`ConstraintLayoutGuideDSL` wrapping `view.safeAreaLayoutGuide`, so
callers can write `make.edges.equalTo(superview.snp.safeArea)` or
`superview.snp.safeArea.makeConstraints { ... }`. The property reads
`view.safeAreaLayoutGuide` from the underlying `ConstraintView`.

### N4: Fix ConstraintMakerEditable.dividedBy not guarding against zero divisor

`ConstraintMakerEditable.dividedBy(_:)` in
`ConstraintMakerEditable.swift` computes
`1.0 / amount.constraintMultiplierTargetValue` without checking for
zero. Passing zero produces an infinite multiplier that creates an
invalid `NSLayoutConstraint`, causing undefined Auto Layout behavior
at runtime with no diagnostic message. Fix `dividedBy` to check for
a zero divisor and trigger a `preconditionFailure` with a descriptive
error message.

### N5: Fix ConstraintConfig.interfaceLayoutDirection not being thread-safe

`ConstraintConfig.interfaceLayoutDirection` in
`ConstraintConfig.swift` is a static `var` with no synchronization.
It is read during constraint constant resolution in
`ConstraintConstantTarget.swift` (which can execute on any thread)
and may be written from the main thread simultaneously, creating a
data race. Fix `ConstraintConfig.swift` to use a thread-safe access
pattern (e.g., a serial dispatch queue or atomic wrapper) for the
static layout direction property.

### N6: Fix ConstraintDSL.setLabel using OBJC_ASSOCIATION_COPY_NONATOMIC for concurrent access

`ConstraintDSL.setLabel(_:)` in `ConstraintDSL.swift` stores the
label string via `objc_setAssociatedObject` with
`OBJC_ASSOCIATION_COPY_NONATOMIC`. When `setLabel` and `label()` are
called concurrently from different threads (e.g., main thread setting
labels while a background thread reads them for debugging), the
non-atomic association policy can produce torn reads. Fix
`ConstraintDSL.swift` to use `OBJC_ASSOCIATION_COPY` (which is
atomic) for the label association.

### N7: Fix Debugging descriptionForAttribute missing margin attributes on macOS

The `descriptionForAttribute` function in `Debugging.swift` provides
human-readable names for all layout attributes on iOS (including
margin variants like `topMargin`, `leadingMargin`, etc.) but the macOS
(`#else`) branch omits these cases entirely. On macOS 10.11+,
`NSLayoutAttribute` includes the same margin attributes as iOS
(`leftMargin`, `rightMargin`, `topMargin`, `bottomMargin`,
`leadingMargin`, `trailingMargin`, `centerXWithinMargins`,
`centerYWithinMargins`). Any constraint involving these attributes on
macOS falls through to the `@unknown default` case and is described
as "unknown". Fix the macOS branch in `Debugging.swift` to include
descriptions for all margin attributes available on macOS 10.11+.

### N8: Fix ConstraintConstantTarget using hardcoded layout direction default

In `ConstraintConstantTarget.swift`, the `constraintConstantTargetValueFor`
method maps `.leading`/`.trailing` inset values by reading
`ConstraintConfig.interfaceLayoutDirection` — a static variable that
defaults to `.leftToRight` and must be set manually by the developer.
On a device configured for a right-to-left language (e.g., Arabic,
Hebrew), if the developer does not explicitly set
`ConstraintConfig.interfaceLayoutDirection = .rightToLeft`, leading and
trailing insets will be mapped using the wrong side, silently producing
incorrect layouts. Fix `ConstraintConstantTarget.swift` to derive the
effective layout direction from the system (`UIApplication.shared.
userInterfaceLayoutDirection` on iOS, `NSApplication.shared.
userInterfaceLayoutDirection` on macOS) when applying insets for
`.leading` and `.trailing` attributes, falling back to
`ConstraintConfig.interfaceLayoutDirection` only when the application
shared instance is unavailable.

### N9: Fix remake not deactivating constraints when superview changes

`snp.remakeConstraints` deactivates all existing constraints before
creating new ones. If the view has been moved to a different superview
between the original `makeConstraints` and the `remakeConstraints`
call, the deactivation fails silently because the old constraints
reference a stale view hierarchy. Fix `ConstraintMaker.swift` to
handle stale constraints by removing them from the stored list when
deactivation fails instead of leaving them as dangling entries.

### N10: Fix equalToSuperview() fatalError not including view description

Calling `.equalToSuperview()`, `.lessThanOrEqualToSuperview()`, or
`.greaterThanOrEqualToSuperview()` on a view with no superview
triggers a `fatalError` in `ConstraintMakerRelatable.swift`. The
current error message — `"Expected superview but found nil when
attempting make constraint \`equalToSuperview\`."` — does not include
any description of which view triggered the crash, making it hard to
diagnose the problem in a complex view hierarchy. Fix the three
`*ToSuperview` methods in `ConstraintMakerRelatable.swift` to include
the view's `debugDescription` (accessed via
`self.description.item`) in the fatal error message so the
offending view can be immediately identified from the crash log.

### N11: Fix CONTRIBUTING.md not explaining macOS testing or CI pipeline

The `CONTRIBUTING.md` file describes how to open the Xcode project
and run tests but does not explain how to test macOS-specific
constraint behavior or mention the `.travis.yml` CI configuration.
The `ISSUE_TEMPLATE.md` does not ask reporters to specify their
platform (iOS vs macOS vs tvOS), which is critical for reproducing
platform-specific layout bugs. The `CodeSnippets/SnapKit Constraint
Make.codesnippet` and `CodeSnippets/SnapKit Constraint
Remake.codesnippet` Xcode code snippets use outdated syntax that
does not include `.labeled()` for debugging. Fix `CONTRIBUTING.md`
to add macOS testing instructions and `.travis.yml` CI context,
update `ISSUE_TEMPLATE.md` with a platform field, and update both
code snippets to include `.labeled()` in the example closures.

## Medium

### M1: Expose SnapKit-managed constraints through the public DSL

Once constraints are created with `snp.makeConstraints`, there is no
public way to retrieve them for later inspection or targeted updates
short of calling `snp.updateConstraints` again. The
`LayoutConstraintItem.constraints` property that stores all
SnapKit-managed `Constraint` objects is marked `internal`, making it
inaccessible to callers. Promote the `constraints` property to
`public` in `LayoutConstraintItem.swift`, add a `constraints: [Constraint]`
accessor to `ConstraintViewDSL.swift` and `ConstraintLayoutGuideDSL.swift`,
extend `Constraint.swift` with `public` read-only `fromAttributes:
ConstraintAttributes` and `toTarget: AnyObject?` properties for
post-creation introspection, and add a `constraint(for:
ConstraintAttributes) -> Constraint?` helper on both DSL types that
returns the first constraint matching a given attribute so callers
can retrieve and directly mutate (`.update(offset:)`, `.update(priority:)`)
individual constraints by attribute without going through
`updateConstraints`.

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
updates to `ConstraintViewDSL.swift` to support the representable
lifecycle, and a new "SwiftUI Integration" section in `README.md`
documenting the `SnapKitView` wrapper with usage examples.

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

### M11: Update Documentation/ and README.md for modern SnapKit API

The `Documentation/SnapKit 3.0 Migration Guide.md` is the only
documentation file and covers only the 2.x → 3.0 migration.
There is no migration guide for users upgrading from 3.x to the
current version. The `README.md` does not document the
`.labeled()` debugging feature, `snp.updateConstraints`, or the
safe area layout guide support. The
`SnapKitPlayground.playground/Contents.swift` playground file
contains examples that may not compile against the current API.
The `SnapKit.podspec` and `Package.swift` specify different
Swift version requirements with no documentation explaining why.
The `.travis.yml` CI configuration references Xcode versions
that may be outdated. Add a `Documentation/SnapKit 5.0
Migration Guide.md` covering all API changes since 3.0, update
`README.md` with `labeled()`, `updateConstraints`, and safe area
documentation, update the playground to compile with the current
API, reconcile Swift version requirements between
`SnapKit.podspec` and `Package.swift`, and update `.travis.yml`
with current Xcode versions.

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

### W11: Overhaul README.md, CodeSnippets, playground, and Documentation

The `README.md` has basic installation instructions but lacks a
complete API reference, a platform support matrix (iOS, macOS, tvOS),
and usage examples for `remakeConstraints`, `updateConstraints`,
layout guides, and safe area. The `CodeSnippets/` directory contains
two Xcode code snippets (`SnapKit Constraint Make.codesnippet` and
`SnapKit Constraint Remake.codesnippet`) that do not include
`updateConstraints` or safe-area examples. The
`SnapKitPlayground.playground/` playground has a single
`Contents.swift` file that demonstrates only basic `makeConstraints`
usage and does not cover `remakeConstraints`, `updateConstraints`,
layout guides, or macOS usage. The `Documentation/SnapKit 3.0
Migration Guide.md` is the only migration guide; its examples use
the pre-5.0 API (`.equalTo(UIEdgeInsets(...))` inset patterns that
have since been superseded by `.inset(UIEdgeInsets(...))` and the
directional inset API). The `CONTRIBUTING.md` does not describe the
code review process. The `.travis.yml` CI configuration references
`xcode11` which is outdated and targets only `iPhone 8` and
`Apple TV 4K (at 1080p)` which are legacy device names.
Overhaul `README.md` with a platform support matrix and a full API
reference with examples for make, remake, update, layout guides, and
safe area. Add `updateConstraints` and safe-area code snippets to
`CodeSnippets/`. Expand the playground with `remakeConstraints`,
`updateConstraints`, layout guide, and safe-area examples. Update
`Documentation/SnapKit 3.0 Migration Guide.md` to use current
inset and directional-inset API. Update `CONTRIBUTING.md` to
describe the code review process. Update `.travis.yml` with a
current `osx_image` and modern simulator destination names.
