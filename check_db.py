import sqlite3

conn = sqlite3.connect('data/channel_2.db')
cur = conn.cursor()

# List tables
tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])

# Check runs table
try:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(runs)").fetchall()]
    print("\nRuns cols:", cols)
    rows = cur.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 5").fetchall()
    for r in rows:
        print(dict(zip(cols, r)))
except Exception as e:
    print("runs error:", e)

# Check both target videos
print("\nTarget videos:")
for ttid in ['7641882822097030418', '7641511796104301831']:
    rows = cur.execute(
        "SELECT tiktok_video_id, format_type, status, youtube_video_id, posted_at FROM posted_videos WHERE tiktok_video_id=? AND channel_id=?",
        (ttid, 'channel_2')
    ).fetchall()
    print(f"  TT {ttid}:")
    for r in rows:
        print(f"    {r}")
    if not rows:
        print("    NOT FOUND")

conn.close()
