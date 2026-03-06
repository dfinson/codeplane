# jekyll/jekyll

| Field | Value |
|-------|-------|
| **URL** | https://github.com/jekyll/jekyll |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Medium (multi-module project) |
| **Category** | Static site generator |
| **Set** | ranker-gate |
| **Commit** | `491d4737611298a54d82c91118a40563a00d485f` |

## Why this repo

- **Multi-module with clear boundaries**: Markdown/Liquid template rendering,
  content pipeline (readers, generators, converters, renderers), plugin system,
  development server, incremental build, configuration management — each a
  distinct subsystem requiring navigation between them.
- **Well-structured**: Source under `lib/jekyll/` with clear per-concern files
  and directories. The content pipeline has explicit phases (read → generate →
  render → write) reflected in the code structure.
- **Rich history**: 12K+ commits, 15+ years of development. One of the most
  widely used static site generators. Varied PR patterns from bug fixes to
  plugin system changes.
- **Permissive**: MIT license.

## Structure overview

```
lib/jekyll/
├── site.rb              # Core Site object (orchestrates pipeline)
├── page.rb              # Page content type
├── document.rb          # Collection document type
├── collection.rb        # Content collections
├── configuration.rb     # Config loading and defaults
├── readers/             # Content readers
│   ├── data_reader.rb   # _data/ directory reader
│   ├── page_reader.rb   # Page file reader
│   └── post_reader.rb   # Blog post reader
├── generators/          # Content generators
│   └── pagination.rb    # Pagination generator
├── converters/          # Format converters
│   ├── markdown.rb      # Markdown → HTML
│   └── identity.rb      # Pass-through
├── renderers/           # Template renderers
├── tags/                # Liquid template tags
├── filters.rb           # Liquid template filters
├── commands/            # CLI commands (build, serve, new)
├── server/              # Development server
└── plugin_manager.rb    # Plugin loading and lifecycle
```

## Scale indicators

- ~100 Ruby source files
- ~20K lines of code
- 2-3 levels of module nesting
- Clear pipeline phase boundaries

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix incremental build not detecting `_data` file changes

When a YAML file in `_data/` is modified, the incremental build
(`jekyll build --incremental`) does not regenerate pages that reference
the changed data via `site.data.*`. The dependency tracker does not
record data file dependencies. Fix the incremental build to track
which pages reference which data files and regenerate accordingly.

### N2: Add `where_exp` array filter support for nested properties

The `where_exp` Liquid filter works for top-level properties but fails
for nested properties accessed with dot notation (e.g.,
`site.posts | where_exp: "p", "p.author.name == 'Alice'"`). Fix the
expression evaluator to support dot-notation property access within
filter expressions.

### N3: Fix `--livereload` not injecting script on non-HTML content types

The LiveReload script injection checks for `Content-Type: text/html`
but some templates produce HTML with a different content type (e.g.,
`text/xml` for XHTML). The injection should check the file extension
(`.html`, `.htm`) in addition to the Content-Type header.

### N4: Fix `permalink` template variables not URL-encoded

When a document's title contains special characters (e.g., `C++ Guide`),
the permalink template `:title` placeholder inserts the raw string
without URL-encoding, producing broken paths like `/posts/C++ Guide/`.
Fix the permalink URL builder in `lib/jekyll/url.rb` to properly encode
reserved URI characters in interpolated template variables while
preserving `/` separators.

### N5: Fix `excerpt_separator` not respected in collection documents

The `excerpt_separator` front matter key works correctly for posts but
is ignored for documents in custom collections. The `Document#extract_excerpt`
method only applies separator-based splitting when the collection is
`posts`. Fix it to respect `excerpt_separator` for all collections that
have `output: true`.

### N6: Fix timezone handling in `date_to_xmlschema` filter

The `date_to_xmlschema` Liquid filter produces incorrect offsets for
dates during DST transitions when the site's `timezone` config differs
from the system timezone. The filter converts to the site timezone
after formatting instead of before. Fix the filter in `lib/jekyll/filters.rb`
to apply the timezone conversion before producing the ISO 8601 string.

### N7: Fix `Jekyll::Reader` not filtering `_data` subdirectory entries with `exclude` config

The `DataReader` in `lib/jekyll/readers/data_reader.rb` reads all YAML,
JSON, and CSV files under `_data/` recursively but does not apply the
site's `exclude` configuration patterns to files within `_data/`
subdirectories. A file like `_data/test/fixtures.yml` cannot be
excluded via `exclude: ["_data/test"]` in `_config.yml` because the
`EntryFilter` is not consulted during data directory traversal. Fix
the data reader to apply `EntryFilter` checks defined in
`lib/jekyll/entry_filter.rb` to files and subdirectories within `_data/`.

### N8: Fix Markdown converter not preserving `{:target}` attribute syntax

The Kramdown Markdown converter strips inline attribute lists (IALs)
like `{:target="_blank"}` on links when `input: GFM` mode is enabled.
The GFM parser's link handling bypasses the IAL attachment step. Fix the
converter configuration in `lib/jekyll/converters/markdown/kramdown_parser.rb`
to preserve IAL processing in GFM mode.

### N9: Fix `Jekyll::StaticFile#destination_rel_dir` ignoring collection permalink overrides

When a static file belongs to a collection with a custom `permalink`
pattern, `StaticFile#destination_rel_dir` in `lib/jekyll/static_file.rb`
computes the output path using only the file's relative directory from
the source, ignoring the collection's permalink template. This causes
static files (images, PDFs) within a collection to be written to
unexpected paths when the collection uses a custom permalink like
`/docs/:title/`. Fix `destination_rel_dir` to respect the owning
collection's configured output directory structure.

### N10: Fix `jsonify` filter producing invalid JSON for `nil` values in hashes

The `jsonify` Liquid filter serializes Ruby `nil` values in nested hashes
as empty strings instead of JSON `null`. The custom serializer in
`filters.rb` converts values to strings before JSON encoding. Fix it
to pass `nil` through as `null` in the JSON output.

## Medium

### M1: Implement asset pipeline with fingerprinting

Add built-in asset processing: CSS minification, JavaScript minification,
image optimization, and content-hash fingerprinting for cache busting.
Assets in `_assets/` should be processed and output with fingerprinted
filenames (e.g., `app-a1b2c3.css`). Add an `{% asset %}` Liquid tag
that resolves to the fingerprinted path. Support source maps for CSS
and JS.

### M2: Add internationalization (i18n) support

Implement multi-language site generation. Support `_i18n/` locale files
(YAML), a `{% t key %}` translation tag, locale-prefixed URLs
(`/en/about/`, `/fr/about/`), language switcher helper, and per-locale
front matter defaults. Generate separate page instances for each
configured locale. Add `hreflang` link tag generation for SEO.

### M3: Implement content relationship system

Add support for defining relationships between content types beyond
the current categories and tags. Support `has_many`, `belongs_to`,
and `many_to_many` relationships defined in collection configuration.
Related documents should be accessible in Liquid templates as lazy-loaded
collections. Add a `related_posts` improvement that uses content
similarity (TF-IDF on post body) instead of just date proximity.

### M4: Add content validation framework

Implement a validation system that checks content integrity before
building. Support configurable validators in `_config.yml`: required
front matter fields per collection, broken internal link detection
(cross-referencing `site.pages` and `site.documents`), image reference
validation against `site.static_files`, and schema validation for
`_data/` files against JSON Schema definitions in `_schemas/`. Report
all errors with file and line numbers before the build proceeds.

### M5: Implement draft workflow with preview URLs

Add a draft management workflow beyond the current `_drafts/` folder.
Support draft statuses (`draft`, `review`, `scheduled`) in front matter.
Add a `jekyll serve --drafts` mode that serves drafts at predictable
preview URLs with a configurable token prefix (e.g., `/preview/TOKEN/post-slug`).
Implement scheduled publishing where posts with `status: scheduled` and
a future `date` are automatically moved from `_drafts/` to `_posts/`
by a `jekyll publish --scheduled` command.

### M6: Add structured logging with build profiling

Replace Jekyll's ad-hoc `$stdout` logging with a structured logger
supporting log levels, JSON output format, and build profiling. Track
time spent in each pipeline phase (read, generate, render, write) and
per-converter/generator timing. Add a `--profile` flag that outputs a
build performance report showing the slowest pages, converters, and
generators. Store profiling data in `.jekyll-cache/profile.json` for
trend analysis across builds.

### M7: Implement cross-collection Liquid query filters

Add Liquid filters that enable querying across multiple collections
in templates. Implement a `collection_items` filter in
`lib/jekyll/filters.rb` that retrieves documents from a named
collection with support for filtering and sorting:
`{{ site | collection_items: "projects", "date" }}`. Add a
`merge_collections` filter that combines documents from multiple
collections into a single sorted array. Add backing collection
access logic in `lib/jekyll/collection.rb` and expose the new
capabilities through `lib/jekyll/drops/collection_drop.rb`. Support
the same `where` and `sort` semantics as the existing array filters.

### M8: Add front matter cascade system

Implement a cascading front matter defaults system that goes beyond the
current `defaults` config. Support `_defaults.yml` files placed in any
directory that apply front matter values to all content in that directory
and its children, with deeper files overriding shallower ones. Support
glob patterns for selective application (e.g., `applies_to: "*.md"`)
and conditional defaults based on other front matter values (e.g.,
`when: { layout: "post" }`).

### M9: Implement content embedding and transclusion

Add support for embedding content from one page or document inside
another. Implement a `{% render_content page_path %}` Liquid tag that
renders another page's content inline (after Markdown conversion but
without layout wrapping). Support section-level transclusion with named
anchors (`{% render_content "guide.md#installation" %}`). Track
transclusion dependencies for incremental builds and detect circular
references with a clear error message.

### M10: Add SEO metadata generation system

Implement automatic SEO metadata generation integrated into the build
pipeline. Generate `<meta>` tags (description, Open Graph, Twitter Card)
from front matter and content analysis. Auto-generate `sitemap.xml` with
`lastmod` from git commit dates, `robots.txt` from config, and
structured data (JSON-LD) for articles, breadcrumbs, and FAQ pages.
Support per-page overrides via front matter and a global `_seo.yml`
configuration file for defaults.

## Wide

### W1: Add first-class component system

Implement a reusable component system similar to web components. A
component is a directory under `_components/` with a `.html` template,
optional `.css`, optional `.js`, and a `component.yml` declaring props
with types and defaults. Use as `{% component "alert" type="warning" message="..." %}`. Support slotted content, scoped CSS (auto-prefixed),
and component-local data. Extract component CSS/JS into per-page
bundles.

### W2: Implement build parallelism and caching

Refactor the build pipeline to support parallel page rendering and
persistent build caching. Identify independent render tasks (pages
without cross-page data dependencies) and render them in parallel
using Ruby threads or processes. Cache rendered page fragments keyed
by content hash + dependency hash. Support a local build cache
directory and remote cache (for CI). Add a `--jobs=N` flag.

### W3: Add theme inheritance and override system

Implement a multi-level theme inheritance system. A site can specify a
`parent_theme` in its theme gemspec, creating a chain of fallback
directories for layouts, includes, assets, and sass. Override resolution
walks the chain from site → theme → parent_theme for each requested
file. Add a `jekyll theme:diff` command that shows which theme files
are overridden locally. Support theme configuration namespacing so
parent and child themes can define independent config blocks.

### W4: Implement real-time collaborative preview server

Extend the development server with real-time collaboration features.
Add WebSocket-based live preview that pushes incremental DOM diffs
instead of full page reloads. Implement a split-pane editor view
served at `/_editor/` that shows the Markdown source alongside the
rendered preview with synchronized scrolling. Support multiple
simultaneous browser connections with shared navigation state and
a visual indicator showing which content file is being edited.

### W5: Add content migration framework

Implement a pluggable content migration system for importing from other
platforms. Add a `jekyll migrate` command with provider adapters for
WordPress (XML export), Ghost (JSON), Hugo (content directory), and
generic RSS/Atom feeds. Each adapter maps source content to Jekyll's
collection/front-matter structure, downloads and rewrites image
references to local paths, converts platform-specific shortcodes to
Liquid equivalents, and generates a migration report with warnings for
unmapped features.

### W6: Implement dependency-aware smart rebuild

Replace the current incremental build with a full dependency graph system.
Track dependencies at the Liquid template level: which variables, includes,
layouts, data files, and collection queries each page uses. On file change,
walk the dependency graph to identify the minimal set of pages requiring
re-render. Persist the dependency graph in `.jekyll-cache/deps.msgpack`.
Support `--explain` flag to print why each page was or was not rebuilt.
Handle layout chain invalidation and data file fan-out correctly.

### W7: Add multi-format output pipeline

Extend Jekyll to generate multiple output formats from the same source
content. Support `output_formats: [html, pdf, epub, amp]` in config.
Each format has a dedicated converter chain and layout set (e.g.,
`_layouts/post.pdf.html` for PDF rendering via WeasyPrint). Add a
format-aware permalink system that generates `/post/title/` for HTML
and `/post/title.pdf` for PDF. Implement an `{% if format == "amp" %}`
Liquid variable for format-conditional template logic. Run format
pipelines in parallel.

### W8: Implement plugin sandboxing and dependency resolution

Add a plugin isolation system where each plugin runs in a controlled
environment with declared capabilities. Plugins declare dependencies
and required hooks in a `plugin.yml` manifest. The plugin manager
resolves load order from the dependency DAG, detects conflicts
(two plugins hooking the same phase with incompatible priorities),
and provides a restricted API surface per plugin (e.g., a generator
plugin cannot access the server subsystem). Add `jekyll plugin:check`
to validate plugin compatibility before build.

### W9: Add incremental deployment with content diff

Implement a `jekyll deploy` command that syncs only changed files to
a remote destination. Compute a content manifest (path → SHA-256)
for the built site and compare against the previously deployed manifest.
Support deployment targets: S3 (with CloudFront invalidation), SFTP,
rsync, and GitHub Pages API. Add `--dry-run` to preview changes,
`--rollback` to restore the previous manifest, and a deployment
log recording what was pushed and when. Handle deleted files and
redirects for moved content.

### W10: Implement visual regression testing for themes

Add a `jekyll test:visual` command that renders representative pages
from each layout and compares them against baseline screenshots.
Integrate a headless browser (via Ferrum or Selenium) for screenshot
capture. Support responsive breakpoints (mobile, tablet, desktop),
configurable similarity thresholds, and diff image generation
highlighting changed regions. Store baselines in `_test/visual/baselines/`
and generate an HTML report at `_test/visual/report.html` with
side-by-side comparisons. Run as part of theme CI to catch unintended
style regressions.
