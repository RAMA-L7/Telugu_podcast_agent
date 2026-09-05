#!/usr/bin/env python3
"""
Telugu Podcast Agent - Main Orchestrator
Monitor Google Sheet -> transcript -> Telugu script -> MP3 -> Drive -> Sheet link
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import config
from src.utils import setup_logging, extract_video_id, output_paths, already_processed, save_processed

log = setup_logging()

def process_url(youtube_url: str, dry_run: bool = False) -> dict:
    """Process single YouTube URL end-to-end. Returns {mp3_path, drive_link, script, title}."""
    from src.transcript import fetch_transcript
    from src.script_generator import generate_telugu_script
    from src.tts import generate_podcast_mp3
    from src.drive_uploader import upload_to_drive

    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {youtube_url}")

    log.info("=== Processing %s (video_id=%s) ===", youtube_url, video_id)

    # 1. Transcript
    log.info("Fetching transcript...")
    transcript, title = fetch_transcript(youtube_url, max_chars=config.MAX_TRANSCRIPT_CHARS)
    log.info("Transcript: %d chars | Title: %s", len(transcript), title)

    # 2. Telugu script
    log.info("Generating Telugu podcast script...")
    script = generate_telugu_script(transcript, title=title)
    log.info("Script: %d turns", len(script))
    for turn in script:
        log.info("  %s: %s", turn["speaker"], turn["text"][:80])

    # 3. MP3
    mp3_path, json_path = output_paths(video_id, title)
    # Save script sidecar
    json_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Script saved: %s", json_path)

    log.info("Generating MP3 via %s...", config.TTS_ENGINE)
    generate_podcast_mp3(script, mp3_path)
    log.info("MP3 ready: %s (%.1f KB)", mp3_path, mp3_path.stat().st_size / 1024)

    # 4. Drive upload
    drive_link = ""
    if dry_run:
        log.info("Dry-run: skipping Drive upload")
        drive_link = f"dry-run://{mp3_path.name}"
    else:
        try:
            drive_link = upload_to_drive(mp3_path)
        except Exception as e:
            log.warning("Drive upload failed (saving locally): %s", e)
            drive_link = f"local://{mp3_path.absolute()}"

    save_processed(video_id)
    return {"mp3_path": str(mp3_path), "drive_link": drive_link, "script": script, "title": title, "video_id": video_id}

def sheet_callback(row: dict, ws):
    """Called for each pending row from poll_loop."""
    from src.sheet_monitor import update_row
    url = row["url"]
    row_num = row["row_num"]
    video_id = extract_video_id(url)

    if video_id and already_processed(video_id):
        log.info("Skipping already processed %s", video_id)
        # Still update sheet if Drive link missing - try to find local file
        mp3_candidates = list(config.OUTPUT_DIR.glob(f"*_{video_id}*.mp3"))
        if mp3_candidates:
            link = f"local://{mp3_candidates[0].absolute()}"
            update_row(ws, row_num, "DONE", drive_link=link)
        else:
            update_row(ws, row_num, "DONE", drive_link="already processed")
        return

    try:
        result = process_url(url, dry_run=False)
        update_row(ws, row_num, "DONE", drive_link=result["drive_link"], title=result["title"][:100])
    except Exception as e:
        log.exception("Processing failed for row %d %s", row_num, url)
        update_row(ws, row_num, f"ERROR: {str(e)[:80]}")

def main():
    parser = argparse.ArgumentParser(description="Telugu Podcast Agent")
    parser.add_argument("--once", action="store_true", help="Process pending rows once and exit")
    parser.add_argument("--url", type=str, help="Process single YouTube URL directly (no sheet)")
    parser.add_argument("--dry-run", action="store_true", help="Skip Drive upload and sheet update")
    parser.add_argument("--poll-interval", type=int, default=None, help="Override POLL_INTERVAL_SECONDS")
    parser.add_argument("--test-sheet", action="store_true", help="Sheet connection test: NEW -> TEST_OK + Updated At (no transcript/audio)")
    parser.add_argument("--run-transcripts", action="store_true", help="Milestone 3: process rows NEW/TEST_OK -> fetch transcript -> TRANSCRIPT_DONE/FAILED (use --dry-run to preview)")
    parser.add_argument("--limit", type=int, default=None, help="Limit rows processed (with --run-transcripts/--test-sheet)")
    args = parser.parse_args()

    # Sheet connection test mode (focused step, no transcript/audio)
    if args.test_sheet:
        from src.sheet_monitor import test_sheet_connection
        result = test_sheet_connection(dry_run=args.dry_run)
        print("\n=== Sheet Test Result ===")
        print(f"Header: {result['header']}")
        print(f"Total rows: {result['total_rows']}")
        print(f"Rows with Status=NEW: {result['new_rows']}")
        if args.dry_run:
            print("Dry-run: no writes performed.")
        else:
            print(f"Updated to TEST_OK: {result['updated']} @ {result.get('timestamp','')}")
        return

    # Milestone 3: transcript pipeline
    if args.run_transcripts:
        from src.sheet_monitor import run_transcript_pipeline
        summary = run_transcript_pipeline(dry_run=args.dry_run, limit=args.limit)
        print("\n=== Transcript Pipeline Result ===")
        print(f"Pending (NEW/TEST_OK): {summary['total_pending']} | Done: {summary['done']} | Failed: {summary['failed']} | dry_run={summary['dry_run']}")
        for d in summary["details"]:
            rid = d.get("row_num")
            if d.get("valid"):
                print(f"  Row {rid}: TRANSCRIPT_DONE vid={d.get('video_id')} -> {d.get('transcript_link')}")
            else:
                print(f"  Row {rid}: TRANSCRIPT_FAILED [{d.get('error_type')}] {str(d.get('error',''))[:100]}")
        return

    # Single URL mode
    if args.url:
        result = process_url(args.url, dry_run=args.dry_run)
        print("\n=== DONE ===")
        print(f"MP3: {result['mp3_path']}")
        print(f"Link: {result['drive_link']}")
        print(f"Title: {result['title']}")
        return

    # Sheet polling mode
    if not config.SPREADSHEET_ID:
        print("ERROR: GOOGLE_SHEET_URL not set in .env")
        print("Set GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/<ID>/edit")
        print("Or use --url mode: python main.py --url \"https://youtube.com/watch?v=...\"")
        print("Or test sheet: python scripts/test_sheet.py --dry-run")
        sys.exit(1)

    interval = args.poll_interval or config.POLL_INTERVAL_SECONDS
    log.info("Sheet: %s | Tab: %s | Interval: %ds", config.SPREADSHEET_ID, config.SHEET_NAME, interval)
    log.info("Output: %s | TTS: %s | LLM: %s/%s", config.OUTPUT_DIR, config.TTS_ENGINE, config.LLM_PROVIDER, config.LLM_MODEL)

    # Check credentials early
    if not config.GOOGLE_CREDENTIALS_PATH.exists() and not config.GOOGLE_TOKEN_PATH.exists():
        log.warning("No credentials found at %s - sheet/drive will fail. Use --url --dry-run for local test.",
                    config.GOOGLE_CREDENTIALS_PATH)

    from src.sheet_monitor import poll_loop
    try:
        poll_loop(sheet_callback, interval=interval, once=args.once)
    except KeyboardInterrupt:
        log.info("Stopped by user")

if __name__ == "__main__":
    main()
