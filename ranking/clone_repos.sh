#!/usr/bin/env bash
# Clone all ranking repos (ranker-gate + cutoff + eval), remove origin.
#
# Usage:
#   cd ranking && bash clone_repos.sh
#   cd ranking && bash clone_repos.sh --with-cpl-init   # also run cpl init
#
# Each repo is shallow-cloned (--depth=1). The remote is removed so
# no accidental pushes occur. Pass --with-cpl-init to also index each
# repo with codeplane (slow — ~2min per repo).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLONES_DIR="$SCRIPT_DIR/clones"
DO_CPL_INIT=false
[[ "${1:-}" == "--with-cpl-init" ]] && DO_CPL_INIT=true

mkdir -p "$CLONES_DIR"

# ── Ranker + Gate set (30 repos) ─────────────────────────────────
RANKER_GATE=(
  https://github.com/fmtlib/fmt
  https://github.com/google/googletest
  https://github.com/opencv/opencv
  https://github.com/dotnet/efcore
  https://github.com/Humanizr/Humanizer
  https://github.com/JamesNK/Newtonsoft.Json
  https://github.com/charmbracelet/bubbletea
  https://github.com/caddyserver/caddy
  https://github.com/go-gitea/gitea
  https://github.com/google/gson
  https://github.com/square/okhttp
  https://github.com/spring-projects/spring-boot
  https://github.com/composer/composer
  https://github.com/guzzle/guzzle
  https://github.com/laravel/framework
  https://github.com/django/django
  https://github.com/fastapi/fastapi
  https://github.com/encode/httpx
  https://github.com/jekyll/jekyll
  https://github.com/rack/rack
  https://github.com/rails/rails
  https://github.com/BurntSushi/ripgrep
  https://github.com/serde-rs/serde
  https://github.com/tokio-rs/tokio
  https://github.com/Alamofire/Alamofire
  https://github.com/swiftlang/swift-package-manager
  https://github.com/vapor/vapor
  https://github.com/mermaid-js/mermaid
  https://github.com/nestjs/nest
  https://github.com/colinhacks/zod
)

# ── Cutoff set (31 repos) ───────────────────────────────────────
CUTOFF=(
  https://github.com/pallets/click
  https://github.com/Textualize/rich
  https://github.com/date-fns/date-fns
  https://github.com/trpc/trpc
  https://github.com/spf13/cobra
  https://github.com/gorilla/mux
  https://github.com/clap-rs/clap
  https://github.com/seanmonstar/reqwest
  https://github.com/google/guava
  https://github.com/FasterXML/jackson-databind
  https://github.com/DapperLib/Dapper
  https://github.com/App-vNext/Polly
  https://github.com/heartcombo/devise
  https://github.com/sidekiq/sidekiq
  https://github.com/Seldaek/monolog
  https://github.com/sebastianbergmann/phpunit
  https://github.com/onevcat/Kingfisher
  https://github.com/SnapKit/SnapKit
  https://github.com/nlohmann/json
  https://github.com/gabime/spdlog
  https://github.com/pallets/jinja
  https://github.com/tiangolo/typer
  https://github.com/marshmallow-code/marshmallow
  https://github.com/pallets/flask
  https://github.com/sindresorhus/execa
  https://github.com/go-chi/chi
  https://github.com/crossbeam-rs/crossbeam
  https://github.com/assertj/assertj
  https://github.com/jbogard/MediatR
  https://github.com/lostisland/faraday
  https://github.com/thephpleague/flysystem
  https://github.com/Moya/Moya
)

# ── Eval set (15 repos) ─────────────────────────────────────────
EVAL=(
  https://github.com/celery/celery
  https://github.com/vitest-dev/vitest
  https://github.com/gofiber/fiber
  https://github.com/tokio-rs/axum
  https://github.com/mockito/mockito
  https://github.com/AutoMapper/AutoMapper
  https://github.com/sinatra/sinatra
  https://github.com/symfony/console
  https://github.com/ReactiveX/RxSwift
  https://github.com/catchorg/Catch2
  https://github.com/pydantic/pydantic
  https://github.com/evanw/esbuild
  https://github.com/gin-gonic/gin
  https://github.com/projectlombok/lombok
  https://github.com/grpc/grpc
)

ALL_REPOS=("${RANKER_GATE[@]}" "${CUTOFF[@]}" "${EVAL[@]}")
total=${#ALL_REPOS[@]}
i=0

for url in "${ALL_REPOS[@]}"; do
  i=$((i + 1))
  name=$(basename "$url")
  dest="$CLONES_DIR/$name"

  echo ""
  echo "=== [$i/$total] $name ==="

  # Skip if already cloned
  if [[ -d "$dest/.git" ]]; then
    echo "  already cloned, skipping git clone"
  else
    echo "  cloning $url ..."
    git clone --depth=1 "$url" "$dest"
  fi

  # Remove remote to prevent accidental pushes
  if git -C "$dest" remote get-url origin &>/dev/null; then
    echo "  removing origin remote"
    git -C "$dest" remote remove origin
  fi

  if [[ "$DO_CPL_INIT" == true ]]; then
    echo "  running cpl init ..."
    if (cd "$dest" && cpl init); then
      echo "  cpl init complete"
    else
      echo "  WARNING: cpl init failed for $name (exit $?)"
    fi

    # Commit any files cpl init created
    if [[ -n "$(git -C "$dest" status --porcelain 2>/dev/null)" ]]; then
      echo "  committing cpl init artifacts"
      git -C "$dest" add -A
      git -C "$dest" commit -m "cpl init: add codeplane config files" --no-verify -q
    fi
  fi
done

echo ""
echo "=== Done: $total repos cloned ==="
