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

# Preferred: GOOGLE_CREDENTIALS_FILE, fallback: GOOGLE_CREDENTIALS_PATH — Sheets service-account (unchanged)
_raw_creds = os.getenv("GOOGLE_CREDENTIALS_FILE", "") or os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json")
GOOGLE_CREDENTIALS_PATH = Path(_raw_creds)
GOOGLE_CREDENTIALS_FILE = GOOGLE_CREDENTIALS_PATH  # alias
GOOGLE_TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH", "credentials/token.json"))

# Drive OAuth — separate from Sheets service-account (personal Gmail, no Shared Drives)
# Uses credentials/drive_oauth_client.json (OAuth client ID Desktop) + credentials/drive_oauth_token.json
GOOGLE_DRIVE_OAUTH_CLIENT_FILE = Path(os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_FILE", "credentials/drive_oauth_client.json"))
GOOGLE_DRIVE_OAUTH_TOKEN_FILE = Path(os.getenv("GOOGLE_DRIVE_OAUTH_TOKEN_FILE", "credentials/drive_oauth_token.json"))
# Backward compat aliases
DRIVE_OAUTH_CLIENT_FILE = GOOGLE_DRIVE_OAUTH_CLIENT_FILE
DRIVE_OAUTH_TOKEN_FILE = GOOGLE_DRIVE_OAUTH_TOKEN_FILE

# LLM — Phase 2 Milestone 3: Gemini 3.5 Flash primary, Ollama optional fallback
# Default provider: gemini (requires GEMINI_API_KEY), default model: gemini-3.5-flash
# Rule-based fallback remains and is used when no key / provider disabled / LLMError.
# Basic workflow and sheet/transcript still work with rule-based and no LLM required.
# Ollama remains fully supported as optional provider: LLM_PROVIDER=ollama, LLM_MODEL=gemma2:9b
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()  # default gemini; ""/rule-based/none = force fallback; ollama optional
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash")  # default gemini-3.5-flash for gemini; overridden by env
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # optional — only if LLM_PROVIDER=openai
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # primary — required if LLM_PROVIDER=gemini (never hardcoded)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # optional
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")  # optional — only if LLM_PROVIDER=ollama

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
WATCH_INTERVAL_SECONDS = int(os.getenv("WATCH_INTERVAL_SECONDS", "30"))
# Reliability — retry/backoff for transient external failures (Phase 5.3)
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BASE_DELAY_SECONDS = float(os.getenv("RETRY_BASE_DELAY_SECONDS", "2"))
RETRY_MAX_DELAY_SECONDS = float(os.getenv("RETRY_MAX_DELAY_SECONDS", "30"))
# Validate
if MAX_RETRIES < 0:
    MAX_RETRIES = 3
if RETRY_BASE_DELAY_SECONDS <= 0:
    RETRY_BASE_DELAY_SECONDS = 2
if RETRY_MAX_DELAY_SECONDS < RETRY_BASE_DELAY_SECONDS:
    RETRY_MAX_DELAY_SECONDS = max(30, RETRY_BASE_DELAY_SECONDS)
MAX_TRANSCRIPT_CHARS = int(os.getenv("MAX_TRANSCRIPT_CHARS", "12000"))
MAX_PODCAST_TURNS = int(os.getenv("MAX_PODCAST_TURNS", "14"))

# Transcripts — saved locally under output/transcripts/, never committed
TRANSCRIPT_DIR = OUTPUT_DIR / "transcripts"
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

# TTS — Piper CPU/offline primary (Anjali: padmavathi-medium, Ravi: venkatesh-medium)
# Keep piper CPU/offline as primary; edge-tts/gTTS are fallback only (provider-agnostic)
TTS_ENGINE = os.getenv("TTS_ENGINE", "piper").lower()  # piper (primary, offline) | edge | gtts | coqui
PIPER_BINARY_PATH = os.getenv("PIPER_BINARY_PATH", "")  # optional — only if using piper binary; pip piper-tts uses Python API
PIPER_MODEL_PATH_TE_FEMALE = os.getenv("PIPER_MODEL_PATH_TE_FEMALE", "")  # Anjali → te_IN-padmavathi-medium.onnx (outside Git repo)
PIPER_MODEL_PATH_TE_MALE = os.getenv("PIPER_MODEL_PATH_TE_MALE", "")  # Ravi → te_IN-venkatesh-medium.onnx (outside Git repo)

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
