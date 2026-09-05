"""YouTube transcript extraction — validation, fetch, save locally, sheet-safe.

Milestone 2: Implements YouTube transcript extraction code only.
- Validates empty / invalid links with explicit errors (no sheet side-effects)
- Fetches via youtube_transcript_api -> yt-dlp fallback (both open-source, free, no API key)
- Saves transcripts locally under output/transcripts/ (txt + json metadata)
- Updates sheet ONLY when valid data exists (caller must check result.valid)

Does NOT modify src/sheet_monitor.py polling logic or 12-column schema.
"""
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Tuple, Optional, Dict

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error hierarchy — enables caller to decide sheet Error / Status without
# updating sheet on empty/invalid cases
# ---------------------------------------------------------------------------
class TranscriptError(Exception):
    """Base for all transcript extraction errors."""
    pass

class EmptyLinkError(TranscriptError, ValueError):
    """YouTube Link cell is empty or whitespace."""
    pass

class InvalidLinkError(TranscriptError, ValueError):
    """URL is present but not a parseable YouTube link / video id."""
    pass

class TranscriptNotFoundError(TranscriptError, RuntimeError):
    """Valid link but no captions available (disabled / unsupported)."""
    pass

class TranscriptFetchError(TranscriptError, RuntimeError):
    """Network / API failure while fetching."""
    pass

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_youtube_link(youtube_url: Optional[str]) -> str:
    """Validate raw YouTube Link and return normalized video_id.

    Raises:
        EmptyLinkError  — if url is None / "" / whitespace
        InvalidLinkError — if url is not a YouTube URL or 11-char video id
    """
    if youtube_url is None:
        raise EmptyLinkError("YouTube Link is empty (None)")
    raw = str(youtube_url).strip()
    if not raw:
        raise EmptyLinkError("YouTube Link is empty or whitespace")
    # Allow plain 11-char video id as input as well
    from src.utils import extract_video_id
    video_id = extract_video_id(raw)
    if not video_id:
        raise InvalidLinkError(
            f"Invalid YouTube Link: {raw!r}. "
            "Expected https://www.youtube.com/watch?v=..., https://youtu.be/..., "
            "https://www.youtube.com/embed/..., /shorts/, or 11-char video id"
        )
    return video_id


# ---------------------------------------------------------------------------
# Local save — output/transcripts/<video_id>.txt + .json
# ---------------------------------------------------------------------------
def save_transcript_locally(
    video_id: str,
    transcript: str,
    youtube_url: str,
    title: str = "",
    extra: Optional[Dict] = None,
) -> Dict[str, Path]:
    """Save transcript locally under config.TRANSCRIPT_DIR.

    Creates:
        output/transcripts/<video_id>.txt  — raw transcript text
        output/transcripts/<video_id>.json — metadata + transcript (for pipeline)

    Returns dict with keys txt_path, json_path. Caller may use these to set
    sheet Transcript Link only after this succeeds (sheet-safe pattern).

    Raises only on disk errors (caller decides whether to surface as sheet Error).
    """
    import config
    transcript_dir = Path(config.TRANSCRIPT_DIR)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    txt_path = transcript_dir / f"{video_id}.txt"
    json_path = transcript_dir / f"{video_id}.json"

    ist = timezone(timedelta(hours=5, minutes=30))
    fetched_at = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST")

    # txt
    txt_path.write_text(transcript, encoding="utf-8")
    log.info("Saved transcript txt: %s (%d chars)", txt_path, len(transcript))

    # json metadata
    meta = {
        "video_id": video_id,
        "youtube_url": youtube_url,
        "title": title or "",
        "chars": len(transcript),
        "words": len(transcript.split()),
        "fetched_at": fetched_at,
        "source": extra.get("source") if extra else "unknown",
    }
    payload = {"meta": meta, "transcript": transcript}
    if extra:
        payload["extra"] = extra
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved transcript json: %s", json_path)

    return {"txt_path": txt_path, "json_path": json_path, "meta": meta}


# ---------------------------------------------------------------------------
# Fetch transcript (with validation)
# ---------------------------------------------------------------------------
def fetch_transcript(youtube_url: str, max_chars: int = 12000) -> Tuple[str, str]:
    """Validate, then fetch transcript + title.

    Validation is explicit so callers can distinguish:
        EmptyLinkError    -> sheet: do not update, or set Error="Empty YouTube Link"
        InvalidLinkError  -> sheet: do not update, or set Error="Invalid YouTube Link ..."
        TranscriptNotFoundError -> sheet: Error="No transcript / captions disabled"
        TranscriptFetchError    -> transient network failure

    Returns (transcript_text, video_title) on success.
    Never writes to sheet or disk here — caller decides sheet update & save.

    Truncates at sentence boundary if > max_chars.
    """
    # 1. Validate (empty / invalid) — no network call
    video_id = validate_youtube_link(youtube_url)
    # Normalize url for downstream (yt-dlp prefers full URL; for plain id synthesize)
    normalized_url = youtube_url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", normalized_url):
        normalized_url = f"https://www.youtube.com/watch?v={normalized_url}"

    title = ""
    try:
        title = _get_title(normalized_url, video_id)
    except Exception as e:
        log.debug("Title fetch failed for %s: %s", video_id, e)

    text = ""
    last_err: Optional[Exception] = None

    # Method 1: youtube_transcript_api (fast, no download)
    try:
        text = _fetch_via_transcript_api(video_id)
        if text:
            log.info("Transcript via youtube_transcript_api: %d chars (video_id=%s)", len(text), video_id)
    except EmptyLinkError:
        raise
    except InvalidLinkError:
        raise
    except Exception as e:
        log.warning("transcript_api failed for %s: %s", video_id, e)
        last_err = e

    # Method 2: yt-dlp fallback (downloads auto-captions)
    if not text:
        try:
            text = _fetch_via_ytdlp(normalized_url)
            if text:
                log.info("Transcript via yt-dlp: %d chars (video_id=%s)", len(text), video_id)
        except EmptyLinkError:
            raise
        except InvalidLinkError:
            raise
        except Exception as e:
            log.warning("yt-dlp transcript failed for %s: %s", video_id, e)
            last_err = e if last_err is None else last_err

    if not text:
        # Distinguish not-found vs fetch error
        if last_err and "No transcript" in str(last_err):
            raise TranscriptNotFoundError(
                f"No transcript found for {youtube_url} (video_id={video_id}). "
                "Video may have captions disabled or language unsupported."
            ) from last_err
        if last_err and isinstance(last_err, (TranscriptNotFoundError, TranscriptFetchError)):
            raise last_err
        raise TranscriptNotFoundError(
            f"No transcript found for {youtube_url} (video_id={video_id}). "
            "Video may have captions disabled, is private, or yt-dlp could not extract."
        ) from last_err

    # Truncate at sentence boundary
    if len(text) > max_chars:
        cut = text[:max_chars]
        last_period = max(cut.rfind("।"), cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        if last_period > max_chars * 0.7:
            cut = cut[: last_period + 1]
        text = cut

    return text.strip(), title


def fetch_and_save_transcript(
    youtube_url: str,
    max_chars: int = 12000,
) -> Dict:
    """Convenience: validate -> fetch -> save locally -> return result dict.

    Result dict always contains:
        {
          "valid": bool,
          "video_id": str | None,
          "youtube_url": str,
          "transcript": str | "",
          "title": str,
          "txt_path": Path | None,
          "json_path": Path | None,
          "error": str | None,          # human-readable for sheet Error column
          "error_type": str | None,     # EmptyLinkError | InvalidLinkError | TranscriptNotFoundError | TranscriptFetchError
        }

    Sheet update pattern (sheet-safe): only write Transcript Link / Title / Status
    when valid==True. On valid==False, caller should write Error column and NOT
    overwrite Transcript Link.

    Does NOT write to Google Sheet itself.
    """
    raw = youtube_url if youtube_url is not None else ""
    try:
        video_id = validate_youtube_link(raw)
    except EmptyLinkError as e:
        return {
            "valid": False,
            "video_id": None,
            "youtube_url": raw,
            "transcript": "",
            "title": "",
            "txt_path": None,
            "json_path": None,
            "error": str(e),
            "error_type": "EmptyLinkError",
        }
    except InvalidLinkError as e:
        return {
            "valid": False,
            "video_id": None,
            "youtube_url": raw,
            "transcript": "",
            "title": "",
            "txt_path": None,
            "json_path": None,
            "error": str(e),
            "error_type": "InvalidLinkError",
        }

    # Valid link — attempt fetch
    try:
        transcript, title = fetch_transcript(raw, max_chars=max_chars)
    except TranscriptNotFoundError as e:
        return {
            "valid": False,
            "video_id": video_id,
            "youtube_url": raw,
            "transcript": "",
            "title": "",
            "txt_path": None,
            "json_path": None,
            "error": str(e),
            "error_type": "TranscriptNotFoundError",
        }
    except TranscriptFetchError as e:
        return {
            "valid": False,
            "video_id": video_id,
            "youtube_url": raw,
            "transcript": "",
            "title": "",
            "txt_path": None,
            "json_path": None,
            "error": str(e),
            "error_type": "TranscriptFetchError",
        }
    except Exception as e:
        # Catch-all for network / parsing failures
        return {
            "valid": False,
            "video_id": video_id,
            "youtube_url": raw,
            "transcript": "",
            "title": "",
            "txt_path": None,
            "json_path": None,
            "error": f"Transcript fetch failed: {e}",
            "error_type": type(e).__name__,
        }

    # Fetch succeeded — save locally
    try:
        saved = save_transcript_locally(
            video_id=video_id,
            transcript=transcript,
            youtube_url=raw.strip(),
            title=title,
            extra={"source": "youtube_transcript_api/yt-dlp"},
        )
        return {
            "valid": True,
            "video_id": video_id,
            "youtube_url": raw,
            "transcript": transcript,
            "title": title,
            "txt_path": saved["txt_path"],
            "json_path": saved["json_path"],
            "meta": saved["meta"],
            "error": None,
            "error_type": None,
        }
    except Exception as e:
        return {
            "valid": False,
            "video_id": video_id,
            "youtube_url": raw,
            "transcript": transcript,  # fetched but save failed
            "title": title,
            "txt_path": None,
            "json_path": None,
            "error": f"Failed to save transcript locally: {e}",
            "error_type": "SaveError",
        }

# ---------------------------------------------------------------------------
# Low-level fetchers (unchanged logic, wrapped with specific exceptions)
# ---------------------------------------------------------------------------
def _fetch_via_transcript_api(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi
    langs = ["en", "te", "hi"]
    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        for lang in langs:
            try:
                t = transcripts.find_transcript([lang])
                data = t.fetch()
                return " ".join([x["text"] for x in data])
            except Exception:
                continue
        for tr in transcripts:
            try:
                translated = tr.translate("en")
                data = translated.fetch()
                return " ".join([x["text"] for x in data])
            except Exception:
                continue
        for tr in transcripts:
            data = tr.fetch()
            return " ".join([x["text"] for x in data])
    except Exception:
        try:
            data = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            return " ".join([x["text"] for x in data])
        except Exception:
            pass
        try:
            data = YouTubeTranscriptApi.get_transcript(video_id)
            return " ".join([x["text"] for x in data])
        except Exception as e:
            # Bubble up as fetch error so caller can classify
            raise TranscriptFetchError(str(e)) from e
    return ""


def _fetch_via_ytdlp(youtube_url: str) -> str:
    """Use yt-dlp to download auto captions."""
    import yt_dlp
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
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            video_id = info.get("id", "")
            for lang in ["en", "te", "hi"]:
                for ext in ["json3", "vtt", "srv3"]:
                    cand = list(tmpdir.glob(f"{video_id}*.{lang}*.{ext}"))
                    if not cand:
                        cand = list(tmpdir.glob(f"{video_id}*.{ext}"))
                    for f in cand:
                        text = _parse_subtitle_file(f)
                        if text and len(text) > 100:
                            return text
    except yt_dlp.utils.DownloadError as e:
        raise TranscriptFetchError(f"yt-dlp download error: {e}") from e
    except Exception as e:
        raise TranscriptFetchError(str(e)) from e
    return ""


def _parse_subtitle_file(path) -> str:
    try:
        txt = Path(path).read_text(encoding="utf-8")
        if path.suffix == ".json3":
            data = json.loads(txt)
            parts = []
            for ev in data.get("events", []):
                for seg in ev.get("segs", []) or []:
                    t = seg.get("utf8", "")
                    if t and t.strip():
                        parts.append(t.strip())
            return " ".join(parts)
        else:
            lines = []
            for line in txt.splitlines():
                line = line.strip()
                if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
                    continue
                line = re.sub(r"<[^>]+>", "", line)
                lines.append(line)
            return " ".join(lines)
    except Exception as e:
        log.debug("Parse subtitle %s failed: %s", path, e)
    return ""


def _get_title(youtube_url: str, video_id: str) -> str:
    """Best-effort title fetch via yt-dlp (no API key)."""
    try:
        import yt_dlp
        ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
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


# ---------------------------------------------------------------------------
# CLI for local testing (does NOT touch Google Sheet)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Test YouTube transcript extraction (validation + local save, no sheet update)"
    )
    parser.add_argument("--url", type=str, help="YouTube URL or 11-char video id")
    parser.add_argument("--test-empty", action="store_true", help="Trigger EmptyLinkError (no URL)")
    parser.add_argument("--test-invalid", action="store_true", help="Trigger InvalidLinkError")
    args = parser.parse_args()

    if args.test_empty:
        try:
            validate_youtube_link("")
        except EmptyLinkError as e:
            print(f"EmptyLinkError OK: {e}")
            sys.exit(0)
        print("EmptyLinkError not raised!")
        sys.exit(1)

    if args.test_invalid:
        try:
            validate_youtube_link("https://example.com/not-youtube")
        except InvalidLinkError as e:
            print(f"InvalidLinkError OK: {e}")
            sys.exit(0)
        print("InvalidLinkError not raised!")
        sys.exit(1)

    if not args.url:
        parser.print_help()
        print("\nExamples:")
        print("  python -m src.transcript --url https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        print("  python -m src.transcript --url dQw4w9WgXcQ")
        print("  python -m src.transcript --test-empty")
        print("  python -m src.transcript --test-invalid")
        sys.exit(0)

    # Full fetch + save (local only, no sheet)
    from src.utils import setup_logging
    setup_logging()
    print(f"Fetching: {args.url}")
    result = fetch_and_save_transcript(args.url)
    # Print without assuming utf-8 console (Windows cp1252 safe via repr)
    print(json.dumps(
        {k: str(v) if isinstance(v, Path) else v for k, v in result.items() if k != "transcript"},
        indent=2, ensure_ascii=False
    ))
    if result["valid"]:
        print(f"\nTranscript preview (first 300 chars): {result['transcript'][:300]!r}")
        print(f"\nSaved to: {result['txt_path']} and {result['json_path']}")
        print("Sheet update: would set Transcript Link only now (valid).")
    else:
        print(f"\nFailed: [{result['error_type']}] {result['error']}")
        print("Sheet update: would NOT update Transcript Link; would set Error column only.")
