# filp/whoops

| Field | Value |
|-------|-------|
| **URL** | https://github.com/filp/whoops |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Small |
| **Category** | Error handler |
| **Set** | eval |
| **Commit** | `67342bc807854844244f219fb74687fdf2f62e00` |

## Why this repo

- **Clear architecture**: Core error handler (`Run`), exception inspection (`Inspector`, `Frame`, `FrameCollection`), pluggable handlers (`PrettyPageHandler`, `JsonResponseHandler`, `PlainTextHandler`, `XmlResponseHandler`)
- **Rich error display**: Pretty HTML error pages with source code context, data tables, environment details, editor integration, and custom CSS/JS
- **Extensible design**: Handler pipeline, frame filtering, custom inspector factories, and editor protocol support

## Structure overview

```
src/Whoops/
├── Run.php                       # Main error handler: register, handleException/Error/Shutdown
├── RunInterface.php              # Interface for Run
├── Exception/
│   ├── Inspector.php             # Exception chain inspection, frame extraction
│   ├── Frame.php                 # Single stack frame: file, line, class, function, comments
│   ├── FrameCollection.php      # Filterable, mappable collection of frames
│   ├── Formatter.php             # Exception-to-array formatting
│   └── ErrorException.php        # PHP error-to-exception wrapper
├── Handler/
│   ├── Handler.php               # Base handler with return code constants
│   ├── HandlerInterface.php      # Handler contract
│   ├── PrettyPageHandler.php     # HTML error page: editor links, data tables, resources
│   ├── JsonResponseHandler.php   # JSON error response handler
│   ├── PlainTextHandler.php      # Plain text error output with trace formatting
│   ├── XmlResponseHandler.php    # XML error response handler
│   └── CallbackHandler.php       # Closure-based handler wrapper
├── Inspector/
│   ├── InspectorFactory.php      # Default inspector factory
│   ├── InspectorFactoryInterface.php
│   └── InspectorInterface.php    # Inspector contract
├── Util/
│   ├── TemplateHelper.php        # HTML template rendering, escaping, slug generation
│   ├── SystemFacade.php          # System function wrapper for testability
│   ├── HtmlDumperOutput.php      # Symfony VarDumper HTML output
│   └── Misc.php                  # Static utility methods
└── Resources/
    └── views/                    # HTML templates for the pretty error page
```

## Scale indicators

- ~54 PHP source files
- ~7.3K lines of code
- Small focused codebase with clear handler pipeline
- Template-based HTML error page rendering

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `Run::handleException` not respecting handler return `LAST_HANDLER` when multiple handlers are stacked

In `src/Whoops/Run.php`, the `handleException` method iterates through handlers. When a handler returns `Handler::LAST_HANDLER`, subsequent handlers should be skipped. However, when `LAST_HANDLER` is combined with `QUIT` flag (via bitwise OR), the loop breaks on `QUIT` but doesn't check `LAST_HANDLER` independently, so handlers registered after the `LAST_HANDLER` handler still execute if `QUIT` is not set.

### N2: Fix `Frame::getFileLines` returning wrong lines when file has mixed line endings

In `src/Whoops/Exception/Frame.php`, `getFileLines()` uses `SplFileObject` to read lines from the source file. Files with mixed line endings (`\r\n` and `\n`) cause the line mapping to drift, displaying the wrong source code lines in the pretty error page.

### N3: Add `Frame::getColumnNumber` for exceptions that provide column information

The `Frame` class in `src/Whoops/Exception/Frame.php` tracks file and line number but not column number. PHP 8.0+ exceptions from some parsers provide column information in the trace. Add `getColumnNumber()` that extracts column data from the raw frame array when available, returning `null` otherwise.

### N4: Fix `PrettyPageHandler::getEditorHref` not URL-encoding file paths with spaces

In `src/Whoops/Handler/PrettyPageHandler.php`, the `getEditorHref` method generates editor URLs (e.g., `phpstorm://open?file=/path/to/file&line=42`). File paths containing spaces or special characters are not URL-encoded, causing the editor link to break when clicked.

### N5: Fix `Inspector::getFrames` not deduplicating frames from previous exception chain

In `src/Whoops/Exception/Inspector.php`, `getFrames()` collects frames from the current exception and all previous exceptions in the chain. The `FrameCollection::topDiff` is used to avoid duplicate frames, but when the exception chain contains a re-thrown exception that builds a new trace, identical frames from the same file/line appear multiple times.

### N6: Add `JsonResponseHandler` option to include request context in output

The `JsonResponseHandler` in `src/Whoops/Handler/JsonResponseHandler.php` outputs exception type, message, file, line, and trace. Add an `addRequestData()` option that includes the HTTP method, URI, headers, and query parameters in the JSON output, similar to how `PrettyPageHandler` shows environment data.

### N7: Fix `PlainTextHandler` not respecting `$loggerOnly` when outputting to multiple loggers

In `src/Whoops/Handler/PlainTextHandler.php`, the `$loggerOnly` flag should prevent output to stdout when a PSR-3 logger is configured. When multiple loggers are added (the handler only supports one), the flag check happens before the logger call, but if the logger throws, the output is sent to stdout anyway as a fallback.

### N8: Add support for custom frame sorting in `FrameCollection`

The `FrameCollection` class in `src/Whoops/Exception/FrameCollection.php` supports `filter()` and `map()` but not sorting. Add a `sort(callable $comparator)` method that reorders frames by a user-defined comparator. This enables use cases like sorting application frames before vendor frames.

### N9: Fix `Run::silenceErrorsInPaths` not matching paths with Windows backslash separators

In `src/Whoops/Run.php`, `silenceErrorsInPaths` uses regex patterns to match file paths. On Windows, file paths use backslash separators (`C:\Users\...`) but the patterns are written with forward slashes. The matching fails to silence errors from the intended paths.

### N10: Add `setExceptionMessage` method to `PrettyPageHandler` for message overriding

The `PrettyPageHandler` always displays the exception's original message. Add `setExceptionMessage(callable $formatter)` that allows transforming or replacing the displayed exception message (e.g., to redact sensitive data from database connection error messages) without modifying the exception itself.

## Medium

### M1: Implement dark mode support for `PrettyPageHandler`

Add a dark mode theme to the pretty error page. Detect system preference via `prefers-color-scheme` CSS media query and add a toggle button. Create dark mode CSS variables, update all templates in `Resources/views/` to use CSS custom properties. Add `setTheme('dark'|'light'|'auto')` API method.

### M2: Add source code syntax highlighting to the pretty error page

Implement syntax highlighting for the source code displayed in `frame_code.html.php`. Detect the language from the file extension and apply highlighting using a client-side highlighter bundled as inline JavaScript. Support PHP, JavaScript, Python, Ruby, and HTML/Twig. Update `TemplateHelper` and frame code template.

### M3: Implement exception fingerprinting for error grouping

Add an `ExceptionFingerprint` class that generates a stable identifier for each exception based on exception class, message pattern, and top N application frames. Add `Inspector::getFingerprint()` and include the fingerprint in all handler outputs. Support custom fingerprinting strategies via a callback.

### M4: Add error occurrence rate tracking within a request lifecycle

Implement tracking of repeated error patterns within a single request, including the same error being triggered from different call sites. Add `Run::getErrorStatistics()` that returns a summary of errors by type, frequency, and unique stack traces. Display the statistics in `PrettyPageHandler` as an additional panel.

### M5: Implement searchable stack trace in the pretty error page

Add client-side search functionality to the pretty error page that filters visible frames based on search input. Search across file path, class name, function name, and frame comments. Add keyboard shortcut (Ctrl+F) activation, result counting, and match highlighting. Changes span the JavaScript, CSS, and HTML templates in `Resources/views/`.

### M6: Add exception context enrichment middleware

Implement a `ContextEnricher` that automatically adds contextual data to exceptions: request parameters, session data, authenticated user info, database query log, and memory usage. Add `Run::addContextProvider(string $name, callable $provider)` API. Enrich all handlers' output with gathered context. Changes span `Run.php`, `Inspector`, and handler implementations.

### M7: Implement error notification integration

Add a `NotificationHandler` that sends error notifications via configurable channels (email, Slack webhook, generic HTTP webhook). Support notification throttling (max one per N seconds for the same fingerprint), severity filtering, and custom payload formatting. Add channel configuration API and integrate with the handler pipeline.

### M8: Add interactive REPL console to the pretty error page

Implement a client-side interactive console in the pretty error page that can evaluate PHP expressions in the context of the error frame. Add a WebSocket-based evaluation endpoint, variable inspection for the current scope, and output display within the error page. Changes span `PrettyPageHandler`, templates, add JavaScript console UI, and an evaluation bridge.

### M9: Implement error page localization

Add multi-language support for the pretty error page's UI labels, section headings, and built-in messages. Create a `Translator` utility with language file loading from JSON. Add `PrettyPageHandler::setLanguage($locale)` API. Provide translations for English, French, German, Spanish, and Chinese. Changes span `PrettyPageHandler`, `TemplateHelper`, and templates.

### M10: Add custom error page layouts via template overrides

Implement a template override system where users can provide their own HTML templates to replace the default layout. Add `PrettyPageHandler::setTemplate($name, $path)` for individual view replacement and `setLayoutPath($dir)` for complete layout override. Support template inheritance and variable injection. Changes span `PrettyPageHandler`, `TemplateHelper`, and resource path resolution.

## Wide

### W1: Implement structured error reporting with multiple output backends

Add a structured error reporting system that captures errors, enriches them with context, and dispatches to multiple backends simultaneously (Sentry, Bugsnag, file log, database). Implement a `ReporterRegistry`, backend adapters, unified error envelope format, and async dispatch. Changes span `Run.php`, handlers, inspector, add `Reporter/` module with adapter interfaces and implementations.

### W2: Add error page asset pipeline with bundling and versioning

Implement an asset pipeline for the pretty error page that compiles, bundles, and versions CSS and JavaScript. Support SCSS preprocessing, JavaScript minification, source maps, and cache-busted URLs. Add a build step that embeds compiled assets. Changes span `PrettyPageHandler`, `TemplateHelper`, all `Resources/views/` templates, and add a build/asset infrastructure.

### W3: Implement error trend analysis and deduplication system

Add persistent error tracking that stores error occurrences, deduplicates by fingerprint, tracks occurrence rates, and identifies trending errors. Implement a storage backend (SQLite), a query API for error statistics, and a dashboard handler that visualizes trends. Changes span `Run.php`, add a `Storage/` module, `Analytics/` module, and a `DashboardHandler`.

### W4: Add framework integration adapters for Laravel, Symfony, and Slim

Implement framework-specific adapters that integrate Whoops as the error handler with framework-aware context enrichment. Add middleware for each framework, automatic dependency injection setup, framework-specific data tables (routes, config, container), and graceful fallback when the framework is not available. Changes span handler configuration, context enrichment, and add `Integration/` module with per-framework adapters.

### W5: Implement error replay and reproduction toolkit

Add the ability to capture all state needed to reproduce an error (request data, environment, session, files) and package it into a shareable replay bundle. Implement `ReplayHandler` that serializes error context, a CLI tool that loads replay bundles and re-executes the error path, and a web UI for browsing saved replays. Changes span handlers, inspector, serialization, and add `Replay/` module.

### W6: Add application-aware error grouping with source mapping

Implement error grouping that understands application structure: group by module/namespace, track error frequency per source file, and map minified/compiled stack traces back to original sources using source maps. Support PHP, JavaScript, and compiled template source maps. Changes span `Inspector`, `Frame`, `FrameCollection`, handlers, and add a `SourceMap/` module.

### W7: Implement comprehensive error testing and simulation framework

Add a testing framework that lets developers simulate and test error handling: trigger specific exceptions, verify handler responses, test frame filtering, snapshot error pages, and assert on handler behavior. Support PHPUnit and Pest integration. Changes span `Run`, handlers, inspector, and add a `Testing/` module with assertion helpers, exception simulators, and response validators.

### W8: Add API error response standardization with Problem Details (RFC 7807)

Implement RFC 7807 Problem Details format for API error responses. Add a `ProblemDetailsHandler` that generates standardized JSON/XML error responses with type URI, title, status, detail, and instance. Support custom error type registries, extension members, and content negotiation. Changes span handler pipeline, add `ProblemDetails/` module, formatter integration, and documentation.

### W9: Implement distributed error correlation for microservice architectures

Add distributed tracing support that correlates errors across microservice boundaries. Propagate trace IDs through HTTP headers, link upstream/downstream errors, and display the full error chain across services. Add `DistributedContextHandler`, trace ID propagation middleware, and a timeline view in the error page. Changes span `Run`, handlers, middleware, add `Distributed/` module.

### W10: Add progressive error page with lazy-loaded panels and performance profiling

Implement a progressive error page that loads instantly with critical information and lazily loads additional panels (environment, data tables, full trace) via AJAX. Add performance profiling that measures time spent in each panel's data collection. Support panel prioritization and custom lazy panels. Changes span `PrettyPageHandler`, all templates, `TemplateHelper`, add JavaScript lazy-loading infrastructure, and a profiling module.
