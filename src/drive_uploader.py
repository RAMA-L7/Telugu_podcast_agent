"""Provider-agnostic Google Drive upload for podcast MP3s.

- Reuses existing service-account integration (config.GOOGLE_CREDENTIALS_PATH / SCOPES)
- Destination folder configurable via .env DRIVE_OUTPUT_FOLDER_ID / DRIVE_FOLDER_ID or param
- Returns file ID + usable Drive URL (dict) — secure: do NOT make public by default
- Clear errors: DriveAuthError, DriveUploadError, FileNotFoundError
- Does NOT integrate with Sheets yet (Phase 4 Milestone 1 isolated)
"""
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import config

log = logging.getLogger(__name__)

# --- Explicit errors ---
class DriveError(Exception):
    """Base Drive error."""

class DriveAuthError(DriveError):
    """Authentication / credentials failure."""

class DriveUploadError(DriveError):
    """Upload failure."""

def _get_drive_service():
    """Legacy: reuse existing Google service-account/OAuth integration (for Sheets).

    Kept unchanged for Sheets/service-account. For personal Gmail Drive uploads,
    use _get_drive_oauth_service() which uses credentials/drive_oauth_client.json.
    """
    from google.oauth2.service_account import Credentials
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    cred_path = config.GOOGLE_CREDENTIALS_PATH
    token_path = config.GOOGLE_TOKEN_PATH

    creds = None
    try:
        if cred_path.exists():
            text = cred_path.read_text(encoding="utf-8")
            if '"service_account"' in text:
                creds = Credentials.from_service_account_file(str(cred_path), scopes=config.SCOPES)
                log.info("Drive (legacy): using service account %s", cred_path)
            elif '"installed"' in text or '"client_id"' in text:
                from src.sheet_monitor import _load_credentials
                creds = _load_credentials()
                log.info("Drive (legacy): using OAuth via sheet_monitor")
            else:
                creds = Credentials.from_service_account_file(str(cred_path), scopes=config.SCOPES)
        elif token_path.exists():
            creds = OAuthCredentials.from_authorized_user_file(str(token_path), config.SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            log.info("Drive (legacy): using OAuth token %s", token_path)
        else:
            raise DriveAuthError(f"No Google credentials at {cred_path} or {token_path}. Set GOOGLE_CREDENTIALS_FILE in .env")
    except DriveAuthError:
        raise
    except FileNotFoundError as e:
        raise DriveAuthError(str(e)) from e
    except Exception as e:
        raise DriveAuthError(f"Failed to load Google credentials: {e}") from e

    if creds is None:
        raise DriveAuthError(f"No valid credentials at {cred_path} or {token_path}")

    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return service
    except Exception as e:
        raise DriveAuthError(f"Failed to build Drive service: {e}") from e

def _get_drive_oauth_service():
    """Drive OAuth for personal Gmail My Drive (separate from Sheets service-account).

    Uses credentials/drive_oauth_client.json (OAuth client ID Desktop, installed) and
    credentials/drive_oauth_token.json (generated token, gitignored).

    Flow: if token exists and valid → use/refresh; else use client JSON → InstalledAppFlow
          → run_local_server(port=0) → save token.

    Returns:
        googleapiclient Drive service authenticated as personal Gmail

    Raises:
        DriveAuthError: if client JSON missing or flow fails
    """
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    # Prefer Drive-specific OAuth config, fallback to legacy for backward compat
    client_path = getattr(config, "GOOGLE_DRIVE_OAUTH_CLIENT_FILE", None) or getattr(config, "DRIVE_OAUTH_CLIENT_FILE", None)
    token_path = getattr(config, "GOOGLE_DRIVE_OAUTH_TOKEN_FILE", None) or getattr(config, "DRIVE_OAUTH_TOKEN_FILE", None)
    # Fallback to defaults if config missing (should not happen)
    if client_path is None:
        client_path = Path("credentials/drive_oauth_client.json")
    if token_path is None:
        token_path = Path("credentials/drive_oauth_token.json")

    # Ensure paths are Path objects
    client_path = Path(client_path)
    token_path = Path(token_path)

    # Try existing token first (no client needed if token valid)
    if token_path.exists():
        try:
            creds = OAuthCredentials.from_authorized_user_file(str(token_path), config.SCOPES)
            if creds and creds.valid:
                log.info("Drive OAuth: using existing token %s", token_path)
                return build("drive", "v3", credentials=creds, cache_discovery=False)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
                log.info("Drive OAuth: refreshed token %s", token_path)
                return build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as e:
            log.warning("Drive OAuth token invalid at %s: %s — will try client flow", token_path, e)

    # Need client JSON for new auth
    if not client_path.exists():
        raise DriveAuthError(
            f"No Drive OAuth client at {client_path}. "
            f"Create OAuth client ID (Desktop app) in Google Cloud Console (APIs & Services → Credentials), "
            f"download JSON, save as {client_path} (gitignored). "
            f"Sheets service-account at {config.GOOGLE_CREDENTIALS_PATH} remains unchanged."
        )

    try:
        # Verify client JSON looks like OAuth (installed/web), not service_account
        text = client_path.read_text(encoding="utf-8")
        if '"service_account"' in text:
            raise DriveAuthError(
                f"Drive OAuth client at {client_path} appears to be a service-account JSON, not OAuth client. "
                f"Create OAuth client ID (Desktop) for Drive, download JSON, save as {client_path}. "
                f"Keep Sheets service-account at {config.GOOGLE_CREDENTIALS_PATH} separate."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), config.SCOPES)
        creds = flow.run_local_server(port=0)
        # Save token for next run (gitignored)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        log.info("Drive OAuth: new token saved to %s (personal Gmail)", token_path)
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except DriveAuthError:
        raise
    except Exception as e:
        raise DriveAuthError(f"Drive OAuth flow failed (client {client_path}): {e}") from e

def _resolve_folder_id(folder_id: Optional[str]) -> Optional[str]:
    """Resolve destination folder: param > DRIVE_OUTPUT_FOLDER_ID > DRIVE_FOLDER_ID > None (root)."""
    if folder_id and folder_id.strip():
        return folder_id.strip()
    # Prefer DRIVE_OUTPUT_FOLDER_ID, fallback to DRIVE_FOLDER_ID (config handles alias)
    cfg_folder = (config.DRIVE_OUTPUT_FOLDER_ID or config.DRIVE_FOLDER_ID or "")
    cfg_folder = cfg_folder.strip() if isinstance(cfg_folder, str) else ""
    return cfg_folder or None

def upload_to_drive(
    file_path: Path,
    folder_id: Optional[str] = None,
    make_public: bool = False,
) -> Dict[str, Any]:
    """
    Upload local file to Google Drive (provider-agnostic, secure).

    Args:
        file_path: local MP3/WAV path
        folder_id: Drive folder ID (optional) — if None, uses .env DRIVE_OUTPUT_FOLDER_ID / DRIVE_FOLDER_ID or root
        make_public: if True, sets anyone-reader permission; default False (secure, preserve default permissions)

    Returns:
        dict with fileId and usable Drive URL:
        {"fileId": "...", "webViewLink": "https://drive.google.com/file/d/.../view", "webContentLink": "..."}

    For backward compat, also supports string-like access: result["webViewLink"] or result.get("id")

    Raises:
        FileNotFoundError: if file_path does not exist
        DriveAuthError: if credentials missing/invalid
        DriveUploadError: if upload fails
    """
    from googleapiclient.http import MediaFileUpload

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise FileNotFoundError(f"Not a file: {file_path}")

    # Use Drive OAuth (personal Gmail My Drive), not Sheets service-account (which has 0 quota)
    service = _get_drive_oauth_service()
    resolved_folder = _resolve_folder_id(folder_id)

    file_metadata: Dict[str, Any] = {"name": file_path.name}
    if resolved_folder:
        file_metadata["parents"] = [resolved_folder]

    # mimetype for MP3, fallback to octet-stream for other
    mimetype = "audio/mpeg" if file_path.suffix.lower() in (".mp3", ".mpeg") else "application/octet-stream"
    media = MediaFileUpload(str(file_path), mimetype=mimetype, resumable=True)

    log.info("Uploading %s to Drive (folder=%s, make_public=%s)...", file_path.name, resolved_folder or "root (My Drive)", make_public)

    def _do_upload():
        return service.files().create(
            body=file_metadata, media_body=media, fields="id, webViewLink, webContentLink"
        ).execute()

    # Retry transient Drive/network failures with exponential backoff (not permanent errors like FileNotFound)
    try:
        from src.retry_utils import retry_operation
        uploaded = retry_operation(_do_upload, operation_name=f"Drive upload {file_path.name}")
    except Exception as e:
        # retry_operation already handled transient retries; now it's a permanent or exhausted transient
        raise DriveUploadError(f"Drive upload failed for {file_path.name}: {e}") from e

    file_id = uploaded.get("id")
    if not file_id:
        raise DriveUploadError(f"Drive upload returned no file ID: {uploaded}")

    web_view = uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
    web_content = uploaded.get("webContentLink") or f"https://drive.google.com/uc?id={file_id}"

    # Only make public if explicitly requested — secure default is private
    if make_public:
        try:
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
            ).execute()
            log.info("Made file public (anyone reader): %s", file_id)
        except Exception as e:
            log.warning("Could not make file public (share Drive folder with service account or check Drive API): %s", e)

    result: Dict[str, Any] = {
        "fileId": file_id,
        "id": file_id,  # alias for convenience
        "webViewLink": web_view,
        "webContentLink": web_content,
        "link": web_view,  # alias
    }
    log.info("Drive upload done: id=%s link=%s (folder=%s)", file_id, web_view, resolved_folder or "root")
    return result

# Backward-compat helper: returns string link only (for callers expecting string)
def upload_to_drive_link(file_path: Path, folder_id: Optional[str] = None, make_public: bool = False) -> str:
    """Upload and return link string only (backward compat)."""
    res = upload_to_drive(file_path, folder_id=folder_id, make_public=make_public)
    return res["webViewLink"]

def upload_bytes_to_drive(content: bytes, filename: str, folder_id: Optional[str] = None) -> str:
    """Upload from bytes (alternative)."""
    import tempfile
    tmp = Path(tempfile.gettempdir()) / filename
    tmp.write_bytes(content)
    try:
        return upload_to_drive(tmp, folder_id=folder_id)
    finally:
        try: tmp.unlink()
        except: pass
