"""Unit tests for YouTube transcript extraction — validation + local save.

Run:  python -m pytest tests/test_transcript.py -v
      python tests/test_transcript.py
Does NOT touch Google Sheets. Sheet is updated only when result.valid is True.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.transcript import (
    validate_youtube_link,
    fetch_and_save_transcript,
    save_transcript_locally,
    EmptyLinkError,
    InvalidLinkError,
)

def test_empty_links():
    for val in [None, "", "   ", "\n\t"]:
        try:
            validate_youtube_link(val)
            assert False, f"EmptyLinkError not raised for {val!r}"
        except EmptyLinkError:
            pass
    print("PASS: test_empty_links")

def test_invalid_links():
    for val in [
        "https://example.com/not-youtube",
        "not a url",
        "https://www.youtube.com/watch?v=",
        "abc",  # too short
        "https://youtu.be/short",
    ]:
        try:
            validate_youtube_link(val)
            assert False, f"InvalidLinkError not raised for {val!r}"
        except InvalidLinkError:
            pass
        except EmptyLinkError:
            assert False, "Wrong error for invalid"
    print("PASS: test_invalid_links")

def test_valid_links():
    cases = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("  https://youtu.be/dQw4w9WgXcQ  ", "dQw4w9WgXcQ"),
    ]
    for url, expected_id in cases:
        vid = validate_youtube_link(url)
        assert vid == expected_id, f"{url} -> {vid} != {expected_id}"
    print("PASS: test_valid_links")

def test_fetch_and_save_empty_invalid_do_not_save():
    # Empty — should return valid=False, no file written
    r = fetch_and_save_transcript("")
    assert not r["valid"] and r["error_type"] == "EmptyLinkError"
    assert r["txt_path"] is None
    # Invalid
    r = fetch_and_save_transcript("https://example.com/bad")
    assert not r["valid"] and r["error_type"] == "InvalidLinkError"
    print("PASS: test_fetch_and_save_empty_invalid_do_not_save")

def test_save_locally_creates_files():
    import tempfile
    import config
    orig = config.TRANSCRIPT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="test_transcripts_"))
    try:
        config.TRANSCRIPT_DIR = tmp
        vid = "TEST1234567"
        text = "Hello world transcript for testing. " * 10
        saved = save_transcript_locally(vid, text, "https://youtu.be/TEST1234567", title="Test Title", extra={"source":"test"})
        assert saved["txt_path"].exists() and saved["json_path"].exists()
        assert saved["txt_path"].read_text(encoding="utf-8") == text
        # Ensure not writing to sheet — this function is disk-only
        print("PASS: test_save_locally_creates_files")
    finally:
        config.TRANSCRIPT_DIR = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

def test_cli_validation_paths():
    # Simulate CLI --test-empty / --test-invalid via validation directly
    try:
        validate_youtube_link("   ")
        assert False
    except EmptyLinkError as e:
        assert "empty" in str(e).lower()
    try:
        validate_youtube_link("https://google.com")
        assert False
    except InvalidLinkError:
        pass
    print("PASS: test_cli_validation_paths")

if __name__ == "__main__":
    test_empty_links()
    test_invalid_links()
    test_valid_links()
    test_fetch_and_save_empty_invalid_do_not_save()
    test_save_locally_creates_files()
    test_cli_validation_paths()
    print("\nAll transcript validation tests PASSED (no sheet touched).")
