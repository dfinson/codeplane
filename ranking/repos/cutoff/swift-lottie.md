# airbnb/lottie-ios

| Field | Value |
|-------|-------|
| **URL** | https://github.com/airbnb/lottie-ios |
| **License** | Apache-2.0 |
| **Language** | Swift |
| **Scale** | Large |
| **Category** | Animation library |
| **Set** | Cutoff |
| **Commit** | `ea35e6a4ec7f443a2b0b69ae97cccf0e946ef4a2` |

## Why this repo

- **Well-structured**: Clean public/private API boundary. Public API
  (`Sources/Public/`) exposes `LottieAnimationView`, `LottieAnimation`,
  `LottieView` (SwiftUI), dynamic properties, and configuration.
  Private implementation (`Sources/Private/`) splits into two rendering
  engines (Core Animation and Main Thread), a Lottie JSON model layer,
  and utility/math infrastructure.
- **Rich history**: 3K+ commits, 25K+ stars. The standard iOS animation
  library for After Effects animations. Issues cover rendering
  correctness, performance, dotLottie format, and SwiftUI integration.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
Sources/
├── Public/
│   ├── Animation/
│   │   ├── LottieAnimationView.swift       # UIView subclass — play, pause, loop
│   │   ├── LottieAnimationLayer.swift      # CALayer subclass — headless rendering
│   │   ├── LottieAnimation.swift           # Model — decoded Lottie JSON data
│   │   ├── LottieAnimationHelpers.swift    # Bundle/filepath loading helpers
│   │   ├── LottieView.swift                # SwiftUI wrapper
│   │   ├── LottiePlaybackMode.swift        # Playback mode enum
│   │   └── LottieAnimationViewInitializers.swift
│   ├── Configuration/
│   │   ├── LottieConfiguration.swift       # Rendering engine selection, feature flags
│   │   ├── RenderingEngineOption.swift      # .mainThread / .coreAnimation
│   │   ├── DecodingStrategy.swift          # JSON decoding options
│   │   └── ReducedMotionOption.swift       # Accessibility — reduced motion handling
│   ├── Controls/
│   │   ├── AnimatedButton.swift            # UIButton with Lottie animation
│   │   ├── AnimatedSwitch.swift            # UISwitch with Lottie animation
│   │   ├── AnimatedControl.swift           # Base animated UIControl
│   │   ├── LottieButton.swift              # SwiftUI animated button
│   │   └── LottieSwitch.swift              # SwiftUI animated switch
│   ├── DynamicProperties/
│   │   ├── AnimationKeypath.swift          # Keypath for targeting layers/properties
│   │   ├── AnyValueProvider.swift          # Protocol for dynamic value injection
│   │   └── ValueProviders/                 # Color, Float, Point, Size, Gradient providers
│   ├── AnimationCache/                     # LRU animation caching
│   ├── DotLottie/                          # .lottie archive format support
│   ├── FontProvider/                       # Custom font resolution
│   ├── ImageProvider/                      # Custom image resolution
│   ├── TextProvider/                       # Dynamic text replacement
│   ├── Logging/LottieLogger.swift          # Configurable logging
│   ├── Primitives/                         # LottieColor, Vectors, AnimationTime
│   ├── Keyframes/                          # Keyframe, Interpolatable protocols
│   ├── iOS/                                # iOS-specific views and providers
│   └── macOS/                              # macOS-specific views and providers
├── Private/
│   ├── CoreAnimation/
│   │   ├── CoreAnimationLayer.swift        # Root CA rendering layer
│   │   ├── CompatibilityTracker.swift      # Tracks unsupported features
│   │   ├── ValueProviderStore.swift        # Runtime value provider management
│   │   ├── Animations/                     # Per-property CA animation builders
│   │   │   ├── TransformAnimations.swift   # Position, rotation, scale keyframes
│   │   │   ├── ShapeAnimation.swift        # BezierPath keyframe animation
│   │   │   ├── GradientAnimations.swift    # Gradient stop animation
│   │   │   ├── OpacityAnimation.swift      # Alpha keyframe animation
│   │   │   └── ...
│   │   ├── Layers/                         # CA layer hierarchy
│   │   │   ├── BaseAnimationLayer.swift    # Transform, opacity, visibility
│   │   │   ├── ShapeItemLayer.swift        # Shape rendering (fill, stroke, path)
│   │   │   ├── TextLayer.swift             # Text rendering
│   │   │   ├── ImageLayer.swift            # Image asset layer
│   │   │   ├── PreCompLayer.swift          # Pre-composition nesting
│   │   │   └── ...
│   │   └── Extensions/                     # Keyframe utilities
│   ├── MainThread/
│   │   ├── LayerContainers/                # Main-thread composition layers
│   │   │   ├── MainThreadAnimationLayer.swift  # Root display-link-driven layer
│   │   │   └── CompLayers/                 # Shape, text, image, precomp layers
│   │   └── NodeRenderSystem/               # Node graph for shape rendering
│   │       ├── Nodes/PathNodes/            # Ellipse, rect, star, shape nodes
│   │       ├── Nodes/RenderNodes/          # Fill, stroke, gradient renderers
│   │       ├── Nodes/OutputNodes/          # Render output pipeline
│   │       └── Protocols/                  # AnimatorNode, PathNode, RenderNode
│   ├── Model/
│   │   ├── Layers/LayerModel.swift         # Base layer model (JSON decoded)
│   │   ├── Layers/ShapeLayerModel.swift    # Shape layer with items
│   │   ├── Layers/TextLayerModel.swift     # Text layer model
│   │   ├── ShapeItems/                     # Shape, ellipse, rect, fill, stroke models
│   │   ├── Objects/                        # Transform, mask, marker models
│   │   ├── Keyframes/                      # KeyframeGroup — typed keyframe arrays
│   │   ├── Assets/                         # Image and precomp asset models
│   │   ├── DotLottie/                      # .lottie archive parsing
│   │   └── Text/                           # Text document and animator models
│   ├── Utility/
│   │   ├── Helpers/                        # AnimationContext, RenderingHelpers
│   │   ├── Interpolatable/                 # Interpolation — double, CGPoint, CGPath
│   │   ├── Primitives/                     # BezierPath, CurveVertex
│   │   └── Extensions/                     # Array, CGRect, Bundle extensions
│   └── EmbeddedLibraries/
│       ├── EpoxyCore/                      # Epoxy diffing (embedded dependency)
│       ├── LRUCache/                       # LRU cache implementation
│       └── ZipFoundation/                  # ZIP archive reading for dotLottie
```

## Scale indicators

- ~284 Swift source files
- ~38K lines of Swift code
- Deep structure (4–5 levels under Sources/)
- No external dependencies (all vendored in EmbeddedLibraries/)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix CompatibilityTracker not reporting unsupported gradient types

The `CompatibilityTracker` in `Private/CoreAnimation/CompatibilityTracker.swift`
tracks features unsupported by the Core Animation engine but does not
log when a radial gradient is encountered (only linear gradients are
fully supported in CA layer rendering). Add a compatibility warning for
`GradientType.radial` in the gradient animation setup path in
`GradientAnimations.swift`, registered through the tracker.

### N2: Add animation speed curve to LottiePlaybackMode

The `LottiePlaybackMode` enum in `Public/Animation/LottiePlaybackMode.swift`
defines playback modes but does not support custom speed curves (e.g.,
ease-in, ease-out). Add a `withSpeedCurve(_ curve: CAMediaTimingFunction)`
modifier to `LottiePlaybackMode` and propagate the timing function
through `LottieAnimationLayer` to the Core Animation rendering path.

### N3: Fix LRUAnimationCache not respecting memory warnings on iOS

The `LRUAnimationCache` in `Public/AnimationCache/LRUAnimationCache.swift`
uses a fixed-size LRU cache but does not respond to
`UIApplication.didReceiveMemoryWarningNotification`. Add a memory
warning observer that evicts half the cache entries when the system
signals memory pressure, preventing the app from being terminated.

### N4: Add frame marker lookup by name with fuzzy matching

The `LottieAnimation` model in `Public/Animation/LottieAnimation.swift`
provides marker access by exact name. Add a `marker(fuzzyName:)`
method that matches marker names case-insensitively and with
diacritics-insensitive comparison (using `String.compare(_:options:
[.caseInsensitive, .diacriticInsensitive])`), returning the closest
match or nil.

### N5: Fix BezierPath interpolation producing self-intersecting curves

The `BezierPath` interpolation in `Private/Utility/Primitives/BezierPath.swift`
linearly interpolates control points between keyframes. When two
keyframes have dramatically different control point positions, the
interpolated path can self-intersect. Fix the interpolation to use
De Casteljau subdivision when the control point displacement exceeds
a threshold, ensuring smoother transitions.

### N6: Add animation completion percentage callback

The `LottieAnimationView` provides a completion handler that fires only
when animation finishes, but there is no callback for intermediate
progress. Add `onProgress(_ handler: (AnimationProgressTime) -> Void)`
that fires on each display link tick during Main Thread rendering
and on each `CAAnimation` time update during Core Animation rendering,
reporting the current frame as a percentage of total duration.

### N7: Fix FilepathImageProvider not handling @2x/@3x image resolution

The `FilepathImageProvider` in `Public/iOS/FilepathImageProvider.swift`
loads images from a directory path but does not automatically select
@2x or @3x variants based on screen scale. When a Lottie animation
references `"image_0.png"`, the provider should check for
`"image_0@2x.png"` and `"image_0@3x.png"` based on
`UIScreen.main.scale` before falling back to the 1x variant.

### N8: Add `reduceMotion` animation fallback to show final frame

The `ReducedMotionOption` in `Public/Configuration/ReducedMotionOption.swift`
provides `.none` and `.specific` options. Add a `.showFinalFrame` option
that, when the system has Reduce Motion enabled, immediately displays
the animation's last frame without any animation, providing visual
context without motion.

### N9: Fix TextLayer not applying font tracking (letter spacing)

The `TextLayer` in `Private/CoreAnimation/Layers/TextLayer.swift`
renders text from `TextDocument` model data but does not apply the
`tracking` property (letter spacing) defined in the Lottie JSON
text animator. Add `NSAttributedString` attribute
`NSAttributedString.Key.kern` with the tracking value when rendering
text in both Core Animation and Main Thread engines.

### N10: Add dotLottie animation metadata accessors

The `DotLottieFile` in `Public/DotLottie/DotLottieFile.swift` parses
`.lottie` archives but does not expose the manifest metadata (author,
description, version, theme colour). Add read-only properties for
`author`, `description`, `version`, and `themeColor` parsed from the
`manifest.json` within the archive.

## Medium

### M1: Implement animation diffing for transition blending

Add `LottieAnimationView.transition(to: animation, duration:)` that
smoothly blends between two Lottie animations by interpolating matching
layers. Requires layer matching by name, interpolatable property
extraction from both animation models, a blend layer that cross-fades
non-matchable layers, and integration with both Core Animation and
Main Thread rendering engines.

### M2: Add accessibility support with VoiceOver descriptions per frame range

Implement `setAccessibilityDescription(for frameRange: ClosedRange<AnimationFrameTime>, description:)`
on `LottieAnimationView` that maps frame ranges to VoiceOver
descriptions. As the animation progresses, update
`accessibilityLabel` based on the current frame. Requires a frame-range
to description mapping, display-link integration, `UIAccessibility`
posting of value changes, and SwiftUI `LottieView` equivalent via
`accessibilityLabel` modifier.

### M3: Implement animation expression evaluation for After Effects expressions

After Effects animations can contain JavaScript expressions on
properties. Add an expression evaluator in `Private/Model/` that
interprets a subset of AE expressions (basic math, `time`,
`thisComp.layer`, `transform.position`, `loopIn`, `loopOut`). Requires
an expression parser, an evaluation context with access to the layer
tree, integration with `KeyframeGroup` to use expression-computed
values, and fallback to static keyframes for unsupported expressions.

### M4: Add animation thumbnail generation

Implement `LottieAnimation.thumbnail(at progress: AnimationProgressTime,
size: CGSize) -> CGImage` that renders a single frame to an offscreen
`CGContext` without creating a view. Requires headless rendering setup
via `LottieAnimationLayer`, `CALayer.render(in:)` integration,
proper layer hierarchy setup without display link, and caching of
generated thumbnails in `AnimationCache`.

### M5: Implement layer-level animation control

Add `LottieAnimationView.setLayerPlayback(_ keypath: AnimationKeypath,
mode: LottiePlaybackMode)` to control playback of individual layers
independently. For example, play the background layer on loop while
playing the foreground once. Requires per-layer animation state
tracking, modifications to `CoreAnimationLayer` and
`MainThreadAnimationLayer` for independent layer timing, and keypath
resolution via the existing `AnimationKeypath` infrastructure.

### M6: Add animation asset preloading and prefetching

Implement `LottieAnimation.preloadAssets(from provider:
AnimationImageProvider)` that resolves and caches all image assets
referenced by the animation before playback begins. Requires asset
enumeration from the animation model, async image loading with
progress reporting, integration with `CachedImageProvider`, and a
readiness callback for `LottieAnimationView` to delay playback until
assets are loaded.

### M7: Implement animation colour remapping

Add `LottieAnimationView.remapColors(_ mapping: [LottieColor: LottieColor])`
that replaces all instances of specified colours throughout the
animation at runtime. Requires colour extraction from the animation
model (fill colours, stroke colours, gradient stops), dynamic value
provider creation for each matched colour property, batch registration
with `ValueProviderStore`, and support for both rendering engines.

### M8: Add animation performance profiling instrumentation

Implement `LottieConfiguration.enablePerformanceProfiling` that
instruments the rendering pipeline. Track per-frame render time,
layer count, path complexity (number of vertices), cache hit rates,
and dropped frames. Expose metrics via `LottieAnimationView.performanceMetrics`.
Requires instrumentation in `MainThreadAnimationLayer`,
`CoreAnimationLayer`, `ShapeItemLayer`, `LRUCache`, and a metrics
aggregator.

### M9: Implement animation clipping to arbitrary bezier masks

Add `LottieAnimationView.clipAnimation(to path: BezierPath)` that
masks the entire animation to a custom shape. Requires a `CAShapeLayer`
mask setup in `LottieAnimationLayer`, BezierPath-to-CGPath conversion,
dynamic mask updates for animated clip paths, and support for both
rendering engines with proper layer hierarchy integration.

### M10: Add animation serialisation back to Lottie JSON

Implement `LottieAnimation.toJSON() -> Data` that serialises the
in-memory animation model back to Lottie JSON format. Support
round-tripping: `decode → modify → encode`. Requires `Encodable`
conformance on all model types in `Private/Model/` (layers, shapes,
keyframes, assets, text), careful preservation of unknown JSON keys,
and validation that re-encoded JSON plays identically.

## Wide

### W1: Implement a complete SVG rendering backend

Add a rendering engine option `.svg` that renders Lottie animations to
SVG (string or DOM). Support all shape types, gradients, masks, mattes,
text, images (embedded Base64), and keyframe animations (via CSS
animations or SMIL). Requires a new `Private/SVGRenderer/` module, SVG
element builders for each layer type, animation timing conversion,
integration with `RenderingEngineOption`, and `LottieAnimationLayer`
dispatching.

### W2: Implement a Lottie animation editor and inspector

Add a debug overlay and inspector for `LottieAnimationView`: a layer
hierarchy tree view, per-layer property inspector (transform, opacity,
colour values at current frame), keyframe timeline visualisation, and
interactive property override. Requires a new `Debug/` module, layer
tree extraction from both rendering engines, a SwiftUI inspector panel,
touch-based layer selection in the animation view, and integration with
`AnimationKeypath` for property identification.

### W3: Add support for Lottie interactive animations with gesture binding

Implement `LottieAnimationView.bindProgress(to gesture:
UIPanGestureRecognizer, range: ClosedRange<AnimationProgressTime>)`
for scrubbing animation progress with gestures. Support pan, pinch,
rotation, and long-press bindings. Add physics-based snapping to
keyframe boundaries, momentum-based settling, and haptic feedback
at markers. Requires gesture recogniser integration, progress mapping
with configurable curves, both rendering engine support, and SwiftUI
gesture modifier equivalents.

### W4: Implement multi-animation composition and layering

Add `LottieComposition` that layers multiple `LottieAnimation` instances
with individual timing, position, scale, and blend modes. Support
synchronised playback, sequential chaining, and cross-dissolve
transitions. Requires a composition model, a composite
`LottieAnimationLayer` that manages multiple sub-layers, timeline
synchronisation, per-animation transform and mask application, and
SwiftUI `LottieCompositionView`.

### W5: Add a Lottie animation stress testing and validation framework

Implement `LottieValidator.validate(animation:)` that analyses a
Lottie JSON file for rendering issues: missing image assets, font
references, unsupported features per engine, unreachable layers,
empty shapes, infinite loops in expressions, and memory-budget
estimates. Generate a validation report per rendering engine. Requires
model traversal, compatibility rule definitions, feature support
matrices for CA vs Main Thread engines, and integration with
`LottieLogger`.

### W6: Implement server-driven animation configuration

Add support for fetching animation configuration from a server: which
animation to play, playback parameters, dynamic value overrides, and
A/B test variants. Include caching, offline fallback, and configuration
change detection. Requires a network client using `LottieURLSession`,
a configuration model, integration with `LottieConfiguration`,
`DotLottieFile` remote loading, value provider creation from server
data, and `LottieAnimationView` auto-update.

### W7: Add a Canvas/Metal rendering engine for high-performance scenarios

Implement a `.metal` rendering engine option using Metal for GPU-
accelerated frame rendering. Support shape path tessellation, gradient
rendering via fragment shaders, image compositing, and mask/matte
operations in the GPU pipeline. Requires a Metal rendering module in
`Private/MetalRenderer/`, shader programs for each shape operation,
texture management for image assets, frame synchronisation with
display link, and `RenderingEngineOption` integration.

### W8: Implement a Lottie animation benchmark suite

Build a comprehensive benchmark system: render-time measurement per
frame for all three engines, memory profiling, CPU usage tracking,
battery impact estimation, and regression detection. Include a set of
reference animations covering all feature types. Requires an
`XCTest`-based performance test harness, per-engine rendering
instrumentation, metric collection and aggregation, JSON result
export, and baseline comparison for CI integration.

### W9: Add dynamic theming system for animations

Implement `LottieTheme` that defines colour, opacity, and text
overrides that can be applied to any animation. Support light/dark
mode switching, custom brand themes, and theme inheritance. Theme
application should batch-update all matching properties via
`ValueProviderStore`. Requires a theme model, property matching rules
via `AnimationKeypath` patterns, automatic re-rendering on theme
change, persistence, and SwiftUI `@Environment` integration.

### W10: Implement a complete dotLottie authoring pipeline

Add `DotLottieBuilder` for creating `.lottie` archives programmatically:
add animations with metadata, embed image assets with compression
options, specify themes and states, set manifest properties, and write
to disk or Data. Support multi-animation archives with transition
definitions. Requires a builder API, ZIP archive creation via
`ZipFoundation`, manifest generation, image asset processing, animation
validation, and integration with `DotLottieFile` for round-trip
verification.
