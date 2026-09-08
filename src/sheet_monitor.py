"""Google Sheet monitor - polling for new YouTube URLs."""
import logging
import time
from typing import List, Dict, Optional

import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from google_auth_oauthlib.flow import InstalledAppFlow

from pathlib import Path
import config

log = logging.getLogger(__name__)

SCOPES = config.SCOPES

def _load_credentials():
    """Load service account or OAuth credentials."""
    cred_path = config.GOOGLE_CREDENTIALS_PATH
    token_path = config.GOOGLE_TOKEN_PATH

    # Try service account first
    if cred_path.exists():
        try:
            text = cred_path.read_text(encoding="utf-8")
            # Detect OAuth client file vs service account
            if '"installed"' in text or '"client_id"' in text and '"service_account"' not in text:
                # OAuth client file
                log.info("Found OAuth client file at %s", cred_path)
                return _oauth_flow(cred_path, token_path)
            else:
                log.info("Using service account: %s", cred_path)
                creds = Credentials.from_service_account_file(str(cred_path), scopes=SCOPES)
                return creds
        except Exception as e:
            log.warning("Failed to load service account %s: %s", cred_path, e)

    # Try existing OAuth token
    if token_path.exists():
        try:
            creds = OAuthCredentials.from_authorized_user_file(str(token_path), SCOPES)
            if creds and creds.valid:
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
                return creds
        except Exception as e:
            log.warning("Failed to load token %s: %s", token_path, e)

    raise FileNotFoundError(
        f"No valid Google credentials found. "
        f"Place service_account.json at {cred_path} "
        f"or run OAuth flow. See README."
    )

def _oauth_flow(client_path: Path, token_path: Path):
    creds = None
    if token_path.exists():
        creds = OAuthCredentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        log.info("OAuth token saved to %s", token_path)
    return creds

def get_client() -> gspread.Client:
    creds = _load_credentials()
    return gspread.authorize(creds)

def get_sheet(client: Optional[gspread.Client] = None):
    if not config.SPREADSHEET_ID:
        raise ValueError(
            "No sheet configured. Set GOOGLE_SHEET_URL in .env "
            "(e.g. https://docs.google.com/spreadsheets/d/<ID>/edit) "
            "or SPREADSHEET_ID"
        )
    client = client or get_client()
    sh = client.open_by_key(config.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(config.SHEET_NAME)
    except gspread.WorksheetNotFound:
        log.info("Sheet '%s' not found, creating it", config.SHEET_NAME)
        ws = sh.add_worksheet(title=config.SHEET_NAME, rows=1000, cols=len(config.SHEET_HEADER))
    # Header validation - keep original 12-column schema exactly, do not migrate/reduce/rename
    header = ws.row_values(1)
    if not any(header):
        # Empty sheet - create exact 12-column header A1:L1
        ws.update("A1:L1", [config.SHEET_HEADER])
        log.info("Initialized 12-column header: %s", config.SHEET_HEADER)
    elif header != config.SHEET_HEADER:
        # Do NOT modify existing headers. Log warning and keep original schema.
        log.warning("Header mismatch. Expected exactly %s got %s - keeping existing header unchanged per 12-col policy", config.SHEET_HEADER, header)
    return ws

def _col_index(name: str) -> int:
    """Return 1-based column index for given header name per config.SHEET_HEADER."""
    try:
        return config.SHEET_HEADER.index(name) + 1
    except ValueError:
        raise ValueError(f"Column '{name}' not in SHEET_HEADER {config.SHEET_HEADER}")

# ---------------------------------------------------------------------------
# Podcast Job ID — centralized allocation (sequential, 4-digit, never reuse)
# ---------------------------------------------------------------------------
def _is_valid_podcast_id(id_str: str) -> bool:
    """Check if ID is valid numeric podcast job ID (non-empty, digits only)."""
    s = str(id_str).strip() if id_str is not None else ""
    if not s:
        return False
    # Must be digits only (allow leading zeros, e.g., 0001)
    if not s.isdigit():
        return False
    try:
        num = int(s)
        return num > 0  # 0 is not a valid job ID; IDs start at 1
    except Exception:
        return False

def _parse_podcast_id_numeric(id_str: str) -> Optional[int]:
    """Parse podcast ID to int if valid, else None."""
    if _is_valid_podcast_id(id_str):
        try:
            return int(str(id_str).strip())
        except Exception:
            return None
    return None

def allocate_next_podcast_id(ws=None) -> str:
    """Allocate next sequential Podcast Job ID (4-digit, zero-padded).

    Scans existing sheet IDs, finds MAX numeric ID, returns MAX+1 formatted as 4 digits.
    Ignores blank and non-numeric cells. Never reuses deleted IDs (MAX+1).
    Centralized — caller should persist to sheet immediately to avoid duplicate allocation.
    Single-process safe (reads fresh sheet each call).

    Returns:
        str: e.g., "0001", "0012", "5001"
    """
    ws = ws or get_sheet()
    records = ws.get_all_records()
    max_id = 0
    for r in records:
        id_str = str(r.get("ID", "")).strip()
        num = _parse_podcast_id_numeric(id_str)
        if num is not None and num > max_id:
            max_id = num
    next_id = max_id + 1
    # If no valid IDs exist, start at 1
    if next_id < 1:
        next_id = 1
    return f"{next_id:04d}"

def _clean_title_for_filename(title: str, max_len: int = 60) -> str:
    """Clean title into filesystem-safe readable slug.

    - Removes unsafe Windows filename characters: < > : \" / \\ | ? * and control chars 0x00-0x1F
    - Removes other punctuation except word chars, spaces, hyphens, underscores
    - Normalizes excessive whitespace/underscores to single underscore
    - Truncates to max_len, stripping trailing underscores/hyphens
    - Falls back to 'untitled' if empty
    """
    import re
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

def _format_duration_mm_ss(seconds: float) -> str:
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

def _ensure_podcast_id(ws, row: Dict, dry_run: bool = False) -> str:
    """Ensure row has valid Podcast Job ID; allocate next sequential if empty.

    - Uses existing ID if valid (digits, >0)
    - Else allocates MAX+1 as 4-digit, persists to sheet (unless dry_run)
    - Never reuses deleted IDs, never uses row numbers
    - Safe for single-process watcher (reads fresh sheet each allocation)

    Returns:
        str: podcast_id (existing or newly allocated)
    """
    record = row.get("record", {}) if isinstance(row.get("record"), dict) else {}
    # Try multiple possible keys for ID (row dict vs record)
    current_id = str(record.get("ID", "")).strip() if record else ""
    if not current_id:
        current_id = str(row.get("id", "")).strip()
    if _is_valid_podcast_id(current_id):
        # Preserve existing, ensure 4-digit formatting (but do not unnecessarily change)
        # If existing is "2" we keep "2" as is for traceability, but return padded for filename?
        # Requirement: IDs must be sequential numeric values formatted as 4 digits.
        # For existing valid IDs that are not padded (e.g., "2"), we preserve as is in sheet,
        # but for filename we will use padded version. For consistency, return padded.
        try:
            num = int(current_id)
            pid_padded = f"{num:04d}"
            # Only update sheet if existing is not already padded and we want to normalize?
            # Requirement: Existing valid IDs must not be unnecessarily changed, so keep as is.
            # Return padded for filename generation but don't overwrite sheet.
            return pid_padded if len(current_id) != 4 else current_id
        except:
            return current_id
    # Empty or invalid -> allocate next
    next_id = allocate_next_podcast_id(ws)
    if dry_run:
        log.info("[DRY-RUN] Would allocate Podcast ID %s for Row %s (YouTube %r)", next_id, row.get("row_num", "?"), row.get("youtube_link", row.get("url", ""))[:40])
        # Do not write, but return next_id for dry-run simulation
        # Also update in-memory record for downstream steps in dry-run
        if record is not None:
            record["ID"] = next_id
        row["id"] = next_id
        return next_id
    # Persist to sheet
    row_num = row.get("row_num")
    if row_num:
        try:
            ws.update_cell(row_num, _col_index("ID"), next_id)
            log.info("Allocated Podcast ID %s for Row %d (YouTube %r)", next_id, row_num, row.get("youtube_link", row.get("url", ""))[:40])
            # Update in-memory
            if record is not None:
                record["ID"] = next_id
            row["id"] = next_id
            row["record"] = record
        except Exception as e:
            log.warning("Failed to persist Podcast ID %s for Row %d: %s", next_id, row_num, e)
            # Still return next_id; caller will have it for filename but sheet may be inconsistent
            # Next allocation will see this ID only if persisted, so duplicate risk if persist fails
            # We still update in-memory to avoid immediate duplicate
            if record is not None:
                record["ID"] = next_id
            row["id"] = next_id
    return next_id

def fetch_pending_rows(ws=None) -> List[Dict]:
    """Return rows where Status == NEW (strict, case-insensitive, trimmed).
    
    Reads YouTube Link from the exact 12-column schema; does NOT filter by URL validity
    in test mode - any NEW row is returned. Production filtering can be added separately.
    """
    ws = ws or get_sheet()
    records = ws.get_all_records()  # uses header row
    pending = []
    for idx, row in enumerate(records, start=2):  # row 2 is first data row
        status = str(row.get("Status", "")).strip().upper()
        if status == "NEW":
            yt_link = str(row.get("YouTube Link", "")).strip()
            pending.append({"row_num": idx, "status": status, "record": row, "url": yt_link, "youtube_link": yt_link})
    return pending

def fetch_all_pending(ws=None) -> List[Dict]:
    """Production helper - rows where Status empty/PENDING/NEW/TODO and YouTube Link is YouTube."""
    from src.utils import is_youtube_url
    ws = ws or get_sheet()
    records = ws.get_all_records()
    pending = []
    for idx, row in enumerate(records, start=2):
        yt_link = str(row.get("YouTube Link", "")).strip()
        status = str(row.get("Status", "")).strip().upper()
        if yt_link and is_youtube_url(yt_link) and status in ("", "PENDING", "NEW", "TODO"):
            pending.append({"row_num": idx, "url": yt_link, "youtube_link": yt_link, "status": status, "record": row})
    return pending

# ---------------------------------------------------------------------------
# Milestone 3: Transcript pipeline — connect sheet rows to transcript module
# ---------------------------------------------------------------------------
def fetch_transcript_pending_rows(ws=None) -> List[Dict]:
    """Milestone 3: rows with Status NEW or TEST_OK (sheet -> transcript).

    Reads YouTube Link from exact 12-col schema. Returns rows where
    Status (trimmed, upper) is NEW or TEST_OK. Any YouTube Link value is
    returned (empty/invalid will be handled as TRANSCRIPT_FAILED, not skipped).
    """
    ws = ws or get_sheet()
    records = ws.get_all_records()
    pending = []
    for idx, row in enumerate(records, start=2):
        status = str(row.get("Status", "")).strip().upper()
        if status in ("NEW", "TEST_OK"):
            yt_link = str(row.get("YouTube Link", "")).strip()
            pending.append({
                "row_num": idx,
                "status": status,
                "record": row,
                "url": yt_link,
                "youtube_link": yt_link,
                "id": str(row.get("ID", "")).strip(),
            })
    return pending

def _ist_timestamp() -> str:
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST")

def process_transcript_row(ws, row: Dict, dry_run: bool = False) -> Dict:
    """Process one row: validate YouTube Link, fetch transcript, save locally.

    Sheet-safe:
      - On valid: saves to output/transcripts/<ID>_<clean_title>.{txt,json} (ID-based), then
        updates sheet Transcript Link + Title + Status=TRANSCRIPT_DONE + clear Error + Updated At
      - On invalid/empty/fetch failure: does NOT write Transcript Link; sets
        Status=TRANSCRIPT_FAILED + Error (truncated) + Updated At
      - On dry_run: does everything except ws.update_cell (logs what would happen)
      - Podcast Job ID: ensures sequential 4-digit ID allocated and persisted before processing

    Returns result dict with row_num, status, valid, error etc.
    """
    from src.transcript import fetch_and_save_transcript
    row_num = row["row_num"]
    yt_link = row.get("youtube_link", row.get("url", ""))
    orig_status = row.get("status", "")
    log.info("Row %d (Status=%s) -> YouTube Link=%r", row_num, orig_status, yt_link)

    # Podcast Job ID: allocate if empty, preserve existing, never reassign on retry
    # Only allocate for rows that have a YouTube Link and are entering processing (NEW/TEST_OK or empty ID with NEW)
    # Use centralized allocate_next_podcast_id via _ensure_podcast_id
    podcast_id = None
    record = row.get("record", {})
    current_id = str(record.get("ID", "")).strip() if isinstance(record, dict) else ""
    if not current_id:
        current_id = str(row.get("id", "")).strip()
    # Only allocate if ID is empty/invalid and row has a YouTube Link (avoid allocating for invalid rows that will fail)
    # But even for invalid links, we could allocate? Requirement says when new YouTube row enters pipeline and ID empty, assign next ID before processing
    # So for any row that is being processed (process_transcript_row called), if ID empty, allocate
    if not _is_valid_podcast_id(current_id) and yt_link.strip():
        podcast_id = _ensure_podcast_id(ws, row, dry_run=dry_run)
        log.info("Row %d using Podcast ID %s (YouTube %r)", row_num, podcast_id, yt_link[:40])
    else:
        # Preserve existing valid ID (padded for filename)
        if _is_valid_podcast_id(current_id):
            try:
                podcast_id = f"{int(current_id):04d}"
            except:
                podcast_id = current_id.strip()
        else:
            podcast_id = None

    result = fetch_and_save_transcript(yt_link, podcast_id=podcast_id)
    timestamp = _ist_timestamp()

    if result["valid"]:
        txt_path = result["txt_path"]
        # Store relative path for sheet (portable) — e.g. output/transcripts/<id>.txt
        try:
            rel = txt_path.relative_to(config.BASE_DIR)
        except Exception:
            rel = txt_path
        transcript_link = str(rel).replace("\\", "/")
        title = result.get("title", "") or str(row["record"].get("Title", "")).strip()
        # On success: clear Error, set DONE, fill Title/Transcript Link, Updated At
        if dry_run:
            log.info("[DRY-RUN] Row %d would -> TRANSCRIPT_DONE | Transcript Link=%s | Title=%r | Updated At=%s",
                     row_num, transcript_link, title[:40], timestamp)
            return {"row_num": row_num, "youtube_link": yt_link, "valid": True, "dry_run": True,
                    "would_status": "TRANSCRIPT_DONE", "transcript_link": transcript_link, "title": title,
                    "video_id": result["video_id"], "error": None}
        # Real write — only when valid data exists
        updates = {}
        # Title
        if title:
            ws.update_cell(row_num, _col_index("Title"), title[:180])
            updates["Title"] = title[:180]
        # Transcript Link
        ws.update_cell(row_num, _col_index("Transcript Link"), transcript_link)
        updates["Transcript Link"] = transcript_link
        # Clear Error on success
        ws.update_cell(row_num, _col_index("Error"), "")
        # Status + Updated At last
        ws.update_cell(row_num, _col_index("Status"), "TRANSCRIPT_DONE")
        ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        log.info("Row %d -> TRANSCRIPT_DONE (%s)", row_num, result["video_id"])
        return {"row_num": row_num, "youtube_link": yt_link, "valid": True, "status": "TRANSCRIPT_DONE",
                "transcript_link": transcript_link, "video_id": result["video_id"], "title": title, "timestamp": timestamp, "updates": updates}
    else:
        err = (result.get("error") or "Unknown error")[:300]
        err_type = result.get("error_type", "Unknown")
        log.warning("Row %d -> TRANSCRIPT_FAILED [%s] %s", row_num, err_type, err[:120])
        if dry_run:
            log.info("[DRY-RUN] Row %d would -> TRANSCRIPT_FAILED | Error=[%s] %s | Updated At=%s",
                     row_num, err_type, err[:80], timestamp)
            return {"row_num": row_num, "youtube_link": yt_link, "valid": False, "dry_run": True,
                    "would_status": "TRANSCRIPT_FAILED", "error": err, "error_type": err_type}
        # Real write for failure: do NOT overwrite Transcript Link; set Error + Status + Updated At
        ws.update_cell(row_num, _col_index("Error"), f"[{err_type}] {err}"[:300])
        ws.update_cell(row_num, _col_index("Status"), "TRANSCRIPT_FAILED")
        ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        return {"row_num": row_num, "youtube_link": yt_link, "valid": False, "status": "TRANSCRIPT_FAILED",
                "error": err, "error_type": err_type, "timestamp": timestamp}

def run_transcript_pipeline(dry_run: bool = False, limit: Optional[int] = None, ws=None) -> Dict:
    """Milestone 3 entry: process all rows with NEW or TEST_OK.

    - Reads pending rows via fetch_transcript_pending_rows()
    - For each, calls process_transcript_row (which validates, fetches, saves locally)
    - On dry_run: no sheet writes, only logs
    - Returns summary dict.

    Does NOT touch .env / credentials; sheet writes only when valid or for
    explicit FAILED status+Error.

    Usage:
        from src.sheet_monitor import run_transcript_pipeline
        run_transcript_pipeline(dry_run=True)   # preview
        run_transcript_pipeline(dry_run=False)  # live
    """
    ws = ws or get_sheet()
    header = ws.row_values(1)
    if header != config.SHEET_HEADER:
        log.warning("Header mismatch for transcript pipeline — continuing without modification. Expected %s", config.SHEET_HEADER)
    pending = fetch_transcript_pending_rows(ws)
    log.info("Transcript pipeline: %d rows with Status NEW/TEST_OK", len(pending))
    if limit is not None:
        pending = pending[:limit]
        log.info("Limited to first %d rows", limit)
    summary = {
        "header": header,
        "total_pending": len(pending),
        "processed": 0,
        "done": 0,
        "failed": 0,
        "dry_run": dry_run,
        "details": [],
    }
    if not pending:
        log.info("No rows to process (need Status NEW or TEST_OK with YouTube Link)")
        return summary
    for row in pending:
        try:
            res = process_transcript_row(ws, row, dry_run=dry_run)
            summary["details"].append(res)
            summary["processed"] += 1
            if res.get("valid"):
                summary["done"] += 1
            else:
                summary["failed"] += 1
        except Exception as e:
            log.exception("Unexpected error processing row %d: %s", row["row_num"], e)
            summary["details"].append({"row_num": row["row_num"], "valid": False, "error": str(e), "error_type": "Unexpected"})
            summary["failed"] += 1
            summary["processed"] += 1
    log.info("Transcript pipeline complete: %d done, %d failed (dry_run=%s)", summary["done"], summary["failed"], dry_run)
    return summary

# ---------------------------------------------------------------------------
# Phase 4 Milestone 2: Audio pipeline — Transcript/Script -> Piper TTS -> Drive -> Audio Link
# ---------------------------------------------------------------------------
def _audio_output_path(podcast_id: str, title: str = "", duration_seconds: float = None) -> Path:
    """Human-readable deterministic MP3 path: <ID>_<clean_title>_<duration>.mp3

    - ID must always be at the beginning (4-digit zero-padded, e.g., 0012)
    - Clean title via filesystem-safe slug (no Windows forbidden chars, normalized)
    - Duration formatted as MMmSSs from actual generated audio (e.g., 08m15s)
    - Does NOT include YouTube video ID (remains in Sheet URL / transcript metadata)
    - Deterministic for same ID/title/duration; reasonably bounded length
    - For backward compat, if podcast_id is a legacy 11-char video_id, it will still
      generate but will not satisfy ID-at-beginning requirement — caller must pass podcast_id
    """
    from src.utils import clean_title_for_filename, format_duration_mm_ss
    import re
    pid = str(podcast_id).strip() if podcast_id is not None else ""
    # Validate podcast ID; if invalid, fallback to 0000 with warning (should not happen after allocation)
    if not _is_valid_podcast_id(pid):
        log.warning("Invalid podcast_id %r for audio path, using 0000 (should be 4-digit)", pid)
        # If pid looks like video_id (11 chars), we still need ID at beginning — use 0000 to avoid leaking video_id
        # Caller should have allocated valid ID; this is fallback for legacy tests
        if pid and len(pid) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", pid):
            pid = "0000"
        elif not pid:
            pid = "0000"
        else:
            # Keep as is but ensure 4-digit? If pid is "1", format to 4 digits
            try:
                num = int(pid)
                pid = f"{num:04d}"
            except:
                pid = "0000"
    clean = clean_title_for_filename(title, max_len=60)
    base = f"{pid}_{clean}"
    if duration_seconds is not None:
        try:
            dur_str = format_duration_mm_ss(duration_seconds)
            base = f"{base}_{dur_str}"
        except Exception:
            pass
    # Keep reasonably bounded: total base <=80 chars
    if len(base) > 80:
        excess = len(base) - 80
        # Truncate clean part to fit
        clean_truncated = clean[:max(1, len(clean) - excess)]
        clean_truncated = clean_truncated.rstrip("_-")
        base = f"{pid}_{clean_truncated}"
        if duration_seconds is not None:
            try:
                base = f"{base}_{dur_str}"
            except:
                pass
    # Final safety: ensure no leftover unsafe chars (clean already safe, pid digits, dur safe)
    base = re.sub(r"[^\w-]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = f"{pid}_untitled"
        if duration_seconds is not None:
            base = f"{base}_{dur_str}"
    return config.OUTPUT_DIR / f"{base}.mp3"

def _transcript_output_path(podcast_id: str, title: str = "") -> Path:
    """Human-readable transcript path: <ID>_<clean_title>.txt (and .json)

    - ID at beginning, 4-digit
    - Clean title via filesystem-safe slug
    - Does not include video_id in filename (remains in metadata)
    """
    from src.utils import clean_title_for_filename
    pid = str(podcast_id).strip() if podcast_id is not None else ""
    if not _is_valid_podcast_id(pid):
        log.warning("Invalid podcast_id %r for transcript path, using 0000", pid)
        try:
            num = int(pid)
            pid = f"{num:04d}"
        except:
            pid = "0000"
    clean = clean_title_for_filename(title, max_len=60)
    base = f"{pid}_{clean}"
    if len(base) > 80:
        excess = len(base) - 80
        clean_truncated = clean[:max(1, len(clean) - excess)]
        clean_truncated = clean_truncated.rstrip("_-")
        base = f"{pid}_{clean_truncated}"
    import re
    base = re.sub(r"[^\w-]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = f"{pid}_untitled"
    return config.TRANSCRIPT_DIR / f"{base}.txt"

def _transcript_json_path(podcast_id: str, title: str = "") -> Path:
    """Corresponding JSON path for transcript metadata."""
    txt_path = _transcript_output_path(podcast_id, title)
    return txt_path.with_suffix(".json")

def _is_valid_audio_link(link: str) -> bool:
    """Check if Audio Link is a valid Drive webViewLink (for idempotency)."""
    link = (link or "").strip()
    return link.startswith("http") and "drive.google.com" in link

def _should_retry_failed_row(record: Dict) -> bool:
    """Prevent hammering permanently failing rows (Phase 5.3).

    Uses Updated At (IST timestamp like "2026-09-08 12:02:43 IST") to decide if enough time has passed
    since last failure. No schema change — uses existing Updated At / Error columns.

    - If status is TRANSCRIPT_FAILED/AUDIO_FAILED and Updated At is recent, skip this cycle
    - If Updated At missing/unparsable, allow retry (safe)
    - Uses max(WATCH_INTERVAL*2, RETRY_MAX_DELAY) as minimum retry interval to avoid 30s hammering
    - Simple, backward-compatible, survives watcher restart via sheet timestamp
    """
    status = str(record.get("Status", "")).strip().upper()
    if status not in ("TRANSCRIPT_FAILED", "AUDIO_FAILED"):
        return True  # Not a failed row, always retryable (e.g., NEW, TRANSCRIPT_DONE)
    updated_at = str(record.get("Updated At", "")).strip()
    if not updated_at:
        return True
    try:
        from datetime import datetime, timezone, timedelta
        # Parse "YYYY-MM-DD HH:MM:SS IST" — IST is UTC+5:30
        ist = timezone(timedelta(hours=5, minutes=30))
        # Remove trailing " IST" if present
        ts_str = updated_at.replace(" IST", "").strip()
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=ist)
        now = datetime.now(ist)
        elapsed = (now - dt).total_seconds()
        # Use max of watch interval *2 and retry max delay as backoff
        # This prevents hammering every 30s, but allows retry after ~60s
        try:
            min_interval = max(int(config.WATCH_INTERVAL_SECONDS) * 2, int(config.RETRY_MAX_DELAY_SECONDS))
        except Exception:
            min_interval = 60
        if elapsed < min_interval:
            log.debug("Skipping retry for %s row %r (Updated At %s, %.0fs ago < %ds)", status, record.get("ID"), updated_at, elapsed, min_interval)
            return False
        return True
    except Exception as e:
        log.debug("Could not parse Updated At %r, allowing retry: %s", updated_at, e)
        return True

def fetch_audio_pending_rows(ws=None) -> List[Dict]:
    """Fetch rows ready for audio generation/upload.

    Idempotency: skip rows where Status == AUDIO_DONE and Audio Link is valid Drive link.
    Pending if Status in (TRANSCRIPT_DONE, AUDIO_FAILED) — AUDIO_FAILED is retryable with backoff.
    Uses exact 12-col schema, no header change.

    Returns list of dicts with row_num, status, record, url, youtube_link, id, title, audio_link.
    """
    ws = ws or get_sheet()
    records = ws.get_all_records()
    pending = []
    for idx, row in enumerate(records, start=2):
        status = str(row.get("Status", "")).strip().upper()
        audio_link = str(row.get("Audio Link", "")).strip()
        # Idempotency: already AUDIO_DONE with valid link → skip
        if status == "AUDIO_DONE" and _is_valid_audio_link(audio_link):
            continue
        # Hammering prevention: don't retry failed rows every 30s
        if status in ("AUDIO_FAILED", "TRANSCRIPT_FAILED") and not _should_retry_failed_row(row):
            continue
        # Pending: transcript done, or previous audio failed (retryable with backoff)
        if status in ("TRANSCRIPT_DONE", "AUDIO_FAILED"):
            yt_link = str(row.get("YouTube Link", "")).strip()
            pending.append({
                "row_num": idx,
                "status": status,
                "record": row,
                "url": yt_link,
                "youtube_link": yt_link,
                "id": str(row.get("ID", "")).strip(),
                "title": str(row.get("Title", "")).strip(),
                "audio_link": audio_link,
                "transcript_link": str(row.get("Transcript Link", "")).strip(),
            })
    return pending

def process_audio_row(ws, row: Dict, dry_run: bool = False) -> Dict:
    """Process one row: Transcript -> Telugu script -> Piper TTS -> Drive upload -> Sheet Audio Link.

    Failure handling (critical):
      - TTS succeeds but Drive fails: Do NOT write fake/empty Audio Link, do NOT overwrite valid existing Audio Link,
        Status=AUDIO_FAILED, Error=Drive failure, Updated At=IST, preserve valid fields.
      - TTS fails: do NOT attempt Drive, Error=TTS failure, Status=AUDIO_FAILED, Updated At, preserve links.
      - Sheet update fails after Drive success: do NOT claim success, preserve Drive result in return/log, raise.

    Idempotency: if Status==AUDIO_DONE and valid Audio Link, skip (no regeneration/upload).
    Only mark AUDIO_DONE after BOTH MP3 generation AND Drive upload AND Sheet update succeed.

    Do NOT overwrite valid existing fields unnecessarily.

    Returns result dict with row_num, status, audio_link, fileId, error, etc.
    """
    from src.utils import extract_video_id

    row_num = row["row_num"]
    yt_link = row.get("youtube_link", row.get("url", "")) or str(row.get("record", {}).get("YouTube Link", "")).strip()
    record = row.get("record", {})
    orig_status = str(record.get("Status", "")).strip()
    status_upper = orig_status.strip().upper()
    audio_link_existing = str(record.get("Audio Link", "")).strip()
    title = str(record.get("Title", "")).strip() or row.get("title", "")
    row_id = str(record.get("ID", "")).strip() or row.get("id", "")
    transcript_link = str(record.get("Transcript Link", "")).strip() or row.get("transcript_link", "")

    log.info("Audio Row %d (Status=%s, Audio Link=%s) -> YouTube Link=%r", row_num, orig_status, audio_link_existing[:40], yt_link)

    # Idempotency: already AUDIO_DONE with valid link -> skip
    if status_upper == "AUDIO_DONE" and _is_valid_audio_link(audio_link_existing):
        log.info("Row %d already AUDIO_DONE with valid Audio Link, skipping", row_num)
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_DONE", "skipped": True, "audio_link": audio_link_existing, "valid": True}

    timestamp = _ist_timestamp()

    # Validate YouTube Link and extract video_id for deterministic MP3 filename
    video_id = extract_video_id(yt_link)
    if not video_id:
        err = f"Invalid YouTube Link: {yt_link!r}"
        log.warning("Row %d AUDIO_FAILED [InvalidLink] %s", row_num, err)
        if dry_run:
            log.info("[DRY-RUN] Row %d would -> AUDIO_FAILED | Error=[InvalidLink] %s | Updated At=%s", row_num, err[:80], timestamp)
            return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_FAILED", "valid": False, "dry_run": True, "would_status": "AUDIO_FAILED", "error": err, "error_type": "InvalidLink"}
        # Do NOT overwrite Audio Link
        try:
            ws.update_cell(row_num, _col_index("Error"), f"[InvalidLink] {err}"[:300])
            ws.update_cell(row_num, _col_index("Status"), "AUDIO_FAILED")
            ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        except Exception as e:
            log.exception("Row %d sheet update failed for InvalidLink: %s", row_num, e)
            raise
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_FAILED", "valid": False, "error": err, "error_type": "InvalidLink", "timestamp": timestamp}

    # Ensure Podcast ID for transcript lookup (should have been allocated at transcript stage)
    podcast_id = str(record.get("ID", "")).strip() or str(row.get("id", "")).strip()
    if not _is_valid_podcast_id(podcast_id) and yt_link.strip():
        # Allocate if still empty (e.g., legacy row with empty ID that reached audio stage)
        podcast_id = _ensure_podcast_id(ws, row, dry_run=dry_run)
        # Update record for downstream
        record["ID"] = podcast_id
        row["id"] = podcast_id
    # Load transcript: try ID-based human-readable first, then legacy video_id, then Transcript Link
    transcript_text = ""
    transcript_path = None
    # Try ID-based transcript (new human-readable)
    if _is_valid_podcast_id(podcast_id):
        try:
            # Use helper to get expected ID-based path (with clean title)
            id_based_path = _transcript_output_path(podcast_id, title)
            if id_based_path.exists():
                transcript_text = id_based_path.read_text(encoding="utf-8").strip()
                transcript_path = id_based_path
            else:
                # Also try glob for any file starting with podcast_id_*.txt (in case title changed)
                import glob
                candidates = list(config.TRANSCRIPT_DIR.glob(f"{podcast_id}_*.txt"))
                if candidates:
                    # Prefer most recent
                    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    for cand in candidates:
                        try:
                            txt = cand.read_text(encoding="utf-8").strip()
                            if txt and len(txt) > 50:
                                transcript_text = txt
                                transcript_path = cand
                                log.info("Loaded transcript via glob ID-based %s", cand.name)
                                break
                        except Exception:
                            continue
        except Exception as e:
            log.debug("ID-based transcript load failed for %s: %s", podcast_id, e)
    # Fallback: legacy video_id based
    if not transcript_text:
        legacy_path = config.TRANSCRIPT_DIR / f"{video_id}.txt"
        if legacy_path.exists():
            try:
                transcript_text = legacy_path.read_text(encoding="utf-8").strip()
                transcript_path = legacy_path
            except Exception as e:
                log.warning("Row %d transcript read failed %s: %s", row_num, legacy_path, e)
        else:
            transcript_path = legacy_path
    # Fallback: try Transcript Link if it's a local path
    if not transcript_text and transcript_link:
        try:
            p = Path(transcript_link)
            # Transcript Link may be relative like output/transcripts/...; resolve against BASE_DIR
            if not p.is_absolute():
                p = config.BASE_DIR / p
            if p.exists():
                transcript_text = p.read_text(encoding="utf-8").strip()
                transcript_path = p
        except Exception:
            pass
    if not transcript_text:
        err = f"Transcript not found for video_id={video_id} podcast_id={podcast_id} (need {transcript_path})"
        log.warning("Row %d AUDIO_FAILED [TranscriptMissing] %s", row_num, err)
        if dry_run:
            log.info("[DRY-RUN] Row %d would -> AUDIO_FAILED | Error=[TranscriptMissing] %s | Updated At=%s", row_num, err[:80], timestamp)
            return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_FAILED", "valid": False, "dry_run": True, "would_status": "AUDIO_FAILED", "error": err, "error_type": "TranscriptMissing"}
        # Preserve Audio Link, set failure
        try:
            ws.update_cell(row_num, _col_index("Error"), f"[TranscriptMissing] {err}"[:300])
            ws.update_cell(row_num, _col_index("Status"), "AUDIO_FAILED")
            ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        except Exception as e:
            log.exception("Row %d sheet update failed for TranscriptMissing: %s", row_num, e)
            raise
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_FAILED", "valid": False, "error": err, "error_type": "TranscriptMissing", "timestamp": timestamp}

    # Dry-run: simulate without side effects (no MP3, no Drive, no sheet write)
    if dry_run:
        # Use deterministic ID-based filename with placeholder duration for dry-run
        try:
            mp3_path = _audio_output_path(podcast_id, title, duration_seconds=None)
        except Exception:
            mp3_path = config.OUTPUT_DIR / f"{podcast_id}_dryrun.mp3"
        fake_link = f"dry-run://{mp3_path.name}"
        log.info("[DRY-RUN] Row %d would -> AUDIO_DONE | Audio Link=%s | mp3=%s | Updated At=%s", row_num, fake_link, mp3_path, timestamp)
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_DONE", "valid": True, "dry_run": True, "would_status": "AUDIO_DONE", "audio_link": fake_link, "mp3_path": str(mp3_path), "video_id": video_id, "podcast_id": podcast_id, "timestamp": timestamp}

    # Generate Telugu script (existing abstraction, rule-based fallback preserved)
    try:
        from src.script_generator import generate_telugu_script
        script = generate_telugu_script(transcript_text, title=title)
        if not script or len(script) < 2:
            raise ValueError(f"Script generation returned invalid script: {script}")
    except Exception as e:
        err = f"Script generation failed: {e}"
        log.warning("Row %d AUDIO_FAILED [ScriptFailed] %s", row_num, err)
        try:
            ws.update_cell(row_num, _col_index("Error"), f"[ScriptFailed] {err}"[:300])
            ws.update_cell(row_num, _col_index("Status"), "AUDIO_FAILED")
            ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        except Exception as sheet_e:
            log.exception("Row %d sheet update failed for ScriptFailed: %s", row_num, sheet_e)
            raise
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_FAILED", "valid": False, "error": err, "error_type": "ScriptFailed", "timestamp": timestamp}

    # Generate MP3 via existing TTS (Piper primary) — temp file then human-readable ID-based filename with duration
    import tempfile
    tmp_mp3 = Path(tempfile.gettempdir()) / f"podcast_{podcast_id}_{video_id}_tmp.mp3"
    mp3_path = tmp_mp3  # default for error reporting, will be updated to final after move
    try:
        from src.tts import generate_podcast_mp3
        tmp_mp3.parent.mkdir(parents=True, exist_ok=True)
        log.info("Row %d generating MP3 via piper: %s (podcast %s) -> temp %s (%d turns)", row_num, video_id, podcast_id, tmp_mp3, len(script))
        generate_podcast_mp3(script, tmp_mp3)
        if not tmp_mp3.exists() or tmp_mp3.stat().st_size == 0:
            raise RuntimeError(f"MP3 not created or empty: {tmp_mp3}")
        # Measure actual duration for human-readable filename (MMmSSs)
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(str(tmp_mp3))
            duration_seconds = len(audio) / 1000.0
        except Exception as e:
            log.warning("Could not measure MP3 duration for %s: %s, using 0", tmp_mp3, e)
            duration_seconds = 0
        # Create final human-readable path: <ID>_<clean_title>_<duration>.mp3
        mp3_path = _audio_output_path(podcast_id, title, duration_seconds)
        mp3_path.parent.mkdir(parents=True, exist_ok=True)
        # Deterministic for same ID/title/duration — reuse if already exists
        if mp3_path.exists() and mp3_path.stat().st_size > 0:
            log.info("Final MP3 already exists, reusing %s", mp3_path)
            try:
                tmp_mp3.unlink()
            except:
                pass
        else:
            import shutil
            shutil.move(str(tmp_mp3), str(mp3_path))
            log.info("MP3 moved to final human-readable path: %s (%.1fs, %s)", mp3_path, duration_seconds, _format_duration_mm_ss(duration_seconds))
        if not mp3_path.exists() or mp3_path.stat().st_size == 0:
            raise RuntimeError(f"Final MP3 not created or empty: {mp3_path}")
    except Exception as e:
        err = f"TTS failed: {e}"
        log.warning("Row %d AUDIO_FAILED [TTSFailed] %s", row_num, err)
        try:
            ws.update_cell(row_num, _col_index("Error"), f"[TTSFailed] {err}"[:300])
            ws.update_cell(row_num, _col_index("Status"), "AUDIO_FAILED")
            ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        except Exception as sheet_e:
            log.exception("Row %d sheet update failed for TTSFailed: %s", row_num, sheet_e)
            raise
        # Do NOT attempt Drive upload
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_FAILED", "valid": False, "error": err, "error_type": "TTSFailed", "timestamp": timestamp, "mp3_path": str(mp3_path)}

    # Drive upload (personal Gmail OAuth, secure make_public=False, uses DRIVE_OUTPUT_FOLDER_ID)
    try:
        from src.drive_uploader import upload_to_drive, DriveError
        log.info("Row %d uploading MP3 to Drive: %s", row_num, mp3_path)
        drive_result = upload_to_drive(mp3_path, make_public=False)
        # drive_result is dict {fileId, webViewLink}
        if isinstance(drive_result, dict):
            file_id = drive_result.get("fileId") or drive_result.get("id")
            web_view_link = drive_result.get("webViewLink") or drive_result.get("link")
        else:
            # Backward compat: string link
            web_view_link = str(drive_result)
            file_id = None
            import re as _re
            m = _re.search(r"/d/([a-zA-Z0-9_-]+)", web_view_link)
            if m:
                file_id = m.group(1)
        if not web_view_link or not web_view_link.startswith("http") or "drive.google.com" not in web_view_link:
            raise DriveError(f"Invalid Drive link returned: {web_view_link!r} (fileId={file_id})")
    except Exception as e:
        # Drive upload failure: Do NOT write fake/empty Audio Link, do NOT overwrite valid existing Audio Link
        err = f"Drive upload failed: {e}"
        log.warning("Row %d AUDIO_FAILED [DriveFailed] %s (mp3 preserved at %s)", row_num, err, mp3_path)
        try:
            ws.update_cell(row_num, _col_index("Error"), f"[DriveFailed] {err}"[:300])
            ws.update_cell(row_num, _col_index("Status"), "AUDIO_FAILED")
            ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        except Exception as sheet_e:
            log.exception("Row %d sheet update failed for DriveFailed: %s", row_num, sheet_e)
            # Preserve Drive result in return for visibility, but sheet update failed
            raise RuntimeError(f"Drive upload failed and sheet update also failed: {e} / sheet: {sheet_e}") from sheet_e
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_FAILED", "valid": False, "error": err, "error_type": "DriveFailed", "timestamp": timestamp, "mp3_path": str(mp3_path), "drive_error": str(e)}

    # Sheet update: only after BOTH MP3 and Drive succeed
    try:
        # Use same timestamp for all updates in this row
        # Update Audio Link with webViewLink (do NOT construct manually when valid webViewLink returned)
        ws.update_cell(row_num, _col_index("Audio Link"), web_view_link)
        ws.update_cell(row_num, _col_index("Error"), "")
        ws.update_cell(row_num, _col_index("Status"), "AUDIO_DONE")
        ws.update_cell(row_num, _col_index("Updated At"), timestamp)
        log.info("Row %d -> AUDIO_DONE (Drive %s, mp3 %s)", row_num, file_id, mp3_path)
        return {"row_num": row_num, "youtube_link": yt_link, "status": "AUDIO_DONE", "valid": True, "audio_link": web_view_link, "fileId": file_id, "mp3_path": str(mp3_path), "video_id": video_id, "timestamp": timestamp}
    except Exception as e:
        # Sheet update failed after Drive success: do NOT claim success, preserve Drive result, raise
        err = f"Sheet update failed after Drive upload: {e}"
        log.exception("Row %d Drive upload succeeded (%s) but Sheet update failed: %s", row_num, web_view_link, e)
        # Preserve Drive result in return for visibility
        raise RuntimeError(f"{err} (Drive succeeded: {web_view_link} fileId={file_id})") from e

def run_audio_pipeline(dry_run: bool = False, limit: Optional[int] = None, ws=None) -> Dict:
    """Run audio pipeline for rows needing TTS + Drive upload.

    - Fetches pending via fetch_audio_pending_rows() (TRANSCRIPT_DONE/AUDIO_FAILED, skips AUDIO_DONE+valid link)
    - For each, calls process_audio_row (dry_run logs without writes)
    - Returns summary dict.

    Idempotency: skips AUDIO_DONE with valid Audio Link; failed rows retryable.
    Does NOT touch transcript logic; reuses existing TTS/Drive abstractions.
    """
    ws = ws or get_sheet()
    header = ws.row_values(1)
    if header != config.SHEET_HEADER:
        log.warning("Header mismatch for audio pipeline — continuing without modification. Expected %s", config.SHEET_HEADER)
    pending = fetch_audio_pending_rows(ws)
    log.info("Audio pipeline: %d rows with Status TRANSCRIPT_DONE/AUDIO_FAILED (skipping AUDIO_DONE+valid link)", len(pending))
    if limit is not None:
        pending = pending[:limit]
        log.info("Limited to first %d rows", limit)
    summary = {
        "header": header,
        "total_pending": len(pending),
        "processed": 0,
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": dry_run,
        "details": [],
    }
    if not pending:
        log.info("No rows to process for audio (need TRANSCRIPT_DONE/AUDIO_FAILED with transcript)")
        return summary
    for row in pending:
        # Check idempotency again to count skipped separately (should have been filtered, but keep)
        try:
            res = process_audio_row(ws, row, dry_run=dry_run)
            summary["details"].append(res)
            summary["processed"] += 1
            if res.get("skipped"):
                summary["skipped"] += 1
            elif res.get("valid") and res.get("status") == "AUDIO_DONE":
                summary["done"] += 1
            else:
                summary["failed"] += 1
        except Exception as e:
            log.exception("Unexpected error processing audio row %d: %s", row["row_num"], e)
            summary["details"].append({"row_num": row["row_num"], "valid": False, "error": str(e), "error_type": "Unexpected", "status": "AUDIO_FAILED"})
            summary["failed"] += 1
            summary["processed"] += 1
    log.info("Audio pipeline complete: %d done, %d failed, %d skipped (dry_run=%s)", summary["done"], summary["failed"], summary["skipped"], dry_run)
    return summary

# ---------------------------------------------------------------------------
# Phase 5.1: Full pipeline orchestration — NEW -> TRANSCRIPT_DONE -> AUDIO_DONE
# ---------------------------------------------------------------------------
def fetch_pipeline_pending_rows(ws=None) -> List[Dict]:
    """Fetch rows needing full pipeline processing.

    Primary input is Status=NEW. Also handles restart safety:
    - NEW / TEST_OK -> needs transcript + audio
    - TRANSCRIPT_DONE -> transcript already done, needs audio
    - AUDIO_FAILED -> retryable audio

    Skips AUDIO_DONE with valid Drive link (idempotency).
    Uses exact 12-col schema.

    Returns list with row_num, status, record, url, etc.
    """
    ws = ws or get_sheet()
    records = ws.get_all_records()
    pending = []
    for idx, row in enumerate(records, start=2):
        status = str(row.get("Status", "")).strip().upper()
        audio_link = str(row.get("Audio Link", "")).strip()
        # Idempotency: already done with valid link -> skip
        if status == "AUDIO_DONE" and _is_valid_audio_link(audio_link):
            continue
        # Hammering prevention: don't retry failed rows every cycle
        if status in ("AUDIO_FAILED", "TRANSCRIPT_FAILED") and not _should_retry_failed_row(row):
            continue
        # Primary NEW, plus transcript done for restart, plus audio failed retry
        # Also include TEST_OK for backward compat (transcript pipeline uses it)
        if status in ("NEW", "TEST_OK", "TRANSCRIPT_DONE", "AUDIO_FAILED"):
            yt_link = str(row.get("YouTube Link", "")).strip()
            pending.append({
                "row_num": idx,
                "status": status,
                "record": row,
                "url": yt_link,
                "youtube_link": yt_link,
                "id": str(row.get("ID", "")).strip(),
                "title": str(row.get("Title", "")).strip(),
                "audio_link": audio_link,
                "transcript_link": str(row.get("Transcript Link", "")).strip(),
            })
    return pending

def process_pipeline_row(ws, row: Dict, dry_run: bool = False) -> Dict:
    """Process one row through full pipeline: NEW -> TRANSCRIPT_DONE -> AUDIO_DONE.

    Dry-run: no transcript fetch, no Gemini, no TTS, no Drive, no Sheet mutation.
    Failure isolation:
      - Transcript fails -> TRANSCRIPT_FAILED, no audio
      - Audio fails after transcript success -> AUDIO_FAILED, preserve Transcript Link
    Idempotency: AUDIO_DONE+valid link is skipped (handled by fetch, also checked here).

    Only returns AUDIO_DONE after transcript + audio + sheet all succeed.
    """
    row_num = row["row_num"]
    record = row.get("record", {})
    orig_status = str(record.get("Status", "")).strip()
    status_upper = orig_status.strip().upper()
    audio_link_existing = str(record.get("Audio Link", "")).strip()

    # Idempotency check (also in fetch, but double-check for direct calls)
    if status_upper == "AUDIO_DONE" and _is_valid_audio_link(audio_link_existing):
        log.info("Row %d already AUDIO_DONE with valid Audio Link, skipping (pipeline)", row_num)
        return {"row_num": row_num, "status": "AUDIO_DONE", "skipped": True, "audio_link": audio_link_existing, "valid": True}

    # Podcast Job ID: ensure allocated before processing (centralized, sequential 4-digit)
    # Preserve existing valid ID, allocate next sequential if empty/invalid, never reassign on retry
    yt_for_id = str(record.get("YouTube Link", "")).strip() or str(row.get("youtube_link", row.get("url", ""))).strip()
    current_podcast_id = str(record.get("ID", "")).strip() or str(row.get("id", "")).strip()
    if not _is_valid_podcast_id(current_podcast_id) and yt_for_id:
        # Allocate next sequential ID (MAX+1) and persist (unless dry_run)
        allocated = _ensure_podcast_id(ws, row, dry_run=dry_run)
        record["ID"] = allocated
        row["id"] = allocated
        row["record"] = record
        log.info("Row %d allocated Podcast ID %s for YouTube %r", row_num, allocated, yt_for_id[:40])
    elif _is_valid_podcast_id(current_podcast_id):
        # Normalize to padded for internal use but preserve sheet value
        try:
            padded = f"{int(current_podcast_id):04d}"
            # Keep record as padded for downstream filename generation (sheet retains original if not padded, but we use padded internally)
            record["ID"] = padded
            row["id"] = padded
            row["record"] = record
        except:
            pass

    # Dry-run: report what would happen, no external calls
    if dry_run:
        # Determine would-be transition
        if status_upper in ("NEW", "TEST_OK"):
            would = "NEW -> TRANSCRIPT_DONE -> AUDIO_DONE"
        elif status_upper in ("TRANSCRIPT_DONE", "AUDIO_FAILED"):
            would = f"{status_upper} -> AUDIO_DONE"
        else:
            would = f"{status_upper} -> UNKNOWN"
        log.info("[DRY-RUN] Row %d (%s) would -> %s", row_num, orig_status, would)
        return {"row_num": row_num, "status": status_upper, "would_status": would, "dry_run": True, "valid": True}

    # Stage 1: Transcript if needed (NEW / TEST_OK)
    # Reuse existing transcript logic via process_transcript_row
    # But we need to handle the case where status is already TRANSCRIPT_DONE/AUDIO_FAILED -> skip transcript
    transcript_done = False
    if status_upper in ("NEW", "TEST_OK"):
        log.info("[Row %d] NEW -> fetching transcript", row_num)
        # Call existing transcript row processing (reuses validation, file persistence, sheet update)
        t_result = process_transcript_row(ws, row, dry_run=False)
        # process_transcript_row already updated sheet and returned result
        if not t_result.get("valid"):
            # Transcript failed -> return TRANSCRIPT_FAILED, do not proceed to audio
            log.info("[Row %d] TRANSCRIPT_FAILED: %s", row_num, t_result.get("error"))
            return t_result  # Already has status TRANSCRIPT_FAILED, error, etc.
        # Transcript succeeded -> update in-memory record for audio stage
        # The sheet now has TRANSCRIPT_DONE, but row dict still has old status; update it
        transcript_done = True
        # Update record to reflect new status for audio stage
        record["Status"] = "TRANSCRIPT_DONE"
        record["Transcript Link"] = t_result.get("transcript_link", "")
        if t_result.get("title"):
            record["Title"] = t_result["title"]
        # Also update row status for next check
        status_upper = "TRANSCRIPT_DONE"
        log.info("[Row %d] TRANSCRIPT_DONE -> generating audio", row_num)
    elif status_upper in ("TRANSCRIPT_DONE", "AUDIO_FAILED"):
        transcript_done = True
        log.info("[Row %d] %s -> generating audio (transcript already done)", row_num, orig_status)
    else:
        # Unexpected status, treat as needing transcript
        log.warning("Row %d unexpected status %s for pipeline, attempting transcript", row_num, orig_status)
        t_result = process_transcript_row(ws, row, dry_run=False)
        if not t_result.get("valid"):
            return t_result
        transcript_done = True
        record["Status"] = "TRANSCRIPT_DONE"
        status_upper = "TRANSCRIPT_DONE"

    if not transcript_done:
        # Should not happen, but safety
        return {"row_num": row_num, "status": "AUDIO_FAILED", "valid": False, "error": "Transcript not done and not attempted", "error_type": "PipelineError"}

    # Stage 2: Audio (refresh row dict to ensure audio pipeline sees correct status/record)
    # Build audio row from updated record
    yt_link = str(record.get("YouTube Link", "")).strip() or row.get("youtube_link", "")
    audio_row = {
        "row_num": row_num,
        "status": status_upper,
        "record": record,
        "url": yt_link,
        "youtube_link": yt_link,
        "id": str(record.get("ID", "")).strip(),
        "title": str(record.get("Title", "")).strip(),
        "audio_link": str(record.get("Audio Link", "")).strip(),
        "transcript_link": str(record.get("Transcript Link", "")).strip(),
    }
    # Reuse existing audio logic (handles TTS, Drive, sheet update, failure isolation, idempotency)
    a_result = process_audio_row(ws, audio_row, dry_run=False)
    return a_result

def run_pipeline(dry_run: bool = False, limit: Optional[int] = None, ws=None) -> Dict:
    """Run full pipeline for NEW rows through to AUDIO_DONE (orchestration).

    - Fetches pending via fetch_pipeline_pending_rows() (NEW/TEST_OK/TRANSCRIPT_DONE/AUDIO_FAILED, skips AUDIO_DONE+valid link)
    - For each, calls process_pipeline_row (dry_run logs without external calls)
    - Returns summary with done/failed/skipped.

    Dry-run performs NO YouTube fetch, NO Gemini, NO TTS, NO Drive, NO sheet writes — only reads sheet.
    Limit applies deterministically to pending list.

    Reuses existing transcript/audio functions, does not duplicate Drive OAuth or Piper logic.
    """
    ws = ws or get_sheet()
    header = ws.row_values(1)
    if header != config.SHEET_HEADER:
        log.warning("Header mismatch for pipeline — continuing without modification. Expected %s", config.SHEET_HEADER)
    pending = fetch_pipeline_pending_rows(ws)
    log.info("Pipeline: %d rows pending (NEW/TRANSCRIPT_DONE/AUDIO_FAILED, skipping AUDIO_DONE+valid link)", len(pending))
    if limit is not None:
        pending = pending[:limit]
        log.info("Limited to first %d rows", limit)
    summary = {
        "header": header,
        "total_pending": len(pending),
        "processed": 0,
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": dry_run,
        "details": [],
    }
    if not pending:
        log.info("No rows to process for pipeline (need NEW/TRANSCRIPT_DONE/AUDIO_FAILED)")
        return summary
    for row in pending:
        try:
            res = process_pipeline_row(ws, row, dry_run=dry_run)
            summary["details"].append(res)
            summary["processed"] += 1
            # Dry-run: use simulated would_status, not actual status
            if res.get("skipped"):
                summary["skipped"] += 1
            elif dry_run:
                would = str(res.get("would_status", ""))
                if "AUDIO_DONE" in would:
                    summary["done"] += 1
                else:
                    # Deterministic dry-run failure (e.g., validation) -> Failed
                    summary["failed"] += 1
            elif res.get("status") == "AUDIO_DONE" and res.get("valid"):
                summary["done"] += 1
            elif res.get("status") == "TRANSCRIPT_FAILED":
                summary["failed"] += 1
            elif res.get("status") == "AUDIO_FAILED":
                summary["failed"] += 1
            else:
                summary["failed"] += 1
        except Exception as e:
            log.exception("Unexpected error processing pipeline row %d: %s", row["row_num"], e)
            summary["details"].append({"row_num": row["row_num"], "valid": False, "error": str(e), "error_type": "Unexpected", "status": "AUDIO_FAILED"})
            summary["failed"] += 1
            summary["processed"] += 1
    log.info("Pipeline complete: %d done, %d failed, %d skipped (dry_run=%s)", summary["done"], summary["failed"], summary["skipped"], dry_run)
    return summary

def update_row(ws, row_num: int, status: str = "", drive_link: str = "", title: str = "", updated_at: str = "", error: str = "", **kwargs):
    """Update row by exact 12-column header names.

    Only fields provided are updated. For sheet connection test, only Status + Updated At are set.
    Backward compat: drive_link/title still work via legacy mapping but resolved to exact columns.
    """
    # Resolve column indices from exact header
    if status:
        ws.update_cell(row_num, _col_index("Status"), status)
    # Legacy/future mapping: keep exact columns - do not use Drive Link etc. if header is 12-col
    # drive_link -> Audio Link (if provided), title -> Title
    if drive_link:
        # Prefer Audio Link for pipeline; fallback to column if exists
        try:
            ws.update_cell(row_num, _col_index("Audio Link"), drive_link)
        except ValueError:
            pass
    if title:
        ws.update_cell(row_num, _col_index("Title"), title)
    if error:
        ws.update_cell(row_num, _col_index("Error"), error)
    if updated_at:
        ws.update_cell(row_num, _col_index("Updated At"), updated_at)
    # Explicit kwargs for exact columns (e.g., transcript_link, telugu_script_link)
    for key, col_name in [
        ("transcript_link", "Transcript Link"),
        ("telugu_script_link", "Telugu Script Link"),
        ("audio_link", "Audio Link"),
        ("language", "Language"),
        ("duration", "Duration"),
        ("created_at", "Created At"),
    ]:
        if key in kwargs and kwargs[key]:
            try:
                ws.update_cell(row_num, _col_index(col_name), kwargs[key])
            except ValueError:
                pass
    log.info("Sheet row %d -> Status=%s | Updated At=%s", row_num, status, updated_at)


def test_sheet_connection(dry_run: bool = False) -> dict:
    """Test helper: find rows where Status=NEW, read YouTube Link, update only Status->TEST_OK + Updated At.
    
    Keeps all 12 columns intact; does NOT rename headers or modify other fields.
    Returns summary dict for logging/assertion.
    """
    from datetime import datetime, timezone, timedelta
    ws = get_sheet()
    header = ws.row_values(1)
    log.info("Connected to sheet: '%s' header=%s", config.SHEET_NAME, header)
    # Validate exact 12-col schema but do not modify
    if header != config.SHEET_HEADER:
        log.warning("Header does not match exact 12-column schema - continuing without modification. Expected %s", config.SHEET_HEADER)

    # Read current rows
    all_records = ws.get_all_records()
    log.info("Total data rows: %d", len(all_records))

    pending = fetch_pending_rows(ws)
    log.info("Rows with Status=NEW: %d", len(pending))
    for r in pending:
        log.info("  Row %d: Status=NEW YouTube Link=%s", r["row_num"], r.get("youtube_link", r.get("url","")))

    if dry_run:
        return {"header": header, "total_rows": len(all_records), "new_rows": len(pending), "updated": 0, "dry_run": True}

    # IST timestamp
    ist = timezone(timedelta(hours=5, minutes=30))
    timestamp = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST")

    updated = 0
    for row in pending:
        row_num = row["row_num"]
        # Only update Status and Updated At per spec
        update_row(ws, row_num, status="TEST_OK", updated_at=timestamp)
        updated += 1
        log.info("Row %d: NEW -> TEST_OK @ %s (YouTube Link=%s)", row_num, timestamp, row.get("youtube_link",""))

    return {"header": header, "total_rows": len(all_records), "new_rows": len(pending), "updated": updated, "timestamp": timestamp}

def poll_loop(callback, interval: int = 60, once: bool = False):
    """Poll sheet and invoke callback(row) for each pending row."""
    log.info("Starting poll loop interval=%ds once=%s", interval, once)
    while True:
        try:
            ws = get_sheet()
            pending = fetch_pending_rows(ws)
            if pending:
                log.info("Found %d pending row(s)", len(pending))
                for row in pending:
                    try:
                        update_row(ws, row["row_num"], status="PROCESSING")
                        callback(row, ws)
                    except Exception as e:
                        log.exception("Failed row %d: %s", row["row_num"], e)
                        try:
                            update_row(ws, row["row_num"], status=f"ERROR: {e}"[:100])
                        except Exception:
                            pass
            else:
                log.debug("No pending rows")
        except Exception as e:
            log.exception("Poll error: %s", e)

        if once:
            break
        time.sleep(interval)
