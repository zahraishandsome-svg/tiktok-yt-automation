import sqlite3
conn = sqlite3.connect('data/channel_2.db')
conn.execute(
    "UPDATE posted_videos SET youtube_video_id=NULL WHERE tiktok_video_id=? AND channel_id=?",
    ('7641511796104301831', 'channel_2')
)
conn.commit()
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
print('Done — youtube_video_id cleared')
