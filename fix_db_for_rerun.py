import sqlite3

conn = sqlite3.connect('data/channel_2.db')
cur = conn.cursor()

# Delete run 41 (slot 2 today) to allow re-run
cur.execute("DELETE FROM runs WHERE id = 41")
deleted = cur.rowcount
print(f"Deleted {deleted} run row(s) with id=41")

conn.commit()

# WAL checkpoint
result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
print(f"WAL checkpoint: {result}")

conn.close()

# Verify
conn2 = sqlite3.connect('data/channel_2.db')
cur2 = conn2.cursor()
rows = cur2.execute("SELECT id, slot, status, videos_uploaded FROM runs WHERE channel_id='channel_2' AND run_date='2026-05-29'").fetchall()
print(f"Runs remaining for today: {rows}")

rows2 = cur2.execute(
    "SELECT tiktok_video_id, format_type, status, youtube_video_id FROM posted_videos WHERE channel_id='channel_2' AND tiktok_video_id IN ('7641882822097030418','7641511796104301831')"
).fetchall()
print(f"Target video states: {rows2}")
conn2.close()
