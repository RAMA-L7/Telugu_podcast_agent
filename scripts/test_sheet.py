#!/usr/bin/env python3
"""Sheet connection test - reads Status=NEW, updates to TEST_OK + Updated At.

Does NOT run transcript/TTS/Drive. Focused on Google Sheets read/write.
Usage:
    python scripts/test_sheet.py          # real write TEST_OK
    python scripts/test_sheet.py --dry-run  # read-only verify
"""
import argparse
import sys
from pathlib import Path

# Ensure project root is on path when run as scripts/test_sheet.py
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils import setup_logging
import config

log = setup_logging()

def main():
    parser = argparse.ArgumentParser(description="Test Google Sheet connection (Status NEW -> TEST_OK)")
    parser.add_argument("--dry-run", action="store_true", help="Read only, do not write")
    args = parser.parse_args()

    print(f"GOOGLE_SHEET_URL: {config.GOOGLE_SHEET_URL or '(using SPREADSHEET_ID)'}")
    print(f"SPREADSHEET_ID: {config.SPREADSHEET_ID or 'NOT SET'}")
    print(f"SHEET_NAME: {config.SHEET_NAME}")
    print(f"GOOGLE_CREDENTIALS_FILE: {config.GOOGLE_CREDENTIALS_FILE} (exists={config.GOOGLE_CREDENTIALS_FILE.exists()})")
    print(f"DRIVE_OUTPUT_FOLDER_ID: {config.DRIVE_OUTPUT_FOLDER_ID or '(root)'}")
    print()

    if not config.SPREADSHEET_ID:
        print("ERROR: Set GOOGLE_SHEET_URL in .env (e.g. https://docs.google.com/spreadsheets/d/<ID>/edit)")
        sys.exit(1)
    if not config.GOOGLE_CREDENTIALS_FILE.exists():
        print(f"ERROR: Credentials file not found: {config.GOOGLE_CREDENTIALS_FILE}")
        print("  -> Follow README section 2: download service_account.json to credentials/")
        sys.exit(1)

    from src.sheet_monitor import test_sheet_connection
    try:
        result = test_sheet_connection(dry_run=args.dry_run)
    except Exception as e:
        log.exception("Sheet test failed")
        print(f"\nFAILED: {e}")
        sys.exit(1)

    print("\n=== Sheet Test Result ===")
    print(f"Header: {result['header']}")
    print(f"Total rows: {result['total_rows']}")
    print(f"Rows with Status=NEW: {result['new_rows']}")
    if args.dry_run:
        print("Dry-run: no writes performed. Remove --dry-run to update to TEST_OK.")
    else:
        print(f"Updated to TEST_OK: {result['updated']}")
        print(f"Timestamp: {result['timestamp']}")
        if result["updated"] == 0 and result["new_rows"] == 0:
            print("Tip: Set a row's Status to exactly NEW (case-insensitive) and re-run.")

if __name__ == "__main__":
    main()
