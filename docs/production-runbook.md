# Production Runbook — Telugu Podcast Agent

**For:** Phase 5.3 watcher + full pipeline (`NEW` → `AUDIO_DONE`) on Windows 16GB / i5-11300H, CPU-first, offline Piper primary.
**Source of truth:** `main.py --help`, `config.py`, `.env.example`, `src/watcher.py` → `src/sheet_monitor.run_pipeline()`.

---

## 1. Prerequisites

- **Python 3.10+** (3.11+ recommended for `yt-dlp`), `pip`
- **FFmpeg** on PATH: `winget install ffmpeg` or https://ffmpeg.org → `ffmpeg -version`
- **Git**, stable internet
- **Google Cloud project** with **Google Sheets API** + **Google Drive API** enabled
- **Pip packages:** `pip install -r requirements.txt` (includes `gspread`, `google-api-python-client`, `google-auth`, `youtube-transcript-api`, `yt-dlp`, `pydub`, `piper-tts` via `pip install piper-tts`, `soundfile`)
- **Disk:** `C:` 16GB free, `D:` 200GB+ free (`output/`, `models/` outside Git, `C:\Temp` for pip cache)

Verify:
```powershell
python --version
pip --version
ffmpeg -version
python -c "import piper; print(piper.__file__)"
```

## 2. Environment / Configuration

Copy example and edit (never commit `.env`):

```powershell
Copy-Item .env.example .env
notepad .env
```

Required in `.env`:
```ini
# Sheets (service-account, unchanged)
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/<YOUR_ID>/edit
SHEET_NAME=Sheet1
GOOGLE_CREDENTIALS_FILE=credentials/service_account.json
GOOGLE_TOKEN_PATH=credentials/token.json

# Drive OAuth for personal Gmail My Drive (separate from Sheets service-account)
GOOGLE_DRIVE_OAUTH_CLIENT_FILE=credentials/drive_oauth_client.json
GOOGLE_DRIVE_OAUTH_TOKEN_FILE=credentials/drive_oauth_token.json
DRIVE_OUTPUT_FOLDER_ID=1wVLpFk93dze76pBHK6v8CNnRaDPU_oK4  # from https://drive.google.com/drive/folders/<ID>

# LLM (Gemini primary, optional)
GEMINI_API_KEY=...  # from https://aistudio.google.com/apikey

# TTS Piper (outside Git, ~63MB each, not in repo)
TTS_ENGINE=piper
PIPER_MODEL_PATH_TE_FEMALE=C:\models\piper\te_IN-padmavathi-medium.onnx
PIPER_MODEL_PATH_TE_MALE=C:\models\piper\te_IN-venkatesh-medium.onnx

# Behavior / Reliability (Phase 5.3)
POLL_INTERVAL_SECONDS=60          # legacy poll_loop
WATCH_INTERVAL_SECONDS=30         # watcher polling, CLI --interval overrides
MAX_RETRIES=3
RETRY_BASE_DELAY_SECONDS=2
RETRY_MAX_DELAY_SECONDS=30
MAX_TRANSCRIPT_CHARS=12000
MAX_PODCAST_TURNS=14
OUTPUT_DIR=output
```

All `WATCH_INTERVAL`, `MAX_RETRIES`, `RETRY_BASE/MAX` are validated in `config.py` (fallback to defaults if invalid).

Check without printing secrets:
```powershell
python -c "import config; print('WATCH',config.WATCH_INTERVAL_SECONDS, 'MAX_RETRIES',config.MAX_RETRIES)"
```

## 3. Credential Setup Locations (never commit secrets)

| Purpose | File (gitignored via `credentials/*.json`, `.env`) | How to obtain |
|---|---|---|
| **Sheets** (service-account) | `credentials/service_account.json` (`GOOGLE_CREDENTIALS_FILE`) | GCP → IAM & Admin → Service Accounts → Create → Keys → JSON → Share Sheet with service-account email (Editor) |
| **Drive OAuth** (personal Gmail My Drive) | `credentials/drive_oauth_client.json` (`GOOGLE_DRIVE_OAUTH_CLIENT_FILE`) + `credentials/drive_oauth_token.json` (auto-generated) | GCP → APIs & Services → Credentials → Create OAuth client ID → **Desktop app** → Download JSON → Save as `drive_oauth_client.json`. First `upload_to_drive` opens browser for Gmail consent and saves token. Both files are `credentials/*.json` ignored. |
| **Gemini** | `.env` `GEMINI_API_KEY` | https://aistudio.google.com/apikey |

**Never** commit `.env`, `credentials/*.json`, `*.onnx`, `output/*.mp3`.

## 4. Required Services / Components

- **Google Sheets API** + **Google Drive API** enabled (same project `telugu-podcast-agent-personal`)
- **Drive folder** shared or owned by personal Gmail, ID in `DRIVE_OUTPUT_FOLDER_ID` (service-account has 0 quota, personal Drive OAuth has quota)
- **Piper voices** `te_IN-padmavathi-medium.onnx` (Anjali) + `te_IN-venkatesh-medium.onnx` (Ravi) from https://huggingface.co/rhasspy/piper-voices/tree/main/te/te_IN → outside Git, `*.onnx` gitignored
- **Gemini** `gemini-3.5-flash` (or rule-based fallback if key empty)
- **Output dirs** `output/`, `output/transcripts/` (auto-created, `output/*.mp3` ignored)

## 5. How to Start Normal Watcher

```powershell
# Live, 30s (from .env WATCH_INTERVAL_SECONDS), all pending rows
python main.py --watch

# Custom interval, limit per cycle for testing
python main.py --watch --interval 30 --limit 1

# Uses existing idempotent pipeline: NEW/TEST_OK -> TRANSCRIPT_DONE -> Piper (Anjali padmavathi, Ravi venkatesh) -> Drive OAuth -> AUDIO_DONE
```

Watcher logs (ASCII-safe, `cp1252`):
```
[Watcher] Starting --watch in LIVE mode at 2026-09-08 13:00:00 IST
[Watcher] Polling interval: 30s (config WATCH_INTERVAL_SECONDS=30)
[Watcher] Config: MAX_RETRIES=3 RETRY_BASE=2.0s RETRY_MAX=30.0s
[Watcher] Sheet: 1u6VJYy5O_H1OHdTb10lHqZTJoV-aGaRuh4gbB0ImVNY | Tab: Sheet1
[Watcher] Cycle 1 [13:00:00 IST] -- checking for pending rows...
[Watcher] Cycle 1 -- pending: 1 | done: 1 | failed: 0 | skipped: 0 | elapsed: 12.3s
  - Row 8: AUDIO_DONE TESTACCEPT1
[Watcher] Cycle 1 -- 1 row(s) completed successfully
[Watcher] Next check in 30s (Ctrl+C to stop)...
```

## 6. How to Run Dry-Run

Dry-run reads Sheet, reports what *would* happen, **no** YouTube fetch, **no** Gemini, **no** TTS, **no** Drive upload, **no** Sheet writes. No credentials beyond Sheet read needed.

```powershell
python main.py --watch --dry-run --limit 1 --interval 2
python main.py --run-pipeline --dry-run --limit 1
python main.py --run-transcripts --dry-run --limit 1
python main.py --run-audio --dry-run --limit 1
```

Dry-run example output:
```
[Watcher] Starting --watch in DRY-RUN mode
[DRY-RUN] Row 8 (TRANSCRIPT_DONE) would -> TRANSCRIPT_DONE -> AUDIO_DONE
Full Pipeline Result: Pending 1 | Done: 1 | Failed: 0 | Skipped: 0 | dry_run=True
```

Safe to run without `GEMINI_API_KEY` or Drive token.

## 7. Interval / Limit Options

- **Config:** `.env` `WATCH_INTERVAL_SECONDS=30` (default, not hard-coded)
- **CLI override:** `python main.py --watch --interval 10` (must be `>0`, validated in `src/watcher.py:_resolve_interval`, `ValueError` if invalid)
- **Limit:** `python main.py --watch --limit 1` and `python main.py --run-pipeline --limit 1` — deterministic slice `pending[:limit]`, reported as `Pending: N | Done: ...`, `total_pending` is after limit (matches `run_transcript_pipeline`).

## 8. How to Stop Safely

- Press **Ctrl+C** in the PowerShell/cmd window running `--watch`
- Watcher catches `KeyboardInterrupt`, prints:

```
[Watcher] Stopped by user (Ctrl+C) -- shutting down cleanly
```
- Exits with **code 0**, no traceback, no partial Sheet state, no corrupted `output/*.mp3` (writes are per-row atomic via `update_cell` + `AUDIO_DONE` only after Drive success).

Do **not** kill via Task Manager; use `Ctrl+C`.

## 9. Normal Sheet Status Flow

Exact 12 columns (never changed, via `config.SHEET_HEADER` + `src/sheet_monitor._col_index`):

`ID | YouTube Link | Title | Language | Duration | Status | Transcript Link | Telugu Script Link | Audio Link | Error | Created At | Updated At`

Expected successful:
```
NEW
 ↓ (transcript fetch → save output/transcripts/<id>.txt, Title)
TRANSCRIPT_DONE (Transcript Link populated, Error cleared, Updated At IST)
 ↓ (Gemini script → Piper Anjali/Ravi → MP3 output/<rowId>_<videoId>_<slug>.mp3 → Drive OAuth upload)
AUDIO_DONE (Audio Link = https://drive.google.com/file/d/<fileId>/view?usp=drivesdk, Error "", Updated At IST)
```

Failures:
```
NEW → TRANSCRIPT_FAILED ([EmptyLinkError]/[InvalidLinkError]/[TranscriptFetchError 429] + Updated At)
TRANSCRIPT_DONE → AUDIO_FAILED ([TTSFailed]/[DriveFailed]/[ScriptFailed] + Updated At)
```
`TRANSCRIPT_FAILED`/`AUDIO_FAILED` are **retryable** via backoff (see below); `AUDIO_DONE + valid Drive link` is **skipped** (idempotent, no duplicate Drive upload).

## 10. Retry / Backoff Behavior

Central in `config.py` + `src/retry_utils.py`, **bounded exponential backoff**, no new dependency:

```
MAX_RETRIES=3 (default)
RETRY_BASE_DELAY_SECONDS=2
RETRY_MAX_DELAY_SECONDS=30
delay(attempt) = min(base * 2^attempt, max) → 2,4,8,16,30,30...
```

- **Transient** (retried): `429 Too Many Requests`, `5xx` (`500/502/503/504`), `timeout/Timeout`, `ConnectionError`, `NetworkError`, `HttpError 429/5xx`
- **Permanent** (not retried): `EmptyLinkError`, `InvalidLinkError`, `File not found`, `No transcript`/`Subtitles are disabled`, `GEMINI_API_KEY not set`, `No Google credentials`, `No Drive OAuth client` (service-account JSON vs OAuth)

Used in `src/drive_uploader.py:upload_to_drive` (`_do_upload` via `retry_operation`) and `src/llm.py:generate` (Gemini/Ollama). Sheets polling in watcher also uses backoff on transient polling errors.

**Do not confuse with YouTube `429` after transcript success — that `TRANSCRIPT_FAILED` is already handled as failed status, not retried every 30s (see next).**

## 11. Failed-Row Retry / Throttling Behavior

**Schema not changed** — no new columns. Uses `Updated At` (`YYYY-MM-DD HH:MM:SS IST`) + `max(WATCH_INTERVAL*2, RETRY_MAX_DELAY)` (~60s).

- `src/sheet_monitor.py:_should_retry_failed_row()`:
  - `TRANSCRIPT_FAILED`/`AUDIO_FAILED` with `Updated At` within last `60s` → **throttled, skipped this cycle** (prevents hammering every 30s)
  - `Updated At` older than `60s` or missing/unparsable → **retryable**
  - `NEW`/`TRANSCRIPT_DONE` always retryable (not failed)
- Result: `fetch_audio_pending_rows` and `fetch_pipeline_pending_rows` skip recently failed rows for this cycle, but **allow eventual retry** after backoff window, and **survive watcher restart** via sheet timestamp (not just in-memory).
- **Not permanently blocked** — after `60s` it becomes eligible again.

## 12. Restart / Recovery Behavior

- **Watcher stop/start is safe:** `AUDIO_DONE + valid Drive link` → `fetch_*_pending_rows` skips (0 pending) → no `Gemini`/`Piper`/`Drive` re-run, no duplicate file (verified `Drive files with 3000` still 1).
- **`AUDIO_FAILED` (old, e.g., 70s ago)** → `fetch` returns 1 pending → watcher retries `generate_telugu_script` → `Piper` → `Drive` → `AUDIO_DONE` on success.
- **`TRANSCRIPT_DONE` after crash** (`NEW → TRANSCRIPT_DONE` then crash before audio): next `run_pipeline` detects `TRANSCRIPT_DONE` and resumes at audio (`TRANSCRIPT_DONE -> generating audio`), no re-transcript.

## 13. Idempotency Behavior

- `fetch_*_pending_rows` + `process_*_row` both check `Status==AUDIO_DONE && _is_valid_audio_link(Audio Link)` (`https://drive.google.com/...`) → `skipped=True`, `ws.update_cell` not called.
- `run_pipeline`/`run_audio_pipeline` summary counts `skipped` separately.
- Verified live `ID 3000` `AUDIO_FAILED → AUDIO_DONE` (31.9s) then second cycle `pending:0` (no second Drive upload, `Drive files with 3000` still 1).

## 14. Drive Output Behavior

- **Folder:** `DRIVE_OUTPUT_FOLDER_ID` (or `DRIVE_FOLDER_ID` fallback) from `.env` → `src/drive_uploader._resolve_folder_id` (`param > env > root`). Empty → My Drive root (but service-account has 0 quota, so **must use OAuth + personal My Drive folder**).
- **Upload:** `src/drive_uploader.upload_to_drive(..., make_public=False)` → `MediaFileUpload` `audio/mpeg` → `files.create(parents=[folderId])` → returns `{"fileId":..., "webViewLink": "https://drive.google.com/file/d/.../view?usp=drivesdk"}`. **Secure**: `permissions.create(anyone)` only if `make_public=True` (default `False`).
- **Sheet:** `Audio Link = webViewLink` (never manually constructed when valid), via `_col_index("Audio Link")`.
- **MP3 naming (deterministic, safe):** `_audio_output_path(video_id, title, row_id)` → `output/{rowId}_{videoId}_{slug(title,20)}.mp3` (e.g., `1001_TESTPIPELA1_Pipeline_Resume_Test.mp3`), `re.sub(r"[^\w\-]","_", ...)[:80]`, `mp3` extension, `output/*.mp3` gitignored, outside Git.

## 15. Troubleshooting — Common Failure Categories

Watcher and pipeline log categories (ASCII, `cp1252`-safe):
- `[TranscriptFailed]` — `EmptyLinkError`/`InvalidLinkError`/`TranscriptNotFoundError`/`TranscriptFetchError` (e.g., `429`, `No transcript`)
- `[LLMFailed]` — `LLMTimeoutError`/`LLMConnectionError`/`LLMProviderError` (Gemini/Ollama `429`/`5xx`/`timeout`)
- `[TTSFailed]` — `Piper model not found` / `TTS failed` (check `PIPER_MODEL_PATH_*` outside Git, `*.onnx` ignored)
- `[DriveFailed]` — `DriveUploadError` (`403 storageQuotaExceeded` for service-account, use Drive OAuth + `DRIVE_OUTPUT_FOLDER_ID` shared folder; `No Drive OAuth client at ...` → create Desktop OAuth)
- `[SheetFailed]` — `gspread` `429`/`5xx`/`403 PERMISSION_DENIED` (share Sheet with service-account email, enable Sheets/Drive API)

Check `output/agent.log` and console:
```
[Watcher] Cycle 1 -- polling error [Failed]: HttpError 429: ... (will retry in 4.0s, failures=1)
[Watcher] Backoff 4.0s before next cycle
[Watcher] Next check in 30s
```

## 16. YouTube 429 Limitation (Environmental)

- **Not a code bug** — `youtube_transcript_api` + `yt-dlp` both `429 Too Many Requests` for `Ks-_Mh1QhMc`, `jNQXAC9IVRw` etc. when IP rate-limited.
- Code correctly `TRANSCRIPT_FAILED` + `Error=[TranscriptFetchError] ...429`, no `Transcript Link` overwrite.
- Workarounds: wait/backoff (retry `2→4→8→30s`), reduce `WATCH_INTERVAL_SECONDS`, try different IP/video, or future `faster-whisper` offline.
- Failed rows throttled via `Updated At` backoff (~60s), not hammered every 30s.

## 17. Safe Operational Practices

- **Always dry-run first:** `python main.py --watch --dry-run --limit 1 --interval 2` and `python main.py --run-pipeline --dry-run --limit 1`
- **Limit for testing:** `--limit 1` per cycle to avoid mass Drive uploads
- **Controlled rows:** Use isolated `ID` (e.g., `3000`) with `TRANSCRIPT_DONE` + local `output/transcripts/TESTACCEPT1.txt` for watcher tests, not arbitrary `NEW` rows.
- **Idempotency:** Do not manually set `AUDIO_DONE` without valid `Audio Link`; watcher will skip only when both.
- **Restart:** Stop with `Ctrl+C` (exit 0), start again — `AUDIO_DONE` skipped, `AUDIO_FAILED` retried per backoff.
- **No duplicate uploads:** Verified via `service.files().list(q="'<folder>' in parents and name contains '3000'")` → still 1.

## 18. Basic Production Checklist

Copy-paste:

```
[ ] .env configured (GOOGLE_SHEET_URL, SHEET_NAME, GOOGLE_CREDENTIALS_FILE, GOOGLE_DRIVE_OAUTH_CLIENT_FILE)
[ ] Sheets credentials available: credentials/service_account.json exists=True (not printed)
[ ] Drive OAuth available: credentials/drive_oauth_client.json exists=True, credentials/drive_oauth_token.json exists=True (has refresh_token)
[ ] Gemini API configured: GEMINI_API_KEY set=True (value not printed)
[ ] Piper models available: PIPER_MODEL_PATH_TE_FEMALE/TE_MALE exists=True (60.57MB each, outside Git)
[ ] FFmpeg available: ffmpeg -version
[ ] output/ and output/transcripts/ exist
[ ] Drive folder: DRIVE_OUTPUT_FOLDER_ID=1wVLpFk93... (length 33)
[ ] WATCH_INTERVAL_SECONDS=30, MAX_RETRIES=3, RETRY_BASE=2, RETRY_MAX=30
[ ] Dry-run verified: python main.py --watch --dry-run --limit 1 --interval 2 (max_cycles via test harness)
[ ] Watcher started: python main.py --watch --interval 30 (or --watch --dry-run for preview)
[ ] AUDIO_DONE verified: Status AUDIO_DONE, Audio Link https://..., Error "", Updated At changed
[ ] Drive link verified: file in configured folder, non-public (anyone=False), size matches
[ ] No duplicate Drive file after idempotency test (second cycle 0 pending)
```

---

**Verification (runbook does not affect code):**
```powershell
python -m pytest tests/test_llm.py tests/test_gemini.py tests/test_script_generator.py tests/test_transcript.py tests/test_tts.py tests/test_drive.py tests/test_audio_pipeline.py tests/test_pipeline.py tests/test_watcher.py tests/test_reliability.py -q
# Expected: 110 passed, 1 warning
```

**Git:** No `credentials/*.json`, no `.env`, no `*.onnx`, no `output/*.mp3` ever staged (all `credentials/*.json` via `.gitignore:6`, `output/*.mp3` via `:9`).
