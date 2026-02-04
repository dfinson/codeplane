"""Canonical exclude patterns."""

from __future__ import annotations

PRUNABLE_DIRS: frozenset[str] = frozenset(
    (
        ".git",
        ".svn",
        ".hg",
        ".bzr",
        ".codeplane",
        "node_modules",
        ".npm",
        ".yarn",
        ".pnpm-store",
        "bower_components",
        "venv",
        ".venv",
        ".virtualenv",
        "virtualenv",
        "env",
        ".env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "eggs",
        ".eggs",
        "site-packages",
        ".ipynb_checkpoints",
        ".hypothesis",
        "htmlcov",
        "vendor",
        "pkg",
        "target",
        ".gradle",
        ".m2",
        "out",
        "bin",
        "obj",
        "packages",
        ".terraform",
        ".bundle",
        "dist",
        "build",
        "_build",
        "coverage",
        ".coverage",
        ".nyc_output",
        ".idea",
        ".vscode",
        ".vs",
        ".cache",
        "tmp",
        "temp",
    )
)

UNIVERSAL_EXCLUDE_GLOBS: tuple[str, ...] = tuple(f"**/{d}/**" for d in sorted(PRUNABLE_DIRS))

_CPLIGNORE = """\
# CodePlane ignore patterns (gitignore syntax)

# VCS
.git/

# Dependencies/caches
node_modules/
vendor/
.venv/
venv/
env/
.env/
__pycache__/
*.pyc
.tox/
.nox/
.mypy_cache/
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/
.hypothesis/
.bundle/
go.sum

# Build outputs
dist/
build/
out/
target/
bin/
obj/
*.egg-info/
*.egg
*.whl
*.tar.gz
*.zip
*.jar
*.war
*.class
Cargo.lock
*.dll
*.exe
*.pdb
*.so
*.dylib
*.a
*.o
*.obj

# IDE
.idea/
.vscode/
*.swp
*.swo
*~
.project
.classpath
.settings/

# Secrets (NEVER index)
.env
.env.*
!.env.example
*.pem
*.key
*.crt
*.p12
*.pfx
**/secrets/
**/credentials/
*.keystore
service-account*.json

# Large/binary
*.pdf
*.doc
*.docx
*.xls
*.xlsx
*.ppt
*.pptx
*.rar
*.7z
*.tar
*.gz
*.bz2
*.iso
*.dmg
*.deb
*.rpm
*.msi

# Media
*.jpg
*.jpeg
*.png
*.gif
*.ico
*.svg
*.mp3
*.mp4
*.avi
*.mov
*.webm
*.wav
*.ogg
*.ttf
*.otf
*.woff
*.woff2
*.eot

# Data
*.sqlite
*.sqlite3
*.db
*.dump
*.bak

# Logs/tmp
*.log
logs/
tmp/
temp/
*.tmp
*.cache

# OS
.DS_Store
._*
Thumbs.db
desktop.ini

# Lock files
package-lock.json
yarn.lock
pnpm-lock.yaml
composer.lock
Gemfile.lock
poetry.lock
Pipfile.lock

# Generated
**/generated/
**/*_generated.*
**/*.gen.*
**/*.pb.go
**/*.pb.h
**/*.pb.cc
"""


def generate_cplignore_template() -> str:
    return _CPLIGNORE


__all__ = ["PRUNABLE_DIRS", "UNIVERSAL_EXCLUDE_GLOBS", "generate_cplignore_template"]
