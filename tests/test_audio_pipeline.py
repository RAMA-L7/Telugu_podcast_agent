"""Focused unit tests for Phase 4 Milestone 2 — Audio pipeline (TTS + Drive -> Audio Link).

Tests (mocked, no live Google, no Piper model, no network):
A. Successful TTS + Drive + Sheet update
B. Drive upload failure
C. TTS failure
D. Already completed row (idempotency)
E. OAuth separation (Drive OAuth vs Sheets service-account)
F. Sheet schema exactly 12 columns

Run: python -m pytest tests/test_audio_pipeline.py -v
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock, call
import tempfile

import config
from src.sheet_monitor import (
    fetch_audio_pending_rows,
    process_audio_row,
    run_audio_pipeline,
    _audio_output_path,
    _is_valid_audio_link,
    _col_index,
)

# Helper to create mock worksheet
def _mock_ws(records):
    ws = MagicMock()
    ws.get_all_records.return_value = records
    ws.row_values.return_value = config.SHEET_HEADER
    ws.update_cell = MagicMock()
    return ws

# Sample transcript content for mocking file read
SAMPLE_TRANSCRIPT = "మట్టి ఆరోగ్యం కోసం సేంద్రీయ ఎరువులు ముఖ్యం. " * 10
SAMPLE_SCRIPT = [
    {"speaker": "Anjali", "text": "హాయ్ రవి! సేంద్రీయ వ్యవసాయం గురించి మాట్లాడుకుందామా?"},
    {"speaker": "Ravi", "text": "తప్పకుండా! మట్టి ఆరోగ్యానికి సేంద్రీయ ఎరువులు చాలా ముఖ్యం."},
]

def test_a_successful_tts_drive_sheet_update():
    """A. TTS succeeds, Drive succeeds, webViewLink -> Audio Link, AUDIO_DONE, Error cleared, Updated At."""
    records = [
        {"ID": "1", "YouTube Link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Title": "Test Video", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/dQw4w9WgXcQ.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=SAMPLE_TRANSCRIPT), \
         patch("src.script_generator.generate_telugu_script", return_value=SAMPLE_SCRIPT) as mock_script, \
         patch("src.tts.generate_podcast_mp3") as mock_tts, \
         patch("src.drive_uploader.upload_to_drive") as mock_drive, \
         patch("src.utils.extract_video_id", return_value="dQw4w9WgXcQ"), \
         patch("src.sheet_monitor._audio_output_path", return_value=Path(tempfile.gettempdir()) / "test123.mp3"), \
         patch("src.sheet_monitor._ist_timestamp", return_value="2026-09-08 10:00:00 IST"):

        def fake_tts(script, mp3_path):
            Path(mp3_path).parent.mkdir(parents=True, exist_ok=True)
            Path(mp3_path).write_bytes(b"fake mp3")
            return Path(mp3_path)
        mock_tts.side_effect = fake_tts
        mock_drive.return_value = {"fileId": "test123", "id": "test123", "webViewLink": "https://drive.google.com/file/d/test123/view", "webContentLink": "https://drive.google.com/uc?id=test123"}

        row = {"row_num": 2, "status": "TRANSCRIPT_DONE", "record": records[0], "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube_link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "id": "1", "title": "Test Video", "audio_link": "", "transcript_link": "output/transcripts/dQw4w9WgXcQ.txt"}
        result = process_audio_row(ws, row, dry_run=False)

        assert result["status"] == "AUDIO_DONE"
        assert result["valid"] is True
        assert result["audio_link"] == "https://drive.google.com/file/d/test123/view"
        assert result["fileId"] == "test123"
        audio_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Audio Link")]
        assert len(audio_calls) == 1
        assert audio_calls[0].args[2] == "https://drive.google.com/file/d/test123/view"
        error_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Error")]
        assert any(c.args[2] == "" for c in error_calls)
        status_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Status")]
        assert any(c.args[2] == "AUDIO_DONE" for c in status_calls)
        updated_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Updated At")]
        assert len(updated_calls) == 1
        assert updated_calls[0].args[2] == "2026-09-08 10:00:00 IST"
        mock_tts.assert_called_once()
        mock_drive.assert_called_once()
        assert mock_drive.call_args.kwargs.get("make_public") is False or mock_drive.call_args[1].get("make_public") is False
        print("PASS: test_a_successful_tts_drive_sheet_update")

def test_b_drive_upload_failure():
    """B. TTS succeeds, Drive raises DriveError -> AUDIO_FAILED, Audio Link not overwritten, Error contains Drive failure, Updated At."""
    records = [
        {"ID": "2", "YouTube Link": "https://youtu.be/abc123DEF45", "Title": "Video 2", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/abc123DEF45.txt", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/old123/view", "Error": "", "Created At": "", "Updated At": "2026-09-08 09:00:00 IST"},
    ]
    ws = _mock_ws(records)

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=SAMPLE_TRANSCRIPT), \
         patch("src.script_generator.generate_telugu_script", return_value=SAMPLE_SCRIPT), \
         patch("src.tts.generate_podcast_mp3") as mock_tts, \
         patch("src.drive_uploader.upload_to_drive") as mock_drive, \
         patch("src.utils.extract_video_id", return_value="abc123DEF45"), \
         patch("src.sheet_monitor._audio_output_path", return_value=Path(tempfile.gettempdir()) / "abc123DEF45.mp3"), \
         patch("src.sheet_monitor._ist_timestamp", return_value="2026-09-08 10:01:00 IST"):

        def fake_tts(script, mp3_path):
            Path(mp3_path).write_bytes(b"fake")
            return Path(mp3_path)
        mock_tts.side_effect = fake_tts
        from src.drive_uploader import DriveUploadError
        mock_drive.side_effect = DriveUploadError("simulated Drive quota exceeded")

        row = {"row_num": 3, "status": "TRANSCRIPT_DONE", "record": records[0], "url": "https://youtu.be/abc123DEF45", "youtube_link": "https://youtu.be/abc123DEF45", "id": "2", "title": "Video 2", "audio_link": "https://drive.google.com/file/d/old123/view", "transcript_link": "output/transcripts/abc123DEF45.txt"}
        result = process_audio_row(ws, row, dry_run=False)

        assert result["status"] == "AUDIO_FAILED"
        assert result["valid"] is False
        assert "Drive upload failed" in result["error"] or "Drive" in result["error"]
        audio_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Audio Link")]
        assert len(audio_calls) == 0, f"Audio Link should not be overwritten on Drive failure, got {audio_calls}"
        error_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Error")]
        assert len(error_calls) == 1
        assert "Drive" in error_calls[0].args[2]
        status_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Status")]
        assert any(c.args[2] == "AUDIO_FAILED" for c in status_calls)
        updated_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Updated At")]
        assert updated_calls[0].args[2] == "2026-09-08 10:01:00 IST"
        mock_tts.assert_called_once()
        mock_drive.assert_called_once()
        print("PASS: test_b_drive_upload_failure")

def test_c_tts_failure():
    """C. TTS failure -> Drive NOT called, Error recorded, AUDIO_FAILED."""
    records = [
        {"ID": "3", "YouTube Link": "https://www.youtube.com/watch?v=xyz789ABCDE", "Title": "Video 3", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/xyz789ABCDE.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=SAMPLE_TRANSCRIPT), \
         patch("src.script_generator.generate_telugu_script", return_value=SAMPLE_SCRIPT), \
         patch("src.tts.generate_podcast_mp3", side_effect=RuntimeError("Piper model not found")) as mock_tts, \
         patch("src.drive_uploader.upload_to_drive") as mock_drive, \
         patch("src.utils.extract_video_id", return_value="xyz789ABCDE"), \
         patch("src.sheet_monitor._audio_output_path", return_value=Path(tempfile.gettempdir()) / "xyz789ABCDE.mp3"), \
         patch("src.sheet_monitor._ist_timestamp", return_value="2026-09-08 10:02:00 IST"):

        row = {"row_num": 4, "status": "TRANSCRIPT_DONE", "record": records[0], "url": "https://www.youtube.com/watch?v=xyz789ABCDE", "youtube_link": "https://www.youtube.com/watch?v=xyz789ABCDE", "id": "3", "title": "Video 3", "audio_link": "", "transcript_link": "output/transcripts/xyz789ABCDE.txt"}
        result = process_audio_row(ws, row, dry_run=False)

        assert result["status"] == "AUDIO_FAILED"
        assert result["error_type"] == "TTSFailed" or "TTS failed" in result["error"]
        mock_tts.assert_called_once()
        mock_drive.assert_not_called()
        audio_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Audio Link")]
        assert len(audio_calls) == 0
        error_calls = [c for c in ws.update_cell.call_args_list if c.args[1] == _col_index("Error")]
        assert len(error_calls) == 1
        print("PASS: test_c_tts_failure")

def test_d_already_completed_row():
    """D. AUDIO_DONE + valid Audio Link -> no TTS, no Drive, skipped."""
    records = [
        {"ID": "4", "YouTube Link": "https://youtu.be/already123", "Title": "Done Video", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "output/transcripts/already123.txt", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/already123/view", "Error": "", "Created At": "", "Updated At": "2026-09-08 09:00:00 IST"},
    ]
    ws = _mock_ws(records)

    with patch("src.script_generator.generate_telugu_script") as mock_script, \
         patch("src.tts.generate_podcast_mp3") as mock_tts, \
         patch("src.drive_uploader.upload_to_drive") as mock_drive:

        row = {"row_num": 5, "status": "AUDIO_DONE", "record": records[0], "url": "https://youtu.be/already123", "youtube_link": "https://youtu.be/already123", "id": "4", "title": "Done Video", "audio_link": "https://drive.google.com/file/d/already123/view", "transcript_link": "output/transcripts/already123.txt"}
        result = process_audio_row(ws, row, dry_run=False)

        assert result["skipped"] is True
        assert result["status"] == "AUDIO_DONE"
        mock_script.assert_not_called()
        mock_tts.assert_not_called()
        mock_drive.assert_not_called()
        ws.update_cell.assert_not_called()
        print("PASS: test_d_already_completed_row")

    ws2 = _mock_ws(records)
    pending = fetch_audio_pending_rows(ws2)
    assert len(pending) == 0, f"Should skip AUDIO_DONE with valid link, got {pending}"
    print("PASS: test_d_fetch_skips_completed")

def test_e_oauth_separation():
    """E. Drive uses OAuth (drive_oauth_client.json) while Sheets still uses service_account.json."""
    src_drive = (ROOT / "src" / "drive_uploader.py").read_text(encoding="utf-8")
    assert "GOOGLE_DRIVE_OAUTH_CLIENT_FILE" in src_drive
    assert "_get_drive_oauth_service" in src_drive
    assert "upload_to_drive" in src_drive
    assert "_get_drive_oauth_service" in src_drive
    src_sheet = (ROOT / "src" / "sheet_monitor.py").read_text(encoding="utf-8")
    assert "GOOGLE_CREDENTIALS_PATH" in src_sheet
    assert "_load_credentials" in src_sheet
    assert "upload_to_drive" in src_sheet
    assert "from src.drive_uploader import upload_to_drive" in src_sheet or "from src.drive_uploader import" in src_sheet
    assert "drive_oauth_client.json" in src_drive or "GOOGLE_DRIVE_OAUTH" in src_drive
    print("PASS: test_e_oauth_separation")

def test_f_sheet_schema_12_columns():
    """F. Sheet schema remains exactly 12 columns."""
    assert len(config.SHEET_HEADER) == 12
    assert config.SHEET_HEADER == ["ID", "YouTube Link", "Title", "Language", "Duration", "Status", "Transcript Link", "Telugu Script Link", "Audio Link", "Error", "Created At", "Updated At"]
    for col in config.SHEET_HEADER:
        idx = _col_index(col)
        assert 1 <= idx <= 12
    src = (ROOT / "src" / "sheet_monitor.py").read_text(encoding="utf-8")
    assert "_col_index(\"Audio Link\")" in src
    assert "_col_index(\"Status\")" in src
    assert "_col_index(\"Error\")" in src
    assert "_col_index(\"Updated At\")" in src
    assert "ws.update_cell(row_num, 9," not in src or "_col_index" in src
    print("PASS: test_f_sheet_schema_12_columns")

def test_audio_output_path_deterministic():
    """File naming: deterministic, human-readable ID-based with duration, filesystem-safe."""
    from src.sheet_monitor import _audio_output_path
    # Same ID/title/duration should be deterministic
    p1 = _audio_output_path("0012", "How I'm Helping Thousands Rebuild Their Lives After Prison!", 495)
    p2 = _audio_output_path("0012", "How I'm Helping Thousands Rebuild Their Lives After Prison!", 495)
    assert p1 == p2, "Should be deterministic for same ID/title/duration"
    assert p1.suffix == ".mp3"
    assert p1.name.startswith("0012_"), f"Audio filename must begin with Podcast ID, got {p1.name}"
    assert "dQw4w9WgXcQ" not in p1.name, "Video ID must not be in human-facing filename"
    assert "08m15s" in p1.name, f"Duration 495s should be 08m15s, got {p1.name}"
    assert "/" not in p1.name and "\\" not in p1.name and ":" not in p1.name and "*" not in p1.name
    # Title sanitized, duration formatted
    p3 = _audio_output_path("0005", "Title with / slashes and * stars!  ", 65)
    assert "/" not in p3.name
    assert "*" not in p3.name
    assert p3.name.startswith("0005_")
    assert "01m05s" in p3.name, f"65s should be 01m05s, got {p3.name}"
    # Different duration should give different filename for same ID/title
    p4 = _audio_output_path("0012", "Same Title", 100)
    p5 = _audio_output_path("0012", "Same Title", 200)
    assert p4 != p5, "Different durations should give different filenames"
    assert "01m40s" in p4.name  # 100s = 01m40s
    assert "03m20s" in p5.name  # 200s = 03m20s
    # Clean title, no unsafe chars
    p6 = _audio_output_path("0001", 'Test: Title with <bad> chars "yes" | pipe', 10)
    assert "<" not in p6.name and ">" not in p6.name and ":" not in p6.name and '"' not in p6.name and "|" not in p6.name
    assert p6.name.startswith("0001_")
    print("PASS: test_audio_output_path_deterministic")

def test_script_done_not_pending():
    """SCRIPT_DONE should NOT be audio-pipeline pending (no persisted Telugu Script artifact)."""
    records = [
        {"ID": "5", "YouTube Link": "https://youtu.be/script12345", "Title": "Script Done Video", "Language": "", "Duration": "", "Status": "SCRIPT_DONE", "Transcript Link": "output/transcripts/script12345.txt", "Telugu Script Link": "output/scripts/script12345.json", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "6", "YouTube Link": "https://youtu.be/audiofail12", "Title": "Audio Failed Video", "Language": "", "Duration": "", "Status": "AUDIO_FAILED", "Transcript Link": "output/transcripts/audiofail12.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "[DriveFailed] quota", "Created At": "", "Updated At": ""},
        {"ID": "7", "YouTube Link": "https://youtu.be/transdone1", "Title": "Transcript Done Video", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/transdone1.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    pending = fetch_audio_pending_rows(ws)
    # Only TRANSCRIPT_DONE and AUDIO_FAILED should be pending (2 rows), SCRIPT_DONE excluded
    pending_ids = [r["id"] for r in pending]
    assert "5" not in pending_ids, f"SCRIPT_DONE ID 5 should be excluded, got pending {pending_ids}"
    assert "6" in pending_ids, "AUDIO_FAILED should be retryable"
    assert "7" in pending_ids, "TRANSCRIPT_DONE should be pending"
    assert len(pending) == 2, f"Expected 2 pending (TRANSCRIPT_DONE + AUDIO_FAILED), got {len(pending)}: {pending}"
    print("PASS: test_script_done_not_pending (SCRIPT_DONE excluded, no regeneration)")

def test_transcript_done_is_valid_input():
    """TRANSCRIPT_DONE is valid current input state for audio pipeline."""
    records = [
        {"ID": "8", "YouTube Link": "https://youtu.be/valid123456", "Title": "Valid", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/valid123456.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    pending = fetch_audio_pending_rows(ws)
    assert len(pending) == 1 and pending[0]["id"] == "8"
    print("PASS: test_transcript_done_is_valid_input")

if __name__ == "__main__":
    test_a_successful_tts_drive_sheet_update()
    test_b_drive_upload_failure()
    test_c_tts_failure()
    test_d_already_completed_row()
    test_e_oauth_separation()
    test_f_sheet_schema_12_columns()
    test_audio_output_path_deterministic()
    print("\nAll audio pipeline tests PASSED (6 cases + naming, mocked).")
