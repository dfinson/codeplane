# Repo Selection & Tasks

30 repos across 10 languages, 3 scale tiers each (§4.1 of recon-lab/README.md).
Each file contains the repo selection rationale, structure overview, and
task definitions (narrow/medium/wide) per §5.2.

## Selection Criteria

1. **Scale diversity**: small (focused lib) / medium (multi-module) / large (multi-team)
2. **Structural quality**: codeplane indexes successfully, well-structured code
3. **History richness**: meaningful commit/PR history for realistic task generation
4. **Permissive license**: MIT, Apache-2.0, or BSD — usable for training data

## Repos by Language

| Language | Small | Medium | Large |
|----------|-------|--------|-------|
| **Python** | [encode/httpx](python-httpx.md) (BSD-3) | [fastapi/fastapi](python-fastapi.md) (MIT) | [django/django](python-django.md) (BSD-3) |
| **TypeScript** | [colinhacks/zod](typescript-zod.md) (MIT) | [mermaid-js/mermaid](typescript-mermaid.md) (MIT) | [nestjs/nest](typescript-nestjs.md) (MIT) |
| **Go** | [charmbracelet/bubbletea](go-bubbletea.md) (MIT) | [caddyserver/caddy](go-caddy.md) (Apache-2.0) | [go-gitea/gitea](go-gitea.md) (MIT) |
| **Rust** | [serde-rs/serde](rust-serde.md) (MIT/Apache-2.0) | [BurntSushi/ripgrep](rust-ripgrep.md) (MIT/UNLICENSE) | [tokio-rs/tokio](rust-tokio.md) (MIT) |
| **Java** | [google/gson](java-gson.md) (Apache-2.0) | [square/okhttp](java-okhttp.md) (Apache-2.0) | [spring-projects/spring-boot](java-spring-boot.md) (Apache-2.0) |
| **C#** | [Humanizr/Humanizer](csharp-humanizer.md) (MIT) | [JamesNK/Newtonsoft.Json](csharp-newtonsoft-json.md) (MIT) | [dotnet/efcore](csharp-efcore.md) (MIT) |
| **Ruby** | [rack/rack](ruby-rack.md) (MIT) | [jekyll/jekyll](ruby-jekyll.md) (MIT) | [rails/rails](ruby-rails.md) (MIT) |
| **PHP** | [guzzle/guzzle](php-guzzle.md) (MIT) | [composer/composer](php-composer.md) (MIT) | [laravel/framework](php-laravel.md) (MIT) |
| **Swift** | [Alamofire/Alamofire](swift-alamofire.md) (MIT) | [vapor/vapor](swift-vapor.md) (MIT) | [swiftlang/swift-package-manager](swift-package-manager.md) (Apache-2.0) |
| **C/C++** | [fmtlib/fmt](cpp-fmt.md) (MIT) | [google/googletest](cpp-googletest.md) (BSD-3) | [opencv/opencv](cpp-opencv.md) (Apache-2.0) |

## Validation TODO (§5.1)

After indexing all 30 repos via codeplane, confirm:
- [ ] Semantic object count distribution spans a wide range (not clustered)
- [ ] All repos parse and index successfully
- [ ] If counts cluster, swap repos to increase diversity

## Known Risks

- **java-okhttp**: Migrated from Java to Kotlin (OkHttp 4.x). If codeplane's
  Kotlin indexing is insufficient, substitute with `square/retrofit` (Java,
  Apache-2.0) or `apache/commons-lang` (Java, Apache-2.0).
- **swift-package-manager**: Repo moved to `swiftlang/` org. Use current URL.
- **cpp-opencv**: Very large (~1M LoC). May need longer indexing time.
  Exclude `opencv_contrib`.
