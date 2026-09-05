#!/usr/bin/env python3
"""Milestone 3: Connect Google Sheet rows (NEW/TEST_OK) -> YouTube transcript extraction.

For each row with Status NEW or TEST_OK:
  - reads YouTube Link
  - validates (EmptyLinkError / InvalidLinkError) -> TRANSCRIPT_FAILED + Error
  - calls existing src.transcript.fetch_and_save_transcript (api -> yt-dlp fallback)
  - saves locally to output/transcripts/<video_id>.txt/.json
  - updates sheet ONLY when valid: Status TRANSCRIPT_DONE + Transcript Link + Title + Updated At + clear Error
  - on failure: Status TRANSCRIPT_FAILED + Error + Updated At (Transcript Link NOT overwritten)
  - handles timestamps (IST) and errors per 12-col schema

Does NOT touch .env / credentials. Sheet-safe.

Usage (from project root):
  python scripts/run_transcripts.py --dry-run              # preview, no writes
  python scripts/run_transcripts.py --dry-run --limit 2    # preview first 2
  python scripts/run_transcripts.py                          # live (writes sheet)
  python scripts/run_transcripts.py --limit 1

Requires: GOOGLE_SHEET_URL, GOOGLE_CREDENTIALS_FILE, sheet shared with service account.
"""
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils import setup_logging
import config

log = setup_logging()

def main():
    parser = argparse.ArgumentParser(description="Milestone 3: Sheet (NEW/TEST_OK) -> transcript -> Status TRANSCRIPT_DONE/FAILED")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write to sheet (still fetches & saves locally? No — dry-run also skips fetch save? Actually fetches but skips sheet write)")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N rows")
    args = parser.parse_args()

    print(f"GOOGLE_SHEET_URL: {config.GOOGLE_SHEET_URL or '(using SPREADSHEET_ID)'}")
    print(f"SHEET_NAME: {config.SHEET_NAME}")
    print(f"SPREADSHEET_ID: {config.SPREADSHEET_ID or 'NOT SET'}")
    print(f"GOOGLE_CREDENTIALS_FILE: {config.GOOGLE_CREDENTIALS_FILE} (exists={config.GOOGLE_CREDENTIALS_FILE.exists()})")
    print(f"OUTPUT: {config.OUTPUT_DIR} | TRANSCRIPTS: {config.TRANSCRIPT_DIR}")
    print(f"Dry-run: {args.dry_run} | Limit: {args.limit}")
    print()

    if not config.SPREADSHEET_ID:
        print("ERROR: Set GOOGLE_SHEET_URL in .env")
        sys.exit(1)
    if not config.GOOGLE_CREDENTIALS_FILE.exists():
        print(f"ERROR: Credentials not found: {config.GOOGLE_CREDENTIALS_FILE}")
        sys.exit(1)

    from src.sheet_monitor import run_transcript_pipeline
    try:
        summary = run_transcript_pipeline(dry_run=args.dry_run, limit=args.limit)
    except Exception as e:
        log.exception("Transcript pipeline failed")
        print(f"\nFAILED: {e}")
        sys.exit(1)

    print("\n=== Transcript Pipeline Result ===")
    print(f"Header: {summary['header']}")
    print(f"Pending (NEW/TEST_OK): {summary['total_pending']}")
    print(f"Processed: {summary['processed']} | DONE: {summary['done']} | FAILED: {summary['failed']} | dry_run={summary['dry_run']}")
    for d in summary["details"]:
        rid = d.get("row_num")
        if d.get("valid"):
            print(f"  Row {rid}: TRANSCRIPT_DONE vid={d.get('video_id')} -> {d.get('transcript_link')} {'(dry-run)' if d.get('dry_run') else ''}")
        else:
            print(f"  Row {rid}: TRANSCRIPT_FAILED [{d.get('error_type')}] {str(d.get('error',''))[:80]} {'(dry-run)' if d.get('dry_run') else ''}")

if __name__ == "__main__":
    main()
