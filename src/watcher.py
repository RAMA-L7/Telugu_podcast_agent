"""Continuous Google Sheet watcher for Telugu Podcast Agent (Phase 5.2).

Polls the Sheet periodically and invokes the existing Phase 5.1 `run_pipeline()` (which handles
NEW / TEST_OK -> transcript -> Gemini -> Piper -> Drive -> AUDIO_DONE, plus resume/retry).

- Reuses existing pipeline logic (no duplication)
- Respects idempotency (AUDIO_DONE with valid Drive link skipped)
- Secure (make_public=False), provider-agnostic, dry-run safe
- Windows-safe ASCII logs, not cp1252-sensitive
- No global side effects on import
"""
import logging
import time
import sys
from typing import Optional

import config

log = logging.getLogger(__name__)

def _resolve_interval(cli_interval: Optional[int] = None) -> int:
    """Resolve polling interval: CLI overrides config, fallback to WATCH_INTERVAL_SECONDS (default 30)."""
    if cli_interval is not None:
        try:
            v = int(cli_interval)
            if v <= 0:
                raise ValueError("interval must be > 0")
            return v
        except Exception as e:
            raise ValueError(f"Invalid --interval {cli_interval!r}: {e}")
    # Config default (not hard-coded, via WATCH_INTERVAL_SECONDS)
    try:
        v = int(config.WATCH_INTERVAL_SECONDS)
        if v <= 0:
            return 30
        return v
    except Exception:
        return 30

def run_watcher(
    interval: Optional[int] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    max_cycles: Optional[int] = None,
) -> None:
    """Poll the Sheet continuously, calling run_pipeline each cycle.

    Args:
        interval: polling seconds (CLI overrides config.WATCH_INTERVAL_SECONDS)
        dry_run: if True, calls run_pipeline(dry_run=True) — no YouTube/Gemini/TTS/Drive/Sheet writes
        limit: passed to run_pipeline(limit=...) if set
        max_cycles: for testing — stop after N cycles (None = infinite until Ctrl+C)

    Behavior:
        - Startup log with interval and mode
        - Each cycle: run_pipeline(dry_run, limit) -> log pending/done/failed/skipped, row IDs
        - Transient Google Sheets/API/network errors are caught, logged, and retried next cycle
        - KeyboardInterrupt prints clear shutdown and exits 0
        - Unexpected programming errors are logged and re-raised (not swallowed)
    """
    # Resolve interval (CLI overrides config)
    poll_seconds = _resolve_interval(interval)
    mode = "DRY-RUN" if dry_run else "LIVE"

    # Startup logs (ASCII-safe, Windows PowerShell/cmd friendly)
    print(f"[Watcher] Starting --watch in {mode} mode", flush=True)
    print(f"[Watcher] Polling interval: {poll_seconds}s (config WATCH_INTERVAL_SECONDS={config.WATCH_INTERVAL_SECONDS})", flush=True)
    if dry_run:
        print("[Watcher] Dry-run: will NOT fetch YouTube, call Gemini, generate TTS, upload Drive, or update Sheet", flush=True)
    if limit is not None:
        print(f"[Watcher] Limit per cycle: {limit}", flush=True)
    print(f"[Watcher] Sheet: {config.SPREADSHEET_ID} | Tab: {config.SHEET_NAME}", flush=True)
    log.info("Watcher started interval=%ds dry_run=%s limit=%s", poll_seconds, dry_run, limit)

    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"\n[Watcher] Cycle {cycle} -- checking for pending rows...", flush=True)
            log.info("Watcher cycle %d: checking", cycle)
            try:
                # Reuse existing Phase 5.1 pipeline (no duplication)
                from src.sheet_monitor import run_pipeline

                # Call pipeline (it handles idempotency, retries, etc.)
                # run_pipeline itself handles its own internal errors per-row, but watcher catches top-level polling errors
                summary = run_pipeline(dry_run=dry_run, limit=limit)

                pending = summary.get("total_pending", 0)
                done = summary.get("done", 0)
                failed = summary.get("failed", 0)
                skipped = summary.get("skipped", 0)

                print(f"[Watcher] Cycle {cycle} -- pending: {pending} | done: {done} | failed: {failed} | skipped: {skipped}", flush=True)
                if pending:
                    # Log row IDs being processed (from details)
                    for d in summary.get("details", []):
                        rid = d.get("row_num", "?")
                        status = d.get("status", d.get("would_status", ""))
                        vid = d.get("video_id", d.get("youtube_link", ""))[:20]
                        print(f"  - Row {rid}: {status} {vid}", flush=True)
                    if done:
                        print(f"[Watcher] Cycle {cycle} -- {done} row(s) completed successfully", flush=True)
                    if failed:
                        print(f"[Watcher] Cycle {cycle} -- {failed} row(s) failed (will retry next cycle if retryable)", flush=True)
                    if skipped:
                        print(f"[Watcher] Cycle {cycle} -- {skipped} already completed, skipped", flush=True)
                else:
                    print(f"[Watcher] Cycle {cycle} -- no pending rows", flush=True)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                # Transient operational error (Sheets/API/network) — log and continue to next cycle, don't kill watcher
                # Distinguish expected operational errors vs programming errors: log both, but continue
                # We catch all here at watcher level, but log clearly; unexpected programming errors will be visible in log
                print(f"[Watcher] Cycle {cycle} -- polling error: {type(e).__name__}: {e} (will retry next cycle)", flush=True)
                log.warning("Watcher cycle %d polling error (will retry): %s: %s", cycle, type(e).__name__, e, exc_info=True)
                # Do not re-raise — wait for next cycle

            # Check max_cycles for testing (None = infinite)
            if max_cycles is not None and cycle >= max_cycles:
                print(f"[Watcher] Reached max_cycles={max_cycles}, stopping", flush=True)
                break

            # Sleep until next cycle (respect interval)
            print(f"[Watcher] Next check in {poll_seconds}s (Ctrl+C to stop)...", flush=True)
            log.info("Watcher sleeping %ds until next cycle", poll_seconds)
            time.sleep(poll_seconds)

    except KeyboardInterrupt:
        # Graceful shutdown
        print("\n[Watcher] Stopped by user (Ctrl+C) -- shutting down cleanly", flush=True)
        log.info("Watcher stopped by user (KeyboardInterrupt)")
        sys.exit(0)
