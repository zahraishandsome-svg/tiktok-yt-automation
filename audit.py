import sqlite3, sys, io, os
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

dbs = {
    'channel_1': 'data/channel_1.db',
    'channel_2': 'data/channel_2.db',
    'channel_3': 'data/channel_3.db',
}

TODAY = '2026-05-29'

for ch, dbpath in dbs.items():
    if not Path(dbpath).exists():
        print(f"\n=== {ch} — DB FILE MISSING ===")
        continue

    conn = sqlite3.connect(dbpath)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print(f"\n{'='*60}")
    print(f"  CHANNEL: {ch}")
    print(f"{'='*60}")

    # Today's runs
    runs = cur.execute(
        "SELECT id, slot, status, videos_uploaded, started_at, completed_at FROM runs WHERE channel_id=? AND run_date=? ORDER BY slot, id",
        (ch, TODAY)
    ).fetchall()
    print(f"\n[Runs today]")
    if runs:
        for r in runs:
            print(f"  run_id={r['id']} slot={r['slot']} status={r['status']} videos_uploaded={r['videos_uploaded']} started={r['started_at'][:16]} completed={str(r['completed_at'])[:16]}")
    else:
        print("  (none yet)")

    # Latest 5 uploaded videos
    uploaded = cur.execute(
        "SELECT tiktok_video_id, format_type, status, youtube_video_id, posted_at FROM posted_videos WHERE channel_id=? AND status='uploaded' ORDER BY posted_at DESC LIMIT 5",
        (ch,)
    ).fetchall()
    print(f"\n[Last 5 uploaded]")
    for r in uploaded:
        print(f"  TT={r['tiktok_video_id']} fmt={r['format_type']} yt={r['youtube_video_id']} at={str(r['posted_at'])[:16]}")

    # Any pending/retry/deleted
    pending = cur.execute(
        "SELECT tiktok_video_id, format_type, status, next_retry_date FROM posted_videos WHERE channel_id=? AND status NOT IN ('uploaded','skipped','failed_permanent') ORDER BY posted_at DESC LIMIT 10",
        (ch,)
    ).fetchall()
    print(f"\n[Pending/retry/deleted_repost_pending]")
    if pending:
        for r in pending:
            print(f"  TT={r['tiktok_video_id']} fmt={r['format_type']} status={r['status']} next_retry={r['next_retry_date']}")
    else:
        print("  (none)")

    # Check both format rows for today's uploaded videos
    today_vids = cur.execute(
        "SELECT tiktok_video_id, format_type, status, youtube_video_id FROM posted_videos WHERE channel_id=? AND posted_at >= ? ORDER BY posted_at DESC",
        (ch, TODAY)
    ).fetchall()
    print(f"\n[All rows posted today]")
    if today_vids:
        for r in today_vids:
            print(f"  TT={r['tiktok_video_id']} fmt={r['format_type']} status={r['status']} yt={r['youtube_video_id']}")
    else:
        print("  (none)")

    conn.close()
