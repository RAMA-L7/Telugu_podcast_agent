#!/usr/bin/env python3
"""Standalone transcript test — validates empty/invalid handling and local save.

Does NOT update Google Sheet. Sheet is updated only when result.valid is True
(caller would then set Transcript Link / Title).
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.transcript import validate_youtube_link, fetch_and_save_transcript, EmptyLinkError, InvalidLinkError
from src.utils import setup_logging
import argparse
import json

def main():
    parser = argparse.ArgumentParser(description="Test transcript extraction (local-only, sheet-safe)")
    parser.add_argument("--url", type=str, help="YouTube URL or 11-char id")
    parser.add_argument("--test-empty", action="store_true", help="Trigger EmptyLinkError")
    parser.add_argument("--test-invalid", action="store_true", help="Trigger InvalidLinkError")
    args = parser.parse_args()

    if args.test_empty:
        try:
            validate_youtube_link("")
        except EmptyLinkError as e:
            print(f"EmptyLinkError OK: {e}")
            sys.exit(0)
        print("EmptyLinkError not raised!")
        sys.exit(1)

    if args.test_invalid:
        try:
            validate_youtube_link("https://example.com/not-youtube")
        except InvalidLinkError as e:
            print(f"InvalidLinkError OK: {e}")
            sys.exit(0)
        print("InvalidLinkError not raised!")
        sys.exit(1)

    if args.url:
        setup_logging()
        result = fetch_and_save_transcript(args.url)
        print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in result.items() if k != "transcript"}, indent=2, ensure_ascii=False))
        if result["valid"]:
            print(f"\nSaved: {result['txt_path']} ({result['txt_path'].stat().st_size} bytes) and {result['json_path']}")
            print("Sheet: would update Transcript Link + Title now (valid only).")
        else:
            print(f"\nNot saved: [{result['error_type']}] {result['error']}")
            print("Sheet: would NOT update Transcript Link; would set Error column only.")
        sys.exit(0 if result["valid"] else 2)
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python scripts/test_transcript.py --test-empty")
        print("  python scripts/test_transcript.py --test-invalid")
        print("  python scripts/test_transcript.py --url https://www.youtube.com/watch?v=dQw4w9WgXcQ")

if __name__ == "__main__":
    main()
