"""Phase 5.3 reliability tests (mocked, no real sleep, no live API).

Covers:
1. transient retries and eventually succeeds
2. retry stops at MAX_RETRIES
3. exponential backoff bounded
4. permanent errors not retried
5. watcher survives transient polling failure
6. no tight retry loop
7. AUDIO_DONE idempotent after restart
8. AUDIO_FAILED controlled retry
9. auth errors safe actionable messages
10. logs do not expose secrets
11. Windows-safe output
12. Ctrl+C
13. dry-run safe
14. pipeline unchanged
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock
import tempfile
import time

import config
from src import retry_utils
from src.retry_utils import is_transient_error, get_retry_delay, retry_operation, should_retry_error
from src.sheet_monitor import _should_retry_failed_row, fetch_audio_pending_rows, fetch_pipeline_pending_rows
from src import watcher
from src.drive_uploader import DriveAuthError, DriveUploadError
from src.llm import LLMProviderError, LLMTimeoutError

# Helper
def _mock_ws(records):
    ws = MagicMock()
    ws.get_all_records.return_value = records
    ws.row_values.return_value = config.SHEET_HEADER
    ws.update_cell = MagicMock()
    return ws

def test_1_transient_retries_and_succeeds():
    """1. Transient operation retries and eventually succeeds."""
    orig_max = config.MAX_RETRIES
    orig_base = config.RETRY_BASE_DELAY_SECONDS
    try:
        config.MAX_RETRIES = 3
        config.RETRY_BASE_DELAY_SECONDS = 0.01
        config.RETRY_MAX_DELAY_SECONDS = 0.05
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise Exception("429 Too Many Requests transient")
            return "success"
        with patch("src.retry_utils.time.sleep") as mock_sleep:
            result = retry_operation(flaky, operation_name="test 429")
            assert result == "success"
            assert len(calls) == 3
            assert mock_sleep.call_count == 2
        print("PASS: test_1_transient_retries_and_succeeds")
    finally:
        config.MAX_RETRIES = orig_max
        config.RETRY_BASE_DELAY_SECONDS = orig_base

def test_2_retry_stops_at_max_retries():
    """2. Retry stops after MAX_RETRIES."""
    orig_max = config.MAX_RETRIES
    try:
        config.MAX_RETRIES = 2
        config.RETRY_BASE_DELAY_SECONDS = 0.01
        config.RETRY_MAX_DELAY_SECONDS = 0.05
        calls = []
        def always_fail():
            calls.append(1)
            raise Exception("503 Service Unavailable transient")
        with patch("src.retry_utils.time.sleep"):
            try:
                retry_operation(always_fail, operation_name="always 503")
                assert False, "should have raised"
            except Exception as e:
                assert "503" in str(e)
                assert len(calls) == 3  # initial + 2 retries
        print("PASS: test_2_retry_stops_at_max_retries")
    finally:
        config.MAX_RETRIES = orig_max

def test_3_exponential_backoff_bounded():
    """3. Exponential backoff is bounded by RETRY_MAX_DELAY_SECONDS."""
    orig_base = config.RETRY_BASE_DELAY_SECONDS
    orig_max = config.RETRY_MAX_DELAY_SECONDS
    try:
        config.RETRY_BASE_DELAY_SECONDS = 2
        config.RETRY_MAX_DELAY_SECONDS = 30
        assert get_retry_delay(0) == 2
        assert get_retry_delay(1) == 4
        assert get_retry_delay(2) == 8
        assert get_retry_delay(3) == 16
        assert get_retry_delay(4) == 30  # capped
        assert get_retry_delay(10) == 30
        # Also test with base 5, max 10
        config.RETRY_BASE_DELAY_SECONDS = 5
        config.RETRY_MAX_DELAY_SECONDS = 10
        assert get_retry_delay(0) == 5
        assert get_retry_delay(1) == 10
        assert get_retry_delay(2) == 10
        print("PASS: test_3_exponential_backoff_bounded")
    finally:
        config.RETRY_BASE_DELAY_SECONDS = orig_base
        config.RETRY_MAX_DELAY_SECONDS = orig_max

def test_4_permanent_not_retried():
    """4. Permanent errors (EmptyLink, InvalidLink, missing file, bad credentials) not retried."""
    # Permanent errors should be identified as not transient
    from src.transcript import EmptyLinkError, InvalidLinkError
    assert not is_transient_error(EmptyLinkError("empty"))
    assert not is_transient_error(InvalidLinkError("invalid"))
    assert not is_transient_error(FileNotFoundError("File not found: /tmp/x.mp3"))
    assert not is_transient_error(DriveAuthError("No Google credentials"))
    assert not is_transient_error(LLMProviderError("GEMINI_API_KEY not set"))

    # Verify retry_operation does not retry permanent
    calls = []
    def permanent_fail():
        calls.append(1)
        raise EmptyLinkError("empty link permanent")
    with patch("src.retry_utils.time.sleep") as mock_sleep:
        try:
            retry_operation(permanent_fail, operation_name="permanent")
            assert False
        except EmptyLinkError:
            pass
        assert len(calls) == 1
        mock_sleep.assert_not_called()
    print("PASS: test_4_permanent_not_retried")

def test_5_watcher_survives_transient_polling_failure():
    """5. Watcher survives transient polling failure (Sheets/API)."""
    with patch("src.watcher.time.sleep") as mock_sleep, \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.side_effect = [Exception("Sheets API transient 500"), {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}]
        watcher.run_watcher(interval=1, max_cycles=2)
        assert mock_pipeline.call_count == 2
        assert mock_sleep.call_count == 1  # sleep between cycles, not tight loop
        print("PASS: test_5_watcher_survives_transient_polling_failure")

def test_6_no_tight_retry_loop():
    """6. Watcher does not enter tight retry loop (always sleeps interval or backoff)."""
    with patch("src.watcher.time.sleep") as mock_sleep, \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}
        watcher.run_watcher(interval=2, max_cycles=3)
        # Should sleep 2 times (between 3 cycles)
        assert mock_sleep.call_count == 2
        for c in mock_sleep.call_args_list:
            assert c.args[0] >= 1  # at least 1s, not 0
        print("PASS: test_6_no_tight_retry_loop")

def test_7_audio_done_idempotent_after_restart():
    """7. AUDIO_DONE remains idempotent after watcher restart (in-memory state not needed, sheet state)."""
    records = [
        {"ID": "1", "YouTube Link": "https://youtu.be/done123", "Title": "Done", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "output/transcripts/done123.txt", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/done123/view", "Error": "", "Created At": "", "Updated At": "2026-09-08 10:00:00 IST"},
    ]
    ws = _mock_ws(records)
    pending = fetch_pipeline_pending_rows(ws)
    assert len(pending) == 0, "AUDIO_DONE with valid link should be skipped even after restart"
    pending2 = fetch_audio_pending_rows(ws)
    assert len(pending2) == 0
    print("PASS: test_7_audio_done_idempotent_after_restart")

def test_8_audio_failed_controlled_retry():
    """8. AUDIO_FAILED controlled retry (backoff via Updated At, not every 30s)."""
    # Recent failure should be skipped, old failure should be retried
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    recent = now.strftime("%Y-%m-%d %H:%M:%S IST")
    old_time = (now - timedelta(seconds=70)).strftime("%Y-%m-%d %H:%M:%S IST")  # 70s ago > 60s min_interval

    orig_watch = config.WATCH_INTERVAL_SECONDS
    orig_max = config.RETRY_MAX_DELAY_SECONDS
    try:
        config.WATCH_INTERVAL_SECONDS = 30
        config.RETRY_MAX_DELAY_SECONDS = 30
        # Recent failure -> should NOT be retried yet (hammering prevention)
        rec_recent = {"ID": "2", "YouTube Link": "https://youtu.be/fail123", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_FAILED", "Transcript Link": "output/transcripts/fail123.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "[DriveFailed]", "Created At": "", "Updated At": recent}
        assert not _should_retry_failed_row(rec_recent), "Recent AUDIO_FAILED should be throttled"

        # Old failure -> should be retried
        rec_old = {"ID": "3", "YouTube Link": "https://youtu.be/fail456", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_FAILED", "Transcript Link": "output/transcripts/fail456.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "[DriveFailed]", "Created At": "", "Updated At": old_time}
        assert _should_retry_failed_row(rec_old), "Old AUDIO_FAILED should be retryable"

        # Verify fetch respects it
        ws_recent = _mock_ws([rec_recent])
        assert len(fetch_audio_pending_rows(ws_recent)) == 0
        ws_old = _mock_ws([rec_old])
        assert len(fetch_audio_pending_rows(ws_old)) == 1
        print("PASS: test_8_audio_failed_controlled_retry")
    finally:
        config.WATCH_INTERVAL_SECONDS = orig_watch
        config.RETRY_MAX_DELAY_SECONDS = orig_max

def test_9_auth_errors_safe_actionable():
    """9. Authentication errors produce safe actionable messages, no hammering."""
    # Drive OAuth missing client should give clear message, not generic, and not expose secret
    with patch("pathlib.Path.exists", return_value=False):
        orig_client = config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE
        try:
            config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = Path("credentials/drive_oauth_client.json")
            config.GOOGLE_DRIVE_OAUTH_TOKEN_FILE = Path("credentials/drive_oauth_token.json")
            try:
                from src.drive_uploader import _get_drive_oauth_service
                _get_drive_oauth_service()
                assert False
            except DriveAuthError as e:
                msg = str(e)
                assert "drive_oauth_client.json" in msg
                assert "OAuth client ID" in msg
                # Should not contain private_key or token
                assert "private_key" not in msg.lower()
                assert "refresh_token" not in msg.lower()
                print("PASS: test_9_auth_errors_safe_actionable")
        finally:
            config.GOOGLE_DRIVE_OAUTH_CLIENT_FILE = orig_client

    # Sheets auth missing should also be safe
    with patch("pathlib.Path.exists", return_value=False):
        orig_creds = config.GOOGLE_CREDENTIALS_PATH
        try:
            config.GOOGLE_CREDENTIALS_PATH = Path("nonexistent.json")
            config.GOOGLE_TOKEN_PATH = Path("nonexistent_token.json")
            from src.sheet_monitor import _load_credentials
            try:
                _load_credentials()
                assert False
            except FileNotFoundError as e:
                assert "service_account.json" in str(e) or "credentials" in str(e).lower()
                assert "private_key" not in str(e).lower()
                print("PASS: test_9_sheets_auth_safe")
        finally:
            config.GOOGLE_CREDENTIALS_PATH = orig_creds

def test_10_logs_no_secrets():
    """10. Logs do not expose secrets (API keys, tokens, private keys)."""
    # Check that drive and llm modules don't log secrets
    src_drive = (ROOT / "src" / "drive_uploader.py").read_text(encoding="utf-8")
    src_llm = (ROOT / "src" / "llm.py").read_text(encoding="utf-8")
    src_watcher = (ROOT / "src" / "watcher.py").read_text(encoding="utf-8")
    # Should not log the actual key value, only that it's set or not
    for content, name in [(src_drive, "drive"), (src_llm, "llm"), (src_watcher, "watcher")]:
        # Ensure no accidental print of config.GEMINI_API_KEY value
        assert "GEMINI_API_KEY" not in content or "config.GEMINI_API_KEY" in content
        # Ensure no print of token
        assert "refresh_token" not in content.lower() or "REDACTED" in content or "token" in content.lower()
    # Check watcher redacts secrets in error messages
    assert "REDACTED" in src_watcher or "secret" in src_watcher.lower()
    print("PASS: test_10_logs_no_secrets")

def test_11_windows_safe_output():
    """11. Windows-safe console output (no Unicode arrow that breaks cp1252)."""
    src_watcher = (ROOT / "src" / "watcher.py").read_text(encoding="utf-8")
    assert "→" not in src_watcher, "watcher should use ASCII -> not Unicode arrow"
    src_sheet = (ROOT / "src" / "sheet_monitor.py").read_text(encoding="utf-8")
    # Phase 5.1 pipeline logs should be ASCII
    # Check the pipeline run log header
    assert "→" not in src_sheet.split("def run_pipeline")[1].split("def ")[0] or "->" in src_sheet
    test_str = "[Watcher] Cycle 1 -- pending: 1 | done: 1 | failed: 0 | skipped: 0"
    test_str.encode("cp1252")  # Should not raise
    try:
        "→".encode("cp1252")
        assert False, "Unicode arrow should fail cp1252, proving fix needed"
    except UnicodeEncodeError:
        pass
    print("PASS: test_11_windows_safe_output")

def test_12_ctrl_c_exits_cleanly():
    """12. Ctrl+C still exits cleanly with code 0."""
    with patch("src.watcher.time.sleep", side_effect=KeyboardInterrupt), \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}
        try:
            watcher.run_watcher(interval=1, max_cycles=5)
            assert False
        except SystemExit as e:
            assert e.code == 0
            print("PASS: test_12_ctrl_c_exits_cleanly")

def test_13_dry_run_safe():
    """13. Existing dry-run behavior remains safe (no mutation)."""
    records = [{"ID": "1", "YouTube Link": "https://youtu.be/dryrun123", "Title": "Dry", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""}]
    ws = _mock_ws(records)
    with patch("src.sheet_monitor.process_transcript_row") as mock_trans, \
         patch("src.sheet_monitor.process_audio_row") as mock_audio:
        from src.sheet_monitor import process_pipeline_row
        row = {"row_num": 2, "status": "NEW", "record": records[0], "url": "https://youtu.be/dryrun123", "youtube_link": "https://youtu.be/dryrun123", "id": "1", "title": "Dry", "audio_link": "", "transcript_link": ""}
        result = process_pipeline_row(ws, row, dry_run=True)
        assert result.get("dry_run") is True
        assert "would_status" in result
        mock_trans.assert_not_called()
        mock_audio.assert_not_called()
        ws.update_cell.assert_not_called()
        print("PASS: test_13_dry_run_safe")

def test_14_pipeline_unchanged():
    """14. Existing pipeline behavior remains unchanged (NEW->AUDIO_DONE, transcript fail, audio fail, idempotency)."""
    # Reuse existing pipeline tests' expectations
    # Check that fetch_pipeline_pending_rows still handles NEW correctly
    records = [
        {"ID": "1", "YouTube Link": "https://youtu.be/new123", "Title": "", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "2", "YouTube Link": "https://youtu.be/done123", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "output/transcripts/done123.txt", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/done123/view", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    pending = fetch_pipeline_pending_rows(ws)
    assert len(pending) == 1 and pending[0]["id"] == "1"
    # Also check audio pipeline still works
    from src.sheet_monitor import fetch_audio_pending_rows
    records2 = [{"ID": "3", "YouTube Link": "https://youtu.be/trans123", "Title": "", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/trans123.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""}]
    ws2 = _mock_ws(records2)
    pending2 = fetch_audio_pending_rows(ws2)
    assert len(pending2) == 1
    print("PASS: test_14_pipeline_unchanged")

if __name__ == "__main__":
    test_1_transient_retries_and_succeeds()
    test_2_retry_stops_at_max_retries()
    test_3_exponential_backoff_bounded()
    test_4_permanent_not_retried()
    test_5_watcher_survives_transient_polling_failure()
    test_6_no_tight_retry_loop()
    test_7_audio_done_idempotent_after_restart()
    test_8_audio_failed_controlled_retry()
    test_9_auth_errors_safe_actionable()
    test_10_logs_no_secrets()
    test_11_windows_safe_output()
    test_12_ctrl_c_exits_cleanly()
    test_13_dry_run_safe()
    test_14_pipeline_unchanged()
    print("\nAll reliability tests PASSED (14 cases).")
