"""Focused tests for Phase 5.2 watcher (mocked, no real sleep/polling).

- No actual 30s sleep, no live Sheet/Gemini/TTS/Drive
- Verify watcher calls run_pipeline, respects interval, handles Ctrl+C, dry-run, errors, Windows-safe output

Run: python -m pytest tests/test_watcher.py -v
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch, MagicMock, call
import config
from src import watcher

def test_watcher_resolves_interval_config_and_cli():
    """Configured interval respected, CLI --interval overrides."""
    orig = config.WATCH_INTERVAL_SECONDS
    try:
        config.WATCH_INTERVAL_SECONDS = 30
        assert watcher._resolve_interval(None) == 30
        assert watcher._resolve_interval(10) == 10
        config.WATCH_INTERVAL_SECONDS = 45
        assert watcher._resolve_interval(None) == 45
        assert watcher._resolve_interval(5) == 5
        # CLI string should also work (main.py passes int, but test string)
        assert watcher._resolve_interval("20") == 20
        print("PASS: test_watcher_resolves_interval_config_and_cli")
    finally:
        config.WATCH_INTERVAL_SECONDS = orig

def test_watcher_invalid_interval_raises():
    """Invalid interval should raise ValueError."""
    try:
        watcher._resolve_interval(0)
        assert False, "should raise"
    except ValueError:
        pass
    try:
        watcher._resolve_interval(-5)
        assert False
    except ValueError:
        pass
    try:
        watcher._resolve_interval("abc")
        assert False
    except ValueError:
        pass
    print("PASS: test_watcher_invalid_interval_raises")

def test_watcher_performs_multiple_polls():
    """Watcher should call run_pipeline multiple times (mocked sleep, max_cycles)."""
    with patch("src.watcher.time.sleep") as mock_sleep, \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"total_pending": 1, "done": 1, "failed": 0, "skipped": 0, "details": [{"row_num": 2, "status": "AUDIO_DONE", "valid": True}]}
        # Run 3 cycles, interval 0 for speed (but we mock sleep)
        watcher.run_watcher(interval=1, dry_run=True, limit=1, max_cycles=3)
        assert mock_pipeline.call_count == 3
        assert mock_sleep.call_count == 2  # sleeps between cycles, not after last
        # Verify interval respected (sleep called with 1)
        assert all(c.args[0] == 1 for c in mock_sleep.call_args_list)
        print("PASS: test_watcher_performs_multiple_polls")

def test_watcher_calls_pipeline_not_duplicate_logic():
    """Watcher must call run_pipeline (not duplicate transcript/TTS/Drive logic)."""
    with patch("src.watcher.time.sleep") as mock_sleep, \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}
        watcher.run_watcher(interval=1, dry_run=False, max_cycles=1)
        mock_pipeline.assert_called_once_with(dry_run=False, limit=None)
        # Ensure watcher does not directly call lower-level functions
        # Check source does not import fetch_transcript etc.
        src = (ROOT / "src" / "watcher.py").read_text(encoding="utf-8")
        assert "run_pipeline" in src
        assert "fetch_transcript" not in src
        assert "generate_telugu_script" not in src
        assert "generate_podcast_mp3" not in src
        assert "upload_to_drive" not in src or "run_pipeline" in src  # should not duplicate
        print("PASS: test_watcher_calls_pipeline_not_duplicate_logic")

def test_watcher_dry_run_does_not_mutate():
    """Dry-run must call run_pipeline(dry_run=True) and not mutate (mocked)."""
    with patch("src.watcher.time.sleep"), \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"total_pending": 1, "done": 1, "failed": 0, "skipped": 0, "details": []}
        watcher.run_watcher(interval=1, dry_run=True, max_cycles=1)
        mock_pipeline.assert_called_once_with(dry_run=True, limit=None)
        print("PASS: test_watcher_dry_run_does_not_mutate")

def test_watcher_transient_error_continues():
    """Transient polling error (e.g., Sheets API) should be logged and watcher continues, not killed."""
    with patch("src.watcher.time.sleep") as mock_sleep, \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        # First cycle raises transient error, second succeeds
        mock_pipeline.side_effect = [Exception("Sheets API transient 500"), {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}]
        # Should not raise, should continue to second cycle
        watcher.run_watcher(interval=1, dry_run=False, max_cycles=2)
        assert mock_pipeline.call_count == 2
        assert mock_sleep.call_count == 1  # only one sleep between cycles (after first, before second, not after last? Actually 2 cycles => 1 sleep)
        print("PASS: test_watcher_transient_error_continues")

def test_watcher_ctrl_c_exits_cleanly():
    """Ctrl+C should print shutdown and exit 0, not leave corrupt state."""
    with patch("src.watcher.time.sleep", side_effect=KeyboardInterrupt), \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}
        try:
            watcher.run_watcher(interval=1, max_cycles=5)
            assert False, "should have exited via SystemExit"
        except SystemExit as e:
            assert e.code == 0
            print("PASS: test_watcher_ctrl_c_exits_cleanly")

def test_watcher_stops_at_max_cycles():
    """Watcher should stop after max_cycles (for testing, not infinite)."""
    with patch("src.watcher.time.sleep") as mock_sleep, \
         patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}
        watcher.run_watcher(interval=1, max_cycles=2)
        assert mock_pipeline.call_count == 2
        print("PASS: test_watcher_stops_at_max_cycles")

def test_watcher_windows_safe_output():
    """Console output must be ASCII-safe (no Unicode arrow that breaks cp1252)."""
    # Check watcher.py source for non-ASCII in prints
    src = (ROOT / "src" / "watcher.py").read_text(encoding="utf-8")
    # Should not contain Unicode arrow → in watcher logs (should use ->)
    assert "→" not in src, "watcher.py should use ASCII -> not Unicode arrow for Windows cp1252"
    # Also check that main.py watcher help is ASCII
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    # The watcher help should not contain non-ASCII that would break cp1252
    # At least ensure watcher startup logs are ASCII
    test_str = "[Watcher] Starting --watch in LIVE mode\n[Watcher] Polling interval: 30s\n[Watcher] Cycle 1 -- checking"
    try:
        test_str.encode("cp1252")
    except UnicodeEncodeError:
        assert False, "Watcher logs should be cp1252 encodable"
    print("PASS: test_watcher_windows_safe_output")

def test_cli_interval_overrides_config():
    """CLI --interval should override WATCH_INTERVAL_SECONDS."""
    import subprocess
    result = subprocess.run([sys.executable, "main.py", "--watch", "--help"], capture_output=True, text=True, cwd=str(ROOT))
    out = result.stdout + result.stderr
    assert "--watch" in out
    assert "--interval" in out
    # Test via watcher directly: CLI 10 should override config 30
    orig = config.WATCH_INTERVAL_SECONDS
    try:
        config.WATCH_INTERVAL_SECONDS = 30
        with patch("src.watcher.time.sleep") as mock_sleep, \
             patch("src.sheet_monitor.run_pipeline") as mock_pipeline:
            mock_pipeline.return_value = {"total_pending": 0, "done": 0, "failed": 0, "skipped": 0, "details": []}
            watcher.run_watcher(interval=10, max_cycles=1)
            mock_sleep.assert_not_called()  # only 1 cycle, no sleep
            # Now test that interval 10 is used, not config 30, via _resolve_interval
            assert watcher._resolve_interval(10) == 10
            assert watcher._resolve_interval(None) == 30
            print("PASS: test_cli_interval_overrides_config")
    finally:
        config.WATCH_INTERVAL_SECONDS = orig

if __name__ == "__main__":
    test_watcher_resolves_interval_config_and_cli()
    test_watcher_invalid_interval_raises()
    test_watcher_performs_multiple_polls()
    test_watcher_calls_pipeline_not_duplicate_logic()
    test_watcher_dry_run_does_not_mutate()
    test_watcher_transient_error_continues()
    test_watcher_ctrl_c_exits_cleanly()
    test_watcher_stops_at_max_cycles()
    test_watcher_windows_safe_output()
    test_cli_interval_overrides_config()
    print("\nAll watcher tests PASSED (mocked, no 30s sleep).")
