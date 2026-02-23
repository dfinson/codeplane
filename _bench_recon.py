"""Recon benchmark — measure precision/recall/F1 against evee ground truth.

Calls the recon MCP tool via HTTP for each of the 5 benchmark tasks and
compares returned seed file paths against the known-relevant files.
"""

import json
import sys
import time
import httpx

MCP_URL = "http://127.0.0.1:7777/mcp"

# =====================================================================
# Ground truth: files an agent MUST discover to complete each task
# Derived from benchmarking/evee/benchmark-design.md
# =====================================================================

GROUND_TRUTH: dict[str, dict] = {
    "#260 (progress bars)": {
        "task": (
            "Expose a configuration flag in config.yaml to disable rich-based progress bars. "
            "We already have the logic to suppress progress bars for MCP and AzureML runs. "
            "This change would make that behavior configurable via a dedicated flag. "
            "When disabled, the framework should avoid rendering rich progress output and "
            "fall back to minimal or plain logging. "
            "The progress bar logic is in src/evee/evaluation/progress_tracker.py and is used "
            "from src/evee/evaluation/model_evaluator.py. Configuration models live in "
            "src/evee/config/models.py."
        ),
        "relevant_files": {
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/config/models.py",
        },
    },
    "#233 (early stop)": {
        "task": (
            "Implement early stop for the inferencing phase if we count too many errors. "
            "There's no point to run over the whole dataset. Alert the user. "
            "The inference loop is in src/evee/evaluation/model_evaluator.py. When inference "
            "fails, exceptions are caught and a failed_count is incremented. "
            "Configuration models live in src/evee/config/models.py. "
            "The progress tracker is in src/evee/evaluation/progress_tracker.py. "
            "The runner is in src/evee/execution/runner.py."
        ),
        "relevant_files": {
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/config/models.py",
            "src/evee/evaluation/progress_tracker.py",
            "src/evee/execution/runner.py",
        },
    },
    "#108 (mocked tests)": {
        "task": (
            "Implement integration tests that run Evee end to end without calling external "
            "services. Load a real config, run a full evaluation, and validate outputs using "
            "deterministic mocked LLM responses. "
            "The evaluation pipeline flows: config -> runner (src/evee/execution/runner.py) -> "
            "model_evaluator (src/evee/evaluation/model_evaluator.py) -> model inference -> "
            "metrics -> output. Config models are in src/evee/config/models.py. "
            "Base classes in src/evee/core/. Existing integration patterns in tests/evee/integration/."
        ),
        "relevant_files": {
            "src/evee/execution/runner.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/config/models.py",
            "src/evee/core/base_model.py",
        },
    },
    "#4 (cache inference)": {
        "task": (
            "Implement caching for deterministic model inference results to save costs and time "
            "when rerunning model evaluation. "
            "The model abstraction is in src/evee/core/base_model.py (BaseModel with a run() method). "
            "Model inference is called from src/evee/evaluation/model_evaluator.py. "
            "Configuration lives in src/evee/config/models.py."
        ),
        "relevant_files": {
            "src/evee/core/base_model.py",
            "src/evee/evaluation/model_evaluator.py",
            "src/evee/config/models.py",
        },
    },
    "#262 (REST models)": {
        "task": (
            "Add support for defining REST-based models through configuration instead of "
            "creating custom models that just wrap REST calls. Enable easier integration "
            "of REST endpoints as models. "
            "The model abstraction is in src/evee/core/base_model.py (BaseModel with a run() method). "
            "Configuration lives in src/evee/config/models.py. "
            "Model evaluation is in src/evee/evaluation/model_evaluator.py."
        ),
        "relevant_files": {
            "src/evee/core/base_model.py",
            "src/evee/config/models.py",
            "src/evee/evaluation/model_evaluator.py",
        },
    },
}


def call_recon(task: str, verbosity: str = "detailed") -> dict:
    """Call the recon MCP tool via JSON-RPC over HTTP."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "recon",
            "arguments": {
                "task": task,
                "verbosity": verbosity,
                "max_seeds": 15,
                "depth": 0,  # seeds only — we're measuring retrieval, not expansion
            },
        },
    }
    resp = httpx.post(MCP_URL, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def extract_seed_paths(result: dict) -> set[str]:
    """Extract file paths from recon result."""
    paths = set()
    # Navigate MCP response structure
    r = result.get("result", result)
    # MCP tools/call returns {"content": [{"type": "text", "text": "..."}]}
    content = r.get("content", [])
    for item in content:
        if item.get("type") == "text":
            try:
                data = json.loads(item["text"])
            except (json.JSONDecodeError, TypeError):
                continue
            for seed in data.get("seeds", []):
                p = seed.get("path", "")
                if p:
                    paths.add(p)
    # Also try direct structure (non-MCP wrapper)
    for seed in r.get("seeds", []):
        p = seed.get("path", "")
        if p:
            paths.add(p)
    return paths


def compute_metrics(predicted: set[str], actual: set[str]) -> dict:
    """Compute precision, recall, F1."""
    tp = len(predicted & actual)
    fp = len(predicted - actual)
    fn = len(actual - predicted)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "predicted": sorted(predicted),
        "actual": sorted(actual),
        "hits": sorted(predicted & actual),
        "misses": sorted(actual - predicted),
        "extras": sorted(predicted - actual),
    }


def main():
    print("=" * 70)
    print("RECON BENCHMARK — evee ground truth")
    print("=" * 70)
    
    all_metrics = {}
    total_tp = total_fp = total_fn = 0
    
    for label, spec in GROUND_TRUTH.items():
        print(f"\n{'─' * 60}")
        print(f"Task: {label}")
        print(f"{'─' * 60}")
        
        t0 = time.perf_counter()
        try:
            result = call_recon(spec["task"])
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        dt = time.perf_counter() - t0
        
        predicted = extract_seed_paths(result)
        actual = spec["relevant_files"]
        
        m = compute_metrics(predicted, actual)
        m["latency_ms"] = round(dt * 1000)
        all_metrics[label] = m
        
        total_tp += m["tp"]
        total_fp += m["fp"]
        total_fn += m["fn"]
        
        print(f"  Latency:   {m['latency_ms']}ms")
        print(f"  Predicted: {len(predicted)} files  |  Actual: {len(actual)} files")
        print(f"  Precision: {m['precision']:.3f}  |  Recall: {m['recall']:.3f}  |  F1: {m['f1']:.3f}")
        print(f"  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}")
        if m["hits"]:
            print(f"  ✓ Hits:   {', '.join(m['hits'])}")
        if m["misses"]:
            print(f"  ✗ Misses: {', '.join(m['misses'])}")
        if m["extras"]:
            print(f"  + Extras: {', '.join(m['extras'])}")
    
    # Aggregate
    print(f"\n{'=' * 70}")
    print("AGGREGATE METRICS")
    print(f"{'=' * 70}")
    
    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) > 0 else 0
    
    macro_p = sum(m["precision"] for m in all_metrics.values()) / len(all_metrics) if all_metrics else 0
    macro_r = sum(m["recall"] for m in all_metrics.values()) / len(all_metrics) if all_metrics else 0
    macro_f1 = sum(m["f1"] for m in all_metrics.values()) / len(all_metrics) if all_metrics else 0
    
    avg_latency = sum(m["latency_ms"] for m in all_metrics.values()) / len(all_metrics) if all_metrics else 0
    
    print(f"  Micro:  P={micro_p:.3f}  R={micro_r:.3f}  F1={micro_f1:.3f}")
    print(f"  Macro:  P={macro_p:.3f}  R={macro_r:.3f}  F1={macro_f1:.3f}")
    print(f"  Total:  TP={total_tp}  FP={total_fp}  FN={total_fn}")
    print(f"  Avg latency: {avg_latency:.0f}ms")
    
    # Save results
    output = {
        "per_task": all_metrics,
        "aggregate": {
            "micro_precision": round(micro_p, 3),
            "micro_recall": round(micro_r, 3),
            "micro_f1": round(micro_f1, 3),
            "macro_precision": round(macro_p, 3),
            "macro_recall": round(macro_r, 3),
            "macro_f1": round(macro_f1, 3),
            "total_tp": total_tp,
            "total_fp": total_fp,
            "total_fn": total_fn,
            "avg_latency_ms": round(avg_latency),
        },
    }
    with open("/tmp/recon_benchmark_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to /tmp/recon_benchmark_results.json")


if __name__ == "__main__":
    main()
