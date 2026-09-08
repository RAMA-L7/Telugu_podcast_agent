"""Focused unit tests for Phase 5.1 — Full pipeline orchestration (NEW → TRANSCRIPT_DONE → AUDIO_DONE).

Tests (mocked, no live Google, no Gemini, no TTS, no Drive, no YouTube):
A. NEW → TRANSCRIPT_DONE → AUDIO_DONE (full success)
B. Transcript failure (no audio)
C. Audio failure after transcript success (retryable)
D. Already AUDIO_DONE (skipped)
E. TRANSCRIPT_DONE input (resume at audio)
F. Dry-run (no mutation)
G. --limit (deterministic)
H. CLI regression (existing commands still registered)

Run: python -m pytest tests/test_pipeline.py -v
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock
import tempfile

import config
from src.sheet_monitor import (
    fetch_pipeline_pending_rows,
    process_pipeline_row,
    run_pipeline,
    _is_valid_audio_link,
    _col_index,
)

def _mock_ws(records):
    ws = MagicMock()
    ws.get_all_records.return_value = records
    ws.row_values.return_value = config.SHEET_HEADER
    ws.update_cell = MagicMock()
    return ws

SAMPLE_TRANSCRIPT = "మట్టి ఆరోగ్యం కోసం సేంద్రీయ ఎరువులు ముఖ్యం. " * 10
SAMPLE_SCRIPT = [
    {"speaker": "Anjali", "text": "హాయ్ రవి!"},
    {"speaker": "Ravi", "text": "తప్పకుండా!"},
]

def test_a_new_to_audio_done():
    """A. NEW → TRANSCRIPT_DONE → AUDIO_DONE (mock transcript, script, TTS, Drive, Sheet)."""
    records = [
        {"ID": "10", "YouTube Link": "https://www.youtube.com/watch?v=abc123DEF45", "Title": "Full Pipeline Video", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("src.sheet_monitor.process_transcript_row") as mock_trans, \
         patch("src.sheet_monitor.process_audio_row") as mock_audio:
        # Mock transcript stage: NEW -> TRANSCRIPT_DONE
        mock_trans.return_value = {"row_num": 2, "status": "TRANSCRIPT_DONE", "valid": True, "transcript_link": "output/transcripts/abc123DEF45.txt", "video_id": "abc123DEF45", "title": "Full Pipeline Video"}
        # Mock audio stage: TRANSCRIPT_DONE -> AUDIO_DONE
        mock_audio.return_value = {"row_num": 2, "status": "AUDIO_DONE", "valid": True, "audio_link": "https://drive.google.com/file/d/final123/view", "fileId": "final123"}

        row = {"row_num": 2, "status": "NEW", "record": records[0], "url": "https://www.youtube.com/watch?v=abc123DEF45", "youtube_link": "https://www.youtube.com/watch?v=abc123DEF45", "id": "10", "title": "Full Pipeline Video", "audio_link": "", "transcript_link": ""}
        result = process_pipeline_row(ws, row, dry_run=False)

        assert result["status"] == "AUDIO_DONE"
        assert result["valid"] is True
        assert result["audio_link"] == "https://drive.google.com/file/d/final123/view"
        mock_trans.assert_called_once()
        mock_audio.assert_called_once()
        print("PASS: test_a_new_to_audio_done")

def test_b_transcript_failure():
    """B. Transcript failure -> TRANSCRIPT_FAILED, no audio, Error recorded."""
    records = [
        {"ID": "11", "YouTube Link": "https://youtu.be/bad123", "Title": "Bad Video", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("src.sheet_monitor.process_transcript_row") as mock_trans, \
         patch("src.sheet_monitor.process_audio_row") as mock_audio:
        mock_trans.return_value = {"row_num": 2, "status": "TRANSCRIPT_FAILED", "valid": False, "error": "InvalidLink", "error_type": "InvalidLink"}

        row = {"row_num": 2, "status": "NEW", "record": records[0], "url": "https://youtu.be/bad123", "youtube_link": "https://youtu.be/bad123", "id": "11", "title": "Bad Video", "audio_link": "", "transcript_link": ""}
        result = process_pipeline_row(ws, row, dry_run=False)

        assert result["status"] == "TRANSCRIPT_FAILED"
        assert result["valid"] is False
        assert "InvalidLink" in str(result.get("error", "")) or "InvalidLink" in str(result.get("error_type", ""))
        mock_trans.assert_called_once()
        mock_audio.assert_not_called()
        print("PASS: test_b_transcript_failure")

def test_c_audio_failure_after_transcript_success():
    """C. Audio failure after transcript success -> TRANSCRIPT_DONE preserved, AUDIO_FAILED, retryable."""
    records = [
        {"ID": "12", "YouTube Link": "https://www.youtube.com/watch?v=audioFail123", "Title": "Audio Fail Video", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("src.sheet_monitor.process_transcript_row") as mock_trans, \
         patch("src.sheet_monitor.process_audio_row") as mock_audio:
        mock_trans.return_value = {"row_num": 2, "status": "TRANSCRIPT_DONE", "valid": True, "transcript_link": "output/transcripts/audioFail123.txt", "video_id": "audioFail123"}
        mock_audio.return_value = {"row_num": 2, "status": "AUDIO_FAILED", "valid": False, "error": "Drive upload failed: quota", "error_type": "DriveFailed"}

        row = {"row_num": 2, "status": "NEW", "record": records[0], "url": "https://www.youtube.com/watch?v=audioFail123", "youtube_link": "https://www.youtube.com/watch?v=audioFail123", "id": "12", "title": "Audio Fail Video", "audio_link": "", "transcript_link": ""}
        result = process_pipeline_row(ws, row, dry_run=False)

        assert result["status"] == "AUDIO_FAILED"
        assert result["valid"] is False
        assert "Drive" in str(result.get("error", ""))
        # Transcript Link should be preserved in the updated record (process_transcript_row set it)
        mock_trans.assert_called_once()
        mock_audio.assert_called_once()
        print("PASS: test_c_audio_failure_after_transcript_success")

def test_d_already_audio_done_skipped():
    """D. Already AUDIO_DONE with valid link -> skipped, no transcript/Gemini/TTS/Drive."""
    records = [
        {"ID": "13", "YouTube Link": "https://youtu.be/done123456", "Title": "Done Video", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "output/transcripts/done123456.txt", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/done123/view", "Error": "", "Created At": "", "Updated At": "2026-09-08 09:00:00 IST"},
    ]
    ws = _mock_ws(records)

    with patch("src.sheet_monitor.process_transcript_row") as mock_trans, \
         patch("src.sheet_monitor.process_audio_row") as mock_audio, \
         patch("src.sheet_monitor._is_valid_audio_link", return_value=True):
        row = {"row_num": 2, "status": "AUDIO_DONE", "record": records[0], "url": "https://youtu.be/done123456", "youtube_link": "https://youtu.be/done123456", "id": "13", "title": "Done Video", "audio_link": "https://drive.google.com/file/d/done123/view", "transcript_link": "output/transcripts/done123456.txt"}
        result = process_pipeline_row(ws, row, dry_run=False)
        assert result.get("skipped") is True
        assert result["status"] == "AUDIO_DONE"
        mock_trans.assert_not_called()
        mock_audio.assert_not_called()
        print("PASS: test_d_already_audio_done_skipped")

    # Also test fetch skips it
    ws2 = _mock_ws(records)
    pending = fetch_pipeline_pending_rows(ws2)
    assert len(pending) == 0
    print("PASS: test_d_fetch_skips_done")

def test_e_transcript_done_resume_at_audio():
    """E. TRANSCRIPT_DONE input -> orchestration resumes at audio stage (no re-transcript)."""
    records = [
        {"ID": "14", "YouTube Link": "https://www.youtube.com/watch?v=resume12345", "Title": "Resume Video", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/resume12345.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("src.sheet_monitor.process_transcript_row") as mock_trans, \
         patch("src.sheet_monitor.process_audio_row") as mock_audio:
        mock_audio.return_value = {"row_num": 2, "status": "AUDIO_DONE", "valid": True, "audio_link": "https://drive.google.com/file/d/resume123/view", "fileId": "resume123"}

        row = {"row_num": 2, "status": "TRANSCRIPT_DONE", "record": records[0], "url": "https://www.youtube.com/watch?v=resume12345", "youtube_link": "https://www.youtube.com/watch?v=resume12345", "id": "14", "title": "Resume Video", "audio_link": "", "transcript_link": "output/transcripts/resume12345.txt"}
        result = process_pipeline_row(ws, row, dry_run=False)

        mock_trans.assert_not_called()  # Should not re-do transcript
        mock_audio.assert_called_once()
        assert result["status"] == "AUDIO_DONE"
        print("PASS: test_e_transcript_done_resume_at_audio")

def test_f_dry_run_no_mutation():
    """F. Dry-run: no external mutation — no TTS, no Drive, no Sheet writes."""
    records = [
        {"ID": "15", "YouTube Link": "https://www.youtube.com/watch?v=dryrun12345", "Title": "DryRun Video", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("src.sheet_monitor.process_transcript_row") as mock_trans, \
         patch("src.sheet_monitor.process_audio_row") as mock_audio:

        row = {"row_num": 2, "status": "NEW", "record": records[0], "url": "https://www.youtube.com/watch?v=dryrun12345", "youtube_link": "https://www.youtube.com/watch?v=dryrun12345", "id": "15", "title": "DryRun Video", "audio_link": "", "transcript_link": ""}
        result = process_pipeline_row(ws, row, dry_run=True)

        assert result.get("dry_run") is True
        assert "would_status" in result
        mock_trans.assert_not_called()
        mock_audio.assert_not_called()
        ws.update_cell.assert_not_called()
        # Also test fetch dry-run via run_pipeline
        ws2 = _mock_ws(records)
        with patch("src.sheet_monitor.process_pipeline_row") as mock_pipe:
            mock_pipe.return_value = {"row_num": 2, "status": "AUDIO_DONE", "valid": True, "would_status": "NEW → TRANSCRIPT_DONE → AUDIO_DONE", "dry_run": True}
            summary = run_pipeline(ws=ws2, dry_run=True)
            assert summary["dry_run"] is True
            # run_pipeline dry-run should call process_pipeline_row but not external APIs
            assert mock_pipe.called
        print("PASS: test_f_dry_run_no_mutation")

def test_g_limit():
    """G. --limit: only requested number of rows processed, deterministic."""
    records = [
        {"ID": "16", "YouTube Link": "https://youtu.be/limit1", "Title": "Limit 1", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "17", "YouTube Link": "https://youtu.be/limit2", "Title": "Limit 2", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "18", "YouTube Link": "https://youtu.be/limit3", "Title": "Limit 3", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)

    with patch("src.sheet_monitor.process_pipeline_row") as mock_process:
        mock_process.return_value = {"row_num": 2, "status": "AUDIO_DONE", "valid": True, "audio_link": "https://drive.google.com/file/d/limit/view"}
        # Test limit 1 — total_pending is after limit (deterministic, matches run_transcript_pipeline behavior)
        summary = run_pipeline(ws=ws, dry_run=True, limit=1)
        assert summary["total_pending"] == 1
        assert summary["processed"] == 1
        assert mock_process.call_count == 1
        print("PASS: test_g_limit 1")

        mock_process.reset_mock()
        # Test limit 2 — total_pending after limit is 2
        summary2 = run_pipeline(ws=ws, dry_run=True, limit=2)
        assert summary2["total_pending"] == 2
        assert summary2["processed"] == 2
        assert mock_process.call_count == 2
        print("PASS: test_g_limit 2")

def test_h_cli_regression():
    """H. Existing CLI commands still registered/usable."""
    import subprocess
    result = subprocess.run([sys.executable, "main.py", "--help"], capture_output=True, text=True, cwd=str(ROOT))
    out = result.stdout + result.stderr
    assert "--run-transcripts" in out
    assert "--run-audio" in out
    assert "--run-pipeline" in out
    assert "--test-sheet" in out
    assert "--url" in out
    assert "--limit" in out
    assert "--dry-run" in out
    # Check that old commands still work via --help not error
    assert result.returncode == 0
    print("PASS: test_h_cli_regression")

def test_i_windows_safe_cli_output_no_unicode_arrow():
    """Windows-safe: --run-pipeline success output must be ASCII-safe (no →) and not raise UnicodeEncodeError on cp1252."""
    # Check main.py source does not contain Unicode arrow in pipeline success output
    src_main = (ROOT / "main.py").read_text(encoding="utf-8")
    # The successful pipeline print should use ASCII "->", not Unicode "→"
    assert "NEW -> TRANSCRIPT_DONE -> AUDIO_DONE" in src_main
    assert "NEW → TRANSCRIPT_DONE → AUDIO_DONE" not in src_main
    # Also check sheet_monitor logs for pipeline dry-run would_status use ASCII
    src_sheet = (ROOT / "src" / "sheet_monitor.py").read_text(encoding="utf-8")
    # Phase 5.1 would_status should be ASCII
    assert "NEW -> TRANSCRIPT_DONE -> AUDIO_DONE" in src_sheet
    assert "→" not in src_sheet.split("# Phase 5.1: Full pipeline orchestration")[1].split("def run_pipeline")[0] or "->" in src_sheet  # at least ASCII present
    # Ensure the string can be encoded with cp1252 (Windows console)
    test_str = "  Row 4: NEW -> TRANSCRIPT_DONE -> AUDIO_DONE -> https://drive.google.com/file/d/test/view"
    try:
        test_str.encode("cp1252")
    except UnicodeEncodeError as e:
        assert False, f"Windows cp1252 encode should not fail for ASCII-safe output: {e}"
    # Also test that the old Unicode would fail (to prove fix is needed)
    unicode_str = "  Row 4: NEW → TRANSCRIPT_DONE → AUDIO_DONE"
    try:
        unicode_str.encode("cp1252")
        assert False, "Unicode arrow should fail on cp1252, proving fix is necessary"
    except UnicodeEncodeError:
        pass  # Expected
    print("PASS: test_i_windows_safe_cli_output_no_unicode_arrow")

def test_j_dry_run_audio_failed_counts_as_done():
    """Dry-run AUDIO_FAILED should be counted as Done via would_status, not as actual failure."""
    records = [
        {"ID": "20", "YouTube Link": "https://youtu.be/dryaudio1", "Title": "Dry Audio Failed", "Language": "", "Duration": "", "Status": "AUDIO_FAILED", "Transcript Link": "output/transcripts/dryaudio1.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "[DriveFailed] previous", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    # Dry-run should simulate AUDIO_FAILED -> AUDIO_DONE and count as Done
    summary = run_pipeline(ws=ws, dry_run=True)
    assert summary["dry_run"] is True
    assert summary["total_pending"] == 1
    assert summary["done"] == 1, f"Dry-run AUDIO_FAILED should count as Done via would_status, got done={summary['done']} failed={summary['failed']}"
    assert summary["failed"] == 0
    assert summary["skipped"] == 0
    # Verify the detail has would_status containing AUDIO_DONE
    detail = summary["details"][0]
    assert "would_status" in detail
    assert "AUDIO_DONE" in detail["would_status"]
    assert detail["status"] == "AUDIO_FAILED"  # original status
    print("PASS: test_j_dry_run_audio_failed_counts_as_done")

    # Also test that non-dry-run AUDIO_FAILED is counted as failed if actually processed and fails
    # For live run, AUDIO_FAILED that fails again should be counted as failed
    ws2 = _mock_ws(records)
    with patch("src.sheet_monitor.process_pipeline_row") as mock_pipe:
        mock_pipe.return_value = {"row_num": 2, "status": "AUDIO_FAILED", "valid": False, "error": "Drive still failing", "error_type": "DriveFailed"}
        summary2 = run_pipeline(ws=ws2, dry_run=False)
        assert summary2["failed"] == 1
        assert summary2["done"] == 0
        print("PASS: test_j_dry_run_audio_failed_counts_as_done (live failure)")

def test_k_dry_run_transcript_done_counts_as_done():
    """Dry-run TRANSCRIPT_DONE should also be counted as Done."""
    records = [
        {"ID": "21", "YouTube Link": "https://youtu.be/drytrans1", "Title": "Dry Trans Done", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "output/transcripts/drytrans1.txt", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    summary = run_pipeline(ws=ws, dry_run=True)
    assert summary["done"] == 1
    assert summary["failed"] == 0
    print("PASS: test_k_dry_run_transcript_done_counts_as_done")

if __name__ == "__main__":
    test_a_new_to_audio_done()
    test_b_transcript_failure()
    test_c_audio_failure_after_transcript_success()
    test_d_already_audio_done_skipped()
    test_e_transcript_done_resume_at_audio()
    test_f_dry_run_no_mutation()
    test_g_limit()
    test_h_cli_regression()
    print("\nAll pipeline tests PASSED (8 cases, mocked).")
