"""Central config - loads .env and exposes typed settings."""
import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR = BASE_DIR / OUTPUT_DIR if not OUTPUT_DIR.is_absolute() else OUTPUT_DIR


def _extract_sheet_id(url_or_id: str) -> str:
    """Accept full URL https://docs.google.com/spreadsheets/d/<ID>/... or plain ID."""
    if not url_or_id:
        return ""
    url_or_id = url_or_id.strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    if m:
        return m.group(1)
    return url_or_id  # already an ID


# --- Google Sheet / Drive (new names with backward compat) ---
# Preferred: GOOGLE_SHEET_URL (full URL), fallback: SPREADSHEET_ID (plain ID for backward compat)
_raw_sheet = os.getenv("GOOGLE_SHEET_URL", "") or os.getenv("SPREADSHEET_ID", "")
SPREADSHEET_ID = _extract_sheet_id(_raw_sheet)
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")
if GOOGLE_SHEET_URL and "/spreadsheets/d/" not in GOOGLE_SHEET_URL and SPREADSHEET_ID:
    # If user set plain ID in GOOGLE_SHEET_URL, keep it as-is
    GOOGLE_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"

SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")

# Preferred: DRIVE_OUTPUT_FOLDER_ID, fallback: DRIVE_FOLDER_ID
DRIVE_FOLDER_ID = os.getenv("DRIVE_OUTPUT_FOLDER_ID", "") or os.getenv("DRIVE_FOLDER_ID", "") or None
DRIVE_OUTPUT_FOLDER_ID = DRIVE_FOLDER_ID

# Preferred: GOOGLE_CREDENTIALS_FILE, fallback: GOOGLE_CREDENTIALS_PATH
_raw_creds = os.getenv("GOOGLE_CREDENTIALS_FILE", "") or os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json")
GOOGLE_CREDENTIALS_PATH = Path(_raw_creds)
GOOGLE_CREDENTIALS_FILE = GOOGLE_CREDENTIALS_PATH  # alias
GOOGLE_TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH", "credentials/token.json"))

# LLM — all optional. Basic workflow (including sheet connection test) needs NO LLM.
# Default is free rule-based Python fallback; Ollama/OpenAI are opt-in for higher quality.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").lower()  # "" = auto / rule-based; set to ollama/openai/gemini/groq to force
LLM_MODEL = os.getenv("LLM_MODEL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # optional — only if LLM_PROVIDER=openai
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # optional
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # optional
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")  # optional — only if Ollama installed

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
MAX_TRANSCRIPT_CHARS = int(os.getenv("MAX_TRANSCRIPT_CHARS", "12000"))
MAX_PODCAST_TURNS = int(os.getenv("MAX_PODCAST_TURNS", "14"))

TTS_ENGINE = os.getenv("TTS_ENGINE", "edge").lower()
PIPER_BINARY_PATH = os.getenv("PIPER_BINARY_PATH", "")
PIPER_MODEL_PATH_TE_FEMALE = os.getenv("PIPER_MODEL_PATH_TE_FEMALE", "")
PIPER_MODEL_PATH_TE_MALE = os.getenv("PIPER_MODEL_PATH_TE_MALE", "")

# Telugu voices for edge-tts
VOICE_MAP = {
    "Anjali": "te-IN-ShrutiNeural",  # female
    "Ravi": "te-IN-MohanNeural",     # male
}

# Sheet header - EXACT 12-column schema. Do not migrate, rename, or reduce.
SHEET_HEADER = [
    "ID",
    "YouTube Link",
    "Title",
    "Language",
    "Duration",
    "Status",
    "Transcript Link",
    "Telugu Script Link",
    "Audio Link",
    "Error",
    "Created At",
    "Updated At",
]

# Scopes needed
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
]
