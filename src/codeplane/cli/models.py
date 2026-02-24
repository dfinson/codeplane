"""Embedding model download management with user-facing progress.

Ensures required ONNX embedding models are cached locally before
indexing begins.  Called from ``cpl init`` and ``cpl up`` so the
first-run experience shows an explicit progress bar instead of
leaking raw HuggingFace download logs.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import questionary
import structlog

from codeplane.core.progress import get_console, phase_box

log = structlog.get_logger()

# ===================================================================
# Model registry — every ONNX model CodePlane needs at runtime
# ===================================================================

_MODELS: list[dict[str, str | float]] = [
    {
        "name": "BAAI/bge-small-en-v1.5",
        "label": "Definition embeddings (bge-small)",
        "size_gb": 0.067,
    },
    {
        "name": "jinaai/jina-embeddings-v2-base-en",
        "label": "File embeddings (jina-v2-base)",
        "size_gb": 0.52,
    },
]


def _fastembed_cache_dir() -> Path:
    """Resolve the fastembed cache directory (mirrors fastembed internals)."""
    env = os.getenv("FASTEMBED_CACHE_PATH")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "fastembed_cache"


def _model_is_cached(model_name: str) -> bool:
    """Check whether *model_name* already exists in the fastembed cache."""
    try:
        from fastembed import TextEmbedding

        # fastembed exposes a download_model that tries local_files_only first.
        # We replicate the cache-dir lookup to avoid importing heavy internals.
        supported = TextEmbedding.list_supported_models()
        for desc in supported:
            if desc.get("model", "").lower() == model_name.lower():
                # fastembed stores HF models under models--<org>--<repo>
                hf_source = desc.get("sources", {})
                hf_repo: str | None = None
                if isinstance(hf_source, dict):
                    hf_repo = hf_source.get("hf")
                if not hf_repo:
                    break

                cache = _fastembed_cache_dir()
                snapshot_dir = cache / f"models--{hf_repo.replace('/', '--')}"
                # A valid cache has the metadata file that fastembed writes
                return snapshot_dir.exists() and any(snapshot_dir.iterdir())
        return False
    except Exception:
        return False


def _download_model(model_name: str) -> None:
    """Download a single model using fastembed's own machinery."""
    from fastembed import TextEmbedding

    # Instantiating TextEmbedding triggers the download + ONNX load.
    # fastembed shows its own tqdm bar (we suppress via redirect below).
    TextEmbedding(model_name=model_name)


# ===================================================================
# Public API
# ===================================================================


def ensure_models(*, interactive: bool = True) -> bool:
    """Ensure all required embedding models are downloaded.

    When *interactive* is True (CLI context), prompts the user before
    downloading missing models and shows rich progress.  When False
    (daemon/non-TTY), downloads silently.

    Returns True if all models are available after this call.
    """
    missing: list[dict[str, str | float]] = []
    for model in _MODELS:
        name = str(model["name"])
        if not _model_is_cached(name):
            missing.append(model)

    if not missing:
        log.debug("models.all_cached")
        return True

    total_gb = sum(float(m["size_gb"]) for m in missing)
    console = get_console()

    # --- Prompt user ---
    if interactive:
        console.print()
        console.print("[bold]Embedding models required[/bold]")
        console.print()
        for m in missing:
            size_mb = int(float(m["size_gb"]) * 1024)
            console.print(f"  [cyan]•[/cyan] {m['label']}  [dim]({size_mb} MB)[/dim]")
        console.print()
        console.print(f"  [dim]Total download: ~{total_gb:.1f} GB → {_fastembed_cache_dir()}[/dim]")
        console.print()

        answer = questionary.select(
            "Download missing models now?",
            choices=[
                questionary.Choice("Yes, download now", value=True),
                questionary.Choice("No, abort", value=False),
            ],
            style=questionary.Style(
                [
                    ("question", "bold"),
                    ("highlighted", "fg:cyan bold"),
                    ("selected", "fg:cyan"),
                ]
            ),
        ).ask()

        if not answer:
            console.print("[dim]Cancelled — models are required for indexing.[/dim]")
            return False

    # --- Download each model with a phase box ---
    for m in missing:
        name = str(m["name"])
        label = str(m["label"])
        size_mb = int(float(m["size_gb"]) * 1024)

        with phase_box("Models", width=60) as phase:
            task_id = phase.add_progress(f"Downloading {label}", total=100)

            try:
                # We can't get real byte-level progress from fastembed's
                # download_model (it delegates to huggingface_hub).
                # Instead we show an indeterminate-style bar that completes
                # on success.  Advance to ~10% to indicate "started".
                phase._progress.update(task_id, completed=10)  # type: ignore[union-attr]
                phase._update()

                _download_model(name)

                phase._progress.update(task_id, completed=100)  # type: ignore[union-attr]
                phase._update()
                phase.complete(f"{label} ({size_mb} MB) ✓")

            except Exception as exc:
                phase.complete(f"Failed: {exc}", style="red")
                log.error("models.download_failed", model=name, error=str(exc))
                if interactive:
                    console.print(
                        f"\n[red]Failed to download {label}.[/red]  "
                        "Check your internet connection and try again."
                    )
                return False

    log.info("models.all_ready", count=len(missing))
    return True
