"""
Runs the full TikTok→YouTube pipeline for a single channel.
Called by orchestrator.py — never runs all channels directly.
Returns a result dict so orchestrator can aggregate and notify.
"""

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

from googleapiclient.errors import HttpError

from . import db
from .tiktok_downloader import (
    get_profile_videos, download_video, is_watermarked,
    is_short_video, cleanup_download, cleanup_stale_downloads,
)
from .youtube_uploader import get_authenticated_client, upload_video

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"


def run_channel(channel: Dict[str, Any], slot: int, dry_run: bool = False) -> Dict[str, Any]:
    """
    Full pipeline for one channel, one slot.
    Returns: {channel_id, slot, status, video_uploaded, youtube_url, error}
    Never raises — all exceptions are caught and returned in the result dict.
    """
    channel_id = channel["id"]
    result = {
        "channel_id": channel_id,
        "slot": slot,
        "status": "skipped",
        "video_uploaded": None,
        "youtube_url": None,
        "error": None,
    }

    run_id = db.start_run(channel_id, slot)

    try:
        # Guard: don't double-run a slot that already succeeded today
        if db.slot_already_ran(channel_id, slot):
            logger.info("[%s] Slot %d already ran successfully today — skipping", channel_id, slot)
            db.finish_run(run_id, "skipped")
            result["status"] = "skipped"
            return result

        # Sync channel to DB registry
        db.upsert_channel(channel)

        # Clean up any stale files from previous failed runs
        cleanup_stale_downloads(DOWNLOADS_DIR, max_age_days=7)

        # Pick one video to upload this slot
        video = _pick_next_video(channel, slot)
        if video is None:
            logger.info("[%s] No unposted videos available for slot %d", channel_id, slot)
            db.finish_run(run_id, "no_content")
            result["status"] = "no_content"
            return result

        logger.info("[%s] Selected video: %s | '%s'", channel_id, video["id"], video.get("title", ""))

        # Download
        local_file = _download_with_retry(channel, video, dry_run)
        if local_file is None:
            _handle_download_failure(channel, video, "Download failed after retries")
            db.finish_run(run_id, "failed", error_message="Download failed")
            result["status"] = "failed"
            result["error"] = "Download failed"
            return result

        # Determine Short vs regular
        short = is_short_video(
            duration=video.get("duration"),
            width=video.get("width"),
            height=video.get("height"),
            max_seconds=channel.get("shorts_max_seconds", 180),
        )

        # Upload
        youtube_id = _upload_video(channel, video, local_file, short, dry_run)

        if youtube_id:
            db.mark_uploaded(channel_id, video["id"], youtube_id)
            cleanup_download(local_file)
            db.finish_run(run_id, "success", videos_uploaded=1)
            result["status"] = "success"
            result["video_uploaded"] = video.get("title", video["id"])
            result["youtube_url"] = f"https://www.youtube.com/watch?v={youtube_id}"
            logger.info("[%s] ✓ Uploaded: %s", channel_id, result["youtube_url"])
        else:
            _handle_upload_failure(channel, video, "Upload returned no video ID")
            db.finish_run(run_id, "failed", error_message="Upload failed")
            result["status"] = "failed"
            result["error"] = "Upload returned no video ID"

    except HttpError as exc:
        error_msg = f"YouTube API error: {exc.reason}"
        logger.error("[%s] %s", channel_id, error_msg)
        db.finish_run(run_id, "failed", error_message=error_msg)
        result["status"] = "failed"
        result["error"] = error_msg

    except Exception as exc:
        error_msg = f"Unexpected error: {exc}"
        logger.exception("[%s] %s", channel_id, error_msg)
        db.finish_run(run_id, "failed", error_message=error_msg)
        result["status"] = "failed"
        result["error"] = error_msg

    return result


# ── Video selection ───────────────────────────────────────────────────────────

def _pick_next_video(channel: Dict[str, Any], slot: int) -> Optional[Dict[str, Any]]:
    """
    Priority order:
      1. Videos in pending_retry state that are due today (retries take priority)
      2. New unposted videos, sorted newest-first

    This ensures new content always floats to the top while failed videos
    get their retry window without blocking the queue indefinitely.
    """
    channel_id = channel["id"]
    today = date.today()

    # Check for pending retries first
    retries = db.get_videos_for_retry(channel_id, today)
    if retries:
        logger.info("[%s] Found %d video(s) due for retry", channel_id, len(retries))
        return {
            "id": retries[0]["tiktok_video_id"],
            "url": retries[0]["tiktok_url"],
            "title": retries[0]["tiktok_title"],
            "timestamp": retries[0]["tiktok_timestamp"],
        }

    # Fetch fresh profile and find newest unposted
    videos = get_profile_videos(channel["tiktok_username"])
    if not videos:
        return None

    already_posted = db.get_posted_video_ids(channel_id)

    for video in videos:  # already sorted newest-first
        if video["id"] not in already_posted:
            # Record it in DB so we can track it even if download fails
            db.record_video_seen(channel_id, video)
            return video

    return None


# ── Download ──────────────────────────────────────────────────────────────────

def _download_with_retry(channel: Dict[str, Any], video: Dict[str, Any],
                         dry_run: bool) -> Optional[Path]:
    """Attempt download once (yt-dlp has its own internal retries)."""
    if dry_run:
        logger.info("[DRY RUN] Skipping download for %s", video["id"])
        return DOWNLOADS_DIR / f"{video['id']}.mp4"   # fake path

    channel_dir = DOWNLOADS_DIR / channel["id"]
    return download_video(
        video_url=video["url"],
        video_id=video["id"],
        output_dir=channel_dir,
    )


def _handle_download_failure(channel: Dict[str, Any], video: Dict[str, Any],
                              error_msg: str) -> None:
    today = date.today()
    db.mark_retry(
        channel_id=channel["id"],
        tiktok_video_id=video["id"],
        error_message=error_msg,
        next_retry_date=today + timedelta(days=1),
        max_retries=channel.get("max_retry_days", 3),
    )
    logger.warning("[%s] Video %s queued for retry tomorrow: %s",
                   channel["id"], video["id"], error_msg)


# ── Upload ────────────────────────────────────────────────────────────────────

def _upload_video(channel: Dict[str, Any], video: Dict[str, Any],
                  local_file: Path, is_short: bool, dry_run: bool) -> Optional[str]:
    youtube = get_authenticated_client(
        credentials_file=channel["google_credentials_file"],
        token_file=channel["oauth_token_file"],
    )
    return upload_video(
        youtube_client=youtube,
        video_path=local_file,
        title=video.get("title") or video["id"],
        description=video.get("description") or "",
        tags=list(channel.get("default_tags") or []),
        category_id=str(channel.get("youtube_category_id", "22")),
        is_short=is_short,
        description_footer=channel.get("description_footer", ""),
        dry_run=dry_run,
    )


def _handle_upload_failure(channel: Dict[str, Any], video: Dict[str, Any],
                            error_msg: str) -> None:
    today = date.today()
    db.mark_retry(
        channel_id=channel["id"],
        tiktok_video_id=video["id"],
        error_message=error_msg,
        next_retry_date=today + timedelta(days=1),
        max_retries=channel.get("max_retry_days", 3),
    )
