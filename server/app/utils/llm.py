"""Shared LLM factory for Phase 3 agents.

Reads `LLM_PROVIDER` from settings:
  - "nim"  → NVIDIA NIM (OpenAI-compatible), model qwen/qwen2.5-coder-32b-instruct
  - "groq" → Groq with key rotation

Usage:
    from app.utils.llm import call_llm
    response = call_llm(prompt, max_tokens=1024)
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


def call_llm(prompt: str, max_tokens: int | None = None) -> str:
    """Call the configured LLM provider and return the response text."""
    provider = (settings.llm_provider or "groq").lower().strip()

    if provider == "nim":
        return _call_nim(prompt, max_tokens)
    if provider == "openrouter":
        return _call_openrouter(prompt, max_tokens)
    return _call_groq(prompt, max_tokens)


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
