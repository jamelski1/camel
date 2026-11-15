"""Centralized model configuration, preflight checks, and error recovery
(SENG 691 Phase 2, Enhancement 3).

Phase 1 resolved the provider, model, and API key in two different places
(`make_model` and `choose_model`), and a configuration mistake only surfaced
after the user pressed Start and the first model call failed mid-run. This
module puts the checks in one place:

  * `preflight` validates the resolved configuration before a run starts:
    the provider is known, a model name resolves, the API key is present,
    and (optionally) the model object can be constructed. Each check reports
    pass or fail with the fix, so a bad configuration is caught on the setup
    screen instead of mid-run.
  * `classify_error` and `guidance_for` turn provider errors into a category
    (rate limit, rejected key, unknown model, timeout, connection) and one
    line of advice, the same wording the Playground has always shown.
  * `call_with_retry` retries the transient categories (rate limit, timeout,
    connection) with exponential backoff and reports each retry through a
    callback, while permanent errors (a rejected key, an unknown model) fail
    at once with their guidance instead of being retried pointlessly.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

TRANSIENT = ("rate_limit", "timeout", "connection")


def classify_error(exc: Exception) -> str:
    """Bucket a provider error: rate_limit, auth, model_not_found, timeout,
    connection, or unknown. Mirrors the cases playground.friendly() handles."""
    low = str(exc).lower()
    if any(s in low for s in ("429", "rate limit", "rate_limit", "quota",
                              "too many requests")):
        return "rate_limit"
    if any(s in low for s in ("401", "invalid api key", "incorrect api key",
                              "unauthorized", "403", "forbidden")):
        return "auth"
    if any(s in low for s in ("404", "model not found", "does not exist",
                              "no endpoints")):
        return "model_not_found"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "connection" in low or "connect" in low:
        return "connection"
    return "unknown"


def guidance_for(category: str) -> str:
    """One line of advice per error category, for a non-developer reader."""
    return {
        "rate_limit": ("The model provider's free-tier limit was reached. Wait a "
                       "minute and try again, or switch provider under Advanced "
                       "(free limits: OpenRouter 50 requests/day until $10 of "
                       "credit is bought, then 1,000/day; Groq about 1,000/day)."),
        "auth": "The API key was rejected. Check the key for the chosen provider.",
        "model_not_found": ("That model name is not available on this provider; "
                            "clear the Model box to use the provider default."),
        "timeout": "The provider did not answer in time. Try again.",
        "connection": "The provider could not be reached. Check the connection and try again.",
        "unknown": "Something went wrong calling the model. See the details below.",
    }.get(category, "Something went wrong calling the model.")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class PreflightResult:
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def render(self) -> str:
        lines = ["**Model setup check**"]
        for c in self.checks:
            mark = "OK" if c.ok else "Needs attention"
            lines.append(f"- {c.name}: {mark}. {c.detail}")
        lines.append("**Ready to start.**" if self.ok
                     else "**Fix the items above before starting.**")
        return "\n".join(lines)


def preflight(platforms: dict, platform: str | None, model_name: str | None,
              stub: bool = False, env: dict | None = None, create=None) -> PreflightResult:
    """Validate a resolved model configuration before a run starts.

    `platforms` is the shared table {name: (key_env_var, default_model)}.
    `create`, when given, is called as create(platform, model_name) to prove
    the model object can be constructed; it spends no tokens.
    """
    env = os.environ if env is None else env
    result = PreflightResult()
    if stub:
        result.checks.append(Check(
            "Offline stub", True,
            "Offline test mode is on: canned replies, no key or provider needed."))
        return result

    platform = (platform or env.get("MODEL_PLATFORM") or "openai").lower()
    known = platform in platforms
    key_var, default_model = platforms.get(platform, (f"{platform.upper()}_API_KEY", None))
    resolved_model = model_name or env.get("MODEL_NAME") or default_model

    result.checks.append(Check(
        "Provider", known or bool(resolved_model),
        f"'{platform}' is a configured provider." if known
        else f"'{platform}' is not in the provider table; it will be used as a "
             "custom platform with the model name given."))
    result.checks.append(Check(
        "Model name", bool(resolved_model),
        f"Model resolves to '{resolved_model}'." if resolved_model
        else "No model name given and this provider has no default. "
             "Enter a model name."))
    if key_var is None:
        result.checks.append(Check(
            "API key", True, "This provider needs no API key (local model)."))
    else:
        present = bool((env.get(key_var) or "").strip())
        result.checks.append(Check(
            "API key", present,
            f"{key_var} is set." if present
            else f"{key_var} is not set. Put it in .env, or tick 'offline stub' "
                 "to try the UI without a key."))
    if create is not None and result.ok:
        try:
            create(platform, resolved_model)
            result.checks.append(Check(
                "Model object", True,
                "The model object was constructed. The key itself is verified "
                "by the provider on the first call."))
        except Exception as exc:  # construction failed: report, do not raise
            result.checks.append(Check(
                "Model object", False,
                f"{guidance_for(classify_error(exc))} Details: {str(exc)[:200]}"))
    return result


def call_with_retry(fn, attempts: int = 3, base_delay: float = 0.5,
                    max_delay: float = 8.0, sleep=time.sleep, on_retry=None):
    """Call fn(), retrying transient provider errors with exponential backoff.

    `on_retry(attempt, category, exc)` is called before each retry so the
    caller can tell the user what happened. Permanent errors raise at once.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            category = classify_error(exc)
            if category not in TRANSIENT or attempt == attempts:
                raise
            if on_retry is not None:
                on_retry(attempt, category, exc)
            sleep(min(base_delay * 2 ** (attempt - 1), max_delay))
    raise last_exc  # pragma: no cover - the loop always returns or raises
