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
      - On valid: saves to output/transcripts/<video_id>.{txt,json}, then
        updates sheet Transcript Link + Title + Status=TRANSCRIPT_DONE + clear Error + Updated At
      - On invalid/empty/fetch failure: does NOT write Transcript Link; sets
        Status=TRANSCRIPT_FAILED + Error (truncated) + Updated At
      - On dry_run: does everything except ws.update_cell (logs what would happen)

    Returns result dict with row_num, status, valid, error etc.
    """
    from src.transcript import fetch_and_save_transcript
    row_num = row["row_num"]
    yt_link = row.get("youtube_link", row.get("url", ""))
    orig_status = row.get("status", "")
    log.info("Row %d (Status=%s) -> YouTube Link=%r", row_num, orig_status, yt_link)

    result = fetch_and_save_transcript(yt_link)
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
