"""Unit tests for Phase 2 Milestone 2: script_generator integrated with src/llm.py.

All tests mocked — no Ollama, no network, no API keys required.

Run:
  python -m pytest tests/test_script_generator.py -v
  python tests/test_script_generator.py
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock
import json
import config
from src.llm import LLMError, LLMTimeoutError, LLMConnectionError

# Import after mock setup to avoid config side-effects
import src.script_generator as sg

# Sample transcript for tests — factual, short, English (to test Telugu translation path)
SAMPLE_TRANSCRIPT = (
    "Artificial Intelligence is transforming education. "
    "It helps students learn at their own pace with personalized feedback. "
    "Teachers can use AI to automate grading and focus on mentoring. "
    "However, we must ensure data privacy and avoid bias in AI models. "
    "The future of learning will be human plus AI collaboration."
)
SAMPLE_TITLE = "AI in Education"

# Expected LLM JSON — simple Telugu, Anjali/Ravi alternating, factual (no invented stats)
MOCK_LLM_JSON = json.dumps([
    {"speaker": "Anjali", "text": "హాయ్ రవి! AI విద్యలో ఎలా మారుస్తోంది?"},
    {"speaker": "Ravi", "text": "AI విద్యార్థులకు వారి వేగంతో నేర్చుకునేందుకు సహాయపడుతుంది, వ్యక్తిగత ఫీడ్బ్యాక్ ఇస్తుంది."},
    {"speaker": "Anjali", "text": "టీచర్లకు ఏం లాభం?"},
    {"speaker": "Ravi", "text": "టీచర్లు గ్రేడింగ్ ఆటోమేట్ చేసి మెంటరింగ్ పై దృష్టి పెట్టవచ్చు."},
    {"speaker": "Anjali", "text": "జాగ్రత్తలు ఏమిటి?"},
    {"speaker": "Ravi", "text": "డేటా ప్రైవసీ కాపాడాలి, బయాస్ నివారించాలి, మానవుడు ప్లస్ AI కలిసి నేర్చుకోవాలి."},
    {"speaker": "Anjali", "text": "చాలా బాగుంది, క్లుప్తంగా చెప్పు."},
    {"speaker": "Ravi", "text": "ముఖ్యంగా AI వ్యక్తిగత అభ్యాసం, టీచర్ సహకారం, ప్రైవసీ జాగ్రత్తలతో భవిష్యత్తు మెరుగవుతుంది."},
], ensure_ascii=False)

MOCK_LLM_JSON_FENCED = "```json\n" + MOCK_LLM_JSON + "\n```"

def test_successful_llm_generated_telugu_script():
    with patch("src.script_generator.llm_generate", return_value=MOCK_LLM_JSON) as mock_gen:
        # Ensure provider is ollama (default after Milestone 1)
        orig_provider = config.LLM_PROVIDER
        orig_model = config.LLM_MODEL
        try:
            config.LLM_PROVIDER = "ollama"
            config.LLM_MODEL = "gemma2:9b"
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            assert isinstance(result, list) and len(result) >= 4
            # Check speakers are Anjali/Ravi
            for item in result:
                assert item["speaker"] in ("Anjali", "Ravi")
                assert isinstance(item["text"], str) and len(item["text"]) > 5
            # Verify mocked LLM was called (not fallback)
            assert mock_gen.called
            print("PASS: test_successful_llm_generated_telugu_script")
        finally:
            config.LLM_PROVIDER = orig_provider
            config.LLM_MODEL = orig_model

def test_correct_use_of_transcript_content_in_prompt():
    captured = {}
    def fake_generate(prompt, system, timeout=90):
        captured["prompt"] = prompt
        captured["system"] = system
        return MOCK_LLM_JSON
    with patch("src.script_generator.llm_generate", side_effect=fake_generate):
        orig_provider = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "ollama"
            sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            # Transcript content must be in prompt
            assert SAMPLE_TRANSCRIPT[:40] in captured["prompt"] or "Artificial Intelligence" in captured["prompt"]
            assert SAMPLE_TITLE in captured["prompt"] or "AI in Education" in captured["prompt"]
            # System should contain Telugu podcast writer instruction and factual rule
            assert "Telugu podcast script writer" in captured["system"]
            assert "Do NOT invent" in captured["system"] or "Do NOT invent" in captured["system"]
            # Prompt should mention Anjali/Ravi
            assert "Anjali" in captured["system"] and "Ravi" in captured["system"]
            print("PASS: test_correct_use_of_transcript_content_in_prompt")
        finally:
            config.LLM_PROVIDER = orig_provider

def test_expected_anjali_ravi_two_speaker_format():
    with patch("src.script_generator.llm_generate", return_value=MOCK_LLM_JSON):
        orig_provider = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "ollama"
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            # Check alternating Anjali/Ravi, starting with Anjali
            assert result[0]["speaker"] == "Anjali"
            for i, item in enumerate(result):
                expected = "Anjali" if i % 2 == 0 else "Ravi"
                assert item["speaker"] == expected, f"Turn {i} expected {expected} got {item['speaker']}"
            print("PASS: test_expected_anjali_ravi_two_speaker_format")
        finally:
            config.LLM_PROVIDER = orig_provider

def test_llm_error_causes_rule_based_fallback():
    with patch("src.script_generator.llm_generate", side_effect=LLMTimeoutError("timed out")) as mock_gen:
        orig_provider = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "ollama"
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            # Should have fallen back to rule-based (contains topic from title)
            assert any(SAMPLE_TITLE in item["text"] or "ఈ వీడియో" in item["text"] or "AI" in item["text"] for item in result)
            # Verify fallback was rule-based: first turn is Anjali greeting with topic
            assert result[0]["speaker"] == "Anjali" and "హాయ్ రవి" in result[0]["text"]
            assert mock_gen.called
            print("PASS: test_llm_error_causes_rule_based_fallback")
        finally:
            config.LLM_PROVIDER = orig_provider

    # Also test connection error
    with patch("src.script_generator.llm_generate", side_effect=LLMConnectionError("not running")):
        orig_provider = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "ollama"
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            assert len(result) >= 4
            print("PASS: test_llm_error_causes_rule_based_fallback (connection)")
        finally:
            config.LLM_PROVIDER = orig_provider

def test_existing_rule_based_still_works():
    # Directly test _rule_based_script still produces expected structure
    result = sg._rule_based_script(SAMPLE_TRANSCRIPT, SAMPLE_TITLE, max_turns=8)
    assert len(result) == 8
    assert result[0]["speaker"] == "Anjali"
    assert SAMPLE_TITLE in result[0]["text"]
    # Check all have speaker/text
    for item in result:
        assert "speaker" in item and "text" in item
    print("PASS: test_existing_rule_based_still_works")

def test_disabled_provider_still_works():
    # LLM disabled -> should not call llm_generate, directly fallback
    # Note: after Milestone 1, empty LLM_PROVIDER defaults to ollama (not disabled),
    # so explicit "none"/"rule-based" must be used to force fallback.
    with patch("src.script_generator.llm_generate") as mock_gen:
        orig_provider = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "none"
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            assert len(result) >= 4
            assert not mock_gen.called, "llm_generate should not be called when disabled"
            assert result[0]["speaker"] == "Anjali"
            print("PASS: test_disabled_provider_still_works (none)")
        finally:
            config.LLM_PROVIDER = orig_provider

    with patch("src.script_generator.llm_generate") as mock_gen:
        orig_provider = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "rule-based"
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            assert not mock_gen.called
            print("PASS: test_disabled_provider_still_works (rule-based)")
        finally:
            config.LLM_PROVIDER = orig_provider

def test_markdown_fences_normalized():
    # LLM may still emit ```json fences despite instruction — should be stripped
    with patch("src.script_generator.llm_generate", return_value=MOCK_LLM_JSON_FENCED):
        orig_provider = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "ollama"
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            assert len(result) >= 4
            # Should not contain fences in output
            for item in result:
                assert "```" not in item["text"]
            print("PASS: test_markdown_fences_normalized")
        finally:
            config.LLM_PROVIDER = orig_provider

def test_no_openai_key_required_when_using_ollama():
    # When provider is ollama, OPENAI_API_KEY empty should not affect
    orig_provider = config.LLM_PROVIDER
    orig_openai = config.OPENAI_API_KEY
    try:
        config.LLM_PROVIDER = "ollama"
        config.OPENAI_API_KEY = ""
        with patch("src.script_generator.llm_generate", return_value=MOCK_LLM_JSON) as mock_gen:
            result = sg.generate_telugu_script(SAMPLE_TRANSCRIPT, title=SAMPLE_TITLE)
            assert mock_gen.called
            # Ensure generate was called with ollama (provider arg default)
            assert result[0]["speaker"] == "Anjali"
            print("PASS: test_no_openai_key_required_when_using_ollama")
    finally:
        config.LLM_PROVIDER = orig_provider
        config.OPENAI_API_KEY = orig_openai

if __name__ == "__main__":
    test_successful_llm_generated_telugu_script()
    test_correct_use_of_transcript_content_in_prompt()
    test_expected_anjali_ravi_two_speaker_format()
    test_llm_error_causes_rule_based_fallback()
    test_existing_rule_based_still_works()
    test_disabled_provider_still_works()
    test_markdown_fences_normalized()
    test_no_openai_key_required_when_using_ollama()
    print("\nAll script_generator tests PASSED (mocked, no Ollama/network required).")
