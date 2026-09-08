"""Tests for Podcast Job ID and human-readable artifact naming.

Covers:
- Empty ID gets sequential next ID
- Existing ID preserved
- Zero-padded 4 digits
- Deleted/missing IDs do not cause reuse
- Non-numeric/blank ignored
- Audio filename begins with Podcast ID
- Title sanitized
- Duration MMmSSs
- Transcript filename uses ID and clean title
- Idempotency remains intact
- 12-column schema unchanged
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock
import tempfile
import config
from src.sheet_monitor import (
    allocate_next_podcast_id,
    _is_valid_podcast_id,
    _clean_title_for_filename,
    _format_duration_mm_ss,
    _audio_output_path,
    _transcript_output_path,
    _col_index,
)
from src.utils import clean_title_for_filename, format_duration_mm_ss

def _mock_ws(records):
    ws = MagicMock()
    ws.get_all_records.return_value = records
    ws.row_values.return_value = config.SHEET_HEADER
    ws.update_cell = MagicMock()
    return ws

def test_empty_id_gets_sequential_next_id():
    records = [
        {"ID": "0001", "YouTube Link": "https://youtu.be/a1", "Title": "A", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/1/view", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "0002", "YouTube Link": "https://youtu.be/a2", "Title": "B", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/2/view", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "", "YouTube Link": "https://youtu.be/a3", "Title": "C", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    next_id = allocate_next_podcast_id(ws)
    assert next_id == "0003", f"Expected 0003, got {next_id}"
    print("PASS: test_empty_id_gets_sequential_next_id")

def test_existing_id_is_preserved():
    records = [
        {"ID": "0012", "YouTube Link": "https://youtu.be/b1", "Title": "Existing", "Language": "", "Duration": "", "Status": "TRANSCRIPT_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    # Simulate process_transcript_row with existing ID - should preserve
    from src.sheet_monitor import _ensure_podcast_id
    row = {"row_num": 2, "status": "TRANSCRIPT_DONE", "record": records[0], "url": "https://youtu.be/b1", "youtube_link": "https://youtu.be/b1", "id": "0012", "title": "Existing"}
    pid = _ensure_podcast_id(ws, row, dry_run=False)
    assert pid == "0012", f"Existing ID should be preserved, got {pid}"
    # Should not have called allocate for existing? Check that ws.update_cell not called for ID (since preserved)
    # _ensure should not update if already valid
    assert not ws.update_cell.called or all(call.args[1] != _col_index("ID") for call in ws.update_cell.call_args_list)
    print("PASS: test_existing_id_is_preserved")

def test_ids_zero_padded_to_4_digits():
    records = [
        {"ID": "1", "YouTube Link": "https://youtu.be/c1", "Title": "A", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/1/view", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "2", "YouTube Link": "https://youtu.be/c2", "Title": "B", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/2/view", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "10", "YouTube Link": "https://youtu.be/c3", "Title": "C", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/3/view", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    next_id = allocate_next_podcast_id(ws)
    assert next_id == "0011", f"MAX is 10, next should be 0011, got {next_id}"
    assert len(next_id) == 4 and next_id.isdigit()
    # Also test that 1-digit allocation is padded
    ws2 = _mock_ws([])
    assert allocate_next_podcast_id(ws2) == "0001"
    ws3 = _mock_ws([{"ID": "0001", "YouTube Link": "", "Title": "", "Language": "", "Duration": "", "Status": "", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""}])
    assert allocate_next_podcast_id(ws3) == "0002"
    print("PASS: test_ids_zero_padded_to_4_digits")

def test_deleted_missing_ids_do_not_cause_reuse():
    # IDs 1,2,5 present, missing 3,4 deleted -> next should be 6, not 3
    records = [
        {"ID": "0001", "YouTube Link": "https://youtu.be/d1", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/1/view", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "0002", "YouTube Link": "https://youtu.be/d2", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/2/view", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "0005", "YouTube Link": "https://youtu.be/d5", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/5/view", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    next_id = allocate_next_podcast_id(ws)
    assert next_id == "0006", f"Should be 0006 (MAX 5 +1), not reuse 0003, got {next_id}"
    print("PASS: test_deleted_missing_ids_do_not_cause_reuse")

def test_non_numeric_blank_ignored_when_finding_max():
    records = [
        {"ID": "0003", "YouTube Link": "https://youtu.be/e1", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/1/view", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "", "YouTube Link": "https://youtu.be/e2", "Title": "", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "   ", "YouTube Link": "https://youtu.be/e3", "Title": "", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "abc", "YouTube Link": "https://youtu.be/e4", "Title": "", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "12a", "YouTube Link": "https://youtu.be/e5", "Title": "", "Language": "", "Duration": "", "Status": "NEW", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "", "Error": "", "Created At": "", "Updated At": ""},
        {"ID": "0007", "YouTube Link": "https://youtu.be/e6", "Title": "", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/7/view", "Error": "", "Created At": "", "Updated At": ""},
    ]
    ws = _mock_ws(records)
    next_id = allocate_next_podcast_id(ws)
    assert next_id == "0008", f"MAX should be 7 ignoring non-numeric, next 0008, got {next_id}"
    # Also test _is_valid
    assert _is_valid_podcast_id("0003") is True
    assert _is_valid_podcast_id("") is False
    assert _is_valid_podcast_id("   ") is False
    assert _is_valid_podcast_id("abc") is False
    assert _is_valid_podcast_id("12a") is False
    print("PASS: test_non_numeric_blank_ignored_when_finding_max")

def test_audio_filename_begins_with_podcast_id():
    p = _audio_output_path("0012", "Any Title", 495)
    assert p.name.startswith("0012_"), f"Audio filename must begin with Podcast ID, got {p.name}"
    # Also test with different ID
    p2 = _audio_output_path("0001", "Test", 60)
    assert p2.name.startswith("0001_")
    print("PASS: test_audio_filename_begins_with_podcast_id")

def test_title_safely_sanitized():
    # Windows forbidden chars, excessive whitespace, underscores
    title = '  How: Im/Helping   Thousands  * Rebuild?  Their | Lives <After> Prison  "Test"  '
    clean = clean_title_for_filename(title, max_len=60)
    assert "/" not in clean and "\\" not in clean and ":" not in clean and "*" not in clean and "?" not in clean and "|" not in clean and "<" not in clean and ">" not in clean and '"' not in clean
    assert "  " not in clean
    assert "__" not in clean
    assert clean[0] != "_" and clean[-1] != "_"
    # Also test via _clean_title_for_filename
    clean2 = _clean_title_for_filename(title, max_len=60)
    assert clean2 == clean
    # Test audio path uses sanitized title
    p = _audio_output_path("0012", title, 100)
    assert "<" not in p.name and ">" not in p.name and ":" not in p.name and '"' not in p.name
    # Bounded length
    long_title = "A" * 200
    clean_long = clean_title_for_filename(long_title, max_len=60)
    assert len(clean_long) <= 60
    p_long = _audio_output_path("0012", long_title, 100)
    assert len(p_long.name) <= 85  # ID + _ + clean(60) + _ + duration(7) + .mp3
    print("PASS: test_title_safely_sanitized")

def test_title_truncation_word_boundary():
    """Maximum-length truncation must end at last complete word, not cut mid-word."""
    # Example from task: 60-char limit should not cut "Prison_Su" but end at "Prison"
    title = "How I'm Helping Thousands Rebuild Their Lives After Prison Susan Burton TED Extra Words To Exceed Limit For Testing Purposes"
    # This title's clean version with max_len 60 would previously cut inside "Susan"
    clean = clean_title_for_filename(title, max_len=60)
    assert len(clean) <= 60
    # Must not end with partial word "Su" — should end at complete word boundary
    # The example: current produced "..._Prison_Su" (cut), preferred "..._Prison" (complete)
    assert not clean.endswith("_Su"), f"Should not cut word in middle, got {clean!r}"
    assert clean.endswith("Prison") or clean.endswith("Prison_Susan") is False  # Should end at word boundary, not partial
    # Verify it ends at a complete word (no truncated fragment)
    # Check that the next word in original clean would be incomplete, so we cut at boundary
    # For this specific title, expected to end at "Prison" (last complete word within 60)
    assert clean == "How_Im_Helping_Thousands_Rebuild_Their_Lives_After_Prison", f"Expected word-boundary truncation, got {clean!r}"
    # Also test via sheet_monitor version
    clean2 = _clean_title_for_filename(title, max_len=60)
    assert clean2 == clean
    # Short title should not be truncated at all
    short = "Short Title"
    assert clean_title_for_filename(short, max_len=60) == "Short_Title"
    # Exactly at limit should not truncate
    exact = "A" * 10 + " " + "B" * 10 + " " + "C" * 10  # ~32 chars
    assert len(clean_title_for_filename(exact, max_len=60)) <= 60
    print("PASS: test_title_truncation_word_boundary")

def test_duration_formatted_MMmSSs():
    assert format_duration_mm_ss(0) == "00m00s"
    assert format_duration_mm_ss(5) == "00m05s"
    assert format_duration_mm_ss(65) == "01m05s"
    assert format_duration_mm_ss(495) == "08m15s"
    assert format_duration_mm_ss(600) == "10m00s"
    assert _format_duration_mm_ss(495) == "08m15s"
    # Audio filename should contain formatted duration
    p = _audio_output_path("0012", "Title", 495)
    assert "08m15s" in p.name
    p2 = _audio_output_path("0012", "Title", 65)
    assert "01m05s" in p2.name
    print("PASS: test_duration_formatted_MMmSSs")

def test_transcript_filename_uses_id_and_clean_title():
    p = _transcript_output_path("0012", "How I'm Helping Thousands Rebuild Their Lives After Prison!")
    assert p.name.startswith("0012_"), f"Transcript filename must begin with Podcast ID, got {p.name}"
    assert p.suffix == ".txt"
    assert "dQw4w9WgXcQ" not in p.name, "Video ID must not be in transcript filename"
    # Check sanitized
    assert "/" not in p.name
    # Check via save_transcript_locally with podcast_id
    import tempfile
    import json
    orig = config.TRANSCRIPT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="test_transcript_id_"))
    try:
        config.TRANSCRIPT_DIR = tmp
        from src.transcript import save_transcript_locally
        saved = save_transcript_locally("dQw4w9WgXcQ", "Hello transcript", "https://youtu.be/dQw4w9WgXcQ", title="My Test Video: With * Bad Chars?", extra={"source":"test"}, podcast_id="0012")
        assert saved["txt_path"].name.startswith("0012_")
        assert saved["txt_path"].name.endswith(".txt")
        assert "dQw4w9WgXcQ" not in saved["txt_path"].name
        assert saved["json_path"].name.startswith("0012_")
        assert saved["txt_path"].exists()
        # Metadata should contain podcast_id and video_id
        assert saved["meta"].get("podcast_id") == "0012"
        assert saved["meta"].get("video_id") == "dQw4w9WgXcQ"
        # Check that fallback without podcast_id still works (legacy)
        saved2 = save_transcript_locally("dQw4w9WgXcQ", "Hello", "https://youtu.be/dQw4w9WgXcQ", title="Test", podcast_id=None)
        assert "dQw4w9WgXcQ" in saved2["txt_path"].name
        print("PASS: test_transcript_filename_uses_id_and_clean_title")
    finally:
        config.TRANSCRIPT_DIR = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

def test_existing_idempotency_remains_intact():
    # AUDIO_DONE with valid Drive link must still be skipped
    records = [
        {"ID": "0012", "YouTube Link": "https://youtu.be/done123", "Title": "Done Video", "Language": "", "Duration": "", "Status": "AUDIO_DONE", "Transcript Link": "output/transcripts/0012_Done_Video.txt", "Telugu Script Link": "", "Audio Link": "https://drive.google.com/file/d/done123/view", "Error": "", "Created At": "", "Updated At": "2026-09-08 09:00:00 IST"},
    ]
    ws = _mock_ws(records)
    from src.sheet_monitor import fetch_audio_pending_rows, fetch_pipeline_pending_rows, process_audio_row
    pending = fetch_audio_pending_rows(ws)
    assert len(pending) == 0, f"AUDIO_DONE with valid link should be skipped, got {pending}"
    pending2 = fetch_pipeline_pending_rows(ws)
    assert len(pending2) == 0
    # Also test process_audio_row skipped
    row = {"row_num": 2, "status": "AUDIO_DONE", "record": records[0], "url": "https://youtu.be/done123", "youtube_link": "https://youtu.be/done123", "id": "0012", "title": "Done Video", "audio_link": "https://drive.google.com/file/d/done123/view", "transcript_link": "output/transcripts/0012_Done_Video.txt"}
    result = process_audio_row(ws, row, dry_run=False)
    assert result.get("skipped") is True
    print("PASS: test_existing_idempotency_remains_intact")

def test_12_column_schema_unchanged():
    assert len(config.SHEET_HEADER) == 12
    assert config.SHEET_HEADER == ["ID", "YouTube Link", "Title", "Language", "Duration", "Status", "Transcript Link", "Telugu Script Link", "Audio Link", "Error", "Created At", "Updated At"]
    print("PASS: test_12_column_schema_unchanged")

def test_centralized_allocation_function_exists():
    # Check that allocate_next_podcast_id is centralized and not duplicated
    import inspect
    import pathlib
    src = pathlib.Path(ROOT) / "src" / "sheet_monitor.py"
    content = src.read_text(encoding="utf-8")
    assert "def allocate_next_podcast_id" in content, "Centralized function must exist"
    # Ensure not duplicated across pipeline (should only appear once or in one module)
    assert content.count("def allocate_next_podcast_id") == 1
    # Check that process_transcript_row and process_pipeline_row use it via _ensure
    assert "_ensure_podcast_id" in content
    print("PASS: test_centralized_allocation_function_exists")

if __name__ == "__main__":
    test_empty_id_gets_sequential_next_id()
    test_existing_id_is_preserved()
    test_ids_zero_padded_to_4_digits()
    test_deleted_missing_ids_do_not_cause_reuse()
    test_non_numeric_blank_ignored_when_finding_max()
    test_audio_filename_begins_with_podcast_id()
    test_title_safely_sanitized()
    test_duration_formatted_MMmSSs()
    test_transcript_filename_uses_id_and_clean_title()
    test_existing_idempotency_remains_intact()
    test_12_column_schema_unchanged()
    test_centralized_allocation_function_exists()
    print("\nAll podcast ID tests PASSED")
