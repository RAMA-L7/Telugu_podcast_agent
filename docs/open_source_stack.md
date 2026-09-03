# Open-Source-First Policy — Telugu Podcast Agent

> **Rule — Open-Source-First (strict):** Every module **must prefer free and fully open-source tools in the default workflow**. No paid or proprietary API is called unless the user **explicitly opts in** via an env key / `TTS_ENGINE` flag. Free-but-proprietary endpoints (e.g., Microsoft Edge Read-Aloud) are **never default** — they are only secondary/optional fallbacks. Fully open-source offline engines are the default. The Google Sheets connection (`src/sheet_monitor.py:1`, `GOOGLE_SHEET_URL` / `GOOGLE_CREDENTIALS_FILE`) is unchanged by this policy.

**What this means in practice:**
- **Default path = reproducible without billing:** `pip install -r requirements.txt` with an empty `.env` (no API keys) must run end-to-end using only open-source/free components.
- **Paid/proprietary = opt-in fallback:** OpenAI / Gemini / Groq / Azure TTS / Google Cloud TTS are documented only as `Optional fallback — requires key, metered — not default` and are gated behind `if API_KEY` checks (`config.py:46`, `src/script_generator.py:52`).
- **Fully open-source TTS is default:** `Piper` / `AI4Bharat` / `Coqui` (MPL-2.0 / Apache-2.0 / MIT) are default; `edge-tts` is documented only as secondary/optional (free but proprietary endpoint, not open-source).
- **No sheet-header change:** The exact 12-column schema (`config.py:68`) is preserved.

This document records the selected open-source stack per module, alternatives considered, and known limitations. Default `requirements.txt:1` installs only open-source / free-to-use packages; paid providers are pip-installed but dormant unless keys are set.

---

## 1. Transcript Extraction — `src/transcript.py:1`

**Selected (default, 100% open-source, free, no API key):**
- **`youtube-transcript-api==0.6.2`** (MIT) — direct YouTube timed-text endpoint, supports `en`/`te`/`hi`, auto-translated captions. First attempt in `fetch_transcript():26`.
- **`yt-dlp==2024.10.7`** (Unlicense) — fallback in `_fetch_via_ytdlp():97` downloads `json3`/`vtt` auto-captions; also provides video title via `extract_info()` without API key.
- **`pytube==15.0.0`** (MIT) — secondary title fallback only, `_get_title():171`.

**Alternatives considered:**
- `youtube-dl` (legacy fork of yt-dlp, slower updates) — rejected in favor of yt-dlp.
- `YouTube Data API v3` (official Google API, requires API key, quota-limited, paid beyond free tier) — **not default**, documented only as optional future alternative.
- Whisper-based re-transcription (`openai-whisper`, `faster-whisper` — both MIT) — viable fully-offline alternative for videos without captions, but requires audio download + GPU; listed as future open-source enhancement, not default due to heavy deps.

**Limitations:**
- No captions → `RuntimeError` if both `youtube-transcript-api` and `yt-dlp` find no subtitles. Workaround: add `faster-whisper` offline fallback or require captions-enabled videos.
- Auto-captions quality varies by language; Telugu auto-captions often noisy.
- `yt-dlp` writes temp files to `tempfile.gettempdir()/telugu_podcast_yt`; needs cleanup on disk-full.
- Private / age-restricted / live videos may be blocked.

---

## 2. Script Generation (Telugu dialogue, 2 speakers) — `src/script_generator.py:1`

> **Policy for this module:** No LLM is required for the basic workflow. The **sheet connection test (`Status NEW → TEST_OK`) must run with zero LLM / zero paid API keys.** A free, zero-dependency rule-based Python fallback is the default. Local LLM (Ollama) is optional for higher quality later. `OPENAI_API_KEY` is optional-only.

**Selected (default, open-source, free, no API key, no local LLM required):**

| Priority | Tool | License | When used | Requires |
|---|---|---|---|---|
| **1 — Default for basic workflow & sheet test** | **Rule-based fallback** `_rule_based_script():166` (pure Python, no deps) | MIT (project code) | **Always default.** Used when `LLM_PROVIDER` is empty/`rule-based`/`none` or when no API key is set. Produces 8–14 turn Anjali/Ravi extractive template. Guarantees offline operation and **sheet test never breaks**. | Nothing — no key, no server, no extra install |
| 2 — Optional for quality | **Ollama** via `requests` → `${OLLAMA_BASE_URL}/api/generate` (`ollama` — MIT) | MIT | **Opt-in only.** Runs `llama3.1:8b` / `phi-3` / `sarvam-2b` locally. `_via_ollama():132`. Free, private, open weights. **Enable only by setting `LLM_PROVIDER=ollama` explicitly.** Auto-detect does **not** add Ollama — prevents breaking when Ollama not installed. Missing Ollama never breaks sheet test. | Local Ollama server (`ollama serve`) + model pull |

**Paid fallback (optional, never default, never required for sheet test):**
- `openai==1.52.0` (MIT client, but API is paid) — `_via_openai():93` — only if `LLM_PROVIDER=openai` **and** `OPENAI_API_KEY` is set. With empty `.env`, not tried. Missing key never breaks anything.
- `google-generativeai==0.5.4` (Apache-2.0 client, Gemini API metered) — `_via_gemini():108` — same opt-in gating.
- `groq==0.9.0` (MIT client, Groq API metered) — `_via_groq():118`.
- Selection logic in `generate_telugu_script():47` — if no provider/keys, goes straight to rule-based; no Ollama auto-try. Paid APIs are **opt-in only**.

**Guarantee:** `python scripts/test_sheet.py --dry-run` and `python main.py --test-sheet` import only `config` + `gspread`/`google-auth` and **never import or call** `script_generator` / `openai` / `groq` / `requests` to Ollama. Therefore:
- Missing `OPENAI_API_KEY` → no error, sheet test passes.
- Missing Ollama / `requests` / no local model → no error, sheet test passes — rule-based is used for later pipeline steps only.
- Sheet test has **zero LLM dependency**.

**Open-source alternatives considered:**
- **Hugging Face `transformers` + `Sarvam-1/2B`, `AI4Bharat/Indic` models** (Apache-2.0) — true open Telugu LLMs; compatible with Ollama wrapper; recommended for production open-source deployment but requires `transformers` + `torch` (~2 GB).
- **vLLM / llama.cpp** — faster local inference, MIT.
- **LangChain / LiteLLM** — abstraction layer, MIT, but adds dependency weight; deferred.

**Limitations:**
- Rule-based fallback (default) is extractive, not generative; Telugu is template-wrapped, not fluent for long transcripts — but sufficient for offline/basic workflow and eliminates any key/server requirement.
- Ollama (optional) quality depends on model size and prompt; small models may emit JSON format errors (mitigated by `_parse_json_script():143` with fence stripping).
- Local LLMs need ~4–8 GB RAM/VRAM; CPU inference is slower (10–30s vs. 2s for paid APIs).
- No fine-tuned Telugu dialogue model yet; prompting relies on multilingual capability of base model.

---

## 3. TTS (Telugu, 2 speakers: Anjali/Ravi) — `src/tts.py:1`

> **Policy for this module:** Fully open-source offline TTS is the **default**. `edge-tts` (free but proprietary Microsoft endpoint, not open-source voices) is **only secondary / optional**, never default.

**Selected (default chain — fully open-source first):**

| Priority | Engine | License / Openness | Voice mapping | Role per policy |
|---|---|---|---|---|
| **1 — Default** | **`Piper TTS`** (`piper-tts==1.2.0`, MIT) + `te_TE` ONNX models | **Fully open-source, offline, Apache-2.0/MIT weights** | `PIPER_MODEL_PATH_TE_FEMALE/MALE` `config.py:58` (`Anjali` = female model, `Ravi` = male model) — `_via_piper():163` | **Default per open-source-first.** No API key, no network, runs 100% locally. ~80 MB per model. Recommended `TTS_ENGINE=piper`. |
| **2 — Default alternative** | **`AI4Bharat TTS` (Indic-TTS / Parler-TTS Indic)** (`ai4bharat-transliteration` + `TTS` Indic fine-tunes, MIT/Apache-2.0) | **Fully open-source, offline** | `language="te"` / `ai4bharat/indic-parler-tts` | **Default alternative.** Native Telugu prosody, community-trained on AI4Bharat data. Same offline guarantees as Piper; heavier than Piper (~300 MB) but more natural Telugu. Used when `TTS_ENGINE=ai4bharat` or via `Coqui` wrapper. |
| **3 — Default fallback** | **`Coqui TTS==0.22.0`** (MPL-2.0) `tts_models/multilingual/multi-dataset/xtts_v2` | **Fully open-source, offline** | `language="te"` — `_via_coqui():136` | **Default fallback.** Open-source, voice-clonable, but heavy (PyTorch + ~500 MB). Useful when Piper/AI4Bharat models unavailable. |
| Secondary (optional) | **`edge-tts==6.1.10`** (GPL-3.0 client, **proprietary Microsoft Edge Read-Aloud endpoint — not open-source** ) | Free but proprietary, no API key, network-dependent | `te-IN-ShrutiNeural` (Anjali) + `te-IN-MohanNeural` (Ravi) `config.py:62` | **Only secondary / optional per policy.** Best perceptual quality and keyless, but requires internet and relies on unofficial Microsoft endpoint. Never the default workflow. Use only when `TTS_ENGINE=edge` is explicitly set. |
| Secondary (optional) | **`gTTS==2.5.3`** (MIT client, **unofficial Google Translate endpoint — not open-source service**) | Free but proprietary endpoint, no key | `lang="te"` single voice | **Only secondary fallback** if Piper/AI4Bharat/Coqui unavailable and offline not required. Single voice, cannot distinguish speakers without post-processing. |

Default code path per policy: `TTS_ENGINE=piper` (or `ai4bharat`/`coqui`) → `generate_podcast_mp3():22` should be configured to `engines_to_try = [piper|ai4bharat|coqui]` first; `edge`/`gtts` are tried only when explicitly configured or when all fully open-source engines fail and user has opted into network TTS.

**Alternatives considered (not selected):**
- `espeak-ng` (GPL-3.0) — fully open-source but raw Telugu quality poor, rejected.
- `Mimic3`, `Festival` — open-source, English-centric, limited Telugu coverage.
- Paid/proprietary: `Azure TTS`, `Google Cloud TTS`, `ElevenLabs`, `OpenAI TTS` — **not default**, documented only as `Optional paid fallback — requires billing — not default`.

**Limitations:**
- **Piper `te_TE`** — fully open-source and fastest (real-time on CPU), but prosody less natural than Microsoft neural; requires separate binary + ONNX download and `PIPER_*` paths in `.env`; Telugu models are community-trained (~80 MB each).
- **AI4Bharat Indic-TTS** — most natural native Telugu prosody among open options, but models are larger (~200–400 MB), need `transformers` + `torch`, and inference slower on CPU than Piper.
- **Coqui XTTS** — open-source and voice-clonable, but heaviest (~500 MB + PyTorch), needs GPU for real-time; Telugu is via multilingual model, not Telugu-native, so accent may be off.
- **edge-tts (secondary only)** — network-dependent, unofficial Microsoft endpoint may rate-limit or change without notice; not reproducible offline, not open-source voices — hence **not default** per this policy.
- **gTTS (secondary only)** — single Telugu voice, no speaker distinction; unofficial Google endpoint may be throttled.
- All engines require `FFmpeg` for `pydub` concat (see §4).

---

## 4. MP3 Export & Audio Concatenation — `src/tts.py:50`, `pydub==0.25.1`

**Selected:**
- **`pydub==0.25.1`** (MIT) — `AudioSegment.from_file()`, silence insertion (`400ms` inter-turn, `300ms` intro / `500ms` outro), `export(..., bitrate="192k")`.
- **`FFmpeg`** (LGPL 2.1 / GPL 2.0 — binary dependency) — required runtime for `pydub`; install via `winget install ffmpeg` or `apt install ffmpeg`. Documented in `README.md:49`.

**Alternatives:**
- `ffmpeg-python` (Apache-2.0) — thin wrapper, same FFmpeg backend; `pydub` preferred for simpler `AudioSegment` API.
- `sox` / `audioread` — lighter but less format-flexible.

**Limitations:**
- FFmpeg must be on `PATH`; missing binary yields `Couldn't find ffmpeg` warning (handled by falling back engines, but export still fails).
- MP3 export is CPU-bound; long podcasts (>10 min) need ~2× duration for encoding on slow machines.
- No loudness normalization yet (`pyloudnorm` could be added, MIT).

---

## 5. Drive Upload — `src/drive_uploader.py:1`

**Selected (open-source, free within Google quota):**
- **`google-api-python-client==2.143.0`** (Apache-2.0) — `googleapiclient.discovery.build("drive","v3")`, `MediaFileUpload` resumable.
- **`google-auth==2.35.0` + `google-auth-oauthlib==1.2.1`** (Apache-2.0) — service account `Credentials.from_service_account_file()` and OAuth fallback `_get_drive_service():10`.
- Uses existing `GOOGLE_CREDENTIALS_FILE` + `DRIVE_OUTPUT_FOLDER_ID`; respects free Drive quota (15 GB for service account's My Drive, shared-drive quota for shared folders).

**Alternatives:**
- `PyDrive2` (MIT) — wrapper over same API, adds caching; not needed because `google-api-python-client` already minimal.
- `rclone` (MIT) — external binary, out of scope for Python pipeline.
- Paid: `S3` / `GCS` upload — not default; Drive covers free use case.

**Limitations:**
- Service account My Drive is quota-limited; recommend `DRIVE_OUTPUT_FOLDER_ID` shared folder (shared-drive quota).
- `permissions.create(anyone, reader)` may fail on Workspace-restricted domains (warns, retains owner-only link).
- Resumable upload needs stable network; no multipart retry beyond `tenacity` (could add).

---

## 6. Sheet Update (Read/Write) — `src/sheet_monitor.py:1`

**Selected (open-source, free):**
- **`gspread==6.1.2`** (MIT) — `open_by_key()`, `worksheet()`, `get_all_records()`, `update_cell()`, `update("A1:L1", header)` with exact 12-col schema.
- **`google-api-python-client` / `google-auth`** (Apache-2.0) — same credentials as Drive, single scopes `SCOPES` `config.py:71`:
  ```
  https://www.googleapis.com/auth/spreadsheets
  https://www.googleapis.com/auth/drive.file
  https://www.googleapis.com/auth/drive
  ```
- Header `config.py:68` exact 12 columns `ID | YouTube Link | Title | Language | Duration | Status | Transcript Link | Telugu Script Link | Audio Link | Error | Created At | Updated At` — created `A1:L1` if empty, never migrated/reduced/renamed; status flow `NEW → TEST_OK` (test, only `Status` + `Updated At` written via `_col_index()`) / `NEW → PROCESSING → DONE/ERROR` (production). Test helper `test_sheet_connection():150` reads `YouTube Link`, writes only `Status` + `Updated At`.

**Alternatives:**
- Raw `google-api-python-client` Sheets API without gspread — lower-level, more verbose; `gspread` wraps it ergonomically.
- `pygsheets` (BSD) — similar, less maintained than gspread.

**Limitations:**
- Google Sheets API free quota: 300 read / 300 write per minute per project; polling `POLL_INTERVAL_SECONDS=60` stays well within.
- `update_cell()` issues one API call per field; batch `update()` could be added for quota optimization.
- `get_all_records()` loads entire sheet into memory; large sheets (>10k rows) may be slow — paginated `get()` + `batch_get()` is alternative.

---

## Policy Enforcement

1. **Basic workflow & sheet test require NO LLM and NO key:** `pip install -r requirements.txt` with empty `.env` (no `OPENAI_API_KEY`, no Ollama) must pass `python scripts/test_sheet.py --dry-run` and run the full pipeline via rule-based fallback: transcript (`youtube-transcript-api`/`yt-dlp`) → **rule-based Telugu script (pure Python, no deps)** → **Piper / AI4Bharat / Coqui (fully open-source, offline)** → `pydub` → Drive/Sheets (needs only free Google credentials). No billable API invoked, no proprietary TTS endpoint called by default, no local LLM required.
2. **OPENAI_API_KEY is optional-only, never required:** `config.py:46` — `OPENAI_API_KEY` empty by default; `src/script_generator.py:47` only tries `openai` when `LLM_PROVIDER=openai` **and** key is present. Empty key never errors; sheet test never checks it. Same for `GEMINI_API_KEY`/`GROQ_API_KEY`.
3. **Local LLM (Ollama) is optional for quality later:** `src/script_generator.py:47` does **not** auto-add `ollama` when no keys are set — user must set `LLM_PROVIDER=ollama` explicitly to enable it. Missing Ollama / missing `requests` / no local model never breaks the sheet connection test or the basic rule-based workflow.
4. **Proprietary/free-but-not-open TTS endpoints are secondary only:** `edge-tts` (Microsoft) and `gTTS` (Google Translate) are **never default** — they are documented as `Secondary (optional) — free but proprietary endpoint — not open-source` and are reached only when `TTS_ENGINE=edge`/`gtts` is explicitly set or all fully open-source engines fail and user has opted in. Code default per policy is `TTS_ENGINE=piper` (or `ai4bharat`/`coqui`).
5. **No breaking change to Sheet connection / sheet headers:** `src/sheet_monitor.py`, `config.py:68` (exact 12-column schema `ID … Updated At`), and related logic are **not changed to require LLM**. Doc and code change guarantees sheet test is LLM-free.

---

## Future Open-Source Enhancements (not in default yet)

- Local Whisper (`faster-whisper`) for caption-less videos.
- Fine-tuned Telugu dialogue model (Sarvam) via Ollama for better `Anjali/Ravi` fluency.
- Piper `te_TE` model hosting in repo `models/` for fully offline TTS without Microsoft dependency.
- Batch Sheet updates via `gspread.batch_update()` for quota efficiency.

