# Telugu Podcast Agent 🎙️

> **Open-Source-First Policy:** This project prefers free and open-source tools for every module. Paid APIs (OpenAI/Gemini/Groq, Azure/Google TTS) are **never default** — they are optional fallback only when keys are set. See `docs/open_source_stack.md` for the full stack, alternatives, and limitations.

**Phase 1 — Complete (v0.3.0):** Google Sheets integration (12-col) → YouTube transcript extraction (open-source, local save) → Sheet-to-Transcript pipeline (NEW/TEST_OK → TRANSCRIPT_DONE/FAILED). All local, no billing, no LLM required for basic flow.

```
Google Sheet (12-col, NEW/TEST_OK) → validate YouTube Link → fetch transcript (youtube-transcript-api → yt-dlp) → save output/transcripts/<id>.{txt,json} → update Status/Transcript Link/Error/Updated At (IST)
```

---

## 📌 Project Overview

**Telugu Podcast Agent** automates a simple Telugu podcast workflow from a Google Sheet:

1. User pastes a **YouTube Link** into the sheet (`Status=NEW` or `TEST_OK`)
2. Agent validates the link (empty / invalid → `TRANSCRIPT_FAILED` + `Error`)
3. Fetches the video transcript (free, open-source, no API key) and saves it locally under `output/transcripts/<video_id>.{txt,json}`
4. Updates the sheet **only when valid data exists**: `Status=TRANSCRIPT_DONE` + `Transcript Link` (relative path) + `Title` + `Updated At`; on failure: `TRANSCRIPT_FAILED` + `Error` + `Updated At` (no overwrite of `Transcript Link`)
5. (Roadmap) Later: Telugu 2-speaker script (Anjali/Ravi) → TTS MP3 → Drive upload → `Audio Link`

- **Exact 12-column schema** (never migrated): `ID | YouTube Link | Title | Language | Duration | Status | Transcript Link | Telugu Script Link | Audio Link | Error | Created At | Updated At` (`A:L`)
- **Sheet-safe:** empty/invalid links never create transcript files, never overwrite `Transcript Link`
- **Open-source-first:** Fully offline, free, no paid API in default path; `OPENAI_API_KEY` and local LLM (Ollama) are opt-in only — sheet connection and transcript validation work with empty `.env`

---

## 🏗️ Architecture

```
┌─────────────────┐      ┌──────────────────┐      ┌────────────────────┐
│ Google Sheet    │      │  Agent (Python)  │      │  Local Output      │
│ 12-col A:L      │◄────►│                  │─────►│ output/transcripts │
│ Status: NEW /   │ poll │ src/sheet_monitor│      │   <id>.txt/.json   │
│ TEST_OK         │      │  ├─ fetch NEW/   │      │  output/*.mp3/.json│
│                 │      │  │   TEST_OK     │      │  output/agent.log  │
│ YouTube Link ───┼─────►│  ├─ validate    │      └────────────────────┘
│                 │      │  │  (Empty/      │              │
│ Status ◄────────┼──────┤  │   Invalid)    │              │
│ Transcript Link │      │  ├─ fetch_and_  │              │
│ Title / Error   │      │  │   save (yt)   │              │
│ Updated At (IST)│      │  └─ update_row │◄─────────────┘
└─────────────────┘      │   (_col_index)   │  youtube-transcript-api
                         │                  │  yt-dlp fallback
                         │ src/transcript   │
                         │  ├─ EmptyLinkError
                         │  ├─ InvalidLinkError
                         │  ├─ TranscriptNotFound/ FetchError
                         │  └─ save_transcript_locally
                         │ config.TRANSCRIPT_DIR
                         └──────────────────┘
```

**Modules:**
- `config.py` — env, `GOOGLE_SHEET_URL`, `GOOGLE_CREDENTIALS_FILE`, `TRANSCRIPT_DIR`, `SHEET_HEADER` (12-col)
- `src/sheet_monitor.py` — `get_sheet`, `_col_index`, `fetch_transcript_pending_rows` (NEW/TEST_OK), `process_transcript_row(dry_run)`, `run_transcript_pipeline(dry_run, limit)`, `poll_loop`
- `src/transcript.py` — `validate_youtube_link`, `fetch_transcript`, `fetch_and_save_transcript` → `{valid, video_id, transcript, title, txt_path, json_path, error, error_type}`, `save_transcript_locally`, `EmptyLinkError`/`InvalidLinkError`/`TranscriptNotFoundError`/`TranscriptFetchError`
- `src/utils.py` — `extract_video_id`, `setup_logging`, `output_paths`
- `main.py` — `--run-transcripts` / `--test-sheet` / `--run-transcripts --dry-run` + full pipeline polling
- `scripts/run_transcripts.py` — Milestone 3 CLI wrapper (dry-run + limit)
- `scripts/test_sheet.py`, `scripts/test_transcript.py` — sheet / transcript validation (no sheet writes on failure)
- `src/script_generator.py`, `src/tts.py`, `src/drive_uploader.py` — roadmap (not in Phase 1 dry-run path)

---

## ✅ Completed Milestones (Phase 1 — v0.3.0)

### Milestone 1 — Google Sheets Integration ✅ (v0.1.0 `310a631`)
- 12-column exact schema `ID … Updated At` auto-created `A1:L1`, never migrated
- `gspread` + `google-api-python-client` (MIT/Apache-2.0), service account `GOOGLE_CREDENTIALS_FILE`, `.env` excluded (`credentials/*.json` ignored)
- `scripts/test_sheet.py --dry-run` / `main.py --test-sheet` → `NEW → TEST_OK + Updated At IST`
- `git` safe: `output/*.json|*.log` ignored, `.env`/`service_account.json` never committed

### Milestone 2 — YouTube Transcript Extraction Module ✅ (v0.2.0 `0138614`)
- Open-source stack: `youtube-transcript-api==0.6.2` (MIT, timed-text) → `yt-dlp==2024.10.7` (Unlicense, `json3`/`vtt` fallback) → `pytube` title
- Explicit validation: `EmptyLinkError` / `InvalidLinkError` (no network, no sheet)
- Local save: `output/transcripts/<video_id>.txt` + `.json` (`meta: video_id, youtube_url, title, chars, words, fetched_at IST, source`) — ignored by git (`output/transcripts/*.txt|*.json` + `!output/transcripts/.gitkeep`)
- Sheet-safe: `fetch_and_save_transcript` returns `{valid, error, error_type, txt_path}`; `Transcript Link` only set when `valid=True`, `Error` only on failure
- Tests: `python -m src.transcript --test-empty/--test-invalid --url`, `python tests/test_transcript.py` (6 cases, offline, no sheet)

### Milestone 3 — Sheet-to-Transcript Pipeline ✅ (v0.3.0 `842d24a`)
- Connects sheet rows `Status NEW` / `TEST_OK` → transcript module
- `fetch_transcript_pending_rows()` (trimmed, upper, 12-col `YouTube Link`), `process_transcript_row(dry_run)` (validate → fetch → save → sheet update guard), `run_transcript_pipeline(dry_run, limit)` (summary: pending/done/failed)
- Sheet update: `valid=True` → `Status=TRANSCRIPT_DONE` + `Transcript Link=output/transcripts/<id>.txt` + `Title` + clear `Error` + `Updated At IST`; `valid=False` → `Status=TRANSCRIPT_FAILED` + `Error=[Type] msg` + `Updated At` (no `Transcript Link` overwrite); empty `YouTube Link` → `EmptyLinkError`
- Timestamps: IST `YYYY-MM-DD HH:MM:SS IST` via `_ist_timestamp()` (both paths)
- CLIs: `python scripts/run_transcripts.py --dry-run` / `--limit 1` and `python main.py --run-transcripts --dry-run` (dry-run logs `would ->` without `update_cell`)
- Validation: dry-run `1 pending FAILED` idempotent, live `Row 2 TRANSCRIPT_FAILED [EmptyLinkError]` + `Row 3 TRANSCRIPT_FAILED [429]` correctly handled, second dry-run still `1 pending` proves no spurious write; `output/transcripts/` only `.gitkeep` for empty link (no leaked file)

---

## ⚠️ Known Limitations

| Area | Limitation | Impact / Workaround |
|------|------------|---------------------|
| **YouTube 429 Too Many Requests** | `youtube_transcript_api` and `yt-dlp` both hit `HTTP 429: Too Many Requests` / `429 Client Error: Too Many Requests for url: https://www.youtube.com/api/timedtext...` — transient IP rate-limit, not a code bug. Observed on TED/TED-Ed videos `Ks-_Mh1QhMc`, `8S0FDjFBj8o`, `UF8uR6Z6KLc`, `jNQXAC9IVRw`, `BaW_jenozKc` (music, `No transcript` / `No video formats found`) | Code correctly classifies as `TranscriptFetchError` → `TRANSCRIPT_FAILED` + `Error=[TranscriptFetchError] ... 429` + `Updated At`, no `Transcript Link` overwrite. Retry after backoff or from different IP, or reduce polling frequency (`POLL_INTERVAL_SECONDS=60`). Consider `faster-whisper` offline fallback for caption-less videos (roadmap). |
| Transcript availability | Private / age-restricted / captions-disabled videos → `TranscriptNotFoundError` / `Subtitles are disabled` | Valid link but no captions → `TRANSCRIPT_FAILED`; user must use caption-enabled video. Mock `save_transcript_locally` (e.g., `TEDDEMO1234` 3000 chars/500 words) proves local save path works offline. |
| yt-dlp Python version | `yt-dlp 2026.8.19` warns `Deprecated Feature: Support for Python 3.10` | Upgrade to Python 3.11+ to silence; functionality unaffected. |
| Sheet quota | `gspread update_cell` per field → 3 calls per row (Status+Error+Updated At) | Within free quota (300/min); future: batch `gspread.batch_update` |
| Transcript size | `MAX_TRANSCRIPT_CHARS=12000` truncates at sentence boundary (`।`/./!/ ? `>0.7` rule) | Long videos truncated; full transcript still saved locally under `output/transcripts/` before truncation? Currently fetched then truncated before save — future: save full then truncate for LLM. |
| LLM/TTS not in Phase 1 dry-run | `src/script_generator.py` / `src/tts.py` not invoked in `run_transcript_pipeline` dry-run path | Intentional — Phase 1 is sheets + transcript only; full pipeline in Phase 2 |

---

## 🗺️ Future Roadmap (post-v0.3.0)

- **Script Generation (Telugu 2-speaker)** — Rule-based default (`_rule_based_script`) + optional Ollama (`LLM_PROVIDER=ollama` + `Sarvam/AI4Bharat`) + paid `OPENAI_API_KEY` fallback (`OPENAI_API_KEY` opt-in, never required). `Anjali` (curious) / `Ravi` (explanatory), simple Telugu, `8–14` turns, `400–700` words.
- **TTS (Open-Source-First)** — Default `Piper TTS` / `AI4Bharat Indic-TTS` / `Coqui XTTS` (offline, MIT/MPL-2.0), secondary `edge-tts`/`gTTS` (free but proprietary endpoint, not default). `pydub` + `FFmpeg` concat `400ms` silence, `192k` MP3 in `output/`.
- **Drive Upload** — `google-api-python-client` `MediaFileUpload` → `Audio Link`, `permissions.create(anyone, reader)`
- **Full Pipeline Polling** — `main.py --once` / continuous `poll_loop` (`NEW/TEST_OK → TRANSCRIPT_DONE → SCRIPT_DONE → AUDIO_DONE` or `FAILED` variants), idempotent `output/processed.json`
- **Enhancements** — `faster-whisper` for no-caption videos, `gspread.batch_update` for quota, loudness normalization, Telugu prosody tuning, `models/` Piper weights

---

## 📁 Project Structure

```
Telugu_podcast_agent/
├── output/                 # gitignored except .gitkeep
│   ├── transcripts/        # <video_id>.txt/.json (Milestone 2/3, ignored)
│   ├── *.mp3/.json/.log    # future pipeline (ignored)
│   └── .gitkeep
├── credentials/            # service_account.json (gitignored) + README.md
├── docs/
│   ├── open_source_stack.md # policy + stack per module
│   └── releases/
├── scripts/
│   ├── run_transcripts.py  # Milestone 3: NEW/TEST_OK → transcript pipeline (+ --dry-run)
│   ├── test_sheet.py       # sheet connection test (NEW → TEST_OK)
│   └── test_transcript.py  # transcript validation (empty/invalid)
├── src/
│   ├── sheet_monitor.py    # 12-col sheet, fetch_transcript_pending_rows, process_transcript_row, run_transcript_pipeline
│   ├── transcript.py       # validate_youtube_link, fetch_transcript, fetch_and_save_transcript, save_transcript_locally
│   ├── script_generator.py # (roadmap) Telugu dialogue
│   ├── tts.py              # (roadmap) TTS → MP3
│   ├── drive_uploader.py   # (roadmap) Drive upload
│   └── utils.py            # helpers
├── tests/
│   └── test_transcript.py  # 6 offline validation cases
├── config.py               # env, GOOGLE_SHEET_URL, SHEET_HEADER (12-col), TRANSCRIPT_DIR
├── main.py                 # --run-transcripts / --test-sheet
├── requirements.txt
├── .env.example
├── CHANGELOG.md
└── README.md
```

## ⚙️ Setup

### 1. Prerequisites
- Python 3.10+ (3.11+ recommended for `yt-dlp`)
- FFmpeg (`winget install ffmpeg` or https://ffmpeg.org) — for future TTS
- Google Cloud Project with **Google Sheets API** + **Google Drive API** enabled

### 2. Google Sheets API Setup (Step-by-Step)

This project uses **Google Sheets API + Google Drive API** via a Service Account.

#### A. Create/Select GCP Project
1. Go to https://console.cloud.google.com → New Project or select existing.

#### B. Enable APIs
1. **APIs & Services → Library** → `Google Sheets API` → **Enable**.
2. `Google Drive API` → **Enable**.

#### C. Create Service Account
1. **IAM & Admin → Service Accounts → Create** (`Google Service Account` → Create → skip role)
2. **Keys → Add Key → JSON** → Save as `D:\Telugu_podcast_agent\credentials\service_account.json` (`GOOGLE_CREDENTIALS_FILE`).
3. Copy the Google Service Account email.

#### D. Share Sheet & Drive Folder
1. Sheet (`GOOGLE_SHEET_URL`) → **Share** → Google Service Account → **Editor**.
2. Drive folder (`DRIVE_OUTPUT_FOLDER_ID`) → **Share** → same Google Service Account → **Editor** (or leave empty for My Drive root).

#### E. Scopes (`config.py:84`)
```
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/drive.file
https://www.googleapis.com/auth/drive
```

#### F. OAuth Alternative
1. **APIs & Services → Credentials → OAuth Client ID → Desktop** → `credentials/oauth.json` → first run creates `credentials/token.json`.

> `403 PERMISSION_DENIED` = not shared with Google Service Account. `403 rateLimitExceeded` = APIs not enabled.

### 3. Install

```powershell
cd D:\Telugu_podcast_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
ffmpeg -version
```

### 4. Configure

```powershell
Copy-Item .env.example .env
notepad .env
```

```ini
# Required (sheet connection)
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/1AbC.../edit
SHEET_NAME=Sheet1
GOOGLE_CREDENTIALS_FILE=credentials/service_account.json
DRIVE_OUTPUT_FOLDER_ID=1XyZ...  # optional

# LLM — all optional, sheet/transcript work with empty (.env)
# rule-based default; Ollama opt-in for quality
LLM_PROVIDER=
LLM_MODEL=
# OPENAI_API_KEY= only if LLM_PROVIDER=openai

# TTS — open-source-first
TTS_ENGINE=piper
```

Backward compat: `SPREADSHEET_ID`, `GOOGLE_CREDENTIALS_PATH`, `DRIVE_FOLDER_ID`.

> `.env` and `credentials/*.json` are gitignored.

### 5. Sheet Format
**Exact 12-column schema, do not rename or reduce** (auto-created `A1:L1` if empty):

| A (ID) | B (YouTube Link) | C (Title) | D (Language) | E (Duration) | F (Status) | G (Transcript Link) | H (Telugu Script Link) | I (Audio Link) | J (Error) | K (Created At) | L (Updated At) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | https://www.youtube.com/watch?v=... |  |  |  | NEW |  |  |  |  |  |  |
| 1 | https://youtu.be/... | My Video | te | 05:12 | TEST_OK |  |  |  |  | 2026-04-11 15:00:00 IST | 2026-04-11 15:30:00 IST |

- Test: `Status=NEW` + `YouTube Link` → `TEST_OK` (`scripts/test_sheet.py`)
- Milestone 3: `NEW/TEST_OK` → `TRANSCRIPT_DONE` (+ `Transcript Link=output/transcripts/<id>.txt`, `Title`) or `TRANSCRIPT_FAILED` (+ `Error=[Type] msg`) + `Updated At IST`

### 6. Run

```powershell
# Sheet connection test
python scripts/test_sheet.py --dry-run
python scripts/test_sheet.py

# Transcript validation (no sheet, no network for empty/invalid)
python -m src.transcript --test-empty
python -m src.transcript --test-invalid
python tests/test_transcript.py
python -m src.transcript --url https://www.youtube.com/watch?v=dQw4w9WgXcQ

# Milestone 3: sheet → transcript (dry-run first, then live)
python scripts/run_transcripts.py --dry-run
python scripts/run_transcripts.py --dry-run --limit 2
python scripts/run_transcripts.py --limit 1
python main.py --run-transcripts --dry-run
python main.py --run-transcripts --limit 1

# Full pipeline (roadmap, after Phase 1)
python main.py --once
python main.py
```

Outputs: `output/transcripts/<id>.txt|.json`, `output/<id>.mp3|.json`, `output/agent.log` (all gitignored except `.gitkeep`).

---

## 🔊 Telugu TTS Engines (Roadmap — Open-Source-First, Fully Offline Default)

| Priority | Engine | License | Notes |
|----------|--------|---------|-------|
| 1 — Default | **Piper TTS** (`te_TE`) | MIT | Offline, ~80 MB/model, real-time CPU. `TTS_ENGINE=piper` |
| 2 — Default alt | **AI4Bharat TTS** (Indic) | MIT/Apache-2.0 | Native Telugu prosody, ~300 MB, `ai4bharat` |
| 3 — Fallback | **Coqui TTS** (`xtts_v2`) | MPL-2.0 | Heavy (~500 MB + torch), clonable |
| Secondary | `edge-tts` (`te-IN-Shruti/Mohan`) | GPL-3.0 client, proprietary endpoint | Free but not open-source, only if `TTS_ENGINE=edge` |
| Secondary | `gTTS` (`te`) | MIT client, Google endpoint | Single voice |

Two-speaker: `Anjali` (female) / `Ravi` (male), `pydub` `400ms` silence, `192k` MP3.

## 🤖 LLM Prompt Logic (Roadmap)

- Input: transcript (truncated `MAX_TRANSCRIPT_CHARS=12000` at `।`/./!/ ? `>0.7`)
- Output: JSON `8–16` turns `1–3` sentences, simple Telugu, `Anjali`/`Ravi` alternating, `400–700` words
- Default: rule-based extractive (`_rule_based_script`, pure Python, no key) — sheet test never breaks
- Opt-in: `LLM_PROVIDER=ollama` (`Sarvam`/`AI4Bharat`) or `openai`/`gemini`/`groq` with key

## 📊 Monitoring & Logs

- `output/agent.log` (rotating), `output/processed.json` dedup
- `Status`: `NEW → TEST_OK → TRANSCRIPT_DONE` (valid) / `TRANSCRIPT_FAILED` (empty/invalid/429/captions disabled) → (roadmap) `SCRIPT_DONE` → `AUDIO_DONE`; `Error=[Type] msg[:300]`

## 🛠️ Troubleshooting

| Issue | Fix |
|---|---|
| `403 PERMISSION_DENIED` | Share sheet/folder with Google Service Account |
| `EmptyLinkError` / `InvalidLinkError` | Fill `YouTube Link` with `watch?v=`, `youtu.be`, `embed`, `shorts`, or 11-char id |
| `TranscriptNotFoundError` / `Subtitles are disabled` | Video has no captions — use captioned video |
| `TranscriptFetchError 429 Too Many Requests` | **YouTube rate-limit (not code bug)** — wait/backoff, reduce polling, or try different IP/video; see Known Limitations. Retries as `TRANSCRIPT_FAILED`, `Transcript Link` not overwritten, `Updated At` set |
| `pydub Couldn't find ffmpeg` | Install FFmpeg, add to PATH |
| `edge-tts` timeout | Secondary only; default is Piper (offline) |
| Drive `403` | Share Drive folder with Google Service Account |

## 🔐 Security

- `credentials/` and `.env` gitignored (`output/*.log`, `output/transcripts/*.txt|*.json` ignored, `.gitkeep` kept)
- Minimal scopes: `spreadsheets` + `drive.file`
- No secrets in logs

## 📄 License

MIT — TTS voices per Microsoft/Google/Coqui licenses.

## 🔖 Releases

- `CHANGELOG.md` — Phase 1 milestones (Keep a Changelog)
- `docs/releases/v0.3.0.md` — draft release notes (v0.3.0: sheet-to-transcript pipeline, 429 handling)

---

Made for Telugu podcast automation — paste a link, get a podcast. 🎧
