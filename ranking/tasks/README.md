# Tasks — Summary

260 tasks across 30 repos, following §5.2 of ranking-design.md.

## Task distribution

| Scope | Count | Description |
|-------|-------|-------------|
| **Narrow (N)** | 90 | Bug fix or small feature, 1-2 files |
| **Medium (M)** | 104 | Feature or refactor spanning a module/subsystem |
| **Wide (W)** | 66 | Cross-cutting change touching multiple subsystems |

## Task types represented

- Bug fixes (race conditions, edge cases, incorrect behavior)
- New features (new APIs, new capabilities)
- Refactors (internal restructuring, migration)
- Performance improvements (parallelism, caching, optimization)
- Test improvements (new test infrastructure, coverage)
- API changes (new endpoints, protocol support)
- Config/build changes (new build modes, config formats)
- Security improvements (audit, sandboxing, encryption)
- Observability (tracing, metrics, logging)

## Per-repo task counts

| Repo | Narrow | Medium | Wide | Total |
|------|--------|--------|------|-------|
| [python-httpx](python-httpx.md) | 3 | 3 | 2 | 8 |
| [python-fastapi](python-fastapi.md) | 3 | 3 | 2 | 8 |
| [python-django](python-django.md) | 3 | 4 | 3 | 10 |
| [typescript-zod](typescript-zod.md) | 3 | 3 | 2 | 8 |
| [typescript-mermaid](typescript-mermaid.md) | 3 | 3 | 2 | 8 |
| [typescript-nestjs](typescript-nestjs.md) | 3 | 4 | 3 | 10 |
| [go-bubbletea](go-bubbletea.md) | 3 | 3 | 2 | 8 |
| [go-caddy](go-caddy.md) | 3 | 3 | 2 | 8 |
| [go-gitea](go-gitea.md) | 3 | 4 | 3 | 10 |
| [rust-serde](rust-serde.md) | 3 | 3 | 2 | 8 |
| [rust-ripgrep](rust-ripgrep.md) | 3 | 3 | 2 | 8 |
| [rust-tokio](rust-tokio.md) | 3 | 4 | 3 | 10 |
| [java-gson](java-gson.md) | 3 | 3 | 2 | 8 |
| [java-okhttp](java-okhttp.md) | 3 | 3 | 2 | 8 |
| [java-spring-boot](java-spring-boot.md) | 3 | 4 | 3 | 10 |
| [csharp-humanizer](csharp-humanizer.md) | 3 | 3 | 2 | 8 |
| [csharp-newtonsoft-json](csharp-newtonsoft-json.md) | 3 | 3 | 2 | 8 |
| [csharp-efcore](csharp-efcore.md) | 3 | 4 | 3 | 10 |
| [ruby-rack](ruby-rack.md) | 3 | 3 | 2 | 8 |
| [ruby-jekyll](ruby-jekyll.md) | 3 | 3 | 2 | 8 |
| [ruby-rails](ruby-rails.md) | 3 | 4 | 3 | 10 |
| [php-guzzle](php-guzzle.md) | 3 | 3 | 2 | 8 |
| [php-composer](php-composer.md) | 3 | 3 | 2 | 8 |
| [php-laravel](php-laravel.md) | 3 | 4 | 3 | 10 |
| [swift-alamofire](swift-alamofire.md) | 3 | 3 | 2 | 8 |
| [swift-vapor](swift-vapor.md) | 3 | 3 | 2 | 8 |
| [swift-package-manager](swift-package-manager.md) | 3 | 4 | 3 | 10 |
| [cpp-fmt](cpp-fmt.md) | 3 | 3 | 2 | 8 |
| [cpp-googletest](cpp-googletest.md) | 3 | 3 | 2 | 8 |
| [cpp-opencv](cpp-opencv.md) | 3 | 4 | 3 | 10 |

## Quality checklist (§5.2)

Each task should be:
- [x] Well-defined enough to start working without clarifying questions
- [x] Grounded in the actual codebase architecture
- [x] Scoped as intended (narrow/medium/wide)
- [x] Written as a natural-language issue/task assignment
- [x] Contains no code, diffs, or hints about which files to touch
