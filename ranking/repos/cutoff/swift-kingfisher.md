# onevcat/Kingfisher

| Field | Value |
|-------|-------|
| **URL** | https://github.com/onevcat/Kingfisher |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Medium (~50 source files, ~15K LOC) |
| **Category** | Image downloading and caching |
| **Set** | Cutoff |

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

### N1: Fix ImageDownloader not cancelling duplicate requests on timeout

When two callers request the same URL and the first request times out,
the second caller's completion handler is never invoked because the
download task's session delegate entry is removed on timeout without
notifying pending callbacks. Fix the timeout handling in
`SessionDelegate.swift` to invoke all registered completion handlers
for the URL before removing the entry.

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

### N3: Fix DiskStorage calculating wrong file size for expiration check

`DiskStorage` uses `FileManager.attributesOfItem(atPath:)` to read
file sizes for the total-size expiration policy, but it reads
`FileAttributeKey.size` as an `Int` instead of `UInt64`, causing
overflow on large caches. Fix the size calculation in
`DiskStorage.swift` to use the correct numeric type.

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

### N6: Fix GIFAnimatedImage not validating nil frames from corrupt image sources

When `GIFAnimatedImage` in `Image/GIFAnimatedImage.swift` decodes
frames from a `CGImageSource`, it uses `CGImageSourceGetCount()` to
determine the frame count and then iterates up to that count calling
`CGImageSourceCreateImageAtIndex()`. When the image source is created
from corrupt or truncated GIF data, the reported frame count may
exceed the actual number of decodable frames. Frames that fail to
decode return `nil`, which is passed through without validation,
causing `nil` entries in the frame array and a crash during animation
playback. Fix `GIFAnimatedImage.swift` to validate each frame result
and skip or terminate early if a `nil` frame is encountered.

### N7: Fix SessionDataTask not cancelling underlying URLSession task when last callback is removed

When multiple callers download the same URL and each cancels
independently, `cancel(token:)` in `SessionDataTask.swift` removes
the caller's callback from `callbacksStore` but does not check
whether the store is now empty. The orphaned URLSession data task
continues downloading data that no caller wants, wasting bandwidth.
Fix `cancel(token:)` to also cancel the underlying task when no
callbacks remain.

### N8: Fix ImageCache.retrieveImage returning expired disk entry

`ImageCache.retrieveImage()` checks disk cache without verifying
the entry's expiration date, returning stale images when the disk
cache has not yet been cleaned. Fix the retrieval path in
`ImageCache.swift` to check the file's expiration metadata before
returning a disk-cached result.

### N9: Fix AnimatedImageView not stopping display link when removed from view hierarchy

`AnimatedImageView` in `Views/AnimatedImageView.swift` uses a
`CADisplayLink` (or platform equivalent via `DisplayLinkCompatible`)
to drive frame-by-frame animation. The display link is invalidated
in `deinit`, but the view does not override `didMoveToSuperview()`
or `didMoveToWindow()` to pause or invalidate the link when removed
from the view hierarchy. When an `AnimatedImageView` is removed from
its superview but retained by other references, the display link
continues firing on every screen refresh, consuming CPU for invisible
animation. Fix `AnimatedImageView.swift` to pause the display link
when `window` becomes `nil` and resume when re-added.

### N10: Fix Indicator view not removed from superview after image load cancellation

When an image view's download task is cancelled via
`cancelDownloadTask()`, the `Indicator`'s `stopAnimatingView()` is
called but the indicator view itself remains in the view hierarchy
as an invisible subview. Over repeated load-cancel cycles, orphaned
indicator views accumulate in `Indicator.swift`. Fix the indicator to
remove its view from the superview when animation stops due to
cancellation.

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
update `CacheSerializer.swift` to write version headers, and modify
`DiskStorage.swift` to read and validate headers on retrieval.

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
serialize HEIF data, and ensure all built-in `ImageProcessor`
implementations in `ImageProcessor.swift` handle HEIF input.

### M7: Implement animated WebP playback in AnimatedImageView

Add WebP animation support to `AnimatedImageView` by implementing
a `WebPFrameDecoder` that extracts frames and durations from WebP
container data. Integrate the decoder into `AnimatedImageView.swift`
alongside the existing GIF path, update `ImageFormat.swift` to
detect WebP, and modify `GIFAnimatedImage.swift` to share the
frame-provider protocol with the new WebP decoder.

### M8: Add request-priority and bandwidth-throttling to ImageDownloader

Implement download priority levels (`.high`, `.normal`, `.low`) and
a bandwidth throttle that limits concurrent bytes-per-second. Add a
`DownloadPriority` enum, integrate priority into the URLSession task
configuration in `ImageDownloader.swift`, add a throttle controller
in `SessionDelegate.swift` that schedules task resumptions, and
expose `.downloadPriority` and `.bandwidthLimit` options in
`KingfisherOptionsInfo.swift`.

### M9: Add placeholder transition with blur-to-sharp animation

Implement a placeholder strategy where a blurred low-resolution
version of the image is shown immediately, then cross-fades to the
full-resolution image when the download completes. Add a
`BlurPlaceholder` conforming to `Placeholder` in `Placeholder.swift`,
implement the blur generation in `ImageDrawing.swift`, wire the
transition into `ImageTransition.swift`, and update
`UIImageView+Kingfisher.swift` to apply the transition sequence.

### M10: Implement cache-aware ImagePrefetcher with priority queue

Redesign `ImagePrefetcher` to check both memory and disk caches
before scheduling downloads, skip already-cached URLs, and order
pending downloads by a caller-supplied priority. Add a priority
queue data structure, update `ImagePrefetcher.swift` to query
`ImageCache` before enqueuing, modify `ImageDownloader.swift` to
accept priority-ordered tasks, and update `KingfisherManager.swift`
to surface prefetch progress to callers.

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
