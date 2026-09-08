"""Provider-agnostic LLM adapter for Telugu Podcast Agent.

Phase 2 Milestone 3: Gemini 3.5 Flash as primary provider, Ollama preserved as optional.

- Default provider: gemini (requires GEMINI_API_KEY) — model gemini-3.5-flash
- Fallback provider: ollama (local, open-source, no API key) — model gemma2:9b via OLLAMA_BASE_URL
- Provider-agnostic: gemini | ollama | openai | groq — selected via LLM_PROVIDER
- Rule-based generation stays as the existing fallback (caller decides; this module never
  silently returns rule-based — it raises LLMError so caller can fallback).
- Clear timeout and error handling (requests timeout, connection errors, HTTP errors).
- GEMINI_API_KEY is read from env (config.GEMINI_API_KEY) — never hardcoded or logged.
- Does NOT modify Google Sheets or transcript behavior.
- Does NOT implement Telugu prompting/TTS/Drive/pipeline integration — pure LLM transport.

Usage:
    from src.llm import generate, is_ollama_available, LLMError
    try:
        text = generate(prompt="Summarize...", system="You are helpful...", timeout=60)
    except LLMError as e:
        # fallback to rule-based
        text = rule_based(...)

For script generation, see src/script_generator.py which uses this adapter (Milestone 2+).
"""

import logging
from typing import Optional

import config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config defaults for this adapter (Phase 2 Milestone 3 — Gemini primary)
# ---------------------------------------------------------------------------
DEFAULT_PROVIDER = "gemini"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_OLLAMA_MODEL = "gemma2:9b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 60  # seconds for generate; 5s for health check

# Provider-specific defaults when LLM_MODEL is empty
_PROVIDER_DEFAULT_MODELS = {
    "gemini": DEFAULT_GEMINI_MODEL,
    "ollama": DEFAULT_OLLAMA_MODEL,
    "openai": "gpt-4o-mini",
    "groq": "llama-3.1-70b-versatile",
}

# ---------------------------------------------------------------------------
# Errors — explicit so callers can distinguish timeout vs connection vs API
# ---------------------------------------------------------------------------
class LLMError(Exception):
    """Base for all LLM adapter errors."""

class LLMTimeoutError(LLMError):
    """LLM request timed out."""

class LLMConnectionError(LLMError):
    """Cannot connect to LLM provider (e.g., Ollama not running)."""

class LLMProviderError(LLMError):
    """Provider returned an API error (HTTP 4xx/5xx, invalid response)."""


# ---------------------------------------------------------------------------
# Helpers — provider / model resolution (env-driven, no hardcoding in call sites)
# ---------------------------------------------------------------------------
def get_provider() -> str:
    """Resolve provider from config.LLM_PROVIDER, default to gemini (primary)."""
    raw = (config.LLM_PROVIDER or "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    if raw in ("rule-based", "rules", "none", "off", "fallback"):
        # Explicitly asked for no LLM — caller should use rule-based directly
        return ""
    return raw

def get_model(provider: str = "") -> str:
    """Resolve model for provider. Uses LLM_MODEL if set, else provider default."""
    provider = (provider or get_provider()).strip().lower()
    if config.LLM_MODEL and config.LLM_MODEL.strip():
        return config.LLM_MODEL.strip()
    # No LLM_MODEL set — use provider default (gemini-3.5-flash for gemini)
    return _PROVIDER_DEFAULT_MODELS.get(provider, DEFAULT_GEMINI_MODEL)

def get_ollama_url() -> str:
    """Resolve Ollama base URL from config, default to localhost:11434."""
    url = (config.OLLAMA_BASE_URL or "").strip()
    return url or DEFAULT_OLLAMA_URL


# ---------------------------------------------------------------------------
# Health check — does not require a model, never raises, useful for preflight
# ---------------------------------------------------------------------------
def is_ollama_available(timeout: int = 5) -> bool:
    """Return True if Ollama server responds at OLLAMA_BASE_URL/api/tags."""
    import requests
    url = f"{get_ollama_url().rstrip('/')}/api/tags"
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.status_code == 200
    except Exception as e:
        log.debug("Ollama not available at %s: %s", url, e)
        return False


# ---------------------------------------------------------------------------
# Ollama core — single place for timeout/error handling
# ---------------------------------------------------------------------------
def _call_ollama(prompt: str, system: str = "", model: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Call Ollama /api/generate (stream=False) and return response text.

    Raises:
        LLMTimeoutError      — on requests.Timeout
        LLMConnectionError   — on ConnectionError / not running
        LLMProviderError     — on HTTP error or malformed response
    """
    import requests

    base = get_ollama_url().rstrip("/")
    url = f"{base}/api/generate"
    mdl = model or get_model("ollama")
    # Ollama expects combined prompt; system passed as separate field if provided
    # We follow the same pattern as script_generator: system + "\n\n" + user
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    payload = {"model": mdl, "prompt": full_prompt, "stream": False}
    log.debug("Ollama generate model=%s timeout=%s prompt_len=%d", mdl, timeout, len(full_prompt))
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.Timeout as e:
        raise LLMTimeoutError(f"Ollama timeout after {timeout}s at {url}: {e}") from e
    except requests.ConnectionError as e:
        raise LLMConnectionError(f"Cannot connect to Ollama at {url}. Is Ollama running? {e}") from e
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:500] if e.response is not None else ""
        except Exception:
            pass
        raise LLMProviderError(f"Ollama HTTP error {e}: {body}") from e
    except Exception as e:
        # Generic request failure
        if "Connection" in type(e).__name__:
            raise LLMConnectionError(str(e)) from e
        raise LLMProviderError(f"Ollama request failed: {e}") from e

    try:
        data = resp.json()
    except Exception as e:
        raise LLMProviderError(f"Ollama returned non-JSON: {resp.text[:500]}") from e
    text = data.get("response")
    if text is None:
        raise LLMProviderError(f"Ollama response missing 'response' field: {data}")
    return str(text).strip()

def _call_openai(prompt: str, system: str = "", model: str = "", timeout: int = 60) -> str:
    from openai import OpenAI
    mdl = model or get_model("openai")
    if not config.OPENAI_API_KEY:
        raise LLMProviderError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=timeout)
    try:
        resp = client.chat.completions.create(
            model=mdl,
            messages=[
                {"role": "system", "content": system or "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2000,
        )
    except Exception as e:
        if "timeout" in str(type(e)).lower() or "Timeout" in str(e):
            raise LLMTimeoutError(str(e)) from e
        raise LLMProviderError(f"OpenAI error: {e}") from e
    text = resp.choices[0].message.content
    if not text:
        raise LLMProviderError("OpenAI returned empty content")
    return text.strip()

def _call_gemini(prompt: str, system: str = "", model: str = "", timeout: int = 60) -> str:
    """Call Gemini via google-generativeai. Uses GEMINI_API_KEY from env, never hardcoded.

    Model defaults to gemini-3.5-flash when LLM_MODEL empty.
    """
    import google.generativeai as genai
    mdl = model or get_model("gemini")
    if not config.GEMINI_API_KEY:
        raise LLMProviderError("GEMINI_API_KEY not set (set GEMINI_API_KEY in .env)")
    # Never log the key — configure silently
    genai.configure(api_key=config.GEMINI_API_KEY)
    gm = genai.GenerativeModel(mdl, system_instruction=system or None)
    try:
        # google-generativeai does not expose direct timeout param; rely on default.
        # timeout arg kept for provider-agnostic signature consistency.
        resp = gm.generate_content(prompt)
    except Exception as e:
        if "timeout" in str(e).lower():
            raise LLMTimeoutError(str(e)) from e
        raise LLMProviderError(f"Gemini error: {e}") from e
    text = getattr(resp, "text", None)
    if not text:
        # Try candidates path if .text is None (some SDK versions)
        try:
            # Fallback: concatenated candidate parts
            if hasattr(resp, "candidates") and resp.candidates:
                parts = getattr(resp.candidates[0].content, "parts", [])
                text = "".join(getattr(p, "text", "") for p in parts)
        except Exception:
            pass
    if not text:
        raise LLMProviderError("Gemini returned empty response")
    return text.strip()

def _call_groq(prompt: str, system: str = "", model: str = "", timeout: int = 60) -> str:
    from groq import Groq
    mdl = model or get_model("groq")
    if not config.GROQ_API_KEY:
        raise LLMProviderError("GROQ_API_KEY not set")
    client = Groq(api_key=config.GROQ_API_KEY, timeout=timeout)
    try:
        resp = client.chat.completions.create(
            model=mdl,
            messages=[
                {"role": "system", "content": system or "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2000,
        )
    except Exception as e:
        if "timeout" in str(e).lower():
            raise LLMTimeoutError(str(e)) from e
        raise LLMProviderError(f"Groq error: {e}") from e
    text = resp.choices[0].message.content
    if not text:
        raise LLMProviderError("Groq returned empty content")
    return text.strip()


# ---------------------------------------------------------------------------
# Public API — provider-agnostic
# ---------------------------------------------------------------------------
def generate(
    prompt: str,
    system: str = "",
    provider: str = "",
    model: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Generate text via the configured LLM provider.

    Defaults:
        provider = get_provider() -> "gemini" if LLM_PROVIDER empty (primary)
        model    = get_model(provider) -> "gemini-3.5-flash" for gemini, "gemma2:9b" for ollama, or LLM_MODEL if set
        timeout  = DEFAULT_TIMEOUT (60s) — explicit per-call timeout

    Behavior:
        - If provider is "" (explicit rule-based), raises LLMError so caller falls back.
        - If provider is ollama and server not reachable, raises LLMConnectionError.
        - If provider is gemini and GEMINI_API_KEY missing/invalid, raises LLMProviderError.
        - On timeout, raises LLMTimeoutError.
        - On HTTP/API error, raises LLMProviderError.
        - Never returns rule-based content — caller (e.g., script_generator) decides fallback.
        - Never logs or exposes API keys.

    Does not touch Google Sheets or transcript behavior.
    """
    prov = (provider or get_provider()).strip().lower()
    if not prov:
        raise LLMError("LLM disabled (rule-based fallback requested)")
    mdl = (model or get_model(prov)).strip()

    log.info("LLM generate provider=%s model=%s timeout=%s prompt_len=%d", prov, mdl, timeout, len(prompt))
    if prov == "ollama":
        return _call_ollama(prompt, system=system, model=mdl, timeout=timeout)
    if prov == "openai":
        return _call_openai(prompt, system=system, model=mdl, timeout=timeout)
    if prov == "gemini":
        return _call_gemini(prompt, system=system, model=mdl, timeout=timeout)
    if prov == "groq":
        return _call_groq(prompt, system=system, model=mdl, timeout=timeout)
    raise LLMProviderError(f"Unknown LLM provider: {prov!r} (expected ollama|openai|gemini|groq)")

# Convenience: check if currently configured provider is available (for CLI)
def is_provider_available(timeout: int = 5) -> bool:
    prov = get_provider()
    if prov == "ollama":
        return is_ollama_available(timeout=timeout)
    # For key-based providers, availability = key present
    if prov == "openai":
        return bool(config.OPENAI_API_KEY)
    if prov == "gemini":
        return bool(config.GEMINI_API_KEY)
    if prov == "groq":
        return bool(config.GROQ_API_KEY)
    return False
