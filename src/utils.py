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
