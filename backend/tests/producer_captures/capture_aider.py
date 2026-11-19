"""Capture the released Aider writer, without constructing a model or conversation runner."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import socket
import sys
from contextlib import ExitStack
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

SOURCE_COMMIT = "253f0368b873ba30d8ee26e463718f0c03614ddf"
WRITER_SHA256 = "7f62a79c8202f2a3793410bac4c983e417fb886513b90faf0e0579d5bb7d047b"


def main(output: Path) -> None:
    if version("aider-chat") != "0.86.2":
        raise RuntimeError("This capture requires the actual aider-chat==0.86.2 distribution")
    output.mkdir(parents=True, exist_ok=True)
    sample = output / "aider-0.86.2.history.md"
    if sample.exists():
        raise FileExistsError(sample)

    with ExitStack() as stack:
        for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "getaddrinfo")):
            stack.enter_context(
                patch.object(owner, name, side_effect=AssertionError("Native writer qualification permits no network"))
            )
        capture(output, sample)


def capture(output: Path, sample: Path) -> None:
    module = import_module("aider.io")
    if module.__file__ is None:
        raise RuntimeError("The installed Aider writer has no inspectable source")
    module_path = Path(module.__file__)
    source_hash = hashlib.sha256(module_path.read_bytes()).hexdigest()
    if source_hash != WRITER_SHA256:
        raise RuntimeError("Installed Aider writer does not match the pinned release source")
    writer = module.InputOutput(
        pretty=False, fancy_input=False, chat_history_file=str(sample), input=io.StringIO(), output=io.StringIO()
    )
    invocations = [
        ("user_input", "Describe addition in this synthetic writer fixture."),
        ("ai_output", "Addition combines two numbers. No model was invoked for this fixture."),
        ("tool_output", "Synthetic native writer check completed."),
        ("user_input", "Use the synthetic example 2 plus 3."),
        ("ai_output", "The synthetic example gives 5."),
    ]
    for method, content in invocations:
        if method == "tool_output":
            writer.tool_output(content, log_only=True)
        else:
            getattr(writer, method)(content)
    raw = sample.read_bytes()
    if not raw or any(content.encode() not in raw for _, content in invocations):
        raise AssertionError("The native writer did not persist the supplied synthetic messages")
    provenance = {
        "capture_kind": "native-writer/inert",
        "format_id": "aider_markdown",
        "producer": {
            "distribution": "aider-chat",
            "version": version("aider-chat"),
            "source_repository": "https://github.com/Aider-AI/aider",
            "source_release": "v0.86.2",
            "source_commit": SOURCE_COMMIT,
            "writer": "aider.io.InputOutput",
            "writer_sha256": source_hash,
            "python": platform.python_version(),
        },
        "sample": sample.name,
        "sample_sha256": hashlib.sha256(raw).hexdigest(),
        "invocations": [{"method": method, "content": content} for method, content in invocations],
        "network": "all socket connections and name resolution blocked",
        "model_inference": False,
        "limitations": [
            "Actual native writer methods, not a live vendor conversation",
            "No fabricated token usage, tool execution, or structured Aider JSON producer claim",
        ],
    }
    (output / "aider-0.86.2.provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"sample": str(sample), "sha256": provenance["sample_sha256"], "version": version("aider-chat")}))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
