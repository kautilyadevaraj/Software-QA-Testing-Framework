"""Shared LLM factory for Phase 3 agents.

Resilience features (prod-grade):
  - Global in-flight semaphore (`llm_max_concurrent`) prevents cascade 429s
    when many A5/A7 calls fire in parallel.
  - Fallback chain (`llm_fallback_chain`, e.g. "groq,openrouter,nim") — the
    primary provider is tried first, then each fallback in order.
  - Retry with exponential backoff on 429 / rate-limit / 5xx / network errors.
  - Hard fail after all providers exhausted (raises RuntimeError).

Usage:
    from app.utils.llm import call_llm
    response = call_llm(prompt, max_tokens=1024)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global semaphore shared by all callers in this process. Workers that
# run in the SAME process (embedded mode) will share this limit; standalone
# worker containers each have their own. For multi-container coordination
# a Redis token bucket would be needed — out of scope for this slice.
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
    "500", "502", "503", "504", "timeout", "timed out", "connection",
    "temporarily", "retry",
)


def _is_retryable(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(h in msg for h in _RETRYABLE_HINTS)


def _provider_chain() -> list[str]:
    """Resolve provider order. Primary (llm_provider) first, then fallbacks."""
    primary = (settings.llm_provider or "groq").lower().strip()
    raw_chain = (settings.llm_fallback_chain or "").lower().strip()
    chain = [p.strip() for p in raw_chain.split(",") if p.strip()]
    ordered: list[str] = []
    if primary:
        ordered.append(primary)
    for p in chain:
        if p not in ordered:
            ordered.append(p)
    return ordered


_PROVIDER_FUNCS: dict[str, Callable[[str, int | None], str]] = {}


def _register_providers() -> None:
    if _PROVIDER_FUNCS:
        return
    _PROVIDER_FUNCS["groq"] = _call_groq
    _PROVIDER_FUNCS["gemini"] = _call_gemini
    _PROVIDER_FUNCS["nim"] = _call_nim
    _PROVIDER_FUNCS["openrouter"] = _call_openrouter


def call_llm(prompt: str, max_tokens: int | None = None) -> str:
    """Call LLM with concurrency cap, fallback chain, and backoff retries."""
    _register_providers()
    sema = _get_semaphore()
    providers = _provider_chain()
    max_retries = max(1, int(settings.llm_retry_attempts or 3))
    backoff_base = max(0.1, float(settings.llm_retry_backoff_base_s or 2.0))

    with _Acquired(sema):
        last_exc: Exception | None = None
        for provider in providers:
            fn = _PROVIDER_FUNCS.get(provider)
            if fn is None:
                logger.warning("LLM: unknown provider %r — skipping", provider)
                continue

            for attempt in range(1, max_retries + 1):
                try:
                    return fn(prompt, max_tokens)
                except Exception as exc:
                    last_exc = exc
                    if not _is_retryable(exc) or attempt == max_retries:
                        logger.warning(
                            "LLM provider=%s attempt=%d/%d failed (not retrying): %s",
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


class _Acquired:
    """Context manager that acquires a semaphore and releases on exit."""
    def __init__(self, sema: threading.Semaphore):
        self.sema = sema

    def __enter__(self):
        self.sema.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.sema.release()
        return False


# ── NVIDIA NIM ────────────────────────────────────────────────────────────────

def _call_nim(prompt: str, max_tokens: int | None = None) -> str:
    if not settings.nim_api_key:
        raise RuntimeError("NIM_API_KEY is not configured. Set LLM_PROVIDER=groq or add NIM_API_KEY to .env")

    tokens = max_tokens or settings.nim_max_tokens

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("langchain-openai is not installed. Run: pip install langchain-openai>=0.3.0") from exc

    llm = ChatOpenAI(
        model=settings.nim_model,
        api_key=settings.nim_api_key,
        base_url=settings.nim_base_url,
        temperature=0,
        max_retries=1,
    )
    response = llm.invoke(prompt)
    content = getattr(response, "content", response)
    logger.debug("NIM call success: model=%s tokens=%d", settings.nim_model, tokens)
    return str(content)


# ── Groq (with key rotation) ──────────────────────────────────────────────────

def _call_groq(prompt: str, max_tokens: int | None = None) -> str:
    api_keys = settings.groq_api_keys
    if not api_keys:
        raise RuntimeError("GROQ_API_KEY is not configured. Set LLM_PROVIDER=nim or add GROQ_API_KEY to .env")

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


# ── Google Gemini ─────────────────────────────────────────────────────────────

def _call_gemini(prompt: str, max_tokens: int | None = None) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured. Add it to .env or remove 'gemini' from LLM_FALLBACK_CHAIN")

    tokens = max_tokens or settings.gemini_max_tokens

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise RuntimeError(
            "langchain-google-genai is not installed. Run: pip install langchain-google-genai>=2.0.0"
        ) from exc

    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,
        max_output_tokens=tokens,
        max_retries=0,
    )
    response = llm.invoke(prompt)
    content = getattr(response, "content", response)
    logger.debug("Gemini call success: model=%s tokens=%d", settings.gemini_model, tokens)
    return str(content)


# ── OpenRouter (OpenAI-compatible, free tier) ─────────────────────────────────

def _call_openrouter(prompt: str, max_tokens: int | None = None) -> str:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured. Add it to .env")

    tokens = max_tokens or settings.openrouter_max_tokens

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("langchain-openai is not installed. Run: pip install langchain-openai>=0.3.0") from exc

    llm = ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=0,
        max_tokens=tokens,
        max_retries=2,
        default_headers={
            "HTTP-Referer": "https://github.com/sqat",   # OpenRouter requires this
            "X-Title": "SQAT Phase3 Agent",
        },
    )
    response = llm.invoke(prompt)
    content = getattr(response, "content", response)
    logger.debug("OpenRouter call success: model=%s tokens=%d", settings.openrouter_model, tokens)
    return str(content)
