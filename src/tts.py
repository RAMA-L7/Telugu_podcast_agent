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

    Provider-agnostic: tries TTS_ENGINE first (default piper CPU/offline, Anjali→padmavathi, Ravi→venkatesh),
    then fallback edge-tts/gTTS/coqui. Piper models are outside Git repo, configurable via .env
    PIPER_MODEL_PATH_TE_FEMALE/MALE (see .env.example).
    """
    engine = config.TTS_ENGINE.lower()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Provider-agnostic fallback: preferred engine first, then piper (primary) → edge → gtts → coqui
    # If TTS_ENGINE=piper (default), tries piper then edge/gtts; if edge, tries edge then piper/gtts
    engines_to_try = [engine]
    for fallback in ["piper", "edge", "gtts", "coqui"]:
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

# ---------- Piper (offline, CPU, primary) — Anjali→padmavathi, Ravi→venkatesh ----------
def _via_piper(script, output_path: Path) -> Path:
    """Piper TTS primary — CPU/offline, Anjali=padmavathi-medium (female), Ravi=venkatesh-medium (male).

    Requires: pip install piper-tts + model files outside Git repo (see .env.example).
    - PIPER_MODEL_PATH_TE_FEMALE: te_IN-padmavathi-medium.onnx (Anjali) — ~63MB, 22050Hz
    - PIPER_MODEL_PATH_TE_MALE: te_IN-venkatesh-medium.onnx (Ravi) — ~63MB
    Models downloaded from https://huggingface.co/rhasspy/piper-voices (te/te_IN) — NOT in Git.
    Falls back to PIPER_BINARY_PATH subprocess if Python API unavailable, else raises to trigger edge/gtts fallback.
    """
    from pathlib import Path as _Path

    # Validate model paths — both should be set for 2-speaker podcast; if one missing, fallback to available
    female_model = (config.PIPER_MODEL_PATH_TE_FEMALE or "").strip()
    male_model = (config.PIPER_MODEL_PATH_TE_MALE or "").strip()
    if not female_model and not male_model:
        raise FileNotFoundError("PIPER_MODEL_PATH_TE_FEMALE/MALE not set — set in .env to models outside Git (see .env.example)")

    # Prefer Python API (pip piper-tts) — no binary needed, works on Windows
    try:
        from piper import PiperVoice
        import soundfile as sf
        from pydub import AudioSegment
        import tempfile
        import numpy as np

        # Load voices — cache per speaker to avoid reload per segment
        voices = {}
        def _get_voice(speaker: str):
            key = speaker if speaker in ("Anjali", "Ravi") else "Anjali"
            if key in voices:
                return voices[key]
            model_path = female_model if key == "Anjali" else male_model
            if not model_path:
                # fallback to available
                model_path = female_model or male_model
            if not model_path or not _Path(model_path).exists():
                raise FileNotFoundError(f"Piper model not found for {key}: {model_path}")
            v = PiperVoice.load(str(model_path))
            voices[key] = v
            log.info("Piper voice loaded for %s: %s", key, model_path)
            return v

        tmpdir = _Path(tempfile.mkdtemp(prefix="telugu_piper_"))
        segments = []
        for idx, turn in enumerate(script):
            text = turn["text"].strip()
            if not text:
                continue
            speaker = turn["speaker"] if turn["speaker"] in ("Anjali", "Ravi") else ("Anjali" if idx % 2 == 0 else "Ravi")
            voice = _get_voice(speaker)
            # Synthesize — collect float32 chunks
            chunks = []
            sample_rate = 22050
            for chunk in voice.synthesize(text):
                chunks.append(chunk.audio_float_array)
                sample_rate = chunk.sample_rate
            if not chunks:
                raise RuntimeError(f"Piper produced no audio for segment {idx} ({speaker})")
            audio = np.concatenate(chunks)
            tmp_wav = tmpdir / f"seg_{idx:02d}.wav"
            sf.write(str(tmp_wav), audio, samplerate=sample_rate)
            seg = AudioSegment.from_file(str(tmp_wav))
            segments.append(seg)
            log.info("piper [%d/%d] %s: %d chars -> %d ms", idx+1, len(script), speaker, len(text), len(seg))
            try:
                tmp_wav.unlink()
            except: pass
        if not segments:
            raise ValueError("Piper produced no segments")
        try:
            tmpdir.rmdir()
        except: pass

        silence = AudioSegment.silent(duration=400)
        combined = segments[0]
        for seg in segments[1:]:
            combined += silence + seg
        combined = AudioSegment.silent(duration=300) + combined + AudioSegment.silent(duration=500)
        combined.export(str(output_path), format="mp3", bitrate="192k")
        log.info("Piper MP3 saved: %s (%.1fs, %d turns, Anjali→padmavathi Ravi→venkatesh)", output_path, len(combined)/1000, len(segments))
        return output_path

    except ImportError as e:
        log.warning("Piper Python API not available (%s), trying binary fallback", e)
    except FileNotFoundError:
        raise
    except Exception as e:
        # For other errors, try binary fallback if configured, else re-raise to allow edge/gtts fallback
        if config.PIPER_BINARY_PATH and _Path(config.PIPER_BINARY_PATH).exists():
            log.warning("Piper Python API failed (%s), trying binary at %s", e, config.PIPER_BINARY_PATH)
        else:
            raise

    # Fallback: subprocess with PIPER_BINARY_PATH (legacy)
    import subprocess
    from pydub import AudioSegment
    import tempfile

    if not config.PIPER_BINARY_PATH or not _Path(config.PIPER_BINARY_PATH).exists():
        raise FileNotFoundError("PIPER_BINARY_PATH not set and Piper Python API failed — cannot run Piper")

    tmpdir = _Path(tempfile.mkdtemp(prefix="telugu_piper_"))
    segments = []
    for idx, turn in enumerate(script):
        speaker = turn["speaker"]
        model = female_model if speaker == "Anjali" else male_model
        if not model:
            model = female_model or male_model
        if not model or not _Path(model).exists():
            raise FileNotFoundError(f"Piper model not found for {speaker}: {model}")
        tmp_wav = tmpdir / f"seg_{idx:02d}.wav"
        proc = subprocess.run(
            [config.PIPER_BINARY_PATH, "--model", model, "--output_file", str(tmp_wav)],
            input=turn["text"].encode("utf-8"),
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"piper binary failed for segment {idx}")
        seg = AudioSegment.from_file(str(tmp_wav))
        segments.append(seg)

    silence = AudioSegment.silent(duration=400)
    combined = segments[0]
    for seg in segments[1:]:
        combined += silence + seg
    combined = AudioSegment.silent(duration=300) + combined + AudioSegment.silent(duration=500)
    combined.export(str(output_path), format="mp3", bitrate="192k")
    # Cleanup
    for f in tmpdir.glob("*.wav"):
        try: f.unlink()
        except: pass
    try: tmpdir.rmdir()
    except: pass
    return output_path

# ---------- Helper: list available edge voices ----------
async def list_telugu_voices():
    import edge_tts
    voices = await edge_tts.list_voices()
    for v in voices:
        if "te-IN" in v["ShortName"]:
            print(v["ShortName"], v["Gender"], v["Locale"])
