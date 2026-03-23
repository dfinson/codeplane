#!/usr/bin/env python3
"""Fetch model pricing from LiteLLM and write a compact JSON file.

Run manually or via GitHub Actions on push to main:
    python tools/update_model_pricing.py

Outputs: backend/data/model_pricing.json
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Providers whose models we want to track pricing for.
PROVIDERS = {"anthropic", "openai"}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "backend" / "data" / "model_pricing.json"


def fetch_litellm_prices() -> dict:
    with urllib.request.urlopen(LITELLM_URL, timeout=30) as resp:
        return json.loads(resp.read())


def extract_pricing(raw: dict) -> dict:
    """Extract relevant pricing fields, keyed by model name."""
    models: dict[str, dict] = {}
    for key, entry in raw.items():
        provider = entry.get("litellm_provider", "")
        if provider not in PROVIDERS:
            continue
        mode = entry.get("mode", "")
        if mode != "chat":
            continue
        # Skip date-suffixed duplicates if the short alias exists
        # (e.g. keep "claude-sonnet-4-6" but skip "claude-sonnet-4-6-20260205")
        # We still include them — the frontend normalizes model names to match.

        input_cost = entry.get("input_cost_per_token", 0)
        output_cost = entry.get("output_cost_per_token", 0)
        if not input_cost and not output_cost:
            continue

        models[key] = {
            "provider": provider,
            "input": round(input_cost * 1_000_000, 4),   # $/MTok
            "output": round(output_cost * 1_000_000, 4),  # $/MTok
            "cache_read": round(entry.get("cache_read_input_token_cost", 0) * 1_000_000, 4),
            "cache_write": round(entry.get("cache_creation_input_token_cost", 0) * 1_000_000, 4),
            "max_input_tokens": entry.get("max_input_tokens", 0),
            "max_output_tokens": entry.get("max_output_tokens", 0),
        }

    return models


def main() -> None:
    raw = fetch_litellm_prices()
    pricing = extract_pricing(raw)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(pricing, indent=2, sort_keys=True) + "\n")

    print(f"Wrote {len(pricing)} model prices to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
