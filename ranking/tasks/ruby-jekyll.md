# Tasks — jekyll/jekyll

8 tasks (3 narrow, 3 medium, 2 wide) for the Ruby static site generator.

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
