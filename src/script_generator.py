"""Convert YouTube transcript -> conversational Telugu podcast script (2 speakers).

Phase 2 Milestone 3: Gemini 3.5 Flash primary via src/llm.py + Ollama optional + rule-based fallback.

- Uses src.llm.generate() — provider-agnostic, no direct Gemini/Ollama/OpenAI calls here.
- Respects LLM_PROVIDER / LLM_MODEL via config (default gemini/gemini-3.5-flash, Ollama gemma2:9b optional).
- GEMINI_API_KEY from env (never hardcoded) when LLM_PROVIDER=gemini; Ollama needs no key.
- Focused Telugu prompt + system instruction (Anjali/Ravi, factual, natural, concise for TTS).
- Rule-based fallback preserved exactly; only LLMError triggers fallback (not unrelated programming errors).
- Public API preserved: generate_telugu_script(transcript, title="") -> List[Dict[speaker,text]]
"""
import json
import logging
import re
from typing import List, Dict

import config
from src.llm import generate as llm_generate, LLMError, get_provider

log = logging.getLogger(__name__)

# System instruction for the LLM — act as Telugu podcast writer, follow factual + format rules
SYSTEM_PROMPT = """You are a Telugu podcast script writer.

Your role: Convert the supplied YouTube transcript into a simple, natural, engaging Telugu conversation between two speakers. Act strictly as a writer — do not add explanations, analysis, or metadata beyond the dialogue.

Speakers:
- Anjali (female, curious — asks clear, simple questions as a friendly listener)
- Ravi (male, knowledgeable — answers in simple, warm Telugu)

Language:
- Use simple, everyday spoken Telugu (Unicode Telugu script). Avoid formal / literary / Granthika Telugu.
- Sprinkle common English words only when natural (e.g., concept, example). Prefer Telugu.
- Keep sentences short and listener-friendly.

Content:
- Preserve the transcript's important factual content. Summarize 3–5 key points faithfully.
- Do NOT invent facts, sources, statistics, quotes, numbers, dates, or events that are not in the transcript.
- Skip filler, ads, self-promo, repetition, and off-topic chatter.
- If transcript is English, translate ideas naturally into Telugu — do not transliterate English sentences verbatim.

Structure & Length:
- Total {max_turns} turns max, alternating speakers. Start with Anjali greeting + topic, end with Ravi short takeaway.
- Each turn: 1–3 sentences, ~20–40 words. Total ~400–700 words — concise enough for TTS.
- Maintain Anjali/Ravi alternation; no other speakers.

Output format (strict):
- Output ONLY a VALID JSON array, no markdown, no fences, no explanations, no metadata.
- Format exactly:
[
  {{"speaker": "Anjali", "text": "తెలుగులో ..."}},
  {{"speaker": "Ravi", "text": "తెలుగులో ..."}}
]
- Speaker values must be exactly "Anjali" and "Ravi" alternating.
- Text must be Telugu (Unicode), natural dialogue without stage directions.
"""

USER_TEMPLATE = """Transcript (truncated to {max_chars} chars, may be English/Telugu):
\"\"\"
{transcript}
\"\"\"

Video title hint: {title}

Task: Convert the above transcript into the Telugu podcast JSON described. Keep it factual — do not invent beyond the transcript. Keep language simple and conversational, concise for TTS. Output JSON array only (8–{max_turns} turns, Anjali/Ravi)."""


def generate_telugu_script(transcript: str, title: str = "") -> List[Dict[str, str]]:
    """Main entry — tries LLM via src/llm.py (provider-agnostic), falls back to rule-based on LLMError.

    - Respects LLM_PROVIDER / LLM_MODEL (default gemini/gemini-3.5-flash, ollama/gemma2:9b optional).
    - GEMINI_API_KEY from env when LLM_PROVIDER=gemini (never hardcoded); Ollama needs no key.
    - If provider disabled (rule-based/none/off/empty forcing fallback), skips LLM.
    - If provider unavailable, times out, or raises LLMError (Gemini or Ollama), falls back to _rule_based_script.
    - Does not silently swallow unrelated programming errors (e.g., bugs in _parse_json_script beyond ValueError are re-raised).
    - Keeps _rule_based_script unchanged for compatibility; public API preserved.
    """
    max_turns = config.MAX_PODCAST_TURNS
    transcript = (transcript or "").strip()
    if not transcript:
        log.warning("Empty transcript — using rule-based fallback")
        return _rule_based_script("", title, max_turns)

    # Truncate for LLM context (keep rule-based on truncated as well for consistency)
    truncated = transcript[: config.MAX_TRANSCRIPT_CHARS]

    prov = (get_provider() or "").strip().lower()
    # Explicit rule-based/disabled check — no LLM call, direct fallback (no LLMError)
    if not prov:
        log.info("LLM disabled (provider=%r) — using rule-based script generation", get_provider())
        return _rule_based_script(truncated, title, max_turns)

    # Build prompt + system for LLM adapter (no direct Ollama calls here)
    system = SYSTEM_PROMPT.format(max_turns=max_turns)
    user_prompt = USER_TEMPLATE.format(
        transcript=truncated,
        title=title or "Untitled",
        max_turns=max_turns,
        max_chars=config.MAX_TRANSCRIPT_CHARS,
    )

    # Call provider-agnostic LLM adapter with clear timeout (Gemini/Ollama-aware via src.llm)
    try:
        # Use 90s for gemini-3.5-flash / gemma2:9b (generous for network + large model)
        raw_text = llm_generate(prompt=user_prompt, system=system, timeout=90)
    except LLMError as e:
        # Expected LLM failure — fallback to rule-based (do not swallow programming errors)
        log.warning("LLM %s failed (%s) — falling back to rule-based script: %s", prov, type(e).__name__, e)
        return _rule_based_script(truncated, title, max_turns)
    except Exception as e:
        # Unrelated programming error — do not silently swallow, log and re-raise
        # But to keep pipeline resilient, we still fallback for any Exception that is clearly LLM-related?
        # Spec: do not silently swallow unrelated programming errors — so re-raise if not LLMError
        # However, to avoid crashing pipeline on transient LLM output issues, treat JSON errors as fallback below.
        # Here we only catch LLMError above, so other exceptions bubble up.
        log.exception("Unexpected error calling LLM (not LLMError) — re-raising: %s", e)
        raise

    # Normalize LLM output: strip markdown fences if present, then parse JSON array
    # (LLM may still emit ```json fences despite instruction — handle gracefully)
    try:
        return _parse_json_script(raw_text)
    except (ValueError, json.JSONDecodeError) as e:
        # LLM returned malformed JSON — fallback to rule-based (common with small models)
        log.warning("LLM output JSON parse failed (%s) — falling back to rule-based: %s — raw: %r", type(e).__name__, e, raw_text[:300])
        return _rule_based_script(truncated, title, max_turns)


def _parse_json_script(text: str) -> List[Dict[str, str]]:
    # Strip markdown fences (``` or ```json) and surrounding whitespace
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    # Extract JSON array if LLM added surrounding prose (despite instruction)
    m = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if m:
        text = m.group(0)
    data = json.loads(text)
    # Validate and normalize Anjali/Ravi alternation
    cleaned = []
    for item in data:
        speaker = item.get("speaker", "").strip().capitalize()
        t = item.get("text", "").strip()
        if speaker not in ("Anjali", "Ravi"):
            speaker = "Anjali" if len(cleaned) % 2 == 0 else "Ravi"
        if t:
            cleaned.append({"speaker": speaker, "text": t})
    if len(cleaned) < 4:
        raise ValueError(f"Too few turns: {cleaned}")
    return cleaned

def _rule_based_script(transcript: str, title: str, max_turns: int) -> List[Dict[str, str]]:
    """Extractive fallback - no API key needed. Generates simple Telugu template."""
    # Naive sentence split
    sentences = re.split(r"(?<=[.!?।])\s+", transcript.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    # Pick up to 5 key sentences evenly spaced
    if len(sentences) > 5:
        step = len(sentences) / 5
        picked = [sentences[int(i * step)] for i in range(5)]
    else:
        picked = sentences[:5]
    if not picked:
        picked = [transcript[:200]]

    topic = title or "ఈ వీడియో"
    script = []
    script.append({"speaker": "Anjali", "text": f"హాయ్ రవి! ఈరోజు {topic} గురించి మాట్లాడుకుందామా? వీడియోలో ఏముందో చెప్పు?"})
    for i, sent in enumerate(picked):
        # Shorten sentence for summary style
        short = sent[:120].strip()
        if i % 2 == 0:
            script.append({"speaker": "Ravi", "text": f"తప్పకుండా! ముఖ్యమైన విషయం ఏంటంటే - {short} అని చెప్పారు."})
            script.append({"speaker": "Anjali", "text": "అర్థమైంది! దీని వల్ల మనకు ఏం ఉపయోగం?"})
        else:
            script.append({"speaker": "Ravi", "text": f"మరో ముఖ్య విషయం - {short}"})
            if i < len(picked) - 1:
                script.append({"speaker": "Anjali", "text": "బాగుంది, ఇంకా ఏమైనా ఉందా?"})
    script.append({"speaker": "Ravi", "text": "అవును, చివరగా చెప్పాలంటే - ఈ విషయాలు గుర్తు పెట్టుకుంటే చాలా ఉపయోగంగా ఉంటుంది!"})
    script.append({"speaker": "Anjali", "text": "చాలా బాగా చెప్పావు రవి! మళ్లీ కలుద్దాం!"})
    # Trim to max_turns
    return script[:max_turns]
