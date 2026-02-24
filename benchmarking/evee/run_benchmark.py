#!/usr/bin/env python3
"""Recon Evaluation Benchmark Runner.

Parses ReconEveeEvaluation.md, calls Recon for all 24 issues × 3 queries,
computes metrics, and writes results to a schema-compliant JSON file.

Usage:
    python benchmarking/evee/run_benchmark.py [--port 7777] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ── Paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
EVAL_DOC = SCRIPT_DIR / "ReconEveeEvaluation.md"
RESULTS_DIR = SCRIPT_DIR / "results"
REPO_ROOT = SCRIPT_DIR.parent.parent  # codeplane repo root
EVEE_REPO = Path("/home/dave01/wsl-repos/evees/evee_cpl/evee")
RECON_CACHE_DIR = EVEE_REPO / ".codeplane" / "cache" / "recon_result"

# ── Difficulty mapping (from doc) ────────────────────────────────────
DIFFICULTY: dict[str, str] = {
    "4": "medium", "38": "complex", "57": "medium", "63": "medium",
    "72": "complex", "108": "complex", "172": "complex", "191": "complex",
    "192": "medium", "193": "medium", "201": "complex", "210": "medium",
    "226": "medium", "233": "medium", "234": "complex", "236": "medium",
    "240": "complex", "259": "medium", "260": "medium", "261": "medium",
    "262": "medium", "263": "medium", "268": "complex", "275": "complex",
}


# =====================================================================
# 1. Parse the evaluation document
# =====================================================================


def parse_eval_doc(path: Path) -> list[dict]:
    """Extract issues with GT file tables and Q1/Q2/Q3 queries from the markdown."""
    text = path.read_text(encoding="utf-8")
    issues: list[dict] = []

    # Split on issue headers: ### #NNN — Title
    issue_pattern = re.compile(r"^### #(\d+) — (.+)$", re.MULTILINE)
    matches = list(issue_pattern.finditer(text))

    for i, m in enumerate(matches):
        issue_num = m.group(1)
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end]

        # Extract GT table rows: | N | path | relevance | Category |
        gt_files = _parse_gt_table(section)

        # Extract queries
        queries = _parse_queries(section)

        issues.append({
            "number": issue_num,
            "title": title,
            "gt_files": gt_files,
            "queries": queries,
            "difficulty": DIFFICULTY.get(issue_num, "medium"),
        })

    return issues


def _parse_gt_table(section: str) -> list[dict]:
    """Parse ground truth file table from an issue section."""
    files: list[dict] = []
    # Match table rows: | N | path | relevance | Category |
    row_re = re.compile(
        r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(Edit|Context/Test|Supp/Docs)\s*\|",
        re.MULTILINE,
    )
    for rm in row_re.finditer(section):
        path = rm.group(2).strip()
        # Clean markdown bold/links from path
        path = re.sub(r"\*\*", "", path)
        path = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", path)
        path = path.strip()

        relevance = rm.group(3).strip()
        cat_raw = rm.group(4).strip()

        # Map category to E/C/S
        cat_map = {"Edit": "E", "Context/Test": "C", "Supp/Docs": "S"}
        category = cat_map.get(cat_raw, "C")

        files.append({
            "path": path,
            "category": category,
            "relevance": relevance,
        })
    return files


def _parse_queries(section: str) -> dict[str, str]:
    """Extract Q1, Q2, Q3 query text from issue section."""
    queries: dict[str, str] = {}

    # Pattern: **Q1** *(anchored, precise)*:\ntext until next **Q or ---
    q_pattern = re.compile(
        r"\*\*Q(\d)\*\*\s*\*\([^)]+\)\*:\s*\n(.*?)(?=\n\*\*Q\d\*\*|\n---|\Z)",
        re.DOTALL,
    )
    for qm in q_pattern.finditer(section):
        q_num = f"Q{qm.group(1)}"
        q_text = qm.group(2).strip()
        # Collapse newlines into spaces for clean query
        q_text = re.sub(r"\n+", " ", q_text).strip()
        queries[q_num] = q_text

    return queries


# =====================================================================
# 2. Call Recon via MCP (streamable-http with session management)
# =====================================================================


class MCPSession:
    """Manages an MCP session with init handshake and session ID."""

    def __init__(self, mcp_url: str) -> None:
        self.url = mcp_url
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.session_id: str | None = None
        self._call_id = 0

    def initialize(self) -> None:
        """Perform MCP initialize handshake."""
        init_payload = {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "recon-bench", "version": "1.0"},
            },
        }
        r = httpx.post(self.url, json=init_payload, headers=self.headers, timeout=10)
        r.raise_for_status()
        self.session_id = r.headers.get("mcp-session-id", "")
        self.headers["Mcp-Session-Id"] = self.session_id

        # Send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        httpx.post(self.url, json=notif, headers=self.headers, timeout=10)

    def call_tool(self, name: str, arguments: dict, timeout: int = 120) -> dict:
        """Call an MCP tool and return the parsed response."""
        self._call_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": f"call-{self._call_id}",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        r = httpx.post(self.url, json=payload, headers=self.headers, timeout=timeout)
        r.raise_for_status()
        return r.json()


def call_recon(task: str, session: MCPSession, timeout: int = 120) -> dict:
    """Call recon and return the full result (reading from cache if needed)."""
    raw = session.call_tool("recon", {"task": task}, timeout=timeout)

    # Parse MCP response
    result = raw.get("result", {})
    content = result.get("content", [])

    for item in content:
        if item.get("type") == "text":
            try:
                data = json.loads(item["text"])
            except (json.JSONDecodeError, TypeError):
                continue

            # If delivered as resource, read from cache
            if data.get("delivery") == "resource":
                # Extract recon_id from agentic_hint
                hint = data.get("agentic_hint", "")
                cache_match = re.search(r"recon_result/([a-f0-9]+)\.json", hint)
                if cache_match:
                    cache_file = RECON_CACHE_DIR / f"{cache_match.group(1)}.json"
                    if cache_file.exists():
                        return json.loads(cache_file.read_text())

            # If inline, data IS the result
            if "files" in data:
                return data

    # Fallback: try structuredContent
    sc = result.get("structuredContent", {})
    if sc.get("delivery") == "resource":
        hint = sc.get("agentic_hint", "")
        cache_match = re.search(r"recon_result/([a-f0-9]+)\.json", hint)
        if cache_match:
            cache_file = RECON_CACHE_DIR / f"{cache_match.group(1)}.json"
            if cache_file.exists():
                return json.loads(cache_file.read_text())

    return {"files": [], "summary": "No result parsed"}


def extract_returned_files(recon_data: dict) -> list[dict[str, str]]:
    """Extract file paths and tier assignments from v6 recon result.

    Returns list of {path, tier, similarity, combined_score} dicts.
    """
    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    for entry in recon_data.get("files", []):
        p = entry.get("path", "")
        tier = entry.get("tier", "summary_only")
        if p and p not in seen_paths:
            files.append({
                "path": p,
                "tier": tier,
                "similarity": str(entry.get("similarity", 0.0)),
                "combined_score": str(entry.get("combined_score", 0.0)),
            })
            seen_paths.add(p)

    return files


# =====================================================================
# 3. Metrics computation
# =====================================================================


def compute_query_metrics(
    returned_files: list[dict[str, str]],
    gt_files: list[dict],
) -> dict[str, Any]:
    """Compute all metrics for a single query against ground truth."""
    returned_paths = {f["path"] for f in returned_files}

    gt = {f["path"] for f in gt_files}

    # Core retrieval metrics
    tp = len(returned_paths & gt)
    precision = tp / len(returned_paths) if returned_paths else 0.0
    recall = tp / len(gt) if gt else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    noise_ratio = len(returned_paths - gt) / len(returned_paths) if returned_paths else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "noise_ratio": round(noise_ratio, 4),
        "returned_files": sorted(returned_paths),
        "returned_count": len(returned_paths),
        "gt_count": len(gt),
        "_detail": {
            "tp": tp,
            "fp": len(returned_paths - gt),
            "fn": len(gt - returned_paths),
            "hits": sorted(returned_paths & gt),
            "misses": sorted(gt - returned_paths),
            "extras": sorted(returned_paths - gt),
        },
    }


# =====================================================================
# 4. Aggregation
# =====================================================================


def compute_aggregates(
    issue_results: dict[str, dict],
    issues: list[dict],
) -> dict[str, Any]:
    """Compute aggregate statistics across all issues."""
    # By query level
    by_q: dict[str, list[dict]] = {"Q1": [], "Q2": [], "Q3": []}
    for _issue_num, qresults in issue_results.items():
        for q in ("Q1", "Q2", "Q3"):
            if q in qresults:
                by_q[q].append(qresults[q])

    by_query_level = {}
    for q, results in by_q.items():
        if not results:
            continue
        by_query_level[q] = {
            "avg_precision": round(_mean([r["precision"] for r in results]), 4),
            "avg_recall": round(_mean([r["recall"] for r in results]), 4),
            "avg_f1": round(_mean([r["f1"] for r in results]), 4),
            "avg_noise_ratio": round(_mean([r["noise_ratio"] for r in results]), 4),
            "avg_returned_count": round(_mean([r.get("returned_count", len(r["returned_files"])) for r in results]), 1),
        }

    # Overall
    all_f1 = []
    for qresults in issue_results.values():
        for q in ("Q1", "Q2", "Q3"):
            if q in qresults:
                all_f1.append(qresults[q]["f1"])

    overall = {
        "mean_f1": round(_mean(all_f1), 4) if all_f1 else 0.0,
        "median_f1": round(_median(all_f1), 4) if all_f1 else 0.0,
        "min_f1": round(min(all_f1), 4) if all_f1 else 0.0,
        "max_f1": round(max(all_f1), 4) if all_f1 else 0.0,
    }

    # By difficulty
    difficulty_map: dict[str, str] = {iss["number"]: iss["difficulty"] for iss in issues}
    by_diff: dict[str, list[float]] = {"simple": [], "medium": [], "complex": []}

    for issue_num, qresults in issue_results.items():
        diff = difficulty_map.get(issue_num, "medium")
        for q in ("Q1", "Q2", "Q3"):
            if q in qresults:
                by_diff[diff].append(qresults[q]["f1"])

    by_difficulty = {}
    for diff in ("simple", "medium", "complex"):
        by_difficulty[diff] = {
            "count": len(by_diff[diff]) // 3 if by_diff[diff] else 0,  # issues, not queries
            "avg_f1": round(_mean(by_diff[diff]), 4) if by_diff[diff] else 0.0,
        }

    return {
        "by_query_level": by_query_level,
        "overall": overall,
        "by_difficulty": by_difficulty,
    }


def _mean(vals: list[float]) -> float:
    return statistics.mean(vals) if vals else 0.0


def _median(vals: list[float]) -> float:
    return statistics.median(vals) if vals else 0.0


# =====================================================================
# 5. Git helpers
# =====================================================================


def get_git_sha(repo_path: Path) -> str:
    """Get current git SHA for a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()[:12]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


# =====================================================================
# 6. Alert flags
# =====================================================================


def check_alerts(issue_results: dict[str, dict]) -> list[str]:
    """Check for alert conditions across all issues."""
    alerts: list[str] = []
    for issue_num, qresults in sorted(issue_results.items(), key=lambda x: int(x[0])):
        q1 = qresults.get("Q1", {})
        if q1.get("recall", 1.0) < 0.5:
            alerts.append(f"🔴 #{issue_num} Q1 Recall < 0.5 ({q1['recall']:.2f})")
        for q in ("Q1", "Q2", "Q3"):
            qr = qresults.get(q, {})
            if qr.get("precision", 1.0) < 0.3:
                alerts.append(f"🟡 #{issue_num} {q} Precision < 0.3 ({qr['precision']:.2f})")
    return alerts


# =====================================================================
# 7. Main
# =====================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Recon Evaluation Benchmark")
    parser.add_argument("--port", type=int, default=7777, help="CodePlane daemon port")
    parser.add_argument("--dry-run", action="store_true", help="Parse doc only, don't call Recon")
    parser.add_argument("--issues", type=str, default=None, help="Comma-separated issue numbers to run (default: all)")
    args = parser.parse_args()

    mcp_url = f"http://127.0.0.1:{args.port}/mcp"
    start_time = datetime.now(timezone.utc)
    start_iso = start_time.strftime("%Y-%m-%dT%H%M%SZ")

    print("=" * 70)
    print(f"RECON EVALUATION BENCHMARK — {start_iso}")
    print("=" * 70)

    # 1. Parse evaluation document
    print(f"\n📄 Parsing {EVAL_DOC.name}...")
    issues = parse_eval_doc(EVAL_DOC)
    print(f"   Found {len(issues)} issues with ground truth")

    # Filter issues if requested
    if args.issues:
        requested = set(args.issues.split(","))
        issues = [iss for iss in issues if iss["number"] in requested]
        print(f"   Filtered to {len(issues)} issues: {[i['number'] for i in issues]}")

    # Validate parsing
    for iss in issues:
        if not iss["gt_files"]:
            print(f"   ⚠️  #{iss['number']} — no GT files parsed!")
        for q in ("Q1", "Q2", "Q3"):
            if q not in iss["queries"]:
                print(f"   ⚠️  #{iss['number']} — missing {q} query!")

    total_queries = sum(len(iss["queries"]) for iss in issues)
    print(f"   Total queries: {total_queries}")

    if args.dry_run:
        print("\n🔍 DRY RUN — showing parsed data:")
        for iss in issues:
            print(f"\n   #{iss['number']} — {iss['title']}")
            print(f"   GT files: {len(iss['gt_files'])}")
            for q in ("Q1", "Q2", "Q3"):
                qt = iss["queries"].get(q, "MISSING")
                print(f"   {q}: {qt[:80]}...")
        return

    # 2. Verify daemon health
    print(f"\n🔌 Checking daemon at port {args.port}...")
    try:
        health = httpx.get(f"http://127.0.0.1:{args.port}/health", timeout=5).json()
        print(f"   Status: {health['status']}, Version: {health.get('version', '?')}")
    except Exception as e:
        print(f"   ❌ Daemon not reachable: {e}")
        sys.exit(1)

    # 3. Initialize MCP session
    print("   Initializing MCP session...")
    session = MCPSession(mcp_url)
    try:
        session.initialize()
        print(f"   Session: {session.session_id[:20]}...")
    except Exception as e:
        print(f"   ❌ MCP init failed: {e}")
        sys.exit(1)

    # 4. Get git SHAs
    recon_commit = get_git_sha(REPO_ROOT)
    evee_commit = get_git_sha(EVEE_REPO)
    print(f"   Recon commit: {recon_commit}")
    print(f"   Evee commit:  {evee_commit}")

    # Clear recon cache before run
    for f in RECON_CACHE_DIR.glob("*.json"):
        f.unlink()
    print("   Cleared recon cache")

    # 5. Run queries
    issue_results: dict[str, dict] = {}
    total_done = 0

    for iss in issues:
        issue_num = iss["number"]
        print(f"\n{'─' * 60}")
        print(f"   #{issue_num} — {iss['title']}")
        print(f"   GT: {len(iss['gt_files'])} files")

        qresults: dict[str, dict] = {}
        for q in ("Q1", "Q2", "Q3"):
            query = iss["queries"].get(q)
            if not query:
                print(f"   {q}: SKIPPED (no query)")
                continue

            total_done += 1
            print(f"   {q} [{total_done}/{total_queries}]: ", end="", flush=True)

            t0 = time.monotonic()
            try:
                recon_data = call_recon(query, session)
                elapsed = time.monotonic() - t0
                returned = extract_returned_files(recon_data)
                metrics = compute_query_metrics(returned, iss["gt_files"])
                qresults[q] = metrics

                # Compact result line
                returned_count = len(returned)
                print(
                    f"{returned_count} files | "
                    f"P={metrics['precision']:.2f} R={metrics['recall']:.2f} "
                    f"F1={metrics['f1']:.2f} "
                    f"NR={metrics['noise_ratio']:.2f} "
                    f"({elapsed:.1f}s)"
                )
            except Exception as e:
                elapsed = time.monotonic() - t0
                print(f"ERROR ({elapsed:.1f}s): {e}")
                qresults[q] = {
                    "precision": 0.0, "recall": 0.0, "f1": 0.0,
                    "noise_ratio": 1.0,
                    "returned_files": [],
                    "error": str(e),
                }

        issue_results[issue_num] = qresults

    # 5. Aggregate
    print(f"\n{'=' * 70}")
    print("AGGREGATION")
    print("=" * 70)
    aggregates = compute_aggregates(issue_results, issues)

    for q in ("Q1", "Q2", "Q3"):
        qs = aggregates["by_query_level"].get(q, {})
        print(f"   {q}: P={qs.get('avg_precision', 0):.3f} R={qs.get('avg_recall', 0):.3f} "
              f"F1={qs.get('avg_f1', 0):.3f} NR={qs.get('avg_noise_ratio', 0):.3f}")

    ov = aggregates["overall"]
    print(f"\n   Overall: mean_F1={ov['mean_f1']:.3f} median_F1={ov['median_f1']:.3f} "
          f"min={ov['min_f1']:.3f} max={ov['max_f1']:.3f}")

    for diff in ("simple", "medium", "complex"):
        ds = aggregates["by_difficulty"].get(diff, {})
        print(f"   {diff.capitalize()}: n={ds.get('count', 0)} F1={ds.get('avg_f1', 0):.3f}")

    # 6. Alerts
    alerts = check_alerts(issue_results)
    if alerts:
        print(f"\n{'=' * 70}")
        print("ALERTS")
        print("=" * 70)
        for a in alerts:
            print(f"   {a}")

    # 7. Write results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date_stamp = start_time.strftime("%Y-%m-%d_%H%M%S")
    out_file = RESULTS_DIR / f"recon_v6_{date_stamp}.json"

    output = {
        "meta": {
            "pipeline_version": "v6",
            "date": start_time.strftime("%Y-%m-%d"),
            "started_at": start_iso,
            "recon_commit": recon_commit,
            "evee_commit": evee_commit,
            "total_issues": len(issues),
            "total_queries": total_queries,
            "elapsed_seconds": round(time.monotonic() - time.monotonic(), 1),  # placeholder
        },
        "issues": {},
        "aggregates": aggregates,
    }

    # Strip _detail from issue results for clean schema output
    for issue_num, qresults in issue_results.items():
        clean_q: dict[str, Any] = {}
        for q, metrics in qresults.items():
            clean_q[q] = {
                "precision": metrics["precision"],
                "recall": metrics["recall"],
                "f1": metrics["f1"],
                "noise_ratio": metrics["noise_ratio"],
                "returned_files": metrics["returned_files"],
                "returned_count": metrics.get("returned_count", len(metrics["returned_files"])),
                "gt_count": metrics.get("gt_count", 0),
            }
        output["issues"][issue_num] = clean_q

    # Fix elapsed
    output["meta"]["elapsed_seconds"] = round(
        (datetime.now(timezone.utc) - start_time).total_seconds(), 1
    )

    with open(out_file, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n📁 Results written to: {out_file}")
    print(f"   Elapsed: {output['meta']['elapsed_seconds']:.0f}s")

    # Print alert summary
    if alerts:
        print(f"\n   ⚠️  {len(alerts)} alert(s) — see ALERTS section above")


if __name__ == "__main__":
    main()
