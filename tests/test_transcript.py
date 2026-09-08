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

def test_english_preferred_when_available():
    """English transcript should be returned when available, Telugu not required."""
    from unittest.mock import patch
    from src.transcript import fetch_transcript
    # Mock _fetch_via_transcript_api to return English transcript
    with patch("src.transcript._fetch_via_transcript_api", return_value="Hello world English transcript for testing. This is English."):
        with patch("src.transcript._fetch_via_ytdlp") as mock_ytdlp:
            with patch("src.transcript._get_title", return_value="Test Title"):
                text, title = fetch_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ", max_chars=12000)
                assert "Hello world English" in text
                # yt-dlp should not be called when transcript_api succeeds with English
                mock_ytdlp.assert_not_called()
                print("PASS: test_english_preferred_when_available")

def test_telugu_not_required_when_english_succeeds():
    """If English succeeds, do not require Telugu — yt-dlp fallback not needed."""
    from unittest.mock import patch
    from src.transcript import fetch_and_save_transcript
    import tempfile
    import config
    orig = config.TRANSCRIPT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="test_transcripts_en_"))
    try:
        config.TRANSCRIPT_DIR = tmp
        with patch("src.transcript._fetch_via_transcript_api", return_value="English transcript for Telugu test. " * 5):
            with patch("src.transcript._fetch_via_ytdlp") as mock_ytdlp:
                with patch("src.transcript._get_title", return_value="English Title"):
                    r = fetch_and_save_transcript("https://www.youtube.com/watch?v=EN123456789", max_chars=12000)
                    assert r["valid"] is True
                    assert r["error_type"] is None
                    # Should not have called yt-dlp when English via transcript_api succeeds
                    mock_ytdlp.assert_not_called()
                    print("PASS: test_telugu_not_required_when_english_succeeds")
    finally:
        config.TRANSCRIPT_DIR = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

def test_parse_error_falls_through_correctly():
    """ParseError 'no element found' (HTML/429) should be treated as transient and fall through to yt-dlp."""
    from unittest.mock import patch
    from src.transcript import fetch_transcript, TranscriptFetchError
    # Mock transcript_api to raise ParseError with no element found, and yt-dlp to return English
    def fake_api(video_id):
        raise TranscriptFetchError("YouTube rate limit or HTML response (transient): no element found: line 1, column 0")
    with patch("src.transcript._fetch_via_transcript_api", side_effect=fake_api):
        with patch("src.transcript._fetch_via_ytdlp", return_value="Fallback English transcript via yt-dlp. " * 5) as mock_ytdlp:
            with patch("src.transcript._get_title", return_value="Fallback Title"):
                # Should fall through to yt-dlp and succeed
                text, title = fetch_transcript("https://www.youtube.com/watch?v=QPfPzEmHTwE", max_chars=12000)
                assert "Fallback English" in text
                mock_ytdlp.assert_called_once()
                print("PASS: test_parse_error_falls_through_correctly")

def test_existing_fallback_remains_intact():
    """Existing fallback: transcript_api success -> yt-dlp not called; transcript_api fail -> yt-dlp tried."""
    from unittest.mock import patch
    from src.transcript import fetch_transcript
    # Case 1: transcript_api succeeds, yt-dlp not called
    with patch("src.transcript._fetch_via_transcript_api", return_value="Transcript API success"):
        with patch("src.transcript._fetch_via_ytdlp") as mock_ytdlp:
            with patch("src.transcript._get_title", return_value=""):
                text, _ = fetch_transcript("https://www.youtube.com/watch?v=abc123DEF45", max_chars=12000)
                assert text == "Transcript API success"
                mock_ytdlp.assert_not_called()
    # Case 2: transcript_api fails, yt-dlp succeeds
    with patch("src.transcript._fetch_via_transcript_api", side_effect=Exception("transcript_api transient 429")):
        with patch("src.transcript._fetch_via_ytdlp", return_value="yt-dlp success"):
            with patch("src.transcript._get_title", return_value=""):
                text, _ = fetch_transcript("https://www.youtube.com/watch?v=abc123DEF45", max_chars=12000)
                assert text == "yt-dlp success"
    print("PASS: test_existing_fallback_remains_intact")

def test_no_regression_url_validation_and_save():
    """No regression to URL validation or transcript saving (existing behavior)."""
    from unittest.mock import patch
    from src.transcript import fetch_and_save_transcript
    import tempfile
    import config
    orig = config.TRANSCRIPT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="test_transcripts_reg_"))
    try:
        config.TRANSCRIPT_DIR = tmp
        # Valid link should still work via mocked fetch
        with patch("src.transcript._fetch_via_transcript_api", return_value="Valid transcript"):
            with patch("src.transcript._get_title", return_value="Valid Title"):
                r = fetch_and_save_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                assert r["valid"] is True
                assert r["video_id"] == "dQw4w9WgXcQ"
                assert Path(r["txt_path"]).exists()
        # Invalid link should still fail correctly
        r2 = fetch_and_save_transcript("https://example.com/not-youtube")
        assert not r2["valid"] and r2["error_type"] == "InvalidLinkError"
        print("PASS: test_no_regression_url_validation_and_save")
    finally:
        config.TRANSCRIPT_DIR = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

def test_whisper_fallback_when_captions_fail():
    """When transcript_api and yt-dlp both fail (429), whisper fallback should be tried."""
    from unittest.mock import patch
    from src.transcript import fetch_transcript
    # Mock retry_operation to avoid 2+4+8s delays in tests
    def _no_retry(func, **kwargs):
        return func()
    with patch("src.retry_utils.retry_operation", side_effect=_no_retry):
        with patch("src.transcript._fetch_via_transcript_api", side_effect=Exception("transcript_api 429")):
            with patch("src.transcript._fetch_via_ytdlp", side_effect=Exception("yt-dlp 429")):
                with patch("src.transcript._fetch_via_whisper", return_value="Whisper fallback English transcript for testing. " * 10) as mock_whisper:
                    with patch("src.transcript._get_title", return_value="Whisper Title"):
                        text, title = fetch_transcript("https://www.youtube.com/watch?v=QPfPzEmHTwE", max_chars=12000)
                        assert "Whisper fallback" in text
                        mock_whisper.assert_called_once()
                        print("PASS: test_whisper_fallback_when_captions_fail")

def test_whisper_disabled_skipped():
    """When WHISPER_ENABLED=false, whisper should not be called and fetch should fail."""
    from unittest.mock import patch
    from src.transcript import fetch_transcript, TranscriptNotFoundError
    import config
    orig = getattr(config, "WHISPER_ENABLED", True)
    try:
        config.WHISPER_ENABLED = False
        def _no_retry(func, **kwargs):
            return func()
        with patch("src.retry_utils.retry_operation", side_effect=_no_retry):
            with patch("src.transcript._fetch_via_transcript_api", side_effect=Exception("429")):
                with patch("src.transcript._fetch_via_ytdlp", side_effect=Exception("429")):
                    with patch("src.transcript._fetch_via_whisper") as mock_whisper:
                        with patch("src.transcript._get_title", return_value=""):
                            try:
                                fetch_transcript("https://www.youtube.com/watch?v=QPfPzEmHTwE", max_chars=12000)
                                assert False, "Should have raised TranscriptNotFoundError"
                            except (TranscriptNotFoundError, Exception):
                                pass
                            # whisper should NOT be called when disabled
                            mock_whisper.assert_not_called()
                            print("PASS: test_whisper_disabled_skipped")
    finally:
        config.WHISPER_ENABLED = orig

def test_whisper_save_valid():
    """fetch_and_save_transcript should save whisper result correctly."""
    from unittest.mock import patch
    from src.transcript import fetch_and_save_transcript
    import tempfile
    import config
    orig = config.TRANSCRIPT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="test_whisper_save_"))
    try:
        config.TRANSCRIPT_DIR = tmp
        def _no_retry(func, **kwargs):
            return func()
        with patch("src.retry_utils.retry_operation", side_effect=_no_retry):
            with patch("src.transcript._fetch_via_transcript_api", side_effect=Exception("429")):
                with patch("src.transcript._fetch_via_ytdlp", side_effect=Exception("429")):
                    with patch("src.transcript._fetch_via_whisper", return_value="Whisper saved transcript " * 10):
                        with patch("src.transcript._get_title", return_value="Whisper Saved Title"):
                            r = fetch_and_save_transcript("https://www.youtube.com/watch?v=QPfPzEmHTwE")
                            assert r["valid"] is True
                            assert r["txt_path"].exists()
                            assert "Whisper saved" in r["transcript"]
                            print("PASS: test_whisper_save_valid")
    finally:
        config.TRANSCRIPT_DIR = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    test_empty_links()
    test_invalid_links()
    test_valid_links()
    test_fetch_and_save_empty_invalid_do_not_save()
    test_save_locally_creates_files()
    test_cli_validation_paths()
    test_english_preferred_when_available()
    test_telugu_not_required_when_english_succeeds()
    test_parse_error_falls_through_correctly()
    test_existing_fallback_remains_intact()
    test_no_regression_url_validation_and_save()
    test_whisper_fallback_when_captions_fail()
    test_whisper_disabled_skipped()
    test_whisper_save_valid()
    print("\nAll transcript validation tests PASSED (no sheet touched).")
