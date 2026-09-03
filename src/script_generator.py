"""Convert YouTube transcript → conversational Telugu podcast script (2 speakers)."""
import json
import logging
import re
from typing import List, Dict

import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Telugu podcast scriptwriter.
Convert the given YouTube transcript into a NATURAL, CONVERSATIONAL Telugu podcast dialogue.

Speakers:
- Anjali (female, curious, asks simple questions like a friendly listener)
- Ravi (male, knowledgeable, explains in SIMPLE Telugu)

Requirements:
- Language: Simple, everyday Telugu (mostly Telugu script, sprinkle English words where natural like "concept", "example" is okay). Avoid heavy literary/Granthika Telugu.
- Tone: Friendly, warm, like two friends chatting over chai. Brief summaries, not verbatim lectures.
- Structure: 8 to {max_turns} turns total, alternating speakers. Start with Anjali greeting + topic intro, end with Ravi closing + key takeaway.
- Content: Extract 3-5 KEY points from transcript, summarize each in 1-2 turns. Skip filler, ads, repetition.
- Length: Each turn = 1-3 sentences, ~20-40 words. Total script ~400-700 words.
- Output: VALID JSON array ONLY, no markdown, no extra text. Format:
[
  {{"speaker": "Anjali", "text": "తెలుగులో ..."}},
  {{"speaker": "Ravi", "text": "తెలుగులో ..."}}
]

Rules:
- Speaker names must be exactly "Anjali" and "Ravi" alternating.
- Text must be in Telugu (Unicode Telugu script). Use simple language.
- If transcript is in English, translate ideas to Telugu naturally, don't transliterate English sentences.
- No stage directions, just dialogue text.
"""

USER_TEMPLATE = """Transcript (truncated, may be English):
\"\"\"
{transcript}
\"\"\"

Video title hint: {title}

Generate the Telugu podcast script JSON now. Remember: simple Telugu, conversational, brief summaries, 8-{max_turns} turns."""


def generate_telugu_script(transcript: str, title: str = "") -> List[Dict[str, str]]:
    """Main entry — free rule-based fallback is the default.

    Basic workflow and sheet connection test need NO LLM and NO API key.
    OPENAI_API_KEY / GEMINI / GROQ / Ollama are all optional and tried only
    when explicitly configured. Missing keys or missing Ollama never raises.
    """
    max_turns = config.MAX_PODCAST_TURNS
    transcript = transcript[: config.MAX_TRANSCRIPT_CHARS]

    # Build provider list only from explicit config / available keys.
    # If nothing configured, we go straight to rule-based (no network).
    providers: List[str] = []
    prov = (config.LLM_PROVIDER or "").lower().strip()

    if prov == "openai" and config.OPENAI_API_KEY:
        providers = ["openai"]
    elif prov == "gemini" and config.GEMINI_API_KEY:
        providers = ["gemini"]
    elif prov == "groq" and config.GROQ_API_KEY:
        providers = ["groq"]
    elif prov == "ollama":
        # Ollama is optional — only try if user explicitly asked for it
        providers = ["ollama"]
    elif prov in ("", "rule-based", "rules", "none", "off"):
        providers = []  # stay on rule-based
    else:
        # Auto-detect only if user set a key but left LLM_PROVIDER empty
        if config.OPENAI_API_KEY:
            providers.append("openai")
        if config.GEMINI_API_KEY:
            providers.append("gemini")
        if config.GROQ_API_KEY:
            providers.append("groq")
        # Do NOT auto-add ollama — it requires a local server and should be opt-in.
        # If user wants Ollama, set LLM_PROVIDER=ollama explicitly.

    if not providers:
        log.info("No LLM configured (OPENAI_API_KEY/Ollama not set) — using free rule-based script generation")
        return _rule_based_script(transcript, title, max_turns)

    last_err = None
    for provider in providers:
        try:
            if provider == "openai":
                return _via_openai(transcript, title, max_turns)
            elif provider == "gemini":
                return _via_gemini(transcript, title, max_turns)
            elif provider == "groq":
                return _via_groq(transcript, title, max_turns)
            elif provider == "ollama":
                return _via_ollama(transcript, title, max_turns)
        except Exception as e:
            log.warning("LLM %s failed: %s", provider, e)
            last_err = e
            continue

    log.warning("All configured LLM providers failed (%s), using rule-based fallback", last_err)
    return _rule_based_script(transcript, title, max_turns)


def _via_openai(transcript, title, max_turns):
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(max_turns=max_turns)},
            {"role": "user", "content": USER_TEMPLATE.format(transcript=transcript, title=title, max_turns=max_turns)},
        ],
        temperature=0.7,
        max_tokens=2000,
    )
    text = resp.choices[0].message.content.strip()
    return _parse_json_script(text)

def _via_gemini(transcript, title, max_turns):
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        config.LLM_MODEL or "gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT.format(max_turns=max_turns),
    )
    resp = model.generate_content(USER_TEMPLATE.format(transcript=transcript, title=title, max_turns=max_turns))
    return _parse_json_script(resp.text)

def _via_groq(transcript, title, max_turns):
    from groq import Groq
    client = Groq(api_key=config.GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=config.LLM_MODEL or "llama-3.1-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(max_turns=max_turns)},
            {"role": "user", "content": USER_TEMPLATE.format(transcript=transcript, title=title, max_turns=max_turns)},
        ],
        temperature=0.7,
        max_tokens=2000,
    )
    return _parse_json_script(resp.choices[0].message.content.strip())

def _via_ollama(transcript, title, max_turns):
    import requests
    prompt = SYSTEM_PROMPT.format(max_turns=max_turns) + "\n\n" + USER_TEMPLATE.format(transcript=transcript, title=title, max_turns=max_turns)
    resp = requests.post(
        f"{config.OLLAMA_BASE_URL}/api/generate",
        json={"model": config.LLM_MODEL or "llama3.1:8b", "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return _parse_json_script(resp.json()["response"])

def _parse_json_script(text: str) -> List[Dict[str, str]]:
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    # Extract JSON array
    m = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if m:
        text = m.group(0)
    data = json.loads(text)
    # Validate
    cleaned = []
    for item in data:
        speaker = item.get("speaker", "").strip().capitalize()
        t = item.get("text", "").strip()
        if speaker not in ("Anjali", "Ravi"):
            # Fix alternating if LLM used Telugu names
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
