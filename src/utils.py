"""Shared helpers."""
import re
import json
import logging
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR

LOG_PATH = OUTPUT_DIR / "agent.log"

def setup_logging(level=logging.INFO):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
        ],
    )
    return logging.getLogger("telugu-podcast")

def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL forms."""
    if not url:
        return None
    url = url.strip()
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # plain ID
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    return None

def is_youtube_url(url: str) -> bool:
    return extract_video_id(url) is not None

def slugify(text: str, max_len=40) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len] or "untitled"

def clean_title_for_filename(title: str, max_len: int = 60) -> str:
    """Clean title into filesystem-safe readable slug.

    - Removes unsafe Windows filename characters: < > : \" / \\ | ? * and control chars 0x00-0x1F
    - Removes other punctuation except word chars, spaces, hyphens, underscores
    - Normalizes excessive whitespace/underscores to single underscore
    - Truncates to max_len, stripping trailing underscores/hyphens
    - Falls back to 'untitled' if empty
    """
    if not title or not str(title).strip():
        return "untitled"
    t = str(title).strip()
    # Remove Windows forbidden and control chars
    t = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", t)
    # Keep word chars (including Unicode), spaces, hyphens, underscores; remove other punctuation
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    # Normalize whitespace to single underscore
    t = re.sub(r"\s+", "_", t.strip())
    # Collapse multiple underscores/hyphens
    t = re.sub(r"_+", "_", t)
    t = re.sub(r"-+", "-", t)
    t = t.strip("_-")
    # Truncate to max_len at word boundary (last complete word)
    if len(t) > max_len:
        truncated = t[:max_len]
        # If we are cutting inside a word, backtrack to last separator
        if len(t) > max_len and t[max_len] not in ("_", "-") and truncated[-1] not in ("_", "-"):
            last_us = truncated.rfind("_")
            last_hy = truncated.rfind("-")
            last_sep = max(last_us, last_hy)
            if last_sep > 0:
                truncated = truncated[:last_sep]
        t = truncated.rstrip("_-")
        t = re.sub(r"_+$", "", t)
        t = t.strip("_-")
    if not t:
        return "untitled"
    return t

def format_duration_mm_ss(seconds: float) -> str:
    """Format duration seconds as MMmSSs, zero-padded: e.g., 495 -> 08m15s."""
    try:
        total = int(round(float(seconds)))
    except Exception:
        total = 0
    if total < 0:
        total = 0
    minutes = total // 60
    secs = total % 60
    return f"{minutes:02d}m{secs:02d}s"

def output_paths(video_id: str, title: str = "") -> tuple[Path, Path]:
    date = datetime.now().strftime("%Y%m%d")
    base = f"{date}_{video_id}"
    if title:
        base += f"_{slugify(title, 20)}"
    mp3 = OUTPUT_DIR / f"{base}.mp3"
    json_path = OUTPUT_DIR / f"{base}.json"
    return mp3, json_path

def load_processed() -> set:
    p = OUTPUT_DIR / "processed.json"
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_processed(video_id: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUTPUT_DIR / "processed.json"
    data = load_processed()
    data.add(video_id)
    p.write_text(json.dumps(sorted(data), ensure_ascii=False, indent=2), encoding="utf-8")

def already_processed(video_id: str) -> bool:
    return video_id in load_processed()
