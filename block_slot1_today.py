import sqlite3
from datetime import datetime

conn = sqlite3.connect('data/channel_2.db')
# Insert a dummy slot 1 success run for today so the cron guard skips it
conn.execute(
    "INSERT INTO runs (channel_id, run_date, slot, status, videos_uploaded, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
    ('channel_2', '2026-05-29', 1, 'success', 0,
     '2026-05-29T06:05:00.000000', '2026-05-29T06:05:01.000000')
)
conn.commit()
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')

# Verify
rows = conn.execute(
    "SELECT id, slot, status, videos_uploaded, started_at FROM runs WHERE channel_id='channel_2' AND run_date='2026-05-29' ORDER BY slot"
).fetchall()
print("Runs for today after insert:")
for r in rows:
    print(f"  {r}")
conn.close()
