"""
Behaviour tests for the Longform-only picker rule (owner, 18 Sep 2026):

  On a Longform-only channel any TikTok video may be uploaded as Longform,
  however new, as long as that video has not been uploaded as Longform before.
  A video that once went out as a Short is eligible again.

  Channels that still post Shorts keep the old rules: 15-day minimum age and
  one upload per video in any format.

No network, no DB, no YouTube API.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src import config as cfg
from src import channel_runner as cr


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        sys.exit(1)


NOW = datetime.now(timezone.utc)


def ts(days_ago):
    return int((NOW - timedelta(days=days_ago)).timestamp())


PROFILE = [
    {"id": "v_today", "timestamp": ts(0), "title": "posted today"},
    {"id": "v_2d_short", "timestamp": ts(2), "title": "went out as a Short 2 days ago"},
    {"id": "v_20d_longformed", "timestamp": ts(20), "title": "already a Longform"},
    {"id": "v_30d", "timestamp": ts(30), "title": "old and untouched"},
]

# What the DB "knows": v_2d_short was posted as a Short, v_20d_longformed as Longform.
POSTED_ANY_FORMAT = {"v_2d_short", "v_20d_longformed"}
POSTED_LONGFORM = {"v_20d_longformed"}

seen = []


def install_stubs():
    cr.get_profile_videos = lambda user, end=None: list(PROFILE)
    cr.db.get_videos_for_retry = lambda *a, **k: []
    cr.db.get_longformed_video_ids = lambda cid: set(POSTED_LONGFORM)
    cr.db.get_posted_video_ids = lambda cid, upload_mode="short_only", **k: set(POSTED_ANY_FORMAT)
    cr.db.record_video_seen = lambda cid, v, format_type=None: seen.append((v["id"], format_type))


install_stubs()

conf = cfg.load_config()
ch3 = [c for c in conf["channels"] if c["id"] == "channel_3"][0]

# ── 1. channel_3 is recognised as Longform-only ─────────────────────────────
check("channel_3 counts as Longform-only", cr._is_longform_only_channel(ch3) is True)
check("upload_mode longform_only counts too",
      cr._is_longform_only_channel({"upload_mode": "longform_only"}) is True)
check("a channel that still posts Shorts does NOT count",
      cr._is_longform_only_channel({"upload_mode": "tiered_split"}) is False)
check("short_only does NOT count",
      cr._is_longform_only_channel({"upload_mode": "short_only", "skip_slots": [1]}) is False)

# ── 2. no age filter: today's video is eligible ─────────────────────────────
pick = cr._pick_tiered_split_longform(ch3)
check("picks today's video — no 15-day wait", pick["id"] == "v_today")
check("recorded as longform", seen and seen[-1] == ("v_today", "longform"))

# ── 3. a video already posted as a Short is still eligible ──────────────────
pick = cr._pick_tiered_split_longform(ch3, exclude_ids={"v_today"})
check("old Short can become a Longform", pick["id"] == "v_2d_short")

# ── 4. a video already uploaded as Longform is never picked again ───────────
pick = cr._pick_tiered_split_longform(ch3, exclude_ids={"v_today", "v_2d_short"})
check("already-longformed video is skipped", pick["id"] == "v_30d")

# ── 5. nothing left → None, and no crash ────────────────────────────────────
pick = cr._pick_tiered_split_longform(ch3, exclude_ids={"v_today", "v_2d_short", "v_30d"})
check("returns None when everything is used up", pick is None)

# ── 6. an explicit longform_min_age_days still wins ─────────────────────────
ch3_override = dict(ch3, longform_min_age_days=15)
pick = cr._pick_tiered_split_longform(ch3_override)
check("explicit 15-day override is still honoured", pick["id"] == "v_30d")

# ── 7. channels that still post Shorts are untouched ────────────────────────
mixed = dict(ch3)
mixed.pop("skip_slots", None)
mixed["id"] = "channel_mixed"
pick = cr._pick_tiered_split_longform(mixed)
check("mixed channel keeps the 15-day wait", pick["id"] == "v_30d")
check("mixed channel still excludes any-format uploads",
      cr._pick_tiered_split_longform(mixed, exclude_ids={"v_30d"}) is None)

# ── 8. channels.yaml no longer pins channel_3 to 15 days ────────────────────
check("channel_3 has no longform_min_age_days in config",
      "longform_min_age_days" not in ch3)

# ── 9. the private / no-Shorts settings from earlier today still hold ───────
check("channel_3 still uploads Private", ch3["privacy_status"] == "private")
check("channel_3 still skips slot 1", ch3["skip_slots"] == [1])

print("\nall checks passed")
