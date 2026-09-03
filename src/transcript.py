"""Fetch YouTube transcript with fallbacks."""
import logging
import re
from typing import Tuple

log = logging.getLogger(__name__)

def fetch_transcript(youtube_url: str, max_chars: int = 12000) -> Tuple[str, str]:
    """
    Returns (transcript_text, video_title).
    Tries youtube_transcript_api first, then yt-dlp auto-captions.
    """
    from src.utils import extract_video_id
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {youtube_url}")

    title = ""
    # Try to get title via yt-dlp or pytube (optional)
    try:
        title = _get_title(youtube_url, video_id)
    except Exception as e:
        log.debug("Title fetch failed: %s", e)

    text = ""
    # Method 1: youtube_transcript_api
    try:
        text = _fetch_via_transcript_api(video_id)
        if text:
            log.info("Transcript via youtube_transcript_api: %d chars", len(text))
    except Exception as e:
        log.warning("transcript_api failed for %s: %s", video_id, e)

    # Method 2: yt-dlp fallback
    if not text:
        try:
            text = _fetch_via_ytdlp(youtube_url)
            if text:
                log.info("Transcript via yt-dlp: %d chars", len(text))
        except Exception as e:
            log.warning("yt-dlp transcript failed: %s", e)

    if not text:
        raise RuntimeError(
            f"No transcript found for {youtube_url} (video_id={video_id}). "
            "Video may have captions disabled."
        )

    # Truncate sanely at sentence boundary
    if len(text) > max_chars:
        cut = text[:max_chars]
        last_period = max(cut.rfind("।"), cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        if last_period > max_chars * 0.7:
            cut = cut[: last_period + 1]
        text = cut

    return text.strip(), title

def _fetch_via_transcript_api(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi
    # Try native languages first
    langs = ["en", "te", "hi"]
    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        # Prefer manually created, then auto-generated
        for lang in langs:
            try:
                t = transcripts.find_transcript([lang])
                data = t.fetch()
                return " ".join([x["text"] for x in data])
            except Exception:
                continue
        # Try any transcript with translation to English
        for tr in transcripts:
            try:
                translated = tr.translate("en")
                data = translated.fetch()
                return " ".join([x["text"] for x in data])
            except Exception:
                continue
        # Fallback: first available
        for tr in transcripts:
            data = tr.fetch()
            return " ".join([x["text"] for x in data])
    except Exception:
        # Older API version: direct get_transcript
        try:
            data = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            return " ".join([x["text"] for x in data])
        except Exception:
            pass
        # Try without language filter
        data = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join([x["text"] for x in data])
    return ""

def _fetch_via_ytdlp(youtube_url: str) -> str:
    """Use yt-dlp to download auto captions."""
    import yt_dlp
    import json
    import tempfile
    from pathlib import Path

    tmpdir = Path(tempfile.gettempdir()) / "telugu_podcast_yt"
    tmpdir.mkdir(exist_ok=True)

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "te", "hi", "en.*"],
        "subtitlesformat": "json3",
        "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        video_id = info.get("id", "")
        # Look for downloaded subtitle files
        for lang in ["en", "te", "hi"]:
            for ext in ["json3", "vtt", "srv3"]:
                cand = list(tmpdir.glob(f"{video_id}*.{lang}*.{ext}"))
                if not cand:
                    cand = list(tmpdir.glob(f"{video_id}*.{ext}"))
                for f in cand:
                    text = _parse_subtitle_file(f)
                    if text and len(text) > 100:
                        return text
    return ""

def _parse_subtitle_file(path) -> str:
    try:
        txt = Path(path).read_text(encoding="utf-8")
        # json3 format has "events" with segs
        if path.suffix == ".json3":
            import json
            data = json.loads(txt)
            parts = []
            for ev in data.get("events", []):
                for seg in ev.get("segs", []) or []:
                    t = seg.get("utf8", "")
                    if t and t.strip():
                        parts.append(t.strip())
            return " ".join(parts)
        else:
            # VTT/SRT - strip timestamps
            lines = []
            for line in txt.splitlines():
                line = line.strip()
                if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
                    continue
                # Remove tags like <c> etc
                line = re.sub(r"<[^>]+>", "", line)
                lines.append(line)
            return " ".join(lines)
    except Exception as e:
        log.debug("Parse subtitle %s failed: %s", path, e)
    return ""

def _get_title(youtube_url: str, video_id: str) -> str:
    """Best-effort title fetch via yt-dlp."""
    try:
        import yt_dlp
        ydl_opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            return info.get("title", "") or ""
    except Exception:
        pass
    try:
        from pytube import YouTube
        yt = YouTube(youtube_url)
        return yt.title or ""
    except Exception:
        return ""
