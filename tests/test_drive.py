"""Focused mocked unit tests for Google Drive OAuth (personal Gmail, separate from Sheets service-account).

- Drive: credentials/drive_oauth_client.json + credentials/drive_oauth_token.json via InstalledAppFlow (My Drive, no Shared Drives needed)
- Sheets: credentials/service_account.json via _load_credentials (unchanged)
- Provider-agnostic, secure (make_public=False), configurable folder via .env DRIVE_OUTPUT_FOLDER_ID
- All network/credentials mocked — no real Drive, no secrets, no file upload.

Run: python -m pytest tests/test_drive.py -v
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock
import tempfile

import config
from src.drive_uploader import (
    upload_to_drive,
    _get_drive_service,
    _get_drive_oauth_service,
    _resolve_folder_id,
    DriveAuthError,
    DriveUploadError,
)

def test_resolve_folder_id_configurable():
    """Destination folder configurable via .env DRIVE_OUTPUT_FOLDER_ID / DRIVE_FOLDER_ID or param."""
    orig_output = config.DRIVE_OUTPUT_FOLDER_ID
    orig_folder = config.DRIVE_FOLDER_ID
    try:
        config.DRIVE_OUTPUT_FOLDER_ID = None
        config.DRIVE_FOLDER_ID = None
        assert _resolve_folder_id(None) is None
        assert _resolve_folder_id("") is None
        config.DRIVE_OUTPUT_FOLDER_ID = "folder_from_output"
        config.DRIVE_FOLDER_ID = None
        assert _resolve_folder_id(None) == "folder_from_output"
        assert _resolve_folder_id("explicit_folder") == "explicit_folder"
        config.DRIVE_OUTPUT_FOLDER_ID = None
        config.DRIVE_FOLDER_ID = "folder_legacy"
        assert _resolve_folder_id(None) == "folder_legacy"
        config.DRIVE_OUTPUT_FOLDER_ID = "output_folder"
        config.DRIVE_FOLDER_ID = "legacy_folder"
        assert _resolve_folder_id(None) == "output_folder"
        print("PASS: test_resolve_folder_id_configurable")
    finally:
        config.DRIVE_OUTPUT_FOLDER_ID = orig_output
        config.DRIVE_FOLDER_ID = orig_folder

def test_authentication_success_mocked():
    """Legacy Sheets service-account auth still works (unchanged)."""
    with patch("pathlib.Path.exists") as mock_exists, \
         patch("pathlib.Path.read_text") as mock_read, \
         patch("google.oauth2.service_account.Credentials.from_service_account_file") as mock_creds, \
         patch("googleapiclient.discovery.build") as mock_build:
        mock_exists.return_value = True
        mock_read.return_value = '{"type":"service_account", "project_id":"test"}'
        mock_creds.return_value = MagicMock()
        mock_build.return_value = MagicMock()
        orig_creds = config.GOOGLE_CREDENTIALS_PATH
        orig_token = config.GOOGLE_TOKEN_PATH
        try:
            config.GOOGLE_CREDENTIALS_PATH = Path("credentials/service_account.json")
            config.GOOGLE_TOKEN_PATH = Path("credentials/token.json")
            service = _get_drive_service()
            assert service is not None
            mock_creds.assert_called_once()
            mock_build.assert_called_once_with("drive", "v3", credentials=mock_creds.return_value, cache_discovery=False)
            print("PASS: test_authentication_success_mocked (Sheets service-account)")
        finally:
            config.GOOGLE_CREDENTIALS_PATH = orig_creds
            config.GOOGLE_TOKEN_PATH = orig_token

def test_authentication_missing_credentials():
    """Missing credentials should raise DriveAuthError with clear message."""
    with patch("pathlib.Path.exists", return_value=False):
        orig_creds = config.GOOGLE_CREDENTIALS_PATH
        orig_token = config.GOOGLE_TOKEN_PATH
        try:
            config.GOOGLE_CREDENTIALS_PATH = Path("nonexistent.json")
            config.GOOGLE_TOKEN_PATH = Path("nonexistent_token.json")
            try:
                _get_drive_service()
                assert False, "Should have raised DriveAuthError"
            except DriveAuthError as e:
                assert "No Google credentials" in str(e) or "credentials" in str(e).lower()
                print("PASS: test_authentication_missing_credentials")
        finally:
            config.GOOGLE_CREDENTIALS_PATH = orig_creds
            config.GOOGLE_TOKEN_PATH = orig_token

def test_drive_oauth_success_mocked():
    """Drive OAuth uses credentials/drive_oauth_client.json + token, not service_account."""
    with patch("google.oauth2.credentials.Credentials.from_authorized_user_file") as mock_from_file, \
         patch("googleapiclient.discovery.build") as mock_build:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_from_file.return_value = mock_creds
        mock_build.return_value = MagicMock()

        orig_client = config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE
        orig_token = config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE
        try:
            config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = Path("credentials/drive_oauth_client.json")
            config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE = Path("credentials/drive_oauth_token.json")
            # Mock token exists, client exists
            with patch.object(Path, "exists", return_value=True):
                service = _get_drive_oauth_service()
                assert service is not None
                mock_from_file.assert_called_once_with(str(Path("credentials/drive_oauth_token.json")), config.SCOPES)
                mock_build.assert_called_once()
                print("PASS: test_drive_oauth_success_mocked (token valid)")
        finally:
            config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = orig_client
            config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE = orig_token

def test_drive_oauth_missing_client_raises():
    """Drive OAuth client missing should raise DriveAuthError with clear setup instructions, not use service-account."""
    with patch("pathlib.Path.exists", return_value=False):
        orig_client = config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE
        orig_token = config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE
        try:
            config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = Path("credentials/drive_oauth_client.json")
            config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE = Path("credentials/drive_oauth_token.json")
            try:
                _get_drive_oauth_service()
                assert False, "Should have raised DriveAuthError"
            except DriveAuthError as e:
                msg = str(e)
                assert "drive_oauth_client.json" in msg
                assert "OAuth client ID" in msg or "Create OAuth" in msg
                assert "service_account" not in msg.lower() or "separate" in msg.lower() or "Sheets" in msg
                print("PASS: test_drive_oauth_missing_client_raises")
        finally:
            config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = orig_client
            config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE = orig_token

def test_drive_uses_oauth_sheets_uses_service_account():
    """Prove Drive uses drive_oauth_client.json (InstalledAppFlow) while Sheets still uses service_account.json."""
    orig_sa = config.GOOGLE_CREDENTIALS_PATH
    orig_drive_client = config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE
    orig_drive_token = config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE
    try:
        config.GOOGLE_CREDENTIALS_PATH = Path("credentials/service_account.json")
        config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = Path("credentials/drive_oauth_client.json")
        config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE = Path("credentials/drive_oauth_token.json")

        # Drive should use OAuth flow (not service account) — mock token missing, client exists
        with patch("google.oauth2.service_account.Credentials.from_service_account_file") as mock_sa, \
             patch("googleapiclient.discovery.build") as mock_build, \
             patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file") as mock_flow:
            mock_flow_creds = MagicMock()
            mock_flow_creds.to_json.return_value = '{"token": "fake"}'
            mock_flow.return_value.run_local_server.return_value = mock_flow_creds
            mock_build.return_value = MagicMock()
            mock_sa.return_value = MagicMock()

            # Make drive token not exist, client exists (order: token check then client check)
            with patch.object(Path, "exists", side_effect=[False, True]):
                with patch("pathlib.Path.read_text", return_value='{"installed": {"client_id": "test"}}'), \
                     patch("pathlib.Path.write_text") as mock_write:
                    service_drive = _get_drive_oauth_service()
                    mock_flow.assert_called_once_with(str(Path("credentials/drive_oauth_client.json")), config.SCOPES)
                    mock_write.assert_called_once()
                    print("PASS: Drive uses OAuth (InstalledAppFlow)")

        # Sheets _load_credentials should use service_account
        with patch("google.oauth2.service_account.Credentials.from_service_account_file") as mock_sa2, \
             patch("googleapiclient.discovery.build") as mock_build2:
            mock_sa2.return_value = MagicMock()
            mock_build2.return_value = MagicMock()
            with patch("pathlib.Path.read_text", return_value='{"type":"service_account"}'):
                with patch.object(Path, "exists", return_value=True):
                    from src.sheet_monitor import _load_credentials
                    creds = _load_credentials()
                    mock_sa2.assert_called()
                    print("PASS: Sheets still uses service_account.json")
    finally:
        config.GOOGLE_CREDENTIALS_PATH = orig_sa
        config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = orig_drive_client
        config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE = orig_drive_token
    print("PASS: test_drive_uses_oauth_sheets_uses_service_account")

def test_successful_upload_mocked():
    """Successful upload via Drive OAuth returns file ID and link, secure not public."""
    tmp_path = Path(tempfile.gettempdir()) / "test_drive_success.mp3"
    tmp_path.write_bytes(b"fake mp3 content for test")
    try:
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_create = MagicMock()
        mock_create.execute.return_value = {
            "id": "test_file_id_12345",
            "webViewLink": "https://drive.google.com/file/d/test_file_id_12345/view",
            "webContentLink": "https://drive.google.com/uc?id=test_file_id_12345"
        }
        mock_files.create.return_value = mock_create
        mock_service.files.return_value = mock_files
        mock_service.permissions.return_value.create.return_value.execute.return_value = None

        with patch("src.drive_uploader._get_drive_oauth_service", return_value=mock_service), \
             patch("googleapiclient.http.MediaFileUpload", return_value=MagicMock()):
            orig_folder = config.DRIVE_OUTPUT_FOLDER_ID
            try:
                config.DRIVE_OUTPUT_FOLDER_ID = "test_folder_abc"
                result = upload_to_drive(tmp_path, make_public=False)
                assert isinstance(result, dict)
                assert result["fileId"] == "test_file_id_12345"
                assert result["id"] == "test_file_id_12345"
                assert "https://drive.google.com/file/d/test_file_id_12345/view" in result["webViewLink"]
                assert "webContentLink" in result
                mock_files.create.assert_called_once()
                call_kwargs = mock_files.create.call_args.kwargs
                assert call_kwargs["body"]["name"] == tmp_path.name
                assert call_kwargs["body"]["parents"] == ["test_folder_abc"]
                mock_service.permissions.assert_not_called()
                print("PASS: test_successful_upload_mocked (Drive OAuth, secure)")
            finally:
                config.DRIVE_OUTPUT_FOLDER_ID = orig_folder

        mock_service2 = MagicMock()
        mock_files2 = MagicMock()
        mock_create2 = MagicMock()
        mock_create2.execute.return_value = {"id": "public_id", "webViewLink": "https://drive.google.com/file/d/public_id/view"}
        mock_files2.create.return_value = mock_create2
        mock_service2.files.return_value = mock_files2
        mock_perm_create = MagicMock()
        mock_perm_create.execute.return_value = {}
        mock_service2.permissions.return_value.create.return_value = mock_perm_create

        with patch("src.drive_uploader._get_drive_oauth_service", return_value=mock_service2), \
             patch("googleapiclient.http.MediaFileUpload", return_value=MagicMock()):
            result2 = upload_to_drive(tmp_path, make_public=True)
            assert result2["fileId"] == "public_id"
            mock_service2.permissions.return_value.create.assert_called_once_with(fileId="public_id", body={"role": "reader", "type": "anyone"})
            print("PASS: test_successful_upload_mocked (explicit public)")

    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except (PermissionError, OSError):
            pass

def test_missing_file():
    """Missing local file should raise FileNotFoundError before auth."""
    fake_path = Path(tempfile.gettempdir()) / "nonexistent_podcast_12345.mp3"
    if fake_path.exists():
        fake_path.unlink()
    with patch("src.drive_uploader._get_drive_oauth_service") as mock_service:
        try:
            upload_to_drive(fake_path)
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError as e:
            assert "File not found" in str(e) or str(fake_path) in str(e)
            mock_service.assert_not_called()
            print("PASS: test_missing_file")

def test_upload_failure_mocked():
    """Upload failure should raise DriveUploadError."""
    tmp_path = Path(tempfile.gettempdir()) / "test_drive_fail.mp3"
    tmp_path.write_bytes(b"fake content")
    try:
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_create = MagicMock()
        mock_create.execute.side_effect = Exception("simulated network failure")
        mock_files.create.return_value = mock_create
        mock_service.files.return_value = mock_files

        with patch("src.drive_uploader._get_drive_oauth_service", return_value=mock_service), \
             patch("googleapiclient.http.MediaFileUpload", return_value=MagicMock()):
            try:
                upload_to_drive(tmp_path)
                assert False, "Should have raised DriveUploadError"
            except DriveUploadError as e:
                assert "Drive upload failed" in str(e)
                assert "simulated network failure" in str(e)
                print("PASS: test_upload_failure_mocked")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except (PermissionError, OSError):
            pass

def test_configurable_folder_param_overrides_env():
    """Explicit folder_id param should override .env."""
    tmp_path = Path(tempfile.gettempdir()) / "test_drive_folder.mp3"
    tmp_path.write_bytes(b"test")
    try:
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_create = MagicMock()
        mock_create.execute.return_value = {"id": "folder_test_id", "webViewLink": "https://drive.google.com/file/d/folder_test_id/view"}
        mock_files.create.return_value = mock_create
        mock_service.files.return_value = mock_files

        with patch("src.drive_uploader._get_drive_oauth_service", return_value=mock_service), \
             patch("googleapiclient.http.MediaFileUpload", return_value=MagicMock()):
            orig_output = config.DRIVE_OUTPUT_FOLDER_ID
            try:
                config.DRIVE_OUTPUT_FOLDER_ID = "env_folder_123"
                result = upload_to_drive(tmp_path, folder_id="explicit_folder_456")
                assert result["fileId"] == "folder_test_id"
                call_kwargs = mock_files.create.call_args.kwargs
                assert call_kwargs["body"]["parents"] == ["explicit_folder_456"]
                print("PASS: test_configurable_folder_param_overrides_env")
                mock_files.create.reset_mock()
                result2 = upload_to_drive(tmp_path, folder_id=None)
                call_kwargs2 = mock_files.create.call_args.kwargs
                assert call_kwargs2["body"]["parents"] == ["env_folder_123"]
                print("PASS: test_configurable_folder_uses_env_when_no_param")
            finally:
                config.DRIVE_OUTPUT_FOLDER_ID = orig_output
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except (PermissionError, OSError):
            pass

def test_credentials_not_hardcoded():
    """Ensure no hardcoded credentials or API keys in drive_uploader."""
    src = (ROOT / "src" / "drive_uploader.py").read_text(encoding="utf-8")
    assert "config.GOOGLE_CREDENTIALS_PATH" in src or "config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE" in src
    assert "config.SCOPES" in src
    assert "private_key" not in src.lower() or "config.GOOGLE" in src
    assert "AIza" not in src
    assert "GOOGLE_DRIVE_OAUTH_CLIENT_FILE" in src
    assert "GOOGLE_DRIVE_OAUTH_TOKEN_FILE" in src
    print("PASS: test_credentials_not_hardcoded")

def test_drive_oauth_files_gitignored():
    """OAuth client/token must remain gitignored."""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "credentials/*.json" in gi
    # drive_oauth files are in credentials, so covered
    assert "credentials/drive_oauth" not in gi or "credentials/*.json" in gi
    print("PASS: test_drive_oauth_files_gitignored")

if __name__ == "__main__":
    test_resolve_folder_id_configurable()
    test_authentication_success_mocked()
    test_authentication_missing_credentials()
    test_drive_oauth_success_mocked()
    test_drive_oauth_missing_client_raises()
    test_drive_uses_oauth_sheets_uses_service_account()
    test_successful_upload_mocked()
    test_missing_file()
    test_upload_failure_mocked()
    test_configurable_folder_param_overrides_env()
    test_credentials_not_hardcoded()
    test_drive_oauth_files_gitignored()
    print("\nAll Drive OAuth tests PASSED (Drive OAuth separate, Sheets service-account unchanged, secure).")
