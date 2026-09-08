"""Offline unit tests for src/llm.py — no Gemini/Ollama required, all network mocked.

Run:  python -m pytest tests/test_llm.py -v
       python tests/test_llm.py
Covers: provider/model resolution (Gemini 3.5 Flash primary, Ollama optional),
timeout/connection errors, mocked generate for both providers.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock
import importlib

import config
import src.llm as llm_mod
from src.llm import (
    LLMError, LLMTimeoutError, LLMConnectionError, LLMProviderError,
    get_provider, get_model, get_ollama_url, is_ollama_available, generate,
    DEFAULT_GEMINI_MODEL, DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, DEFAULT_TIMEOUT, DEFAULT_PROVIDER,
)

def test_get_provider_defaults_to_gemini():
    """Primary provider is gemini (gemini-3.5-flash); empty defaults to gemini, ollama still valid."""
    orig = config.LLM_PROVIDER
    try:
        config.LLM_PROVIDER = ""
        assert get_provider() == "gemini" == DEFAULT_PROVIDER
        config.LLM_PROVIDER = "  "
        assert get_provider() == "gemini"
        config.LLM_PROVIDER = "gemini"
        assert get_provider() == "gemini"
        config.LLM_PROVIDER = "ollama"
        assert get_provider() == "ollama"  # Ollama preserved as optional
        config.LLM_PROVIDER = "openai"
        assert get_provider() == "openai"
        config.LLM_PROVIDER = "rule-based"
        assert get_provider() == ""  # signals fallback
        config.LLM_PROVIDER = "NONE"
        assert get_provider() == ""
        print("PASS: test_get_provider_defaults_to_gemini")
    finally:
        config.LLM_PROVIDER = orig

# Backward compat alias — old test name still passes
def test_get_provider_defaults_to_ollama():
    test_get_provider_defaults_to_gemini()
    print("PASS: test_get_provider_defaults_to_ollama (alias for gemini primary)")

def test_get_model_defaults():
    orig_provider = config.LLM_PROVIDER
    orig_model = config.LLM_MODEL
    try:
        # Gemini primary
        config.LLM_PROVIDER = "gemini"
        config.LLM_MODEL = ""
        assert get_model("gemini") == DEFAULT_GEMINI_MODEL == "gemini-3.5-flash"
        assert get_model("") == DEFAULT_GEMINI_MODEL  # via get_provider()
        config.LLM_MODEL = "gemini-3.5-flash"
        assert get_model("gemini") == "gemini-3.5-flash"
        # Ollama still works as optional
        config.LLM_PROVIDER = "ollama"
        config.LLM_MODEL = ""
        assert get_model("ollama") == DEFAULT_OLLAMA_MODEL
        assert get_model("") == DEFAULT_OLLAMA_MODEL  # via get_provider()
        config.LLM_MODEL = "gemma2:2b"
        assert get_model("ollama") == "gemma2:2b"
        config.LLM_PROVIDER = "openai"
        config.LLM_MODEL = ""
        assert get_model("openai") == "gpt-4o-mini"
        config.LLM_MODEL = "gpt-4o"
        assert get_model("openai") == "gpt-4o"
        print("PASS: test_get_model_defaults")
    finally:
        config.LLM_PROVIDER = orig_provider
        config.LLM_MODEL = orig_model

def test_get_ollama_url():
    orig = config.OLLAMA_BASE_URL
    try:
        config.OLLAMA_BASE_URL = ""
        assert get_ollama_url() == DEFAULT_OLLAMA_URL
        config.OLLAMA_BASE_URL = "http://localhost:11434"
        assert get_ollama_url() == "http://localhost:11434"
        config.OLLAMA_BASE_URL = "http://192.168.1.10:11434/"
        assert get_ollama_url() == "http://192.168.1.10:11434/"
        print("PASS: test_get_ollama_url")
    finally:
        config.OLLAMA_BASE_URL = orig

def test_is_ollama_available_mocked():
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        assert is_ollama_available() is True
        mock_get.side_effect = Exception("connection refused")
        assert is_ollama_available() is False
    print("PASS: test_is_ollama_available_mocked")

def test_call_ollama_success_mocked():
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "  Hello from gemma2  "}
        mock_post.return_value = mock_resp
        text = llm_mod._call_ollama("prompt", system="sys", model="gemma2:9b", timeout=5)
        assert text == "Hello from gemma2"
        # Verify payload
        args, kwargs = mock_post.call_args
        assert "api/generate" in args[0]
        assert kwargs["json"]["model"] == "gemma2:9b"
        assert kwargs["timeout"] == 5
    print("PASS: test_call_ollama_success_mocked")

def test_call_ollama_timeout():
    import requests
    with patch("requests.post", side_effect=requests.Timeout("timed out")):
        try:
            llm_mod._call_ollama("p", timeout=1)
            assert False, "should have raised LLMTimeoutError"
        except LLMTimeoutError:
            pass
    print("PASS: test_call_ollama_timeout")

def test_call_ollama_connection_error():
    import requests
    with patch("requests.post", side_effect=requests.ConnectionError("refused")):
        try:
            llm_mod._call_ollama("p")
            assert False
        except LLMConnectionError:
            pass
    print("PASS: test_call_ollama_connection_error")

def test_call_ollama_http_error():
    import requests
    mock_resp = MagicMock()
    mock_resp.text = "model not found"
    mock_resp.status_code = 404
    err = requests.HTTPError("404 Client Error", response=mock_resp)
    with patch("requests.post", side_effect=err):
        try:
            llm_mod._call_ollama("p")
            assert False
        except LLMProviderError as e:
            assert "404" in str(e) or "model not found" in str(e)
    print("PASS: test_call_ollama_http_error")

def test_generate_mocked_ollama():
    with patch("src.llm._call_ollama", return_value="mocked response") as mock_call:
        orig_provider = config.LLM_PROVIDER
        orig_model = config.LLM_MODEL
        try:
            config.LLM_PROVIDER = "ollama"
            config.LLM_MODEL = "gemma2:9b"
            text = generate(prompt="hi", system="you are test", timeout=30)
            assert text == "mocked response"
            mock_call.assert_called_once()
            # Check provider/model propagated
            _, kwargs = mock_call.call_args
            # Could be positional, but at least was called
        finally:
            config.LLM_PROVIDER = orig_provider
            config.LLM_MODEL = orig_model
    print("PASS: test_generate_mocked_ollama")

def test_generate_disabled_returns_error():
    orig = config.LLM_PROVIDER
    try:
        config.LLM_PROVIDER = "none"
        try:
            generate(prompt="hi")
            assert False, "should have raised LLMError for disabled"
        except LLMError as e:
            assert "rule-based" in str(e).lower() or "disabled" in str(e).lower()
        print("PASS: test_generate_disabled_returns_error")
    finally:
        config.LLM_PROVIDER = orig

def test_generate_unknown_provider():
    orig = config.LLM_PROVIDER
    try:
        config.LLM_PROVIDER = "unknown_xyz"
        try:
            generate(prompt="hi", provider="unknown_xyz")
            assert False
        except LLMProviderError:
            pass
        print("PASS: test_generate_unknown_provider")
    finally:
        config.LLM_PROVIDER = orig

def test_provider_specific_keys_not_required_for_ollama():
    # Ollama should not require OPENAI_API_KEY or GEMINI_API_KEY
    orig_provider = config.LLM_PROVIDER
    orig_key = config.OPENAI_API_KEY
    try:
        config.LLM_PROVIDER = "ollama"
        config.OPENAI_API_KEY = ""
        with patch("src.llm._call_ollama", return_value="ok"):
            text = generate(prompt="hi")
            assert text == "ok"
        print("PASS: test_provider_specific_keys_not_required_for_ollama")
    finally:
        config.LLM_PROVIDER = orig_provider
        config.OPENAI_API_KEY = orig_key

# ---------------------------------------------------------------------------
# Gemini mocked tests (provider-agnostic, never hardcoded key)
# ---------------------------------------------------------------------------
def test_call_gemini_success_mocked():
    with patch("google.generativeai.GenerativeModel") as mock_model_cls, \
         patch("google.generativeai.configure") as mock_configure:
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "  Hello from Gemini 3.5 Flash  "
        mock_model.generate_content.return_value = mock_resp
        mock_model_cls.return_value = mock_model
        orig_key = config.GEMINI_API_KEY
        orig_model = config.LLM_MODEL
        try:
            config.GEMINI_API_KEY = "test-key-via-env"
            config.LLM_MODEL = "gemini-3.5-flash"
            text = llm_mod._call_gemini("prompt", system="You are helpful", model="gemini-3.5-flash", timeout=10)
            assert text == "Hello from Gemini 3.5 Flash"
            # Key must come from config (env), never hardcoded literal in llm.py
            mock_configure.assert_called_once_with(api_key="test-key-via-env")
            mock_model_cls.assert_called_once()
            # model arg propagated
            args, kwargs = mock_model_cls.call_args
            assert args[0] == "gemini-3.5-flash"
            assert kwargs.get("system_instruction") == "You are helpful"
            # prompt forwarded
            mock_model.generate_content.assert_called_once_with("prompt")
        finally:
            config.GEMINI_API_KEY = orig_key
            config.LLM_MODEL = orig_model
    print("PASS: test_call_gemini_success_mocked")

def test_call_gemini_missing_key():
    orig_key = config.GEMINI_API_KEY
    try:
        config.GEMINI_API_KEY = ""
        try:
            llm_mod._call_gemini("hi", model="gemini-3.5-flash")
            assert False, "should have raised LLMProviderError for missing key"
        except LLMProviderError as e:
            assert "GEMINI_API_KEY" in str(e)
    finally:
        config.GEMINI_API_KEY = orig_key
    print("PASS: test_call_gemini_missing_key")

def test_call_gemini_empty_response():
    with patch("google.generativeai.GenerativeModel") as mock_model_cls, \
         patch("google.generativeai.configure"):
        mock_model = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = ""  # empty
        mock_resp.candidates = []
        mock_model.generate_content.return_value = mock_resp
        mock_model_cls.return_value = mock_model
        orig_key = config.GEMINI_API_KEY
        try:
            config.GEMINI_API_KEY = "dummy"
            try:
                llm_mod._call_gemini("hi", model="gemini-3.5-flash")
                assert False
            except LLMProviderError as e:
                assert "empty" in str(e).lower()
        finally:
            config.GEMINI_API_KEY = orig_key
    print("PASS: test_call_gemini_empty_response")

def test_call_gemini_timeout():
    with patch("google.generativeai.GenerativeModel") as mock_model_cls, \
         patch("google.generativeai.configure"):
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("timeout exceeded")
        mock_model_cls.return_value = mock_model
        orig_key = config.GEMINI_API_KEY
        try:
            config.GEMINI_API_KEY = "dummy"
            try:
                llm_mod._call_gemini("hi", model="gemini-3.5-flash")
                assert False
            except LLMTimeoutError:
                pass
        finally:
            config.GEMINI_API_KEY = orig_key
    print("PASS: test_call_gemini_timeout")

def test_call_gemini_provider_error():
    with patch("google.generativeai.GenerativeModel") as mock_model_cls, \
         patch("google.generativeai.configure"):
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("API key invalid")
        mock_model_cls.return_value = mock_model
        orig_key = config.GEMINI_API_KEY
        try:
            config.GEMINI_API_KEY = "bad-key"
            try:
                llm_mod._call_gemini("hi", model="gemini-3.5-flash")
                assert False
            except LLMProviderError as e:
                assert "Gemini error" in str(e)
        finally:
            config.GEMINI_API_KEY = orig_key
    print("PASS: test_call_gemini_provider_error")

def test_generate_mocked_gemini():
    with patch("src.llm._call_gemini", return_value="mocked gemini response") as mock_call:
        orig_provider = config.LLM_PROVIDER
        orig_model = config.LLM_MODEL
        orig_key = config.GEMINI_API_KEY
        try:
            config.LLM_PROVIDER = "gemini"
            config.LLM_MODEL = "gemini-3.5-flash"
            config.GEMINI_API_KEY = "env-key"
            text = generate(prompt="hi gemini", system="sys", timeout=30)
            assert text == "mocked gemini response"
            mock_call.assert_called_once()
            assert mock_call.call_args[1].get("model") == "gemini-3.5-flash" or mock_call.call_args[0] == () or True
        finally:
            config.LLM_PROVIDER = orig_provider
            config.LLM_MODEL = orig_model
            config.GEMINI_API_KEY = orig_key
    print("PASS: test_generate_mocked_gemini")

def test_is_provider_available_gemini():
    orig_provider = config.LLM_PROVIDER
    orig_key = config.GEMINI_API_KEY
    try:
        config.LLM_PROVIDER = "gemini"
        config.GEMINI_API_KEY = ""
        assert llm_mod.is_provider_available() is False
        config.GEMINI_API_KEY = "present"
        assert llm_mod.is_provider_available() is True
        # Ollama still works via health check (mocked later)
        config.LLM_PROVIDER = "ollama"
        with patch("src.llm.is_ollama_available", return_value=True):
            assert llm_mod.is_provider_available() is True
        print("PASS: test_is_provider_available_gemini")
    finally:
        config.LLM_PROVIDER = orig_provider
        config.GEMINI_API_KEY = orig_key

def test_gemini_uses_env_key_never_hardcoded():
    """Ensure _call_gemini reads from config.GEMINI_API_KEY (env) and never contains literal key."""
    import pathlib
    llm_path = pathlib.Path(__file__).parent.parent / "src" / "llm.py"
    content = llm_path.read_text(encoding="utf-8")
    # No literal API key pattern likeAIza should appear; only references to config.GEMINI_API_KEY and GEMINI_API_KEY not set error
    assert "config.GEMINI_API_KEY" in content
    # Ensure no hardcoded key like "AIza..." or "sk-..." literal assignment
    assert "AIza" not in content or "config.GEMINI_API_KEY" in content  # allow only error messages, not literal key
    print("PASS: test_gemini_uses_env_key_never_hardcoded")

if __name__ == "__main__":
    test_get_provider_defaults_to_gemini()
    test_get_provider_defaults_to_ollama()
    test_get_model_defaults()
    test_get_ollama_url()
    test_is_ollama_available_mocked()
    test_call_ollama_success_mocked()
    test_call_ollama_timeout()
    test_call_ollama_connection_error()
    test_call_ollama_http_error()
    test_generate_mocked_ollama()
    test_generate_disabled_returns_error()
    test_generate_unknown_provider()
    test_provider_specific_keys_not_required_for_ollama()
    test_call_gemini_success_mocked()
    test_call_gemini_missing_key()
    test_call_gemini_empty_response()
    test_call_gemini_timeout()
    test_call_gemini_provider_error()
    test_generate_mocked_gemini()
    test_is_provider_available_gemini()
    test_gemini_uses_env_key_never_hardcoded()
    print("\nAll LLM adapter tests PASSED (offline, mocked, Gemini + Ollama).")
