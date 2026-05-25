"""
Discord webhook notifications.
Only fires when DISCORD_WEBHOOK_URL is set in .env — silent otherwise.
"""

import logging
import requests
from datetime import date
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

_TIMEOUT = 10   # seconds


def send_failure_alert(webhook_url: str, failures: List[Dict[str, Any]], slot: int) -> None:
    """Post a failure alert to Discord immediately when any channel fails."""
    if not webhook_url:
        return

    lines = [f"🚨 **TikTok→YT Automation | Slot {slot} Failures** ({date.today()})"]
    for f in failures:
        lines.append(f"• `{f['channel_id']}` — {f.get('error', 'unknown error')}")

    _post(webhook_url, "\n".join(lines))


def send_daily_summary(webhook_url: str, results: List[Dict[str, Any]]) -> None:
    """Post an end-of-day summary after slot 2 completes."""
    if not webhook_url:
        return

    success = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]
    no_content = [r for r in results if r["status"] == "no_content"]

    emoji = "✅" if not failed else "⚠️"
    lines = [
        f"{emoji} **Daily Upload Summary** ({date.today()})",
        f"Uploaded: {len(success)} | Failed: {len(failed)} | No content: {len(no_content)}",
    ]

    for r in success:
        lines.append(f"  ✓ `{r['channel_id']}` → {r.get('youtube_url', '?')}")
    for r in failed:
        lines.append(f"  ✗ `{r['channel_id']}` — {r.get('error', '?')}")

    _post(webhook_url, "\n".join(lines))


def _post(webhook_url: str, content: str) -> None:
    try:
        resp = requests.post(
            webhook_url,
            json={"content": content[:2000]},   # Discord limit is 2000 chars
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        logger.debug("Discord notification sent")
    except requests.RequestException as exc:
        # Notification failure must never crash the main run
        logger.warning("Discord notification failed: %s", exc)
