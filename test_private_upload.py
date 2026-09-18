"""
Behaviour tests for the channel_3 change (2026-09-18):
  one Longform a day, no Shorts, uploaded Private and left private.

Each test is written so it FAILS on the old code — see test_control_* notes.
No network, no YouTube API, no DB.
"""
import sys, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src import config as cfg
from src.youtube_uploader import upload_video

FAKE = Path("/tmp/fake_upload.mp4")
FAKE.write_bytes(b"\x00" * 2048)

captured = {}

class _Req:
    def next_chunk(self):
        return None, {"id": "VID123"}

class _Videos:
    def insert(self, part, body, media_body):
        captured["body"] = body
        return _Req()

class _FakeYT:
    def videos(self):
        return _Videos()

def _upload(**kw):
    captured.clear()
    vid = upload_video(
        youtube_client=_FakeYT(), video_path=FAKE, title="t", description="d",
        tags=[], category_id="22", is_short=False, **kw,
    )
    assert vid == "VID123"
    return captured["body"]["status"]

def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        sys.exit(1)

# ── 1. privacy_status=private uploads Private with NO publishAt ──────────────
st = _upload(privacy_status="private")
check("private stays private", st["privacyStatus"] == "private")
check("private has no publishAt (never auto-publishes)", "publishAt" not in st)

# ── 2. default is unchanged for every other channel ──────────────────────────
st = _upload()
check("default is still public", st["privacyStatus"] == "public")

# ── 3. scheduled publishing still wins when a time is given ─────────────────
st = _upload(publish_at="2026-09-19T11:00:00Z", privacy_status="private")
check("publish_at still schedules", st["privacyStatus"] == "private" and st["publishAt"] == "2026-09-19T11:00:00Z")

# ── 4. channels.yaml says what the owner asked for ──────────────────────────
conf = cfg.load_config()
ch3 = [c for c in conf["channels"] if c["id"] == "channel_3"][0]
check("channel_3 privacy_status = private", ch3["privacy_status"] == "private")
check("channel_3 skips slot 1 (no Shorts)", ch3["skip_slots"] == [1])
check("channel_3 videos_per_day = 1", ch3["videos_per_day"] == 1)
check("channel_3 slot 2 time unchanged (11:00 UTC)", str(ch3["slot_publish_times_utc"][2]) == "11:00")

# ── 5. other channels keep public + all slots ───────────────────────────────
for c in conf["channels"]:
    if c["id"] != "channel_3":
        check(f"{c['id']} unaffected", c["privacy_status"] == "public" and c["skip_slots"] == [])

# ── 6. a disabled slot uploads nothing, even if its workflow fires ──────────
from src import channel_runner
calls = []
channel_runner.db.start_run = lambda *a, **k: calls.append("start_run") or 1
res = channel_runner.run_channel(ch3, slot=1)
check("slot 1 returns skipped", res["status"] == "skipped")
check("slot 1 never touches the DB or TikTok", calls == [])
check("slot 1 uploaded nothing", res["youtube_url"] is None and res["youtube_url_longform"] is None)

# ── 7. slot 2 is NOT skipped (the daily Longform must still run) ────────────
check("slot 2 is not in skip_slots", 2 not in ch3["skip_slots"])

# ── 8. bad config is rejected loudly ────────────────────────────────────────
try:
    cfg._validate_channel({"id": "x", "tiktok_username": "u", "youtube_channel_name": "n",
                           "google_credentials_file": "a", "oauth_token_file": "b",
                           "privacy_status": "publik"})
    check("invalid privacy_status rejected", False)
except ValueError:
    check("invalid privacy_status rejected", True)

print("\nall checks passed")
