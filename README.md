# Telugu Podcast Agent 🎙️

> **Open-Source-First Policy:** This project prefers free and open-source tools for every module. Paid APIs (OpenAI/Gemini/Groq, Azure/Google TTS) are **never default** — they are optional fallback only when keys are set. See `docs/open_source_stack.md` for the full stack, alternatives, and limitations.

Automatically monitor a Google Sheet for new YouTube links → fetch transcript → convert into a conversational **Telugu** podcast script (2 speakers, simple language, brief summaries) → generate MP3 with open-source Telugu TTS → upload to Google Drive → update sheet with file link.

```
Google Sheet (YouTube URL) → transcript → Telugu Dialogue Script → MP3 → Drive → Sheet Link
```

## 📁 Project Structure

```
Telugu_podcast_agent/
├── output/                 # Generated MP3 + script json (gitignored, .gitkeep present)
├── credentials/            # Put Google service_account.json here (gitignored)
├── src/
│   ├── sheet_monitor.py    # Poll Google Sheet for new rows
│   ├── transcript.py       # Fetch YouTube transcript
│   ├── script_generator.py # LLM → Telugu 2-speaker script
│   ├── tts.py              # Open-source Telugu TTS → MP3
│   ├── drive_uploader.py   # Upload MP3 to Drive
│   └── utils.py            # Helpers
├── config.py               # All env/config in one place
├── main.py                 # Orchestrator + polling loop
├── requirements.txt
├── .env.example
└── README.md
```

## 🔄 Workflow

1. **Monitor** — `main.py` polls the Google Sheet every `POLL_INTERVAL_SECONDS` (default 60s).
   Expected sheet columns: **exact 12-column schema** `ID | YouTube Link | Title | Language | Duration | Status | Transcript Link | Telugu Script Link | Audio Link | Error | Created At | Updated At` (`A:L`, auto-created if sheet empty, never migrated/reduced).
2. **Detect** — For this test step, only rows where `Status == NEW` (exact, case-insensitive) are queued; reads `YouTube Link`. Production will expand to `PENDING` etc.
3. **Transcript** — `youtube-transcript-api` with `yt-dlp` fallback. Supports auto-translated English if native transcript missing. *(Not in this test step)*
4. **Script Generation** — LLM prompt generates dialogue between:
   - **Speaker 1: Anjali** (curious, asks simple questions)
   - **Speaker 2: Ravi** (explains in simple Telugu)
   Brief, conversational, easy Telugu (with some English where natural). Output is `JSON: [{speaker, text}]`. *(Not in this test step)*
5. **TTS** — Hierarchy: `edge-tts` (preferred, free, `te-IN-ShrutiNeural` + `te-IN-MohanNeural`) → `gTTS` (`te`) → `Coqui TTS` / `Piper` (optional offline). Concatenates segments with `pydub` → final MP3 in `output/`. *(Not in this test step)*
6. **Upload** — Google Drive API (service account or OAuth). File made shareable → link returned. *(Not in this test step)*
7. **Update** — **Test mode (current):** `Status NEW → TEST_OK` + `Updated At = YYYY-MM-DD HH:MM:SS IST` (only those two columns, all 12 headers preserved). **Production:** `Status → DONE`, `Audio Link → https://drive.google.com/file/d/...`, `Title → video title`, `Error` on failure.

Idempotency: processed `video_id`s are tracked in `output/processed.json` to avoid re-processing on restart.

## ⚙️ Setup

### 1. Prerequisites
- Python 3.10+
- FFmpeg (required for `pydub`/`edge-tts`): `winget install ffmpeg` or https://ffmpeg.org
- Google Cloud Project with **Google Sheets API** + **Google Drive API** enabled

### 2. Google Sheets API Setup (Step-by-Step)

This project uses **Google Sheets API + Google Drive API** via a Service Account.

#### A. Create/Select GCP Project
1. Go to https://console.cloud.google.com → New Project or select existing.
2. Note the Project ID.

#### B. Enable APIs
1. **APIs & Services → Library** → search `Google Sheets API` → **Enable**.
2. Search `Google Drive API` → **Enable**.
3. Wait 1-2 minutes for activation.

#### C. Create Service Account
1. **IAM & Admin → Service Accounts → Create Service Account**
   - Name: `telugu-podcast-agent` → Create
   - Role: skip (grant per-resource via sharing, not project-wide)
2. Open the created account → **Keys → Add Key → Create new key → JSON** → Download.
3. Save as `D:\Telugu_podcast_agent\credentials\service_account.json` (this path is `GOOGLE_CREDENTIALS_FILE`).
4. Open the JSON, copy the `client_email` (looks like `telugu-podcast-agent@<project>.iam.gserviceaccount.com`).

#### D. Share Sheet & Drive Folder
1. Open your Google Sheet (the one from `GOOGLE_SHEET_URL`) → **Share** → paste `client_email` → **Editor** → Send (uncheck Notify).
2. (Optional) Open target Drive folder (`DRIVE_OUTPUT_FOLDER_ID`) → **Share** → paste same `client_email` → **Editor**. If empty, files go to service account's My Drive root (still viewable via link).

#### E. Verify Scopes
Required OAuth scopes (already in `config.py:72`):
```
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/drive.file
https://www.googleapis.com/auth/drive
```

#### F. Alternative: OAuth (for personal Gmail testing)
1. **APIs & Services → Credentials → Create Credentials → OAuth Client ID → Desktop App** → Download as `credentials/oauth.json`.
2. First run triggers browser auth and saves `credentials/token.json`.

> Troubleshooting: `403 PERMISSION_DENIED` = not shared with service account. `403 rateLimitExceeded` = APIs not enabled yet.

### 3. Install

```powershell
cd D:\Telugu_podcast_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# FFmpeg check
ffmpeg -version
```

### 4. Configure

```powershell
Copy-Item .env.example .env
notepad .env
```

Edit `.env` (new names — `GOOGLE_SHEET_URL`, `GOOGLE_CREDENTIALS_FILE`, `DRIVE_OUTPUT_FOLDER_ID`):

```ini
# === Required for sheet connection test ===
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/1AbC.../edit
SHEET_NAME=Sheet1
GOOGLE_CREDENTIALS_FILE=credentials/service_account.json
DRIVE_OUTPUT_FOLDER_ID=1XyZ...  # optional

# LLM - pick one (not needed for sheet test step)
OPENAI_API_KEY=sk-...
# or GEMINI_API_KEY=... / GROQ_API_KEY=...

LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
POLL_INTERVAL_SECONDS=60
TTS_ENGINE=edge
```

Backward compat aliases still work: `SPREADSHEET_ID`, `GOOGLE_CREDENTIALS_PATH`, `DRIVE_FOLDER_ID`.

> `.env` is gitignored. Never commit secrets.

### 5. Sheet Format
Header row — **exact 12-column schema, do not rename or reduce** (auto-created `A1:L1` if empty, otherwise preserved):

| A (ID) | B (YouTube Link) | C (Title) | D (Language) | E (Duration) | F (Status) | G (Transcript Link) | H (Telugu Script Link) | I (Audio Link) | J (Error) | K (Created At) | L (Updated At) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | https://www.youtube.com/watch?v=... |  |  |  | NEW |  |  |  |  |  |  |
| 1 | https://youtu.be/... | My Video | te | 05:12 | TEST_OK |  |  |  |  | 2026-04-11 15:00:00 IST | 2026-04-11 15:30:00 IST |

Exact header `config.py:1`: `ID, YouTube Link, Title, Language, Duration, Status, Transcript Link, Telugu Script Link, Audio Link, Error, Created At, Updated At`

- For this test step, set `Status = NEW` (exact) and fill `YouTube Link`. Script updates **only** `Status → TEST_OK` + `Updated At` (IST), leaving other 10 columns untouched.
- Production: `Status → DONE`, fills `Audio Link` / `Transcript Link` / `Title` etc.

### 6. Run

```powershell
# --- Sheet connection test (current step) ---
# Dry-run read only - verifies auth + shows NEW rows without writing
python scripts/test_sheet.py --dry-run

# Real test - reads NEW rows and updates to TEST_OK + Updated At
python scripts/test_sheet.py

# Alternative via main.py
python main.py --test-sheet
python main.py --test-sheet --dry-run

# --- Full pipeline (later steps, not in this test) ---
python main.py --once
python main.py  # continuous polling
python main.py --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --dry-run
```

MP3s (later) appear in `output/` : `YYYYMMDD_<videoId>.mp3` + `.json` script sidecar.

## 🔊 Telugu TTS Engines

| Engine | Telugu Quality | Offline | Install |
|---|---|---|---|
| **edge-tts** (default) | Excellent (Microsoft Neural `te-IN-Shurti/Mohan`) | No | `pip install edge-tts` (included) |
| **gTTS** | Good (`te`) | No | `pip install gTTS` |
| **Coqui TTS** | Very good, cloneable | Yes | `pip install TTS` + model download |
| **Piper** | Good, fast | Yes | Download `piper` binary + `te_TE` model |

Switch via `TTS_ENGINE` in `.env`. Two-speaker mapping:
- Anjali → `te-IN-ShrutiNeural` (female)
- Ravi → `te-IN-MohanNeural` (male)

Audio is concatenated with 400ms silence via `pydub` and exported as 192kbps MP3.

## 🤖 LLM Prompt Logic (src/script_generator.py)

- Input: full transcript (truncated to ~12k chars)
- Output: JSON array 8–16 turns, each 1–3 sentences, simple Telugu, conversational, brief summaries, no English explanation unless term needs it.
- Fallback: if no API key, uses rule-based summarizer (extractive, Telugu template) so pipeline still works offline.

## 📊 Monitoring & Logs

- Logs to stdout + `output/agent.log` (rotating)
- `output/processed.json` — dedup store
- Sheet `Status` (test step): `NEW` → `TEST_OK` (+ `Updated At`). Production: `NEW` → `PROCESSING` → `DONE` | `ERROR: <reason>`

## 🛠️ Troubleshooting

| Issue | Fix |
|---|---|
| `gspread.exceptions.APIError 403` | Share sheet with service account email |
| `No transcript found` | Video has no captions; yt-dlp fallback will attempt auto-captions |
| `pydub Couldn't find ffmpeg` | Install ffmpeg and add to PATH, restart terminal |
| `edge-tts` timeout | Fallback to gTTS auto-triggers; check internet |
| Drive upload 403 | Share target folder with service account |

## 🔐 Security

- `credentials/` and `.env` are gitignored
- Service account has minimal scopes: `spreadsheets` + `drive.file`
- No secrets in logs

## 📄 License

MIT — use freely. TTS voices follow Microsoft/Google/Coqui respective licenses.

---
Made for Telugu podcast automation — paste a link, get a podcast. 🎧
