import json
import os
import queue
import re
import threading
import time

import requests

from config import (
    ENV_PATH,
    GROQ_API_URL,
    LLM_BACKOFF_MULT,
    LLM_BASE_BACKOFF_S,
    LLM_MAX_ATTEMPTS,
    LLM_RETRY_429_S,
    LLM_TIMEOUT_S,
    MODEL,
)


def load_env_file(env_path=ENV_PATH):
    """Load key/value settings from a local .env file without extra packages."""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


def get_api_key():
    key = os.getenv("GROQ_API_KEY")
    if not key or key in ("your_groq_api_key_here", "paste_your_groq_api_key_here"):
        raise RuntimeError("GROQ_API_KEY not configured in backend/.env")
    return key


def get_model():
    return os.getenv("WEEK7_MODEL") or MODEL


class WallBudgetExceeded(RuntimeError):
    """Raised when a call cannot start/finish inside the agent's wall-clock budget."""


def _post_once(payload, cap_s):
    """Single HTTP POST bounded by cap_s wall-clock seconds.

    requests can block well past its own ``timeout`` when a server drips bytes
    slowly, so the request runs in a daemon thread and a watchdog aborts the
    whole call when cap_s is reached.  The abandoned worker is a daemon and
    eventually times out by itself; the agent never waits for it.
    """
    q = queue.Queue(maxsize=1)

    def worker():
        try:
            resp = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {get_api_key()}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=LLM_TIMEOUT_S,
            )
            q.put(("ok", resp))
        except Exception as exc:  # noqa: BLE001 - carry every failure to caller
            q.put(("err", exc))

    threading.Thread(target=worker, daemon=True).start()
    try:
        tag, value = q.get(timeout=cap_s)
    except queue.Empty:
        raise WallBudgetExceeded(
            f"call did not finish within the {cap_s:.1f}s wall-clock cap"
        )
    if tag == "ok":
        return value
    raise value


def _parse_retry_after(resp):
    """Seconds to wait for a 429, from headers when available."""
    reset = resp.headers.get("x-ratelimit-reset-tokens")
    if reset:
        m = re.match(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", reset)
        if m:
            h, mi, s = (float(x) if x else 0.0 for x in m.groups())
            if h or mi or s:
                return h * 3600 + mi * 60 + s
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return None


def _post_with_retry(payload, deadline_s=None):
    """POST with exponential backoff on 429 / 5xx / network errors.

    When deadline_s is given (seconds remaining on the wall-clock budget) every
    attempt is hard-bounded by the remaining time and the loop stops as soon as
    the deadline is reached, raising WallBudgetExceeded, so a slow/hung Groq
    call can never blow past the budget.
    """
    attempt = 0
    backoff = LLM_BASE_BACKOFF_S
    last_err = None
    while attempt < LLM_MAX_ATTEMPTS:
        if deadline_s is not None:
            cap_s = max(deadline_s, 1.0)
            if deadline_s <= 0:
                raise WallBudgetExceeded(
                    f"wall-clock budget exhausted before call could start "
                    f"({deadline_s:.1f}s left)"
                )
        else:
            cap_s = float("inf")

        try:
            t0 = time.perf_counter()
            resp = _post_once(payload, cap_s=cap_s)
            spent = time.perf_counter() - t0
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                # Model-level rate limit with a multi-minute cooling window on
                # the free tier.  Sleep the FULL Retry-After exactly once;
                # retrying sooner only refreshes the window and pushes the
                # model hotter.  Never exceed the wall-clock budget.
                retry_after = _parse_retry_after(resp) or LLM_RETRY_429_S
                last_err = f"HTTP 429 (retry_after={retry_after:.1f}s)"
                wait = min(max(retry_after, LLM_RETRY_429_S), 240.0)
            elif resp.status_code in (500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}"
                wait = min(backoff * (LLM_BACKOFF_MULT ** (attempt - 1)), 30.0)
            else:
                raise requests.HTTPError(f"{resp.status_code}: {resp.text[:200]}")
            if deadline_s is not None:
                wait = min(wait, max(0.0, deadline_s))
                deadline_s -= spent
            if wait > 0:
                time.sleep(wait)
                if deadline_s is not None:
                    deadline_s -= wait
        except requests.RequestException as exc:
            last_err = str(exc)
            wait = min(backoff * (LLM_BACKOFF_MULT ** (attempt - 1)), 30.0)
            if deadline_s is not None:
                wait = min(wait, max(0.0, deadline_s))
            if wait > 0:
                time.sleep(wait)
                if deadline_s is not None:
                    deadline_s -= wait
        attempt += 1
    raise RuntimeError(
        f"Groq call failed after {LLM_MAX_ATTEMPTS} attempts: {last_err}"
    )


def chat(messages, *, temperature=0.0, max_tokens=768, wall_deadline=None):
    """One chat completion.

    Returns (content: str, usage: dict).  usage is the model-reported
    token usage (keys: prompt_tokens, completion_tokens, total_tokens,
    reasoning_tokens).

    wall_deadline (seconds) bounds the whole call; when the deadline is crossed
    the function raises WallBudgetExceeded instead of returning.
    """
    payload = {
        "model": get_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Groq only accepts response_format json_object when the messages already
    # contain the word "json"; fall back to free-form and let the caller's JSON
    # parser extract the object when they do not.
    if "json" in json.dumps(messages).lower():
        payload["response_format"] = {"type": "json_object"}
    resp = _post_with_retry(payload, deadline_s=wall_deadline)
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return content, {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
        "reasoning_tokens": int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        ),
    }


def token_cost(usage):
    """USD cost of one call using the configured pricing assumption."""
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    from config import PRICE_PER_1M_INPUT, PRICE_PER_1M_OUTPUT

    return (
        prompt_tokens / 1_000_000.0 * PRICE_PER_1M_INPUT
        + completion_tokens / 1_000_000.0 * PRICE_PER_1M_OUTPUT
    )