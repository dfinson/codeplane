# Installation

## Prerequisites

Ensure you have the following installed:

| Tool | Version | Check |
|------|---------|-------|
| Python | ≥ 3.11 | `python --version` |
| Node.js | ≥ 20 | `node --version` |
| Git | any | `git --version` |

## Install

Install CodePlane directly from GitHub:

```bash
pip install git+https://github.com/dfinson/codeplane.git
```

This installs the `cpl` command and all backend dependencies.

!!! note "PyPI coming soon"
    CodePlane will be published to PyPI in a future release. For now, install directly from GitHub.

### Frontend (for UI)

The frontend needs to be built separately:

```bash
git clone https://github.com/dfinson/codeplane.git
cd codeplane/frontend
npm ci && npm run build
```

Or use `make install` from the repo root to install everything at once:

```bash
git clone https://github.com/dfinson/codeplane.git
cd codeplane
make install    # pip install + npm ci
```

## Environment Setup

```bash
cp .env.sample .env
```

Edit `.env` if you want remote access:

```bash
# Password for Dev Tunnels remote access
CPL_DEVTUNNEL_PASSWORD=your-secret-password
```

## Verify Installation

```bash
cpl doctor
```

This checks that all dependencies are installed and configured correctly.

## Next Steps

→ [Quick Start](quick-start.md) — Launch the server and create your first job
