"""Spot-check recon for worst-performing benchmark issues."""
import json

import requests


def call_recon(port: int, task: str) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "recon", "arguments": {"task": task}},
    }
    r = requests.post(f"http://localhost:{port}/mcp", json=payload)
    result = r.json()
    content_items = result.get("result", {}).get("content", [])
    for item in content_items:
        if item.get("type") == "text":
            return json.loads(item["text"])
    return {}


def analyze(data: dict, expected: list[str], label: str) -> None:
    diag = data.get("diagnostics", {})
    seeds = data.get("seeds", [])
    returned_paths = {s["path"] for s in seeds}

    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  harvested: {json.dumps(diag.get('harvested', {}))}")
    print(f"  post_filter: {diag.get('post_filter')}")
    print(f"  n_files: {diag.get('n_files')}")
    print(f"  n_anchors: {diag.get('n_anchors')}")
    print(f"  anchor_floor: {diag.get('anchor_floor')}")
    print(f"  seeds_selected: {diag.get('seeds_selected')}")
    print(f"  file_ranked: {diag.get('file_ranked')}")
    print(f"  def_score_median: {diag.get('def_score_median')}")

    rankings = diag.get("_file_ranking_top20", [])
    print(f"\n  FILE RANKING (top {len(rankings)}):")
    for r in rankings:
        mark = ""
        for ef in expected:
            if ef == r["path"]:
                mark = " <<<< GT"
                break
        print(
            f"    #{r['rank']:2d}  score={r['score']:.4f} "
            f"edit={r['edit']:.4f} ctx={r['ctx']:.4f} "
            f"defs={r['n_defs']}  {r['path']}{mark}"
        )

    print(f"\n  Expected files ({len(expected)}):")
    for ef in expected:
        status = "HIT" if ef in returned_paths else "MISS"
        print(f"    [{status}] {ef}")

    print(f"\n  Returned {len(returned_paths)} unique files")


# Issue #260
data_260 = call_recon(7777, "Add Config Flag to Disable rich Progress Bars in CI")
analyze(
    data_260,
    [
        "src/evee/config/models.py",
        "src/evee/utils/environment.py",
        "src/evee/evaluation/progress_tracker.py",
        "src/evee/evaluation/model_evaluator.py",
        "src/evee/mcp/resources/config.py",
        "tests/evee/evaluation/test_progress_tracker.py",
        "tests/evee/log/test_logger.py",
        "src/evee/logging/logger.py",
        "src/evee/cli/commands/run.py",
        "src/evee/utils/__init__.py",
        "src/evee/config/__init__.py",
        "tests/evee/conftest.py",
        "docs/user-guide/configuration.md",
        "docs/troubleshooting.md",
    ],
    "#260 — Add Config Flag to Disable rich Progress Bars in CI",
)

# Issue #263
data_263 = call_recon(
    7777,
    "Implement Foundry metric automatically and fix metric scaffolding path",
)
analyze(
    data_263,
    [
        "src/evee/cli/commands/metric.py",
        "src/evee/cli/azure_evaluators.json",
        "src/evee/cli/templates/metrics/azure_evaluator_metric.py",
        "tests/evee/cli/utils/test_metric_operations.py",
        "src/evee/cli/constants.py",
        "src/evee/cli/utils/config_manager.py",
        "src/evee/cli/utils/init_file_manager.py",
        "src/evee/cli/utils/validators.py",
        "src/evee/config/models.py",
        "tests/evee/test_azure_evaluators_metadata.py",
        "tests/scripts/test_generate_azure_evaluators.py",
        "docs/user-guide/metrics.md",
        "docs/user-guide/cli.md",
        "example/metrics/rouge_metric.py",
    ],
    "#263 — Implement Foundry metric automatically and fix metric scaffolding path",
)
