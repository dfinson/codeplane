# onevcat/Kingfisher

| Field | Value |
|-------|-------|
| **URL** | https://github.com/onevcat/Kingfisher |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Medium (~50 source files, ~15K LOC) |
| **Category** | Image downloading and caching |
| **Set** | Cutoff |
| **Commit** | `3fe88ce2de62528b9ed5d88df9d9af0494826fdd` |

## Why this repo

- **Widely adopted**: 23K+ stars, the de facto image loading library for
  Swift on iOS/macOS/tvOS/watchOS. Navigation requires understanding
  protocol-oriented design, Swift generics, and platform-conditional
  compilation (`#if os(iOS)`).
- **Well-layered architecture**: Cleanly separated into Networking (download),
  Cache (memory + disk), Image (processing/drawing/animation), Views
  (UIKit/AppKit extensions), General (options/resources), and Utility
  (callback queues, delegates). Tasks exercise cross-layer coordination.
- **Rich feature surface**: Image downloading with authentication,
  progressive JPEG, GIF animation, image processors/filters, prefetching,
  SwiftUI integration, and cache expiration policies provide diverse
  task material.
- **Permissive**: MIT license.

## Structure overview

```
Sources/Kingfisher/
├── KingfisherManager.swift             # Central coordinator — download + cache orchestration
├── Networking/
│   ├── ImageDownloader.swift           # URLSession-based image downloading
│   ├── SessionDelegate.swift           # URLSession delegate handling
│   ├── AuthenticationChallengeResponsable.swift  # Auth challenge protocol
│   ├── ImageDownloaderDelegate.swift   # Download lifecycle delegate
│   ├── ImagePrefetcher.swift           # Batch prefetch orchestration
│   └── RequestModifier.swift           # Request customization protocol
├── Cache/
│   ├── ImageCache.swift                # Unified cache interface (memory + disk)
│   ├── MemoryStorage.swift             # NSCache-backed in-memory storage
│   ├── DiskStorage.swift               # File-system-backed disk cache
│   ├── CacheSerializer.swift           # Data ↔ Image serialization
│   └── FormatIndicatedCacheSerializer.swift  # Format-aware serializer
├── Image/
│   ├── ImageProcessor.swift            # Protocol + built-in processors
│   ├── ImageDrawing.swift              # Core Graphics drawing utilities
│   ├── ImageTransition.swift           # View transition animations
│   ├── GIFAnimatedImage.swift          # GIF frame decoding and animation
│   ├── ImageFormat.swift               # Image format detection
│   ├── Placeholder.swift              # Placeholder protocol and defaults
│   └── Filter.swift                    # CIFilter-based image filters
├── Views/
│   ├── UIImageView+Kingfisher.swift    # UIImageView extension for image setting
│   ├── UIButton+Kingfisher.swift       # UIButton extension for image setting
│   ├── NSButton+Kingfisher.swift       # AppKit button extension
│   ├── WKInterfaceImage+Kingfisher.swift  # watchOS extension
│   ├── KFAnimatedImageView.swift       # Animated GIF image view
│   └── AnimatedImageView.swift         # Frame-by-frame animation driver
├── General/
│   ├── KFOptionsSetter.swift           # Chainable option builder
│   ├── ImageSource.swift               # URL/data provider abstraction
│   ├── Resource.swift                  # Resource protocol (cacheKey + downloadURL)
│   ├── KingfisherOptionsInfo.swift     # Option enum and parsing
│   └── ImageModifier.swift             # Post-download image modification
├── Utility/
│   ├── CallbackQueue.swift             # GCD queue abstraction for callbacks
│   ├── Delegate.swift                  # Type-safe delegate wrapper
│   ├── Result.swift                    # Result type utilities
│   ├── Box.swift                       # Reference-type wrapper
│   ├── Runtime.swift                   # ObjC runtime helpers
│   └── String+MD5.swift               # MD5 hashing for cache keys
└── SwiftUI/
    ├── KFImage.swift                   # SwiftUI Image view wrapper
    └── ImageBinder.swift               # ObservableObject for SwiftUI binding
```

## Scale indicators

- ~50 Swift source files
- ~15K lines of code
- 7 directories under `Sources/Kingfisher/`
- Zero external dependencies

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix SessionDelegate race between callback removal and task removal

In `SessionDelegate.swift`, `onCompleted(task:result:)` calls
`sessionTask.removeAllCallbacks()` to collect pending callbacks, then
calls `sessionTask.onTaskDone.call(...)` (which may take time to
process images), and only afterward calls `remove(sessionTask)` to
unregister the task from the `tasks` dictionary. Because
`remove(sessionTask)` acquires the session lock independently,
a concurrent call to `addDownloadTask` can observe the task still in
`tasks` during this window, call `sessionDelegate.append(existingTask,
callback:)`, and add a new callback to a task whose callbacks have
already been drained. That new callback is never invoked. Fix
`SessionDelegate.swift` so that `remove(sessionTask)` is called before
`onTaskDone.call` is dispatched, or so that the append path detects a
completed task and starts a fresh download instead.

### N2: Fix MemoryStorage not synchronizing config property access during cleanup

`MemoryStorage.Backend` in `Cache/MemoryStorage.swift` protects
storage operations (`store`, `remove`, `removeExpired`) with an
`NSLock`. However, the `config` property’s `didSet` handler updates
the underlying `NSCache` limits and reschedules the cleanup timer
without acquiring the lock. If `removeExpired()` is executing on
the timer thread while `config` is changed from another thread,
the expiration settings read during cleanup may be inconsistent —
partially from the old config and partially from the new. Fix
`MemoryStorage.swift` to acquire the lock in the `config` `didSet`
handler to ensure config changes are atomic with respect to
in-progress cleanup operations.

### N3: Fix DiskStorage crashing on negative file size from URLResourceValues

`DiskStorage.Backend` reads file sizes via `URLResourceValues.fileSize`
(which returns `Int?`) and stores them as `FileMeta.fileSize: Int`.
When the accumulated total is computed in `totalSize()` and in
`removeExpired()`, the code converts each size with `UInt(meta.fileSize)`.
Swift's `UInt(_:)` initializer traps at runtime if the argument is
negative. While Apple's frameworks rarely return negative file sizes,
corrupt filesystem metadata or NFS-mounted volumes can produce such
values, causing a crash. Fix `DiskStorage.swift` to guard against
negative file sizes by clamping to zero before the `UInt` conversion.

### N4: Fix CacheSerializer not preserving image scale on deserialization

`CacheSerializer.data(with:original:)` serializes an image but
discards its `scale` factor. On deserialization, images from Retina
displays are reconstructed at 1x scale, causing them to render at
double size. Fix `CacheSerializer.swift` to encode and restore the
image scale factor.

### N5: Fix ImagePrefetcher not stopping on view controller deallocation

`ImagePrefetcher` holds a strong reference to its completion handler
closure, which often captures `self` (a view controller). When the
view controller is deallocated mid-prefetch, the closure keeps it
alive, causing a memory leak. Fix `ImagePrefetcher.swift` to use
weak capture and cancel outstanding prefetch tasks on `deinit`.

### N6: Fix GIFAnimatedImage failing entirely when a single frame cannot be decoded

`GIFAnimatedImage.init?(from:options:)` in `Image/GIFAnimatedImage.swift`
iterates over all frame indices and uses a `guard let imageRef =
frameSource.frame(at: i) else { return nil }` pattern. If any single
frame fails to decode — which can happen with partially-corrupt or
truncated GIF data where the reported frame count is accurate but one
or more frames cannot be rendered — the entire initializer returns
`nil`, preventing any animation from being displayed even when most
frames are valid. Fix `GIFAnimatedImage.swift` to skip undecodable
frames (continuing the loop rather than returning `nil`) so that a
partially-corrupt GIF still animates with whatever frames are intact.

### N7: Fix SessionDataTask.forceCancel reading callbacksStore without the lock

`SessionDataTask.forceCancel()` in `Networking/SessionDataTask.swift`
iterates `callbacksStore.keys` to collect tokens and then calls
`cancel(token:)` for each one. The key iteration is performed without
acquiring the `NSLock` that guards all other `callbacksStore`
accesses (`addCallback`, `removeCallback`, `removeAllCallbacks`).
If `addCallback` is called concurrently from another thread while
`forceCancel` is iterating, the dictionary is mutated during iteration,
which is undefined behavior in Swift and can crash the process. Fix
`SessionDataTask.swift` to acquire the lock before reading
`callbacksStore.keys` in `forceCancel()`, copying the key set first,
then releasing the lock before cancelling each token.

### N8: Fix ImageCache not removing corrupt disk cache entries on deserialization failure

When `ImageCache.retrieveImageInDiskCache` successfully reads bytes
from `DiskStorage` but `cacheSerializer.image(with:options:)` returns
`nil` (e.g., the stored data is corrupt or was written by an
incompatible serializer), the completion handler receives
`.success(nil)` — a cache miss — but the corrupt file is left on disk.
Every subsequent request for that key triggers the same unnecessary
disk read, serializer failure, and cache-miss round-trip. Fix
`ImageCache.swift` to detect a `nil` deserialization result after a
successful disk read and remove the corrupt entry from `DiskStorage`
before returning the cache-miss result.

### N9: Fix AnimatedImageView not pausing display link when autoPlayAnimatedImage is false

`AnimatedImageView` in `Views/AnimatedImageView.swift` overrides
`didMoveToWindow()` and `didMoveToSuperview()` and calls `didMove()`,
which pauses the display link when both `superview` and `window` become
`nil`. However, `didMove()` is guarded by `autoPlayAnimatedImage &&
animator != nil`. When `autoPlayAnimatedImage` is set to `false` and
the caller manually starts animation via `startAnimating()`, removing
the view from the window does not pause the display link because the
guard short-circuits. The display link continues firing on every
screen refresh, consuming CPU for animation that is not visible. Fix
`AnimatedImageView.swift` to pause the display link when `window`
becomes `nil` regardless of the `autoPlayAnimatedImage` flag.

### N10: Fix ImageIndicator missing animatingCount reference counting

`ActivityIndicator` in `Views/Indicator.swift` uses an `animatingCount`
counter so that concurrent downloads sharing the same image view each
increment the count on start and decrement it on stop; the indicator
is hidden only when the count reaches zero. `ImageIndicator` (the
variant that shows a GIF as a loading spinner) does not implement this
counter: `startAnimatingView()` unconditionally shows the view and
`stopAnimatingView()` unconditionally hides it. When two downloads share
the same image view (e.g., a cell is reused while the first download is
still running), the stale download's `stopAnimatingView()` call hides
the indicator while the new download is still in progress. Fix
`Indicator.swift` to add the same `animatingCount` reference-counting
pattern to `ImageIndicator` that `ActivityIndicator` already uses.

### N11: Fix CHANGELOG.md missing entries for cache serialization format changes

The `CHANGELOG.md` does not document the `CacheSerializer` format
change that added scale-factor preservation (N4) or the
`DiskStorage` file-size fix (N3). Users upgrading between minor
versions have no way to know that the on-disk cache format changed
and may encounter deserialization errors from stale cache entries.
The `.github/ISSUE_TEMPLATE.md` also does not ask reporters to
specify their Kingfisher version or whether they have cleared the
disk cache after upgrading. Fix `CHANGELOG.md` to add entries for
both the serializer and disk-storage changes under an
"Important Notes" section, and update `.github/ISSUE_TEMPLATE.md`
to include Kingfisher version, Swift version, and cache-cleared
fields.

## Medium

### M1: Add cache size reporting API to ImageCache

Implement synchronous and asynchronous methods on `ImageCache.swift`
that return the current memory cache entry count, memory byte usage
from `MemoryStorage.swift`, disk cache entry count, and disk byte
usage from `DiskStorage.swift`. Add a `CacheReport` struct to
aggregate the statistics, and integrate reporting into
`KingfisherManager.swift` so callers can display cache usage.

### M2: Implement disk cache migration with versioned metadata

Add a versioned metadata header to disk cache entries so that format
changes across Kingfisher versions don't silently corrupt cached data.
On startup, `DiskStorage` should detect old-format entries and
migrate or discard them. Add a `DiskStorage.MetadataVersion` enum,
update `CacheSerializer.swift` to write version headers, modify
`DiskStorage.swift` to read and validate headers on retrieval,
add a `CHANGELOG.md` entry describing the versioned cache format
and migration behavior, and update `docs/deployment.md` with
guidance on cache migration during app updates.

### M3: Implement download priority escalation for deduplicated requests

When multiple callers request the same URL via `ImageDownloader.swift`,
a single `SessionDataTask` is created in `SessionDelegate.swift`.
If the first caller requests with `.downloadPriority(0.0)` and a
later caller with `.downloadPriority(1.0)`, the URLSession task
retains the original low priority. Implement priority escalation in
`SessionDataTask.swift` that updates the underlying task's priority
to the maximum of all registered callbacks, and wire the escalation
through `ImageDownloader.swift`.

### M4: Add ImagePrefetcher progress reporting with per-URL status

Extend `ImagePrefetcher.swift` to report per-URL prefetch status
(pending, downloading, cached, failed) in addition to the aggregate
progress callback. Add a `PrefetchStatus` enum, update the prefetch
progress handler signature in `ImagePrefetcher.swift` to include
per-URL status, integrate status tracking with `ImageCache.swift`
for cache-hit detection before scheduling downloads, and surface
the enhanced progress via `KFOptionsSetter.swift`.

### M5: Implement cache access cost tracking with per-key statistics

Add hit/miss/byte tracking per cache key so callers can inspect
which images are frequently accessed and how much memory and disk
they consume. Add a `CacheAccessRecord` struct, integrate counters
into `MemoryStorage.swift` and `DiskStorage.swift`, expose an
aggregation API on `ImageCache.swift`, and add a
`.trackCacheAccess` option to `KingfisherOptionsInfo`.

### M6: Add HEIF/HEIC image format support in processor pipeline

Extend the image processing pipeline to detect and decode HEIF/HEIC
images. Update `ImageFormat.swift` to recognize the `heic` magic
bytes, add HEIF decoding in `ImageDrawing.swift` using
`CGImageSourceCreateWithData`, update `CacheSerializer.swift` to
serialize HEIF data, ensure all built-in `ImageProcessor`
implementations in `ImageProcessor.swift` handle HEIF input, and
update `README.md` to list HEIF/HEIC in the supported formats
section.

### M7: Implement animated WebP playback in AnimatedImageView

Add WebP animation support to `AnimatedImageView` by implementing
a `WebPFrameDecoder` that extracts frames and durations from WebP
container data. Integrate the decoder into `AnimatedImageView.swift`
alongside the existing GIF path, update `ImageFormat.swift` to
detect WebP, and modify `GIFAnimatedImage.swift` to share the
frame-provider protocol with the new WebP decoder.

### M8: Add bandwidth-throttling and typed priority levels to ImageDownloader

`ImageDownloader.swift` already exposes a `.downloadPriority(Float)`
option that maps directly to `URLSessionTask.priority`. However, there
is no bandwidth-throttling mechanism and no higher-level typed priority
enum. Add a `DownloadPriority` enum with `.high`, `.normal`, and `.low`
cases that map to the corresponding `URLSessionTask` priority constants,
replace the raw `Float` option with the new enum in
`KingfisherOptionsInfo.swift`, add a bandwidth throttle controller in
`SessionDelegate.swift` that caps the total bytes-per-second across
concurrent tasks, and expose a `.bandwidthLimit(Int)` option in
`KingfisherOptionsInfo.swift`.

### M9: Add placeholder transition with blur-to-sharp animation

Implement a placeholder strategy where a blurred low-resolution
version of the image is shown immediately, then cross-fades to the
full-resolution image when the download completes. Add a
`BlurPlaceholder` conforming to `Placeholder` in `Placeholder.swift`,
implement the blur generation in `ImageDrawing.swift`, wire the
transition into `ImageTransition.swift`, and update
`ImageView+Kingfisher.swift` to apply the transition sequence.

### M10: Add caller-supplied priority queue to ImagePrefetcher

`ImagePrefetcher.swift` already checks both memory and disk caches
before scheduling a download (via `imageCachedType` in
`startPrefetching`) and skips already-cached URLs. However, all
pending downloads are queued with equal priority; there is no way for
callers to specify that certain URLs should be fetched before others.
Add a `PrefetchPriority` enum and a priority queue data structure,
update `ImagePrefetcher.swift` to accept per-URL priorities and drain
the queue in priority order, modify `ImageDownloader.swift` to forward
the per-task priority to the underlying `URLSessionTask`, and update
`KingfisherManager.swift` to surface prefetch priority through the
existing options system.

### M11: Update docs/architecture.md, README.md, and CONTRIBUTING.md with missing coverage

The `docs/architecture.md` file does not document
`FormatIndicatedCacheSerializer` or explain how it differs from
`DefaultCacheSerializer`. The `README.md` "Features" list does not
mention progressive JPEG support (`ImageProgressive.swift`). The
`CONTRIBUTING.md` file does not describe how to run the test suite on
different platforms (iOS vs macOS vs tvOS vs watchOS) or reference the
`fastlane/Fastfile` CI configuration. Update `docs/architecture.md`
with a `FormatIndicatedCacheSerializer` entry in the storage layer
table and an explanation of format-indicated serialization, update
`README.md` with progressive JPEG in the features list, and update
`CONTRIBUTING.md` with platform-specific testing instructions
referencing `fastlane/Fastfile` and the
`.github/workflows/test.yaml` matrix.

## Wide

### W1: Add comprehensive image request tracing and debug logging

Implement a `KingfisherLogger` protocol with configurable log levels
that provides structured trace logging for the complete image loading
lifecycle — request initiation, cache lookup results (memory/disk
hit/miss), download start with request headers, download progress,
processing step durations, and cache store confirmation. Integrate
trace points into `KingfisherManager.swift`, `ImageDownloader.swift`,
`SessionDelegate.swift`, `ImageCache.swift`, `MemoryStorage.swift`,
`DiskStorage.swift`, and `ImageProcessor.swift`. Add a built-in
`ConsoleLogger` implementation and expose `.logger(KingfisherLogger)`
option in `KingfisherOptionsInfo.swift`.

### W2: Implement image pipeline with composable async/await stages

Replace the callback-based download-process-cache pipeline with a
modern Swift concurrency pipeline using `async`/`await` and
structured concurrency. Add an `ImagePipeline` actor that composes
download, process, and cache stages as `AsyncSequence` steps.
Refactor `KingfisherManager.swift` to use the pipeline,
update `ImageDownloader.swift` and `ImageCache.swift` to expose
`async` APIs, adapt `ImageProcessor.swift` for async processing,
and update all `Views/` extensions to call the new async pipeline.

### W3: Add image loading metrics and performance dashboard

Implement an `ImageLoadMetrics` subsystem that records download
latency, cache hit rates, processing durations, memory pressure
events, and error frequencies. Add a `MetricsCollector` class,
integrate collection points into `ImageDownloader.swift`,
`ImageCache.swift`, `MemoryStorage.swift`, `DiskStorage.swift`,
`ImageProcessor.swift`, and `KingfisherManager.swift`. Create an
in-app `MetricsDashboardView` (SwiftUI) that surfaces live data
and historical charts. Expose a `.metricsEnabled` option in
`KingfisherOptionsInfo.swift`.

### W4: Implement secure image cache with encryption-at-rest and integrity checks

Add optional AES-256 encryption for disk-cached images and SHA-256
integrity verification on reads. Create `EncryptedDiskStorage` that
wraps `DiskStorage` with a `CryptoKit`-based encryption layer, update
`CacheSerializer.swift` to prepend HMAC tags, modify `ImageCache.swift`
to select encrypted vs. plain storage based on a new
`.encryptedDiskCache(key:)` option in `KingfisherOptionsInfo.swift`.
Add key rotation support in `DiskStorage.swift` and migration logic
for re-encrypting existing entries.

### W5: Implement image request batching with transaction semantics

Add an `ImageTransaction` that groups multiple image load requests
into a batch that succeeds or fails atomically. If any image in the
batch fails to load, all successfully loaded images are rolled back
from cache. Implement the transaction coordinator in a new
`ImageTransaction` class, integrate batch lifecycle management into
`KingfisherManager.swift`, add transactional cache operations in
`ImageCache.swift` with rollback support in `MemoryStorage.swift`
and `DiskStorage.swift`, wire batch progress through
`ImagePrefetcher.swift`, update `KFOptionsSetter.swift` for
transaction configuration, and add `.transaction(ImageTransaction)`
to `KingfisherOptionsInfo.swift`.

### W6: Implement background image processing queue with operation dependencies

Add an `ImageProcessingQueue` that manages processor operations as a
dependency graph, enabling complex multi-step processing pipelines
(resize → crop → watermark → compress) with parallel independent
steps. Create the queue scheduler, update `ImageProcessor.swift` to
declare input/output types and dependencies, integrate the queue into
`KingfisherManager.swift`, add progress tracking in
`KFOptionsSetter.swift`, modify `ImageDrawing.swift` and
`Filter.swift` to participate as queue operations, and expose
pipeline configuration in `KingfisherOptionsInfo.swift`.

### W7: Add intelligent cache eviction with usage-frequency and size-awareness

Replace the simple LRU eviction in `MemoryStorage` and date-based
expiration in `DiskStorage` with a frequency-aware eviction policy
(LFU/ARC hybrid). Add access-frequency tracking in
`MemoryStorage.swift`, size-weighted scoring in `DiskStorage.swift`,
a unified eviction coordinator in `ImageCache.swift`, cache analytics
in a new `CacheAnalytics` struct, configurable eviction strategies
in `KingfisherOptionsInfo.swift`, and memory-pressure integration
via `DispatchSource.makeMemoryPressureSource` in
`KingfisherManager.swift`.

### W8: Implement cross-process cache sharing via App Groups

Add support for sharing the disk cache across multiple app targets
(main app, extensions, widgets) using App Groups. Modify
`DiskStorage.swift` to use a shared container URL, add file
coordination via `NSFileCoordinator` for concurrent access, implement
a `SharedCacheCoordinator` that handles cross-process notifications
via `CFNotificationCenter`, update `ImageCache.swift` to support
named shared cache instances, add `.sharedCacheIdentifier(String)`
to `KingfisherOptionsInfo.swift`, and update `KingfisherManager.swift`
to handle cache invalidation across processes.

### W9: Add stale-while-revalidate caching strategy with conditional requests

Implement a caching strategy that serves stale cached images
immediately while asynchronously revalidating them with the server
using HTTP conditional requests (`If-Modified-Since`, `ETag`).
Create a `RevalidationPolicy` protocol with built-in
implementations, update `ImageDownloader.swift` to send conditional
requests via `SessionDelegate.swift`, modify `DiskStorage.swift` to
store HTTP response headers alongside cached data, add revalidation
state tracking in `ImageCache.swift`, update
`KingfisherManager.swift` to orchestrate the stale-serve-then-
revalidate flow, and expose `.revalidationPolicy(RevalidationPolicy)`
option in `KingfisherOptionsInfo.swift`.

### W10: Implement server-driven image variant selection with content negotiation

Add automatic image format and resolution negotiation based on
server-provided metadata (`Accept`, `Content-DPR`, `Vary` headers).
Create a `ContentNegotiator` that inspects HTTP response headers to
select optimal variants, update `ImageDownloader.swift` to send
client hints, modify `SessionDelegate.swift` to parse negotiation
headers, extend `Resource.swift` with variant descriptors, update
`ImageFormat.swift` for dynamic format selection, integrate variant
caching into `ImageCache.swift` and `DiskStorage.swift` with
per-variant cache keys, and add `.enableContentNegotiation` to
`KingfisherOptionsInfo.swift`.

### W11: Overhaul documentation, CONTRIBUTING.md, and CI/distribution pipeline

The `docs/` directory contains seven markdown files that are not
cross-linked and have no index page (`docs/README.md` does not exist).
The `docs/build-system.md` file covers Swift Package Manager and
CocoaPods but does not explain the `Package@swift-5.9.swift`
version-specific manifest or why it exists alongside `Package.swift`.
The `docs/testing.md` guide mentions `fastlane` commands but does not
explain how the `.github/workflows/test.yaml` CI matrix is structured
or which destinations map to which platforms. The `CONTRIBUTING.md`
file contains almost no technical content — it does not describe
branch naming conventions, the release process, or how to run tests
on any platform. Add a `docs/README.md` index page linking all
documentation files, update `docs/build-system.md` to explain the
dual `Package.swift` / `Package@swift-5.9.swift` manifest setup,
update `docs/testing.md` with a CI matrix overview referencing
`.github/workflows/test.yaml`, and update `CONTRIBUTING.md` with
branching conventions, release process steps, and platform-specific
testing instructions.
