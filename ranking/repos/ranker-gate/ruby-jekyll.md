# jekyll/jekyll

| Field | Value |
|-------|-------|
| **URL** | https://github.com/jekyll/jekyll |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Medium (multi-module project) |
| **Category** | Static site generator |
| **Set** | ranker-gate |
| **Commit** | `d0cf1791f6a349519998750f4511822e43e516e4` |

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

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix incremental build not detecting `_data` file changes

When a YAML file in `_data/` is modified, the incremental build
(`jekyll build --incremental`) does not regenerate pages that reference
the changed data via `site.data.*`. The dependency tracker does not
record data file dependencies. Fix the incremental build to track
which pages reference which data files and regenerate accordingly.

### N2: Fix `where_exp` and `find_exp` dropping Hash keys when input is a Hash

When the input to `where_exp` or `find_exp` is a Hash (e.g.,
`site.data.authors | where_exp: "item", "item.active == true"`), the
filters call `.values` on the Hash, discarding the keys entirely. This
means expressions cannot reference key-based identifiers, and the results
contain only values with no way to recover the original key. The same
pattern exists in `where` and `find` (by design for those), but
`where_exp` and `find_exp` are marked with a `# FIXME` comment
acknowledging the issue. Fix `where_exp` and `find_exp` in
`lib/jekyll/filters.rb` to convert a Hash input to an array of
`{"key" => k, "value" => v}` hashes so that both the key and value are
accessible in filter expressions.

### N3: Fix `--livereload` not injecting script on non-HTML content types

The LiveReload script injection checks for `Content-Type: text/html`
but some templates produce HTML with a different content type (e.g.,
`text/xml` for XHTML). The injection should check the file extension
(`.html`, `.htm`) in addition to the Content-Type header.

### N4: Fix `escape_path` encoding only the first `#` character in URLs

The `URL.escape_path` method in `lib/jekyll/url.rb` uses
`String#sub("#", "%23")` to percent-encode the fragment-start character
`#` in generated URL path segments. `sub` replaces only the FIRST
occurrence, so a slug containing multiple `#` characters — such as a
document titled `C# and F# Guide` — produces a path like
`/posts/C%23-and-F#-Guide/` where only the first `#` is encoded. The
second unencoded `#` causes browsers to interpret everything following
it as a URL fragment, silently truncating the path. Fix `escape_path`
in `lib/jekyll/url.rb` to use `gsub` instead of `sub` so that every
`#` in the path segment is percent-encoded.

### N5: Fix excerpt generation wasting resources for non-output collection documents

The `Document#generate_excerpt?` method in `lib/jekyll/document.rb`
returns true for any document whose `excerpt_separator` is non-empty.
Because the default `excerpt_separator` is `"\n\n"`, excerpt objects
(`Jekyll::Excerpt`) are created for every document in every collection
during the read phase, including documents in collections configured
with `output: false` in `_config.yml`. Excerpt generation parses the
full document content and stores the result in `data["excerpt"]`. For
large sites with internal reference collections that use `output: false`
(not rendered to the site), this causes unnecessary memory usage and
build-time overhead. Fix `generate_excerpt?` in `lib/jekyll/document.rb`
to also check `collection.write?`, so that excerpt generation is skipped
for documents in non-output collections.

### N6: Fix `date_to_xmlschema` shifting UTC midnight dates to previous day

The `date_to_xmlschema` Liquid filter in `lib/jekyll/filters/date_filters.rb`
calls `time(date).xmlschema`. The private `time` helper converts the
input with `date.to_time.dup.localtime`. When a YAML front matter field
stores a datetime as midnight UTC — for example
`date: 2024-03-10 00:00:00 +00:00` — `date.to_time` preserves the UTC
representation, and `.localtime` then shifts it to the site's configured
timezone. In a negative-offset timezone such as `America/New_York`
(UTC−5), midnight UTC becomes `2024-03-09 19:00:00 -0500`, pushing the
date into the previous calendar day. Pages then appear in sitemaps and
Atom feeds with a `lastmod` or publication date of March 9 instead of
March 10. Fix the `time` helper in `lib/jekyll/filters/date_filters.rb`
to convert the input to the site's timezone using the timezone name from
`site.config["timezone"]` before calling `xmlschema`, so that the
wall-clock date is preserved regardless of the source UTC offset.

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

### N9: Fix `StaticFile` in collections producing broken URLs when permalink uses `:title`

When a collection defines a permalink template that includes `:title`
(e.g., `permalink: /docs/:title/`), `StaticFile#url` in
`lib/jekyll/static_file.rb` builds the URL using the collection's
`url_template` and the file's `placeholders` hash. However,
`StaticFile#placeholders` sets `:title => ""` (empty string) for all
static files, since static files have no front-matter title. The result
is a URL like `/docs//filename.jpg` which, after `sanitize_url` squeezes
slashes, collapses to `/docs/filename.jpg` — ignoring the intended
permalink structure entirely. For collections with other `:title`-dependent
permalink patterns, all static files end up mapped to the same collapsed
path. Fix `StaticFile#placeholders` in `lib/jekyll/static_file.rb` to
populate `:title` with the file's `basename` (filename without extension)
so that the collection permalink template is honoured for static files.

### N10: Fix `sample` filter silently discarding all exceptions via bare `rescue`

The `sample` Liquid filter in `lib/jekyll/filters.rb` converts the
`num` argument using `Liquid::Utils.to_integer(num) rescue 1`. The bare
`rescue` catches every exception class, including `NoMethodError` and
`LoadError` that indicate programming mistakes rather than bad input.
When `num` is an invalid value, the failure is swallowed silently and
the filter returns one element with no warning to the template developer.
For example, passing a Hash as `num` raises `NoMethodError`, which is
caught, and the caller receives a single-element sample instead of an
error. Fix the `sample` filter in `lib/jekyll/filters.rb` to rescue
only `ArgumentError` and `TypeError`, and emit a `Jekyll.logger.warn`
message when falling back to the default of 1, so that template authors
are informed of the incorrect argument.

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
trend analysis across builds. Add `concurrent-ruby` as a runtime
dependency in `jekyll.gemspec` and `Gemfile` for thread-safe profiling
counters. Document the `--profile` flag in `docs/_docs/` and add a
`History.markdown` entry.

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
parent and child themes can define independent config blocks. Document
the theme inheritance system in `docs/_docs/themes.md`, add a
`History.markdown` entry for the new feature, and update the
`jekyll.gemspec` description to mention multi-level theme support.

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

## Non-code

### N11: Fix `.github/workflows/ci.yml` not running tests on documentation changes

The CI workflow in `.github/workflows/ci.yml` uses a `paths-ignore`
filter that skips the entire test suite when only files under `docs/`
are modified. However, the `docs/` directory contains a Jekyll site
(`docs/_config.yml`) that is built as part of the `profile-docs`
Earthfile target, meaning documentation-only changes can break the
docs build without CI catching it. Remove `docs/**` from the
`paths-ignore` list and add a separate `docs` job that runs
`script/profile-docs` only when `docs/` files change.

### M11: Overhaul `jekyll.gemspec` metadata, `Earthfile` targets, and `.codeclimate.yml` thresholds

Update `jekyll.gemspec` to add `funding_uri` and `wiki_uri` metadata,
pin `required_rubygems_version` to `>= 3.3.0` to match current
Bundler practices, and move the `s.files` shell-out from
`` `git ls-files` `` to `Dir.glob` for reproducible builds. Rewrite
the `Earthfile` to add Ruby 3.4 and 3.5 build targets, remove the
obsolete Ruby 2.5 target, add a `+lint` target that runs the
`.rubocop.yml` and `.rubocop_todo.yml` configurations, and add a
`+benchmark` target. Revise `.codeclimate.yml` to raise the
`method-complexity` threshold from 15 to 20 for the converters
module and update the `rubocop` plugin channel from `rubocop-0-60`
to `rubocop-1-57`.

### W11: Revamp CI/CD, linter config, `Rakefile`, and contributor documentation

Restructure `.github/workflows/` to split the single `ci.yml` into
three focused workflows: `test.yml` (Minitest unit tests across Ruby
versions), `cucumber.yml` (Cucumber integration features), and `lint.yml`
(RuboCop + spelling check). Update the existing
`.github/workflows/release.yml` — which currently triggers on pushes
to `master`/`*-stable` when `lib/**/version.rb` changes — to instead
trigger on version tag pushes (e.g., `v*.*.*`) so that gem publishing
is only initiated by an explicit release tag rather than any version
file edit. Update `Rakefile` to add a `rake docs:build` task that runs
the Jekyll docs site build from `docs/`, a `rake release:prepare` task
that bumps the version in `lib/jekyll/version.rb` and updates
`History.markdown`, and a `rake lint` task unifying `.rubocop.yml`
enforcement. Revise `.rubocop.yml` to enable `Metrics/PerceivedComplexity`
(currently present but only tuned via `Max`) and `Naming/PredicateName`
cops (not currently configured), and clean out resolved entries from
`.rubocop_todo.yml`. Update `.github/CONTRIBUTING.markdown` to document
the Earthfile-based local development workflow and add a DCO
requirement.
