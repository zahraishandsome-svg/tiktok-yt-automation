import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('data/channel_2.db')
cur = conn.cursor()

# Both target videos - key fields only
print("=== Both target videos ===")
for ttid in ['7641882822097030418', '7641511796104301831']:
    rows = cur.execute(
        "SELECT tiktok_video_id, format_type, status, youtube_video_id, tiktok_timestamp, posted_at FROM posted_videos WHERE tiktok_video_id=? AND channel_id=?",
        (ttid, 'channel_2')
    ).fetchall()
    for r in rows:
        print(r)
    if not rows:
        print(f"  {ttid}: NOT FOUND")

# All runs for today
print("\n=== Runs for 2026-05-29 ===")
rcols = [r[1] for r in cur.execute("PRAGMA table_info(runs)").fetchall()]
rows = cur.execute(
    "SELECT id, slot, status, videos_uploaded, started_at, completed_at FROM runs WHERE channel_id=? AND run_date=? ORDER BY id",
    ('channel_2', '2026-05-29')
).fetchall()
for r in rows:
    print(r)

# All deleted_repost_pending
print("\n=== deleted_repost_pending ===")
rows = cur.execute(
    "SELECT tiktok_video_id, format_type, status, tiktok_timestamp FROM posted_videos WHERE channel_id=? AND status=?",
    ('channel_2', 'deleted_repost_pending')
).fetchall()
for r in rows:
    print(r)

conn.close()
