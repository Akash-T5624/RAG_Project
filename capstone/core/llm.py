"""Model access: the Week 7 Groq client (retries, wall-clock cap), plus per-call cost."""

import os

import capstone.core.paths  # noqa: F401

os.environ.setdefault("WEEK7_MODEL", os.getenv("CAPSTONE_MODEL") or "qwen/qwen3.8-27b")

import llm as _w7  # noqa: E402  evals/week7/llm.py (loads backend/.env)
import pricing  # noqa: E402  backend/pricing.py

WallBudgetExceeded = _w7.WallBudgetExceeded


def model_name():
    return _w7.get_model()


def api_key_present():
    try:
        _w7.get_api_key()
        return True
    except RuntimeError:
        return False


def chat(messages, *, temperature=0.0, max_tokens=1000, wall_deadline=None):
    """Return (content, usage, cost_usd). Cost uses backend/pricing.py for the active model."""
    content, usage = _w7.chat(messages, temperature=temperature, max_tokens=max_tokens, wall_deadline=wall_deadline)
    return content, usage, pricing.token_cost(model_name(), usage)
