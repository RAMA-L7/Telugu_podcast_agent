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
