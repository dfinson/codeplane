"""Signal collection — recon_raw_signals phase.

Re-runnable step that calls ``recon_raw_signals()`` for every query
in the ground truth and writes the candidate pool with per-retriever
signals.

Output: ``data/{repo_id}/signals/candidates_rank.jsonl``

See §4.3 Phase 3 of ranking-design.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


def _init_mcp_session(mcp_url: str, timeout: int = 30) -> tuple[httpx.Client, str]:
    """Create an MCP session and return (client, session_id)."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    client = httpx.Client(timeout=timeout)
    r = client.post(
        mcp_url,
        json={
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "cpl-signal-collector", "version": "1.0"},
            },
        },
        headers=headers,
    )
    r.raise_for_status()
    session_id = r.headers.get("mcp-session-id", "")
    client.post(
        mcp_url,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**headers, "Mcp-Session-Id": session_id},
    )
    return client, session_id


def _call_raw_signals(
    client: httpx.Client,
    mcp_url: str,
    session_id: str,
    query: str,
    seeds: list[str],
    pins: list[str],
    timeout: int = 120,
) -> dict[str, Any]:
    """Call recon_raw_signals and return the parsed response."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Session-Id": session_id,
    }
    r = client.post(
        mcp_url,
        json={
            "jsonrpc": "2.0",
            "id": "rs",
            "method": "tools/call",
            "params": {
                "name": "recon_raw_signals",
                "arguments": {
                    "query": query,
                    "seeds": seeds,
                    "pins": pins,
                },
            },
        },
        headers=headers,
        timeout=timeout,
    )
    r.raise_for_status()
    result = r.json()
    content = result.get("result", {}).get("content", [{}])[0].get("text", "{}")
    return json.loads(content)


def collect_signals(
    repo_id: str,
    data_dir: Path,
    mcp_url: str = "http://127.0.0.1:7654/mcp",
) -> dict[str, Any]:
    """Collect retrieval signals for all queries in ground truth.

    Args:
        repo_id: Repository identifier.
        data_dir: Path to ``data/{repo_id}/`` containing ground truth JSONL.
        mcp_url: MCP endpoint URL for the daemon running on this repo.

    Returns:
        Summary dict with counts.
    """
    gt_dir = data_dir / "ground_truth"
    queries_file = gt_dir / "queries.jsonl"
    touched_file = gt_dir / "touched_objects.jsonl"

    if not queries_file.exists():
        raise FileNotFoundError(f"No queries.jsonl in {gt_dir}")

    # Load queries
    queries = [json.loads(ln) for ln in queries_file.read_text().splitlines() if ln.strip()]

    # Load touched def_uids per run_id for labeling
    touched_uids: dict[str, set[str]] = {}
    if touched_file.exists():
        for ln in touched_file.read_text().splitlines():
            if not ln.strip():
                continue
            obj = json.loads(ln)
            touched_uids.setdefault(obj["run_id"], set()).add(obj["def_uid"])

    # Init MCP session
    client, session_id = _init_mcp_session(mcp_url)

    signals_dir = data_dir / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    out_path = signals_dir / "candidates_rank.jsonl"

    total_candidates = 0
    total_queries = 0

    with open(out_path, "w") as out:
        for q in queries:
            run_id = q["run_id"]
            query_id = q["query_id"]
            seeds = q.get("seeds", [])
            pins = q.get("pins", [])
            relevant_uids = touched_uids.get(run_id, set())

            # Call raw_signals
            try:
                result = _call_raw_signals(
                    client, mcp_url, session_id,
                    q["query_text"], seeds, pins,
                )
            except Exception as e:
                print(f"  ERROR {query_id}: {e}")
                continue

            # Handle sidecar cache delivery
            candidates = result.get("candidates", [])
            if not candidates and "cache_id" in result:
                # Read from sidecar cache
                cache_path = Path(f".codeplane/cache/{result['cache_id']}.json")
                if cache_path.exists():
                    cache_data = json.loads(cache_path.read_text())
                    for k in sorted(cache_data.keys()):
                        if k.startswith("candidates."):
                            for ln in cache_data[k].split("\n"):
                                if ln.strip():
                                    try:
                                        candidates.append(json.loads(ln))
                                    except json.JSONDecodeError:
                                        pass

            query_features = result.get("query_features", {})

            for cand in candidates:
                row = {
                    "run_id": run_id,
                    "query_id": query_id,
                    **cand,
                    "query_len": query_features.get("query_len", 0),
                    "has_identifier": query_features.get("has_identifier", False),
                    "has_path": query_features.get("has_path", False),
                    "label_relevant": cand.get("def_uid", "") in relevant_uids,
                }
                out.write(json.dumps(row) + "\n")
                total_candidates += 1

            total_queries += 1
            print(f"  {query_id}: {len(candidates)} candidates")

    client.close()

    summary = {
        "repo_id": repo_id,
        "queries_processed": total_queries,
        "total_candidates": total_candidates,
    }
    (signals_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
