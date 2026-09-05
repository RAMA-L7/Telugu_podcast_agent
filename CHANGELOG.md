# Changelog — Telugu Podcast Agent

All notable changes to this project are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Phase 1 is `v0.3.0`.

## [v0.3.0] — 2026-09-05 — Phase 1 Complete: Sheet-to-Transcript Pipeline

Phase 1 milestone chain: **M1 Sheets (12-col) → M2 Transcript Extraction → M3 Sheet-to-Transcript Pipeline** — all local, open-source-first, no billing, no LLM required for basic flow.

### Added
- `src/sheet_monitor.py` — `fetch_transcript_pending_rows()` (NEW/TEST_OK), `process_transcript_row(dry_run)` (validate → fetch → save → sheet-safe update), `run_transcript_pipeline(dry_run, limit)` (summary pending/done/failed), `_ist_timestamp()` (IST), `_col_index()` for exact 12-col `ID … Updated At`; header `A1:L1` auto-create, never migrated
- `scripts/run_transcripts.py` — Milestone 3 CLI (`--dry-run` preview without `update_cell`, `--limit N`), sheet-safe logging, `GOOGLE_SHEET_URL`/`TRANSCRIPT_DIR` check
- `main.py` — `--run-transcripts` + `--limit` (calls `run_transcript_pipeline`), alongside `--test-sheet`
- `config.py` — `TRANSCRIPT_DIR = OUTPUT_DIR / "transcripts"` (mkdir), 12-col `SHEET_HEADER` preserved
- `output/transcripts/.gitkeep` — transcripts folder kept, contents ignored
- `README` — Phase 1 overview, architecture diagram, completed milestones, known limitations (429), roadmap

### Changed
- `.gitignore` — `output/transcripts/*.txt|*.json|*.log` + `!output/transcripts/.gitkeep` (transcripts stay local)
- `docs/open_source_stack.md` — TTS default now fully open-source (Piper/AI4Bharat/Coqui), `edge-tts` secondary only; script generation rule-based default, Ollama opt-in

### Fixed / Verified
- Sheet update guard: `TRANSCRIPT_DONE` only when `valid=True` (`Transcript Link=output/transcripts/<id>.txt` + `Title` + clear `Error`); `TRANSCRIPT_FAILED` sets `Error=[Type] msg` + `Updated At` without overwriting `Transcript Link` (verified: Row 2 `EmptyLinkError`, Row 3 `429 TranscriptFetchError` both `TRANSCRIPT_FAILED`, `Transcript Link` stayed `''`, `Updated At` IST set)
- Dry-run idempotent: `1 pending → 0 done, 1 failed (dry_run=True)` second run still `1 pending` (no write)
- `youtube-transcript-api==0.6.2` → `yt-dlp` fallback preserved (both MIT/Unlicense, no API key)

### Known Limitations (carried)
- YouTube `429 Too Many Requests` (transient IP rate-limit, not code bug) → classified as `TranscriptFetchError` → `TRANSCRIPT_FAILED`; retry/backoff, reduce polling, or use `faster-whisper` offline future
- Captions-disabled/private videos → `TranscriptNotFoundError` / `Subtitles are disabled`
- `yt-dlp 2026.8.19` warns `Support for Python 3.10 has been deprecated` (upgrade to 3.11+)
- This release is dry-run/live pipeline only; no Telugu script/TTS/Drive yet (roadmap Phase 2)

### Tested
- `python -m py_compile config.py src/transcript.py src/sheet_monitor.py main.py scripts/run_transcripts.py` — OK
- `python -m src.transcript --test-empty/--test-invalid`, `python tests/test_transcript.py` (6 cases, offline) — PASS
- `python scripts/run_transcripts.py --dry-run` / `main.py --run-transcripts --dry-run` — `1 pending FAILED` idempotent
- Live `python scripts/run_transcripts.py --limit 1` on empty link → `Row 2 TRANSCRIPT_FAILED [EmptyLinkError]` `Updated At 2026-09-05 14:25:39 IST`; on real TED link `Ks-_Mh1QhMc` → `Row 3 TRANSCRIPT_FAILED [429]` `Updated At 2026-09-05 14:33:36 IST` (both `Transcript Link` unchanged, correct sheet-safe)

## [v0.2.0] — 2026-09-05 — Milestone 2: YouTube Transcript Extraction Module

### Added
- `src/transcript.py` (519 lines) — `EmptyLinkError`/`InvalidLinkError`/`TranscriptNotFoundError`/`TranscriptFetchError`, `validate_youtube_link`, `fetch_transcript` (`youtube_transcript_api` → `yt-dlp`), `save_transcript_locally` (`output/transcripts/<id>.txt/.json` meta), `fetch_and_save_transcript` → `{valid, video_id, transcript, title, txt_path, json_path, error, error_type}` (sheet-safe), CLI `--test-empty/--test-invalid/--url`
- `tests/test_transcript.py` — 6 offline validation cases (empty/invalid/valid URLs, no-save on invalid, temp-dir save)
- `scripts/test_transcript.py` — wrapper CLI (local-only, no sheet)
- `config.py:TRANSCRIPT_DIR`, `output/transcripts/.gitkeep`, `.gitignore` transcripts ignore

### Tested
- `python -m pip install -r requirements.txt` (youtube-transcript-api 0.6.2, yt-dlp 2024.10.7→2026.8.19)
- Live `python -m src.transcript --url https://www.youtube.com/watch?v=dQw4w9WgXcQ` → `TranscriptNotFoundError` sheet-safe (no `Transcript Link`); `python -m pip show` verified, `git status` clean, secrets ignored

## [v0.1.0] — 2026-09-03 — Milestone 1: Google Sheets Integration

### Added
- 12-column schema `ID | YouTube Link | Title | Language | Duration | Status | Transcript Link | Telugu Script Link | Audio Link | Error | Created At | Updated At` (`config.SHEET_HEADER`, `A1:L1`)
- `src/sheet_monitor.py` — `get_sheet`, `_col_index`, `fetch_pending_rows` (NEW), `update_row` (exact col), `test_sheet_connection` (NEW→TEST_OK+IST), `poll_loop`
- `config.py` — `GOOGLE_SHEET_URL`/`GOOGLE_CREDENTIALS_FILE`/`DRIVE_OUTPUT_FOLDER_ID` (env, `SPREADSHEET_ID` compat), `SCOPES`
- `.env.example` — `LLM_PROVIDER` optional (rule-based default), `TTS_ENGINE` open-source-first
- `docs/open_source_stack.md` — open-source-first policy per module (transcript/script/TTS/MP3/Drive/Sheet)
- `scripts/test_sheet.py` + `main.py --test-sheet` (dry-run + live), `requirements.txt`, `.gitignore` (secrets ignored)

### Tested
- Sheet header auto-create/migrate (now strict 12-col, never migrated), `NEW→TEST_OK` live, `git` safe (no `.env`/`service_account.json`)

## [Unreleased] — Roadmap (Phase 2)
- Telugu 2-speaker script (Anjali/Ravi, rule-based + Ollama/Sarvam opt-in)
- Piper/AI4Bharat/Coqui TTS → `pydub` `192k` MP3 + `gTTS`/`edge-tts` secondary
- Drive `MediaFileUpload` → `Audio Link`
- Full `poll_loop` (`TRANSCRIPT_DONE → SCRIPT_DONE → AUDIO_DONE`), `faster-whisper` fallback, `batch_update` quota

[Unreleased]: https://github.com/RAMA-L7/Telugu_podcast_agent/compare/v0.3.0...HEAD
[v0.3.0]: https://github.com/RAMA-L7/Telugu_podcast_agent/compare/v0.2.0...v0.3.0
[v0.2.0]: https://github.com/RAMA-L7/Telugu_podcast_agent/compare/v0.1.0...v0.2.0
[v0.1.0]: https://github.com/RAMA-L7/Telugu_podcast_agent/releases/tag/v0.1.0
