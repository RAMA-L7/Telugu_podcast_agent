"""Continuous Google Sheet watcher for Telugu Podcast Agent (Phase 5.2 + 5.3 hardening).

Polls the Sheet periodically and invokes the existing Phase 5.1 `run_pipeline()` (which handles
NEW / TEST_OK -> transcript -> Gemini -> Piper -> Drive -> AUDIO_DONE, plus resume/retry).

- Reuses existing pipeline logic (no duplication)
- Respects idempotency (AUDIO_DONE with valid Drive link skipped)
- Secure (make_public=False), provider-agnostic, dry-run safe
- Windows-safe ASCII logs, not cp1252-sensitive
- No global side effects on import
- Phase 5.3: retry/backoff, hammering prevention (via sheet Updated At), error resilience, operational logging
"""
import logging
import time
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
from src.retry_utils import is_transient_error, get_retry_delay

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
    try:
        v = int(config.WATCH_INTERVAL_SECONDS)
        if v <= 0:
            return 30
        return v
    except Exception:
        return 30

def _failure_category(status: str, error_type: str = "") -> str:
    """Map status/error to operational category for logging."""
    s = (status or "").upper()
    et = (error_type or "").lower()
    if "transcript" in s or "transcript" in et:
        return "[TranscriptFailed]"
    if "llm" in et or "gemini" in et.lower() or "llm" in s.lower():
        return "[LLMFailed]"
    if "tts" in et or "piper" in et.lower():
        return "[TTSFailed]"
    if "drive" in et or "drive" in s.lower():
        return "[DriveFailed]"
    if "sheet" in et or "sheet" in s.lower() or "gspread" in et:
        return "[SheetFailed]"
    if s in ("TRANSCRIPT_FAILED", "AUDIO_FAILED"):
        return f"[{s}]"
    return "[Failed]"

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
        - Startup log with interval, mode, start time, config
        - Each cycle: run_pipeline(dry_run, limit) -> log pending/done/failed/skipped, row IDs, failure categories, elapsed
        - Transient Google Sheets/API/network errors are caught, logged with backoff, and retried next cycle
        - One bad row does not stop other rows (pipeline handles per-row)
        - KeyboardInterrupt prints clear shutdown and exits 0
        - Unexpected programming errors are logged with context but watcher continues (except KeyboardInterrupt)
        - No tight loop: always sleeps interval (or backoff) between cycles
    """
    poll_seconds = _resolve_interval(interval)
    mode = "DRY-RUN" if dry_run else "LIVE"
    start_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    start_str = start_time.strftime("%Y-%m-%d %H:%M:%S IST")

    # Startup logs (ASCII-safe, Windows PowerShell/cmd friendly)
    print(f"[Watcher] Starting --watch in {mode} mode at {start_str}", flush=True)
    print(f"[Watcher] Polling interval: {poll_seconds}s (config WATCH_INTERVAL_SECONDS={config.WATCH_INTERVAL_SECONDS})", flush=True)
    print(f"[Watcher] Config: MAX_RETRIES={config.MAX_RETRIES} RETRY_BASE={config.RETRY_BASE_DELAY_SECONDS}s RETRY_MAX={config.RETRY_MAX_DELAY_SECONDS}s", flush=True)
    if dry_run:
        print("[Watcher] Dry-run: will NOT fetch YouTube, call Gemini, generate TTS, upload Drive, or update Sheet", flush=True)
    if limit is not None:
        print(f"[Watcher] Limit per cycle: {limit}", flush=True)
    print(f"[Watcher] Sheet: {config.SPREADSHEET_ID} | Tab: {config.SHEET_NAME}", flush=True)
    log.info("Watcher started at %s interval=%ds dry_run=%s limit=%s mode=%s", start_str, poll_seconds, dry_run, limit, mode)

    cycle = 0
    consecutive_failures = 0
    try:
        while True:
            cycle += 1
            cycle_start = time.perf_counter()
            cycle_start_str = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%H:%M:%S IST")
            print(f"\n[Watcher] Cycle {cycle} [{cycle_start_str}] -- checking for pending rows...", flush=True)
            log.info("Watcher cycle %d: checking (interval %ds, dry_run %s, limit %s)", cycle, poll_seconds, dry_run, limit)
            try:
                from src.sheet_monitor import run_pipeline

                summary = run_pipeline(dry_run=dry_run, limit=limit)

                pending = summary.get("total_pending", 0)
                done = summary.get("done", 0)
                failed = summary.get("failed", 0)
                skipped = summary.get("skipped", 0)
                elapsed = time.perf_counter() - cycle_start

                print(f"[Watcher] Cycle {cycle} -- pending: {pending} | done: {done} | failed: {failed} | skipped: {skipped} | elapsed: {elapsed:.1f}s", flush=True)
                log.info("Watcher cycle %d done: pending=%d done=%d failed=%d skipped=%d elapsed=%.1fs", cycle, pending, done, failed, skipped, elapsed)
                if pending:
                    for d in summary.get("details", []):
                        rid = d.get("row_num", "?")
                        status = d.get("status", d.get("would_status", ""))
                        vid = d.get("video_id", d.get("youtube_link", ""))[:20]
                        err_type = d.get("error_type", "")
                        cat = _failure_category(status, err_type) if status in ("TRANSCRIPT_FAILED", "AUDIO_FAILED") else ""
                        # Use ASCII-safe output
                        print(f"  - Row {rid}: {status} {cat} {vid}", flush=True)
                    if done:
                        print(f"[Watcher] Cycle {cycle} -- {done} row(s) completed successfully", flush=True)
                    if failed:
                        # Log failure categories
                        for d in summary.get("details", []):
                            if d.get("status") in ("TRANSCRIPT_FAILED", "AUDIO_FAILED") or d.get("error_type"):
                                cat = _failure_category(d.get("status",""), d.get("error_type",""))
                                print(f"    * Row {d.get('row_num')} {cat} {str(d.get('error',''))[:80]}", flush=True)
                        print(f"[Watcher] Cycle {cycle} -- {failed} row(s) failed (will retry per backoff if transient)", flush=True)
                    if skipped:
                        print(f"[Watcher] Cycle {cycle} -- {skipped} already completed, skipped", flush=True)
                else:
                    print(f"[Watcher] Cycle {cycle} -- no pending rows", flush=True)

                # Reset consecutive failures on success (or even if no pending, it's not a failure)
                consecutive_failures = 0

            except KeyboardInterrupt:
                raise
            except Exception as e:
                # Check if this is a permanent auth/config error that should not be hammered
                # For permanent errors (invalid credentials, missing client file), we still log and wait, but with backoff
                # to avoid tight loop. Use exponential backoff based on consecutive failures.
                is_transient = is_transient_error(e)
                # For permanent auth errors (e.g., missing drive_oauth_client.json), we should not retry aggressively
                # But we still wait for next cycle with backoff, not tight loop
                consecutive_failures += 1
                backoff = get_retry_delay(consecutive_failures - 1) if is_transient else 5.0
                # Cap backoff to interval * 2 or max delay to avoid too long wait
                backoff = min(backoff, poll_seconds * 2, float(config.RETRY_MAX_DELAY_SECONDS))
                # For permanent errors, use a longer wait to avoid hammering
                if not is_transient:
                    backoff = max(backoff, float(config.RETRY_MAX_DELAY_SECONDS))

                err_cat = _failure_category("", str(type(e).__name__))
                # Safe logging: do not expose secrets (tokens, keys, private_key)
                safe_msg = str(e)
                # Redact potential secrets (simple heuristic)
                for secret in ["private_key", "token", "API_KEY", "client_secret"]:
                    if secret.lower() in safe_msg.lower():
                        safe_msg = "[REDACTED secret in error]"

                print(f"[Watcher] Cycle {cycle} -- polling error {err_cat}: {type(e).__name__}: {safe_msg} (will retry in {backoff:.1f}s, failures={consecutive_failures})", flush=True)
                if is_transient:
                    log.warning("Watcher cycle %d transient polling error (will retry in %.1fs): %s: %s", cycle, backoff, type(e).__name__, safe_msg, exc_info=True)
                else:
                    log.error("Watcher cycle %d polling error (permanent or auth, will retry in %.1fs): %s: %s", cycle, backoff, type(e).__name__, safe_msg, exc_info=True)
                    # For permanent auth errors, provide actionable guidance
                    if "No Drive OAuth client" in safe_msg or "No Google credentials" in safe_msg:
                        print(f"[Watcher] Hint: Check credentials/drive_oauth_client.json and GOOGLE_DRIVE_OAUTH_CLIENT_FILE in .env (Drive OAuth separate from Sheets service-account)", flush=True)
                        print(f"[Watcher] Hint: For personal Gmail, create OAuth client ID (Desktop) and save JSON, not service-account", flush=True)

                # Sleep with backoff before next cycle (avoid tight loop)
                print(f"[Watcher] Backoff {backoff:.1f}s before next cycle (Ctrl+C to stop)...", flush=True)
                try:
                    time.sleep(backoff)
                except KeyboardInterrupt:
                    raise
                # Also do the regular interval sleep? No, we already did backoff, so skip regular sleep for this cycle
                # Check max_cycles before continuing
                if max_cycles is not None and cycle >= max_cycles:
                    print(f"[Watcher] Reached max_cycles={max_cycles}, stopping", flush=True)
                    break
                continue

            # Check max_cycles for testing (None = infinite)
            if max_cycles is not None and cycle >= max_cycles:
                print(f"[Watcher] Reached max_cycles={max_cycles}, stopping", flush=True)
                break

            # Normal sleep until next cycle (respect interval)
            print(f"[Watcher] Next check in {poll_seconds}s (Ctrl+C to stop)...", flush=True)
            log.info("Watcher sleeping %ds until next cycle (cycle %d done, next %d)", poll_seconds, cycle, cycle+1)
            try:
                time.sleep(poll_seconds)
            except KeyboardInterrupt:
                raise

    except KeyboardInterrupt:
        # Graceful shutdown
        print("\n[Watcher] Stopped by user (Ctrl+C) -- shutting down cleanly", flush=True)
        log.info("Watcher stopped by user (KeyboardInterrupt) at cycle %d", cycle)
        sys.exit(0)
