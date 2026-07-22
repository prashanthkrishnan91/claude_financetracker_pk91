"""Thin Anthropic wrapper for the agent pipeline.

Centralises model name, JSON parsing, error handling, rate-limit backoff,
and multi-model failover (Sonnet → Haiku) so every agent node has a single
`await ask_json(...)` call that ALWAYS returns a usable dict.

Hardening (SEV-1 fixes):
  * Per-call timeout via asyncio.wait_for
  * 429 / overloaded retries with exponential backoff + jitter (max 3–4 tries)
  * Sonnet → Haiku failover on error / timeout / rate limit (single fallback)
  * Prompts for the Haiku fallback are auto-trimmed ~60% smaller
  * Detailed logging for every transition (success, 429, fallback, failure)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

# Primary model for the agent pipeline — speed/quality sweet spot.
PRIMARY_MODEL = "claude-sonnet-4-6"
# Fallback model — cheaper + much higher rate limits, shorter prompts.
FALLBACK_MODEL = "claude-haiku-4-5-20251001"
# Back-compat alias used by existing imports.
AGENT_MODEL = PRIMARY_MODEL

# Per-call timeout (seconds). 25s matches the reliability spec — gives Claude
# enough headroom on large portfolios while still bounding pipeline latency.
PRIMARY_TIMEOUT_S = 25.0
FALLBACK_TIMEOUT_S = 18.0

# Exponential backoff schedule (seconds) for 429 / overloaded responses.
# Capped at 4 attempts total (initial + 3 retries) so worst-case latency stays
# bounded. Jitter is added to avoid thundering-herd retries.
_BACKOFF_SCHEDULE_S = (2.0, 5.0, 10.0, 20.0)
_JSON_ONLY_CONTRACT = (
    "\n\nReturn exactly one JSON object. No markdown fences. "
    "No prose before/after JSON. Include all required keys; use null/[]/\"\" defaults."
)

# Provider error classification (best-effort, message/status-based — the
# Anthropic SDK does not expose a stable machine-readable error taxonomy).
# Quota/authentication failures are never worth retrying within a call: the
# key/account is broken until a human fixes it, so callers should make one
# provider call, skip any repair/fallback attempts, and let the DURABLE task
# retry own the backoff instead of burning attempts against a dead key.
ERROR_CLASS_QUOTA = "quota"
ERROR_CLASS_AUTHENTICATION = "authentication"
ERROR_CLASS_RATE_LIMIT = "rate_limit"
ERROR_CLASS_TRANSIENT = "transient"
ERROR_CLASS_UNKNOWN = "unknown"
NON_RETRYABLE_PROVIDER_CLASSES = (ERROR_CLASS_QUOTA, ERROR_CLASS_AUTHENTICATION)


class LLMClient:
    """Stateless async wrapper around anthropic.Anthropic with failover.

    Usage is unchanged for callers — `await ask_json(system, user, max_tokens)`.
    The client handles rate-limit backoff, timeouts, and automatic fallback
    to a secondary model. Returns `{}` only after both models have been
    exhausted — the orchestrator is responsible for supplying degraded JSON
    so the UI never sees a blank response.
    """

    def __init__(
        self,
        api_key: str,
        model: str = PRIMARY_MODEL,
        fallback_model: Optional[str] = FALLBACK_MODEL,
    ):
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self._client = None

    @property
    def _fallback_enabled(self) -> bool:
        """No fallback when unset, empty, or identical to the primary model."""
        return bool(self.fallback_model) and self.fallback_model != self.model

    def _ensure_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    async def ask_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        normalizer: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        metadata: Optional[dict[str, Any]] = None,
        reject_prose: bool = False,
        retry_truncated_response: bool = True,
    ) -> dict[str, Any]:
        """Run a single prompt, parse JSON reply, return `{}` on total failure.

        Strategy:
          1. Try the primary model with exp. backoff on 429 / overloaded.
          2. On timeout or unrecoverable error, fall back to the secondary
             model ONCE with a trimmed prompt and reduced max_tokens.
          3. If the fallback also fails, return `{}`; the caller decides how
             to populate degraded defaults.

        A quota/authentication provider error is never retried against the
        SAME model (single attempt, no backoff) — retrying a dead key/account
        just wastes time on a guaranteed failure. It does NOT by itself skip
        a configured fallback MODEL (e.g. the conflict-review agent's
        Sonnet → Haiku fallback keeps working through a Sonnet quota
        failure); callers that must stop after exactly one provider call
        (e.g. specialist tasks, which run with no fallback model configured)
        get that behavior for free once fallback is disabled.
        `metadata["error_classification"]` carries the last non-empty
        classification so callers can decide whether a repair call is worth
        attempting. ``reject_prose=True`` uses the strict compact-JSON
        extractor (no prose-wrapped-object fallback) instead of the default
        prose-tolerant one.

        ``retry_truncated_response`` (default True, legacy behavior): on a
        truncated-looking parse failure, silently repeats the SAME prompt
        against the SAME model at a larger token budget before giving up.
        Callers whose OWN caller already owns a bounded, scoped repair
        strategy (e.g. distributed specialists, which retry only the
        missing/malformed ticker at its own bounded budget) must pass
        ``False`` — otherwise this hidden retry would silently double an
        already-oversized batch call, invisible to the caller's own call
        count and token-budget bookkeeping. When False, a detected
        truncation is recorded in ``metadata`` exactly as it always was,
        but neither the prompt nor the batch is repeated — `{}` is
        returned immediately for the caller's own repair logic to handle.
        """
        if not self.api_key:
            logger.warning("LLM call skipped — no anthropic_api_key configured")
            return {}

        # ── Primary attempt (with 429 backoff) ────────────────────────────
        meta: dict[str, Any] = metadata if isinstance(metadata, dict) else {}
        logger.info("LLM call start — model=%s max_tokens=%d", self.model, max_tokens)
        text, err, error_class = await self._call_with_backoff(
            model=self.model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            timeout_s=PRIMARY_TIMEOUT_S,
        )
        if text:
            logger.debug("raw_llm_response_preview model=%s preview=%r", self.model, text[:800])
            parsed, debug = _extract_json(text, reject_prose=reject_prose)
            meta.update({f"primary_{k}": v for k, v in debug.items()})
            if parsed is not None:
                logger.debug("extracted_json_preview model=%s preview=%r", self.model, (debug.get("candidate") or "")[:800])
                logger.debug("parsed_keys model=%s keys=%s", self.model, sorted(parsed.keys()))
                meta.update({
                    "model_used": self.model,
                    "parse_success": True,
                    "retry_reason": None,
                })
                return normalizer(parsed) if normalizer else parsed
            _log_parse_failure(self.model, text, debug)
            if debug.get("truncated_response_detected") and retry_truncated_response:
                larger_budget = max(max_tokens + 256, int(max_tokens * 1.6))
                logger.warning(
                    "LLM primary parse looks truncated; retrying once with larger max_tokens=%d",
                    larger_budget,
                )
                trunc_text, trunc_err, trunc_error_class = await self._call_with_backoff(
                    model=self.model,
                    system=system + _JSON_ONLY_CONTRACT,
                    user=user + _JSON_ONLY_CONTRACT,
                    max_tokens=larger_budget,
                    timeout_s=PRIMARY_TIMEOUT_S,
                    max_attempts=2,
                )
                if trunc_error_class:
                    error_class = trunc_error_class
                if trunc_text:
                    trunc_parsed, trunc_debug = _extract_json(
                        trunc_text, reject_prose=reject_prose,
                    )
                    meta.update({f"retry_{k}": v for k, v in trunc_debug.items()})
                    meta["retry_reason"] = "truncated_response_detected"
                    if trunc_parsed is not None:
                        meta.update({
                            "model_used": self.model,
                            "parse_success": True,
                            "truncation_retry_used": True,
                        })
                        return normalizer(trunc_parsed) if normalizer else trunc_parsed
                    _log_parse_failure(self.model, trunc_text, trunc_debug)
                if trunc_err:
                    err = trunc_err
            logger.warning("LLM primary returned unparseable JSON; falling back")

        # ── No fallback configured — the primary is the only attempt ──────
        if not self._fallback_enabled:
            meta.update({
                "model_used": self.model,
                "parse_success": False,
                "retry_reason": err or "no-json",
            })
            if error_class:
                meta["error_classification"] = error_class
            logger.warning(
                "LLM primary failed and no fallback configured (model=%s) — "
                "returning {}", self.model,
            )
            return {}

        # ── Fallback attempt (Haiku, trimmed prompt, single try) ──────────
        logger.warning(
            "Fallback → %s (primary failed: %s)", self.fallback_model, err or "no-json"
        )
        fb_system = _trim_prompt(system + _JSON_ONLY_CONTRACT, ratio=0.8)
        fb_user = _trim_prompt(user + _JSON_ONLY_CONTRACT, ratio=0.8)
        fb_max_tokens = max(320, min(700, int(max_tokens * 0.75)))
        fb_text, fb_err, fb_error_class = await self._call_with_backoff(
            model=self.fallback_model,
            system=fb_system,
            user=fb_user,
            max_tokens=fb_max_tokens,
            timeout_s=FALLBACK_TIMEOUT_S,
            max_attempts=2,  # single retry on the fallback only
        )
        if fb_text:
            logger.debug("raw_llm_response_preview model=%s preview=%r", self.fallback_model, fb_text[:800])
            parsed, debug = _extract_json(fb_text, reject_prose=reject_prose)
            meta.update({f"fallback_{k}": v for k, v in debug.items()})
            if parsed is not None:
                logger.info("LLM fallback succeeded — model=%s", self.fallback_model)
                logger.debug("extracted_json_preview model=%s preview=%r", self.fallback_model, (debug.get("candidate") or "")[:800])
                logger.debug("parsed_keys model=%s keys=%s", self.fallback_model, sorted(parsed.keys()))
                meta.update({
                    "model_used": self.fallback_model,
                    "parse_success": True,
                })
                return normalizer(parsed) if normalizer else parsed
            _log_parse_failure(self.fallback_model, fb_text, debug)
            logger.warning("LLM fallback returned unparseable JSON")

        meta.update({
            "model_used": self.fallback_model,
            "parse_success": False,
            "retry_reason": err or "no-json",
        })
        if fb_error_class:
            meta["error_classification"] = fb_error_class
        logger.warning("LLM fallback failed: %s — returning {}", fb_err or "no-json")
        return {}

    # ── Internal ──────────────────────────────────────────────────────────

    async def _call_with_backoff(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        timeout_s: float,
        max_attempts: int = 4,
    ) -> tuple[Optional[str], Optional[str], str]:
        """Call Anthropic with exp. backoff on 429 / overloaded.

        Returns `(text, error_str, error_class)`. `text` is None on total
        failure. `max_attempts` caps the total number of tries (initial +
        retries). Quota/authentication errors are never retried — they fail
        on the first attempt regardless of `max_attempts` since retrying a
        dead key/account wastes attempts and time for a guaranteed failure.
        """
        attempts = min(max_attempts, len(_BACKOFF_SCHEDULE_S))

        for attempt in range(attempts):
            try:
                text = await asyncio.wait_for(
                    self._single_call(model, system, user, max_tokens),
                    timeout=timeout_s,
                )
                return text, None, ""
            except asyncio.TimeoutError:
                # Timeout is not retryable on same model — escalate to fallback.
                logger.warning("LLM timeout after %.1fs — model=%s", timeout_s, model)
                return None, f"timeout after {timeout_s:.0f}s", ERROR_CLASS_TRANSIENT
            except Exception as exc:  # noqa: BLE001 — we classify below
                err_str = str(exc)
                error_class = _classify_provider_exception(exc)
                if error_class in NON_RETRYABLE_PROVIDER_CLASSES:
                    logger.warning(
                        "LLM call failed non-retryably — model=%s class=%s attempt=%d/%d: %s",
                        model, error_class, attempt + 1, attempts, err_str[:200],
                    )
                    return None, err_str, error_class
                retryable = error_class in (ERROR_CLASS_RATE_LIMIT, ERROR_CLASS_TRANSIENT)
                if not retryable or attempt == attempts - 1:
                    logger.warning(
                        "LLM call failed — model=%s attempt=%d/%d: %s",
                        model, attempt + 1, attempts, err_str[:200],
                    )
                    return None, err_str, error_class
                # Exponential backoff with jitter
                delay = _BACKOFF_SCHEDULE_S[attempt] + random.uniform(0, 1.0)
                logger.warning(
                    "429 encountered, backing off %.1fs — model=%s attempt=%d/%d",
                    delay, model, attempt + 1, attempts,
                )
                await asyncio.sleep(delay)

        return None, "exhausted retries", ERROR_CLASS_RATE_LIMIT

    async def _single_call(
        self, model: str, system: str, user: str, max_tokens: int
    ) -> str:
        """Execute a blocking Anthropic call on the default executor."""
        def _call() -> str:
            client = self._ensure_client()
            try:
                # Primary shape uses prompt-caching hints.
                msg = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=[{
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{"role": "user", "content": user}],
                )
            except Exception as exc:  # noqa: BLE001
                # Some Anthropic SDK/API combinations reject `cache_control`
                # or block-array system prompts with HTTP 400. Retry once
                # with the most compatible payload shape before bubbling up.
                if not _is_compatibility_400(exc):
                    raise
                logger.warning(
                    "LLM 400 with cache_control/system-block payload; retrying "
                    "without cache_control for compatibility"
                )
                msg = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
            return _extract_text_from_message(msg)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _call)


# ── helpers ──────────────────────────────────────────────────────────────────

_OUTER_FENCE_OPEN_RE = re.compile(r"^```[ \t]*(?:json)?[ \t]*\r?\n?", re.IGNORECASE)
_OUTER_FENCE_CLOSE_RE = re.compile(r"\r?\n?[ \t]*```[ \t]*$")


def _strip_single_outer_fence(text: str) -> str:
    """Strip at most one leading/trailing Markdown code fence.

    Only removes the delimiter characters (``` or ```json) — never
    interprets or executes the fenced content. A missing/unfinished closing
    fence still has its opening delimiter stripped so truncation detection
    runs against the actual JSON body instead of failing on stray backticks.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = _OUTER_FENCE_OPEN_RE.sub("", stripped, count=1)
    without_close = _OUTER_FENCE_CLOSE_RE.sub("", without_open, count=1)
    return without_close.strip()


def _extract_json(
    text: str, *, reject_prose: bool = False,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Parse a JSON object from raw model text.

    Default (``reject_prose=False``): accepts raw JSON, fenced JSON blocks,
    and JSON with short prose wrappers (existing tolerant behavior, used by
    legacy callers). ``reject_prose=True`` uses a strict compact-JSON
    contract instead — whitespace trimmed, at most one outer Markdown fence
    stripped, and NOTHING else: no scanning for a JSON object buried in
    surrounding prose. Use this for agents whose prompt contract demands
    JSON-only output (e.g. distributed specialists) so a verbose/commentary
    response is correctly rejected rather than silently salvaged.

    Returns parsed object + debug metadata for targeted logging.
    """
    debug: dict[str, Any] = {
        "candidate": "",
        "error": "",
        "parse_error_type": "",
        "raw_response_length": len(text or ""),
        "had_code_fence": bool("```" in (text or "")),
        "extracted_json_length": 0,
        "truncated_response_detected": False,
    }
    if not text:
        debug["error"] = "empty response text"
        return None, debug

    if reject_prose:
        candidate = _strip_single_outer_fence(text)
        debug["candidate"] = candidate
        debug["extracted_json_length"] = len(candidate)
        try:
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                debug["parse_error_type"] = "none"
                return loaded, debug
            debug["error"] = f"top-level JSON must be object, got {type(loaded).__name__}"
            debug["parse_error_type"] = "top_level_not_object"
        except Exception as exc:  # noqa: BLE001
            debug["error"] = str(exc)
            debug["parse_error_type"] = _classify_parse_error(str(exc), candidate=candidate)
            debug["truncated_response_detected"] = _looks_truncated(
                candidate=candidate, error=str(exc),
            )
        return None, debug

    stripped = text.strip()

    try:
        loaded = json.loads(stripped)
        if isinstance(loaded, dict):
            debug["candidate"] = stripped
            debug["extracted_json_length"] = len(stripped)
            debug["parse_error_type"] = "none"
            return loaded, debug
        debug["candidate"] = stripped
        debug["error"] = f"top-level JSON must be object, got {type(loaded).__name__}"
        debug["parse_error_type"] = "top_level_not_object"
    except Exception as exc:  # noqa: BLE001
        debug["error"] = str(exc)
        debug["parse_error_type"] = _classify_parse_error(str(exc), candidate=stripped)
        debug["truncated_response_detected"] = _looks_truncated(
            candidate=stripped,
            error=str(exc),
        )

    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped, flags=re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        debug["candidate"] = candidate
        debug["extracted_json_length"] = len(candidate)
        try:
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                debug["parse_error_type"] = "none"
                return loaded, debug
            debug["error"] = f"top-level fenced JSON must be object, got {type(loaded).__name__}"
            debug["parse_error_type"] = "top_level_not_object"
        except Exception as exc:  # noqa: BLE001
            debug["error"] = str(exc)
            debug["parse_error_type"] = _classify_parse_error(str(exc), candidate=candidate)
            debug["truncated_response_detected"] = _looks_truncated(
                candidate=candidate,
                error=str(exc),
            )

    candidate = _first_balanced_json_object_substring(stripped)
    if candidate:
        debug["candidate"] = candidate
        debug["extracted_json_length"] = len(candidate)
        try:
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                debug["parse_error_type"] = "none"
                return loaded, debug
            debug["error"] = f"top-level extracted JSON must be object, got {type(loaded).__name__}"
            debug["parse_error_type"] = "top_level_not_object"
        except Exception as exc:  # noqa: BLE001
            debug["error"] = str(exc)
            debug["parse_error_type"] = _classify_parse_error(str(exc), candidate=candidate)
            debug["truncated_response_detected"] = _looks_truncated(
                candidate=candidate,
                error=str(exc),
            )

    if not debug.get("error"):
        debug["error"] = "no JSON object candidate found"
        debug["parse_error_type"] = "no_json_object_found"
    if not candidate and "{" in stripped:
        debug["truncated_response_detected"] = _looks_truncated(
            candidate=stripped,
            error=debug.get("error") or "",
        )
        if debug["truncated_response_detected"]:
            debug["parse_error_type"] = "truncated_json"
    return None, debug


def _first_balanced_json_object_substring(text: str) -> Optional[str]:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:idx + 1]
        start = text.find("{", start + 1)
    return None


def _classify_parse_error(error: str, *, candidate: str) -> str:
    msg = (error or "").lower()
    if _looks_truncated(candidate=candidate, error=error):
        return "truncated_json"
    if "expecting value" in msg:
        return "expecting_value"
    if "unterminated string" in msg:
        return "unterminated_string"
    if "extra data" in msg:
        return "extra_data"
    if "invalid control character" in msg:
        return "invalid_control_character"
    return "json_decode_error"


def _looks_truncated(*, candidate: str, error: str) -> bool:
    msg = (error or "").lower()
    c = candidate or ""
    if "unterminated string" in msg:
        return True
    if "unexpected end" in msg or "unclosed" in msg:
        return True
    if "expecting value" in msg and c.rstrip().endswith((",", ":", "{", "[")):
        return True
    opens = c.count("{")
    closes = c.count("}")
    if opens > closes:
        return True
    quote_count = c.count('"')
    return quote_count % 2 == 1


def _extract_text_from_message(msg: Any) -> str:
    parts = getattr(msg, "content", None) or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            if part.get("type") == "text" and part.get("text"):
                chunks.append(str(part.get("text")))
            elif part.get("text"):
                chunks.append(str(part.get("text")))
            continue
        text = getattr(part, "text", None)
        if text:
            chunks.append(str(text))
    return "\n".join(c.strip() for c in chunks if c and c.strip())


def _log_parse_failure(model: str, response_text: str, debug: dict[str, str]) -> None:
    logger.warning(
        "LLM JSON parse failure — model=%s response_preview=%r candidate_preview=%r error=%s",
        model,
        (response_text or "")[:800],
        (debug.get("candidate") or "")[:800],
        debug.get("error") or "unknown parse/schema error",
    )


def _trim_prompt(text: str, ratio: float = 0.6) -> str:
    """Shrink a prompt by keeping the head + tail so instructions + data both
    survive. Used when falling back to the smaller Haiku model under rate limits.
    """
    if ratio >= 1.0 or not text:
        return text
    target = max(256, int(len(text) * ratio))
    if len(text) <= target:
        return text
    head = int(target * 0.55)
    tail = target - head
    return text[:head] + "\n… [trimmed for fallback] …\n" + text[-tail:]


def _status_code_from_exc(exc: Exception) -> Optional[int]:
    """Extract an HTTP status code from an anthropic SDK exception, if any."""
    # anthropic.APIStatusError exposes .status_code directly
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _is_compatibility_400(exc: Exception) -> bool:
    """True when a 400 likely comes from prompt-cache/system payload shape.

    We only retry 400s that look like SDK/API compatibility mismatches
    (`cache_control`, system content-block format). Other 400s should fail
    fast so callers can surface actionable errors.
    """
    if _status_code_from_exc(exc) != 400:
        return False
    msg = str(exc).lower()
    hints = (
        "cache_control",
        "prompt caching",
        "system",
        "content block",
    )
    return any(h in msg for h in hints)


def _classify_provider_exception(exc: Exception) -> str:
    """Best-effort classification of an Anthropic SDK exception.

    The SDK does not expose a stable machine-readable taxonomy, so this
    combines HTTP status (when available) with message hints. Never raises —
    an unrecognized shape falls back to ``ERROR_CLASS_UNKNOWN`` (fail fast,
    not retried by the caller's default policy).
    """
    status = _status_code_from_exc(exc)
    msg = str(exc).lower()

    if status == 401 or "authentication_error" in msg or "invalid x-api-key" in msg \
            or "invalid api key" in msg:
        return ERROR_CLASS_AUTHENTICATION
    if "credit balance" in msg or "insufficient_quota" in msg \
            or "insufficient credit" in msg or "billing" in msg:
        return ERROR_CLASS_QUOTA
    if status == 403:
        return (
            ERROR_CLASS_QUOTA if ("credit" in msg or "quota" in msg)
            else ERROR_CLASS_AUTHENTICATION
        )
    if status == 429 or "rate_limit" in msg:
        return ERROR_CLASS_RATE_LIMIT
    if status == 529 or "overloaded" in msg:
        return ERROR_CLASS_TRANSIENT
    if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError, httpx.WriteError)):
        return ERROR_CLASS_TRANSIENT
    if status is not None and status >= 500:
        return ERROR_CLASS_TRANSIENT
    return ERROR_CLASS_UNKNOWN


def clamp(v: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        f = float(v)
        if f != f:
            return default
        return max(lo, min(hi, f))
    except (TypeError, ValueError):
        return default
