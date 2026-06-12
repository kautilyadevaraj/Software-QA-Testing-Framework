"""Shared LLM client for all SQAT agents.

Provides two entry points:
- call_llm()        — Phase 3 agents (A3/A5/A7). Enforces a shared concurrency
                      semaphore (LLM_MAX_CONCURRENT) so parallel Playwright workers
                      don't saturate the Anthropic API.
- call_llm_direct() — Phase 2 scenario generation. Runs in a sync FastAPI
                      thread-pool thread; skips the semaphore to avoid starvation
                      when Phase 3 workers hold all semaphore slots.

Both functions use the same provider chain (LLM_PROVIDER / LLM_FALLBACK_CHAIN)
and the same retry/backoff logic.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_inflight_lock = threading.Lock()
_inflight_sema: threading.Semaphore | None = None


def _get_semaphore() -> threading.Semaphore:
    global _inflight_sema
    with _inflight_lock:
        if _inflight_sema is None:
            max_c = max(1, int(settings.llm_max_concurrent or 4))
            _inflight_sema = threading.Semaphore(max_c)
        return _inflight_sema


_RATE_LIMIT_HINTS = ("429", "rate limit", "ratelimit", "too many requests", "quota")
_RETRYABLE_HINTS = _RATE_LIMIT_HINTS + (
    "500", "502", "503", "504", "529", "timeout", "timed out", "connection",
    "temporarily", "retry",
)


def _is_retryable(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(hint in msg for hint in _RETRYABLE_HINTS)


def _provider_chain() -> list[str]:
    allowed = {"anthropic", "groq"}
    primary = (settings.llm_provider or "anthropic").lower().strip()
    raw_chain = (settings.llm_fallback_chain or "").lower().strip()
    chain = [provider.strip() for provider in raw_chain.split(",") if provider.strip()]

    ordered: list[str] = []
    if primary in allowed:
        ordered.append(primary)
    for provider in chain:
        if provider in allowed and provider not in ordered:
            ordered.append(provider)
    return ordered


_PROVIDER_FUNCS: dict[str, Callable[[str, int | None], str]] = {}


def _register_providers() -> None:
    if _PROVIDER_FUNCS:
        return
    _PROVIDER_FUNCS["anthropic"] = _call_anthropic
    _PROVIDER_FUNCS["groq"] = _call_groq


def call_llm(prompt: str, max_tokens: int | None = None) -> str:
    """Phase 3 entry point: call LLM with concurrency cap, retry, and fallback.

    Acquires a slot from the shared semaphore (LLM_MAX_CONCURRENT) before
    calling the provider. Use call_llm_direct() for Phase 2 / sync routes
    that must not block on the Phase 3 semaphore.
    """
    _register_providers()
    providers = _provider_chain()
    max_retries = max(1, int(settings.llm_retry_attempts or 3))
    backoff_base = max(0.1, float(settings.llm_retry_backoff_base_s or 2.0))

    with _Acquired(_get_semaphore()):
        last_exc: Exception | None = None
        for provider in providers:
            fn = _PROVIDER_FUNCS.get(provider)
            if fn is None:
                logger.warning("LLM: unknown provider %r; skipping", provider)
                continue

            for attempt in range(1, max_retries + 1):
                try:
                    return fn(prompt, max_tokens)
                except Exception as exc:
                    last_exc = exc
                    if not _is_retryable(exc) or attempt == max_retries:
                        logger.warning(
                            "LLM provider=%s attempt=%d/%d failed: %s",
                            provider, attempt, max_retries, exc,
                        )
                        break
                    sleep_s = backoff_base * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM provider=%s attempt=%d/%d failed, backing off %.1fs: %s",
                        provider, attempt, max_retries, sleep_s, exc,
                    )
                    time.sleep(sleep_s)

        raise RuntimeError(
            f"All LLM providers exhausted ({','.join(providers)}): {last_exc}"
        )


def call_llm_direct(prompt: str, max_tokens: int | None = None) -> str:
    """Phase 2 entry point: call LLM WITHOUT the concurrency semaphore.

    Phase 2 scenario generation runs inside a sync FastAPI thread-pool thread.
    Using the shared Phase 3 semaphore from that thread would block it when
    Phase 3 workers hold all semaphore slots, causing the HTTP request to hang
    indefinitely from the client's perspective.

    This function uses the same provider chain and retry logic but skips the
    semaphore acquisition. The caller (invoke_json_scenarios) is responsible
    for its own retry on JSON-parse failures.
    """
    _register_providers()
    providers = _provider_chain()
    max_retries = max(1, int(settings.llm_retry_attempts or 3))
    backoff_base = max(0.1, float(settings.llm_retry_backoff_base_s or 2.0))

    last_exc: Exception | None = None
    for provider in providers:
        fn = _PROVIDER_FUNCS.get(provider)
        if fn is None:
            logger.warning("LLM: unknown provider %r; skipping", provider)
            continue

        for attempt in range(1, max_retries + 1):
            try:
                logger.debug("LLM (direct) provider=%s attempt=%d/%d", provider, attempt, max_retries)
                return fn(prompt, max_tokens)
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt == max_retries:
                    logger.warning(
                        "LLM (direct) provider=%s attempt=%d/%d failed: %s",
                        provider, attempt, max_retries, exc,
                    )
                    break
                sleep_s = backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "LLM (direct) provider=%s attempt=%d/%d failed, backing off %.1fs: %s",
                    provider, attempt, max_retries, sleep_s, exc,
                )
                time.sleep(sleep_s)

    raise RuntimeError(
        f"All LLM providers exhausted (direct) ({','.join(providers)}): {last_exc}"
    )


class _Acquired:
    def __init__(self, sema: threading.Semaphore):
        self.sema = sema

    def __enter__(self):
        self.sema.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.sema.release()
        return False


def _call_anthropic(prompt: str, max_tokens: int | None = None) -> str:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured. Add it to .env or set LLM_PROVIDER=groq")

    tokens = max_tokens or settings.anthropic_max_tokens
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.anthropic_model,
            "max_tokens": tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Anthropic API failed status={response.status_code}: {response.text[:1000]}"
        )

    payload = response.json()
    text = "".join(
        str(part.get("text") or "")
        for part in (payload.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    if not text:
        raise RuntimeError(f"Anthropic API returned no text content: {payload}")
    logger.debug("Anthropic call success: model=%s tokens=%d", settings.anthropic_model, tokens)
    return text


def _call_groq(prompt: str, max_tokens: int | None = None) -> str:
    api_keys = settings.groq_api_keys
    if not api_keys:
        raise RuntimeError("GROQ_API_KEY is not configured. Add it to .env or remove 'groq' from LLM_FALLBACK_CHAIN")

    tokens = max_tokens or settings.groq_max_tokens

    try:
        from langchain_groq import ChatGroq
    except ImportError as exc:
        raise RuntimeError("langchain-groq is not installed. Run: pip install langchain-groq>=1.0.0") from exc

    last_exc: Exception | None = None
    for key in api_keys:
        try:
            llm = ChatGroq(
                model=settings.groq_model,
                temperature=0,
                api_key=key,
                max_tokens=tokens,
                max_retries=0,
            )
            response = llm.invoke(prompt)
            content = getattr(response, "content", response)
            logger.debug("Groq call success: model=%s tokens=%d", settings.groq_model, tokens)
            return str(content)
        except Exception as exc:
            logger.warning("Groq key failed: %s", exc)
            last_exc = exc

    raise RuntimeError(f"All Groq API keys failed: {last_exc}")
