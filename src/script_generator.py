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

def _compute_target_params(transcript: str) -> Dict[str, int]:
    """Compute target podcast size proportional to source transcript length.

    Scaling keeps short videos short while preserving substantially more
    coverage for long videos. Targets ~0.6x source duration for Telugu audio.
    For ~12-13 min source (~1000-1100 words, ~5800 chars) targets ~7-9 min
    Telugu audio (~1050-1350 words, ~32-38 turns) instead of fixed 14-turn
    ~2 min compression.

    Returns dict with target_turns, min_words, max_words, source_words, source_chars.
    """
    txt = (transcript or "").strip()
    source_words = len(txt.split()) if txt else 0
    source_chars = len(txt)
    # Fallback if word split undercounts (e.g., no spaces): estimate from chars
    if source_words < 10 and source_chars > 50:
        est = source_chars // 5
        if est > source_words:
            source_words = est

    # Proportional target: scales with source so podcast length ~0.7-0.85x source
    # Piper Telugu ~105 wpm actual (measured: 683 words -> 388s). Use 105 wpm for estimates.
    # Short stays short, long grows substantially: 1095 words (~8.4 min) -> ~800-1050 words (~7.6-10 min)
    if source_words < 300:
        # Short ~1-2 min source -> ~3-4 min podcast
        target_turns, min_w, max_w = 12, 350, 600
    elif source_words < 600:
        # Medium-short ~3-4.5 min source -> ~5-7 min podcast
        target_turns, min_w, max_w = 18, 550, 850
    elif source_words < 1000:
        # Medium ~5-7.5 min source -> ~6-8.5 min podcast
        target_turns, min_w, max_w = 24, 700, 1000
    elif source_words < 1500:
        # ~12-13 min source (1095 words, 5825 chars) -> ~7-9 min Telugu (750-1050 words at 105 wpm = 7.1-10 min)
        # Lowered from 1100-1450 to be achievable for LLM and match actual Piper rate
        target_turns, min_w, max_w = 32, 750, 1050
    else:
        # Very long >11 min source
        target_turns, min_w, max_w = 38, 950, 1300

    # Also respect config override as absolute max for safety, but allow larger than
    # config.MAX_PODCAST_TURNS for long sources (avoid fixed 14 cap)
    # If transcript is short, keep cap at config value; if long, use computed larger value
    if source_words >= 800:
        # For long sources, ignore small config cap and use computed scaling
        pass
    else:
        # For short, ensure we don't exceed config cap too much
        cfg_max = getattr(config, "MAX_PODCAST_TURNS", 14)
        try:
            cfg_max = int(cfg_max)
        except Exception:
            cfg_max = 14
        if target_turns > cfg_max + 6 and source_words < 600:
            target_turns = cfg_max + 4

    return {
        "target_turns": target_turns,
        "min_words": min_w,
        "max_words": max_w,
        "source_words": source_words,
        "source_chars": source_chars,
    }


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
- Preserve substantially more of the transcript's important factual content — do NOT compress a long video into a brief summary. Cover key facts, arguments, examples, and narrative arc proportionally.
- Do NOT invent facts, sources, statistics, quotes, numbers, dates, or events that are not in the transcript. Do not add facts from your own knowledge.
- Do NOT simply translate the transcript word-for-word — synthesize into natural conversational Telugu.
- Skip filler, ads, self-promo, repetition, and off-topic chatter, but keep important substance.
- If transcript is English, translate ideas naturally into Telugu — do not transliterate English sentences verbatim.

Structure & Length (proportional to source — CRITICAL):
- Source length: ~{source_words} words (~{source_chars} chars, ~{source_minutes:.1f} min spoken). Target Telugu podcast: ~{target_turns} turns (±2, NOT ±6), alternating speakers, start with Anjali greeting + topic, end with Ravi short takeaway.
- Each turn: MUST be 2–3 sentences, ~28–35 words per turn (average at least 25 words). Do NOT write 1-sentence 10-15 word turns. Longer turns are required to reach total word target.
- Total target: ~{min_words}–{max_words} words (aim ~{avg_words} words) — this scales with source length. For this source, that corresponds to roughly {target_minutes:.1f} minutes of Telugu audio at ~105 wpm (proportional, not fixed at 8–14 turns). Do NOT limit a 12-minute source to only 14 short turns and do NOT produce a brief 400-500 word summary for this long source.
- You MUST count words before outputting: total words MUST be at least {min_words} and at least {target_turns} turns. If you produce fewer words or turns, you have FAILED — expand with more factual coverage, more examples, more narrative arcs from source.
- Maintain Anjali/Ravi alternation; no other speakers. Keep it natural, engaging spoken Telugu, not repetitive filler, but do NOT sacrifice coverage for brevity.

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

USER_TEMPLATE = """Transcript (truncated to {max_chars} chars, may be English/Telugu, {source_words} words, ~{source_minutes:.1f} min):
\"\"\"
{transcript}
\"\"\"

Video title hint: {title}

Task: Convert the above transcript into the Telugu podcast JSON described. Keep it factual — do not invent beyond the transcript. Keep language simple and conversational. Source is ~{source_words} words (~{source_minutes:.1f} min) — you MUST produce ~{target_turns} turns (±2) and ~{min_words}–{max_words} words total (aim ~{avg_words} words, ~{target_minutes:.1f} min Telugu audio). Count words: each turn ~28-35 words, total must be >= {min_words}. Proportional coverage is REQUIRED, not a brief 400-word summary. Output JSON array only (Anjali/Ravi alternating, start Anjali, end Ravi)."""


def generate_telugu_script(transcript: str, title: str = "") -> List[Dict[str, str]]:
    """Main entry — tries LLM via src/llm.py (provider-agnostic), falls back to rule-based on LLMError.

    - Respects LLM_PROVIDER / LLM_MODEL (default gemini/gemini-3.5-flash, ollama/gemma2:9b optional).
    - GEMINI_API_KEY from env when LLM_PROVIDER=gemini (never hardcoded); Ollama needs no key.
    - If provider disabled (rule-based/none/off/empty forcing fallback), skips LLM.
    - If provider unavailable, times out, or raises LLMError (Gemini or Ollama), falls back to _rule_based_script.
    - Does not silently swallow unrelated programming errors (e.g., bugs in _parse_json_script beyond ValueError are re-raised).
    - Keeps _rule_based_script for compatibility; public API preserved (proportional sizing).
    """
    transcript = (transcript or "").strip()
    # Truncate for LLM context (keep rule-based on truncated as well)
    truncated = transcript[: config.MAX_TRANSCRIPT_CHARS] if transcript else ""
    # Proportional target based on truncated length (so LLM sees same basis)
    target = _compute_target_params(truncated if truncated else transcript)
    target_turns = target["target_turns"]
    min_w, max_w = target["min_words"], target["max_words"]
    source_words, source_chars = target["source_words"], target["source_chars"]
    avg_words = (min_w + max_w) // 2
    # Rough minute estimates for prompt guidance (not binding) — Piper Telugu ~105 wpm measured (683 words -> 388s)
    source_minutes = source_words / 130.0 if source_words else 0  # ~130 wpm English source estimate
    target_minutes = avg_words / 105.0 if avg_words else 0  # Telugu Piper ~105 wpm actual
    # Keep config.MAX_PODCAST_TURNS as fallback for very short but still respect computed scaling
    # For backwards compat, max_turns variable now is target_turns
    max_turns = target_turns

    if not transcript:
        log.warning("Empty transcript — using rule-based fallback (target %s turns)", target_turns)
        return _rule_based_script("", title, target_turns)

    prov = (get_provider() or "").strip().lower()
    # Explicit rule-based/disabled check — no LLM call, direct fallback (no LLMError)
    if not prov:
        log.info("LLM disabled (provider=%r) — using rule-based script generation (target %s turns, %s words)", get_provider(), target_turns, avg_words)
        return _rule_based_script(truncated, title, target_turns)

    # Build proportional prompt + system for LLM adapter (no direct Ollama calls here)
    system = SYSTEM_PROMPT.format(
        source_words=source_words,
        source_chars=source_chars,
        source_minutes=source_minutes,
        target_turns=target_turns,
        min_words=min_w,
        max_words=max_w,
        avg_words=avg_words,
        target_minutes=target_minutes,
    )
    user_prompt = USER_TEMPLATE.format(
        transcript=truncated,
        title=title or "Untitled",
        max_chars=config.MAX_TRANSCRIPT_CHARS,
        source_words=source_words,
        source_minutes=source_minutes,
        target_turns=target_turns,
        min_words=min_w,
        max_words=max_w,
        avg_words=avg_words,
        target_minutes=target_minutes,
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
    """Extractive fallback - no API key needed. Generates simple Telugu template.

    Scales with max_turns (proportional target) so long transcripts get more
    coverage instead of fixed 5-sentence summary.
    """
    # Naive sentence split
    sentences = re.split(r"(?<=[.!?।])\s+", transcript.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    # Number of key sentences proportional to target_turns:
    # turns ≈ 1 greeting + picked*2 + 2 closing => picked ≈ (max_turns -3)//2
    # For short (12 turns) => ~4 picks, for long (32 turns) => ~14 picks
    desired_picks = max(5, (max_turns - 3) // 2)
    # Clamp to available sentences, but at least 3
    desired_picks = min(len(sentences) if sentences else 0, desired_picks) if sentences else 0
    if desired_picks == 0:
        # Fallback if no sentences: use transcript chunk
        picked = [transcript[:200]] if transcript else [title or "ఈ వీడియో"]
    else:
        if len(sentences) > desired_picks:
            step = len(sentences) / desired_picks
            picked = [sentences[int(i * step)] for i in range(desired_picks)]
        else:
            picked = sentences[:desired_picks]
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
