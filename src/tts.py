"""Open-source Telugu TTS → MP3 (2 speakers)."""
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import List, Dict

import config

log = logging.getLogger(__name__)

def generate_podcast_mp3(script: List[Dict[str, str]], output_path: Path) -> Path:
    """
    script: [{"speaker": "Anjali"|"Ravi", "text": "..."}, ...]
    output_path: final .mp3 file
    Returns Path to mp3.
    """
    engine = config.TTS_ENGINE.lower()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Try preferred engine, fallback chain: edge -> gtts -> coqui/piper
    engines_to_try = [engine]
    for fallback in ["edge", "gtts"]:
        if fallback not in engines_to_try:
            engines_to_try.append(fallback)

    last_err = None
    for eng in engines_to_try:
        try:
            if eng == "edge":
                return _via_edge_tts(script, output_path)
            elif eng == "gtts":
                return _via_gtts(script, output_path)
            elif eng == "coqui":
                return _via_coqui(script, output_path)
            elif eng == "piper":
                return _via_piper(script, output_path)
            else:
                log.warning("Unknown TTS engine %s, trying edge", eng)
                return _via_edge_tts(script, output_path)
        except Exception as e:
            log.warning("TTS engine %s failed: %s", eng, e)
            last_err = e
            continue

    raise RuntimeError(f"All TTS engines failed. Last error: {last_err}. Install ffmpeg and check internet.")

# ---------- edge-tts (preferred, free, excellent Telugu neural voices) ----------
def _via_edge_tts(script, output_path: Path) -> Path:
    import edge_tts  # noqa
    # edge-tts is async
    async def _synthesize():
        from pydub import AudioSegment
        import edge_tts

        tmpdir = Path(tempfile.mkdtemp(prefix="telugu_tts_"))
        segments = []
        for idx, turn in enumerate(script):
            speaker = turn["speaker"]
            text = turn["text"].strip()
            if not text:
                continue
            voice = config.VOICE_MAP.get(speaker, "te-IN-ShrutiNeural")
            # Rate slightly slower for clarity
            communicate = edge_tts.Communicate(text, voice, rate="-5%")
            tmp_path = tmpdir / f"seg_{idx:02d}.mp3"
            await communicate.save(str(tmp_path))
            # Load with pydub
            seg = AudioSegment.from_file(str(tmp_path))
            segments.append(seg)
            log.info("edge-tts [%s/%s] %s: %d chars -> %d ms", idx+1, len(script), speaker, len(text), len(seg))

        if not segments:
            raise ValueError("No audio segments generated")

        # Concatenate with 400ms silence + 100ms fade
        from pydub import AudioSegment as AS
        silence = AS.silent(duration=400)
        combined = segments[0]
        for seg in segments[1:]:
            combined += silence + seg

        # Optional: add intro/outro silence
        combined = AS.silent(duration=300) + combined + AS.silent(duration=500)
        combined.export(str(output_path), format="mp3", bitrate="192k")
        log.info("Podcast MP3 saved: %s (%.1fs)", output_path, len(combined)/1000)
        # Cleanup
        for f in tmpdir.glob("*.mp3"):
            try: f.unlink()
            except: pass
        try: tmpdir.rmdir()
        except: pass

    asyncio.run(_synthesize())
    return output_path

# ---------- gTTS fallback ----------
def _via_gtts(script, output_path: Path) -> Path:
    from gtts import gTTS
    from pydub import AudioSegment
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="telugu_gtts_"))
    segments = []
    for idx, turn in enumerate(script):
        text = turn["text"].strip()
        if not text:
            continue
        tmp_path = tmpdir / f"seg_{idx:02d}.mp3"
        # gTTS te (Telugu) - speed slightly slow for clarity
        tts = gTTS(text=text, lang="te", slow=False)
        tts.save(str(tmp_path))
        seg = AudioSegment.from_file(str(tmp_path))
        segments.append(seg)
        log.info("gTTS [%d/%d] %s: %d chars", idx+1, len(script), turn["speaker"], len(text))

    if not segments:
        raise ValueError("gTTS produced no segments")

    silence = AudioSegment.silent(duration=500)
    combined = segments[0]
    for seg in segments[1:]:
        combined += silence + seg
    combined = AudioSegment.silent(duration=300) + combined + AudioSegment.silent(duration=500)
    combined.export(str(output_path), format="mp3", bitrate="192k")
    log.info("gTTS MP3 saved: %s", output_path)
    for f in tmpdir.glob("*.mp3"):
        try: f.unlink()
        except: pass
    try: tmpdir.rmdir()
    except: pass
    return output_path

# ---------- Coqui TTS (offline, optional) ----------
def _via_coqui(script, output_path: Path) -> Path:
    """Requires: pip install TTS . First run downloads model (~500MB)."""
    from TTS.api import TTS
    from pydub import AudioSegment
    import tempfile

    # Telugu model - use XTTS or VITS if available
    # Fallback to generic multilingual
    model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
    tts = TTS(model_name)
    tmpdir = Path(tempfile.mkdtemp(prefix="telugu_coqui_"))
    segments = []
    for idx, turn in enumerate(script):
        tmp_wav = tmpdir / f"seg_{idx:02d}.wav"
        # Note: XTTS needs speaker_wav; for Telugu we use default
        tts.tts_to_file(text=turn["text"], file_path=str(tmp_wav), language="te")
        seg = AudioSegment.from_file(str(tmp_wav))
        segments.append(seg)

    silence = AudioSegment.silent(duration=400)
    combined = segments[0]
    for seg in segments[1:]:
        combined += silence + seg
    combined.export(str(output_path), format="mp3", bitrate="192k")
    return output_path

# ---------- Piper (offline, fast) ----------
def _via_piper(script, output_path: Path) -> Path:
    """Requires piper binary + te_TE models. Set PIPER_* in .env."""
    import subprocess
    from pydub import AudioSegment

    if not config.PIPER_BINARY_PATH or not Path(config.PIPER_BINARY_PATH).exists():
        raise FileNotFoundError("PIPER_BINARY_PATH not set or not found")

    tmpdir = Path(tempfile.mkdtemp(prefix="telugu_piper_"))
    segments = []
    for idx, turn in enumerate(script):
        speaker = turn["speaker"]
        model = config.PIPER_MODEL_PATH_TE_FEMALE if speaker == "Anjali" else config.PIPER_MODEL_PATH_TE_MALE
        if not model:
            model = config.PIPER_MODEL_PATH_TE_FEMALE or config.PIPER_MODEL_PATH_TE_MALE
        tmp_wav = tmpdir / f"seg_{idx:02d}.wav"
        # Piper reads from stdin, writes wav
        proc = subprocess.run(
            [config.PIPER_BINARY_PATH, "--model", model, "--output_file", str(tmp_wav)],
            input=turn["text"].encode("utf-8"),
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"piper failed for segment {idx}")
        seg = AudioSegment.from_file(str(tmp_wav))
        segments.append(seg)

    silence = AudioSegment.silent(duration=400)
    combined = segments[0]
    for seg in segments[1:]:
        combined += silence + seg
    combined.export(str(output_path), format="mp3", bitrate="192k")
    return output_path

# ---------- Helper: list available edge voices ----------
async def list_telugu_voices():
    import edge_tts
    voices = await edge_tts.list_voices()
    for v in voices:
        if "te-IN" in v["ShortName"]:
            print(v["ShortName"], v["Gender"], v["Locale"])
