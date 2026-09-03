"""Upload MP3 to Google Drive and return shareable link."""
import logging
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

def _get_drive_service():
    from google.oauth2.service_account import Credentials
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    cred_path = config.GOOGLE_CREDENTIALS_PATH
    token_path = config.GOOGLE_TOKEN_PATH

    creds = None
    # Detect credential type
    if cred_path.exists():
        text = cred_path.read_text(encoding="utf-8")
        if '"service_account"' in text:
            creds = Credentials.from_service_account_file(str(cred_path), scopes=config.SCOPES)
            log.info("Drive: using service account")
        elif '"installed"' in text or '"client_id"' in text:
            # OAuth flow - reuse sheet_monitor logic
            from src.sheet_monitor import _load_credentials
            creds = _load_credentials()
        else:
            # Try service account anyway
            creds = Credentials.from_service_account_file(str(cred_path), scopes=config.SCOPES)
    elif token_path.exists():
        creds = OAuthCredentials.from_authorized_user_file(str(token_path), config.SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    else:
        raise FileNotFoundError(f"No Google credentials at {cred_path} or {token_path}")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return service

def upload_to_drive(file_path: Path, folder_id: Optional[str] = None, make_public: bool = True) -> str:
    """
    Upload file to Drive. Returns shareable link.
    If folder_id is None, uses DRIVE_FOLDER_ID from config or root.
    """
    from googleapiclient.http import MediaFileUpload

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    service = _get_drive_service()
    folder_id = folder_id or config.DRIVE_FOLDER_ID

    file_metadata = {"name": file_path.name}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(file_path), mimetype="audio/mpeg", resumable=True)

    log.info("Uploading %s to Drive (folder=%s)...", file_path.name, folder_id or "root")
    uploaded = service.files().create(
        body=file_metadata, media_body=media, fields="id, webViewLink, webContentLink"
    ).execute()

    file_id = uploaded.get("id")
    link = uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"

    # Make shareable (anyone with link can view)
    if make_public:
        try:
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
            ).execute()
            log.info("Made file public: %s", file_id)
        except Exception as e:
            log.warning("Could not make file public (share Drive folder with service account): %s", e)
            # Still return link - owner can view
            link = f"https://drive.google.com/file/d/{file_id}/view"

    log.info("Drive upload done: %s", link)
    return link

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
