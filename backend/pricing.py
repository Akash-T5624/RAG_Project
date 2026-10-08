"""Token pricing used for every cost figure this app records.

Prices are USD per 1,000,000 tokens and must be re-checked against the
provider's published price list whenever a model is added or swapped.
"""

from __future__ import annotations

# Groq serverless list price, checked 2026-10-08 against
# https://console.groq.com/docs/models (qwen3.8-27b: $0.80 in / $4.00 out;
# gpt-oss-120b: $0.15 in / $0.60 out; gpt-oss-20b: $0.10 in / $0.50 out).
PRICING = {
    "qwen/qwen3.8-27b": {"input_per_1m": 0.80, "output_per_1m": 4.00},
    "openai/gpt-oss-120b": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "openai/gpt-oss-20b": {"input_per_1m": 0.10, "output_per_1m": 0.50},
    "llama-3.3-70b-versatile": {"input_per_1m": 0.59, "output_per_1m": 0.79},
}

PRICING_SOURCE = "Groq published list price, verified 2026-10-08"


def price_for(model: str) -> dict:
    return PRICING.get(model, {"input_per_1m": 0.0, "output_per_1m": 0.0})


def token_cost(model: str, usage: dict | None) -> float:
    """USD cost of one completion from model-reported token usage."""
    usage = usage or {}
    price = price_for(model)
    return (
        int(usage.get("prompt_tokens", 0)) / 1_000_000.0 * price["input_per_1m"]
        + int(usage.get("completion_tokens", 0)) / 1_000_000.0 * price["output_per_1m"]
    )


def zero_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
