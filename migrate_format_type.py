"""
One-time migration: add format_type column and update PRIMARY KEY.

Before: PRIMARY KEY (channel_id, tiktok_video_id)
After:  PRIMARY KEY (channel_id, tiktok_video_id, format_type)

All existing rows get format_type = 'short'.
SQLite does not support ALTER TABLE … DROP/ADD CONSTRAINT, so the table is
recreated via the standard copy-rename pattern.

Run once per DB file:
    python migrate_format_type.py                  # migrates all channel_*.db files
    python migrate_format_type.py channel_1.db     # single file
"""

import sys
import sqlite3
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"


def needs_migration(conn: sqlite3.Connection) -> bool:
    """Return True if the posted_videos table does NOT yet have format_type."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(posted_videos)")]
    return "format_type" not in cols


def migrate(db_path: Path) -> None:
    print(f"  Checking {db_path.name} …", end=" ")
    conn = sqlite3.connect(str(db_path))
    try:
        if not needs_migration(conn):
            print("already migrated, skipped.")
            return

        # Count rows before
        before = conn.execute("SELECT COUNT(*) FROM posted_videos").fetchone()[0]

        conn.executescript("""
            -- Step 1: create new table with updated schema
            CREATE TABLE posted_videos_new (
                channel_id          TEXT NOT NULL,
                tiktok_video_id     TEXT NOT NULL,
                format_type         TEXT NOT NULL DEFAULT 'short',
                tiktok_url          TEXT,
                tiktok_title        TEXT,
                tiktok_timestamp    INTEGER,
                youtube_video_id    TEXT,
                posted_at           TEXT,
                status              TEXT DEFAULT 'pending',
                retry_count         INTEGER DEFAULT 0,
                next_retry_date     TEXT,
                error_message       TEXT,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (channel_id, tiktok_video_id, format_type)
            );

            -- Step 2: copy all existing rows, setting format_type = 'short'
            INSERT INTO posted_videos_new
            SELECT
                channel_id,
                tiktok_video_id,
                'short'           AS format_type,
                tiktok_url,
                tiktok_title,
                tiktok_timestamp,
                youtube_video_id,
                posted_at,
                status,
                retry_count,
                next_retry_date,
                error_message,
                created_at,
                updated_at
            FROM posted_videos;

            -- Step 3: swap tables
            DROP TABLE posted_videos;
            ALTER TABLE posted_videos_new RENAME TO posted_videos;

            -- Step 4: recreate index
            DROP INDEX IF EXISTS idx_posted_videos_channel_status;
            CREATE INDEX idx_posted_videos_channel_status
                ON posted_videos (channel_id, status);
        """)

        after = conn.execute("SELECT COUNT(*) FROM posted_videos").fetchone()[0]
        print(f"migrated ({before} rows -> {after} rows with format_type='short').")
    finally:
        conn.close()


def main():
    if len(sys.argv) > 1:
        targets = [DATA_DIR / sys.argv[1]]
    else:
        targets = sorted(DATA_DIR.glob("channel_*.db"))
        if not targets:
            targets = [DATA_DIR / "automation.db"]

    missing = [p for p in targets if not p.exists()]
    if missing:
        print("DB file(s) not found:")
        for p in missing:
            print(f"  {p}")
        sys.exit(1)

    print(f"Migrating {len(targets)} DB file(s)…")
    for db_path in targets:
        migrate(db_path)
    print("Done.")


if __name__ == "__main__":
    main()
