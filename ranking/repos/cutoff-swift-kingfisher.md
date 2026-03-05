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

### N2: Fix MemoryStorage not respecting totalCostLimit after config change

Changing `MemoryStorage.Config.totalCostLimit` at runtime does not
propagate to the underlying `NSCache` instance because the cache's
`totalCostLimit` is only set during initialization. Fix
`MemoryStorage.swift` so that updates to the config's cost limit
are applied to the `NSCache` immediately.

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

### N6: Fix GIFAnimatedImage frame duration defaulting to zero for malformed GIFs

When a GIF frame's delay time is missing or set to zero in the frame
metadata, `GIFAnimatedImage` uses `0.0` as the duration, causing the
animation loop to spin at maximum speed. Fix `GIFAnimatedImage.swift`
to clamp the minimum frame duration to 0.1 seconds per the GIF spec
recommendation.

### N7: Fix RequestModifier not applied for redirected URLs

When a download request is redirected (HTTP 301/302), the
`RequestModifier` set in `KingfisherOptionsInfo` is only applied to
the original request, not to the redirected request. Fix
`SessionDelegate.swift` to re-apply the modifier in the
`urlSession(_:task:willPerformHTTPRedirection:)` delegate method.

### N8: Fix ImageCache.retrieveImage returning expired disk entry

`ImageCache.retrieveImage()` checks disk cache without verifying
the entry's expiration date, returning stale images when the disk
cache has not yet been cleaned. Fix the retrieval path in
`ImageCache.swift` to check the file's expiration metadata before
returning a disk-cached result.

### N9: Fix CallbackQueue.asyncAfter not dispatching on correct queue

`CallbackQueue.asyncAfter(deadline:execute:)` ignores the queue
case and always dispatches on `DispatchQueue.main`. Fix
`CallbackQueue.swift` to dispatch on the queue specified by the
enum case (`.mainAsync`, `.untouch`, `.dispatch(queue)`).

### N10: Fix String+MD5 producing incorrect hash for emoji strings

`String+MD5.kf.md5` computes the hash using the string's UTF-16
representation length instead of its UTF-8 byte count, producing
incorrect cache keys for strings containing multi-byte emoji
characters. Fix the MD5 computation in `String+MD5.swift` to use
the UTF-8 encoded byte buffer.

## Medium

### M1: Add progressive JPEG loading with incremental display

Implement progressive JPEG support so that partially downloaded data
is decoded and displayed incrementally as bytes arrive. Add an
`ImageProgressive` struct to configure scan-line thresholds. Wire
the progressive decoding into `SessionDelegate.swift`'s
`urlSession(_:dataTask:didReceive:)` callback, update
`ImageDownloader.swift` to pass partial images to a new
delegate callback, and extend `KingfisherOptionsInfo` with a
`.progressiveJPEG(ImageProgressive)` option.

### M2: Implement disk cache migration with versioned metadata

Add a versioned metadata header to disk cache entries so that format
changes across Kingfisher versions don't silently corrupt cached data.
On startup, `DiskStorage` should detect old-format entries and
migrate or discard them. Add a `DiskStorage.MetadataVersion` enum,
update `CacheSerializer.swift` to write version headers, and modify
`DiskStorage.swift` to read and validate headers on retrieval.

### M3: Add retry policy with exponential backoff for failed downloads

Implement a configurable `RetryStrategy` protocol with a built-in
`DelayRetryStrategy` that supports exponential backoff, maximum
attempt count, and jitter. Integrate the retry logic into
`ImageDownloader.swift`'s download completion path, add a
`.retryStrategy(RetryStrategy)` option to `KingfisherOptionsInfo`,
and update `KingfisherManager.swift` to pass the strategy through
the download pipeline.

### M4: Add low-data-mode support that skips prefetching and reduces quality

Implement detection of the system's Low Data Mode setting and
automatically adjust Kingfisher's behavior: skip all
`ImagePrefetcher` operations, request lower-resolution variants via
`RequestModifier`, and prefer cached images over network fetches.
Changes span `ImagePrefetcher.swift`, `ImageDownloader.swift`,
`KingfisherOptionsInfo.swift` for a `.lowDataModeSource` option,
and `KingfisherManager.swift` for fallback coordination.

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

### W1: Add full SwiftUI lifecycle integration with environment-based configuration

Implement a `KingfisherEnvironment` that propagates configuration
(cache policy, downloader, processors) through SwiftUI's
`EnvironmentValues`. Overhaul `KFImage.swift` to read from the
environment, add `KFAnimatedImage` for SwiftUI GIF support, create
`ImageBinder.swift` integration with `@StateObject`, add view
modifiers for `.kingfisherOptions()`, `.placeholder()`,
`.onProgress()`, and `.onFailure()`. Changes span `SwiftUI/`,
`General/KingfisherOptionsInfo.swift`, `KingfisherManager.swift`,
and `Image/Placeholder.swift`.

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

### W5: Add multi-source image loading with fallback chain

Implement an `ImageSourceChain` that tries multiple sources in order
(memory cache → disk cache → CDN primary → CDN fallback → local
asset) and returns the first successful result. Create the chain
coordinator, update `ImageSource.swift` and `Resource.swift` to
support source arrays, modify `KingfisherManager.swift` to execute
the chain, update `ImageDownloader.swift` to support per-source
timeout and auth, add `.alternativeSources([Source])` to
`KingfisherOptionsInfo.swift`, and update all `Views/` extensions
to accept multi-source resources.

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

### W9: Add full offline mode with request queuing and sync-on-reconnect

Implement an offline mode that queues image download requests when
the network is unavailable and automatically retries them on
reconnection. Add a `NetworkMonitor` using `NWPathMonitor`, create
a persistent request queue in `DiskStorage.swift`, implement queue
draining in `ImageDownloader.swift`, add connection-state-aware
prefetching in `ImagePrefetcher.swift`, update
`KingfisherManager.swift` to coordinate online/offline transitions,
expose `.offlineMode` and `.requestQueuePolicy` options in
`KingfisherOptionsInfo.swift`, and update `SessionDelegate.swift`
to handle mid-download connectivity loss gracefully.

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
