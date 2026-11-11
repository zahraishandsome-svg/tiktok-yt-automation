#!/usr/bin/env python3
"""AI thumbnails for long-form uploads (runs in the upload workflow, after the slot).

  python thumbnails.py --channel channel_3 --lang Japanese            # today's new long videos
  python thumbnails.py --channel channel_3 --lang Japanese --backfill 50   # latest N long videos

For each long-form upload that has no AI thumbnail yet: download the original TikTok,
src.ai_thumbnail makes the thumbnail (Claude plans + checks the main character's identity,
Vertex Gemini draws, we add the text), then YouTube thumbnails.set. Done videos are kept in
the `thumbnails` table so nothing is made twice. A failure never fails the upload job.

Env: ANTHROPIC_API_KEY, VERTEX_API_KEY, VERTEX_PROJECT, DB_PAGE_ID, TIKTOK_COOKIES_FILE
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def db():
    page = os.environ.get("DB_PAGE_ID")
    con = sqlite3.connect(str(ROOT / "data" / (f"{page}.db" if page else "automation.db")))
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS thumbnails (
        youtube_video_id TEXT PRIMARY KEY,
        channel_id TEXT, hook TEXT, identity TEXT, made_at TEXT)""")
    return con


def youtube(channel):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    tok = json.loads((ROOT / channel["oauth_token_file"]).read_text(encoding="utf-8"))
    cid, csec = tok.get("client_id"), tok.get("client_secret")
    if not (cid and csec):
        sec = json.loads((ROOT / channel["google_credentials_file"]).read_text(encoding="utf-8"))
        b = sec.get("installed") or sec.get("web") or {}
        cid, csec = b.get("client_id"), b.get("client_secret")
    c = Credentials(token=tok.get("token"), refresh_token=tok.get("refresh_token"),
                    token_uri=tok.get("token_uri", "https://oauth2.googleapis.com/token"),
                    client_id=cid, client_secret=csec,
                    scopes=tok.get("scopes") or ["https://www.googleapis.com/auth/youtube.upload"])
    if not c.valid:
        c.refresh(Request())
    return build("youtube", "v3", credentials=c, cache_discovery=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--lang", default="Japanese")
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--video", default="", help="one YouTube id (re-made even if done)")
    a = ap.parse_args()
    if not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("VERTEX_API_KEY")):
        print("thumbnails: API keys missing - skipped")
        return 0

    from googleapiclient.http import MediaFileUpload
    from src import ai_thumbnail as at
    from src.tiktok_downloader import download_video

    cfg = yaml.safe_load((ROOT / "channels.yaml").read_text(encoding="utf-8-sig"))
    channel = next(c for c in cfg["channels"] if c["id"] == a.channel)
    con = db()
    done = {r[0] for r in con.execute("SELECT youtube_video_id FROM thumbnails")}
    seed = ROOT / "data" / "thumbnails_done.txt"   # thumbnails made outside the workflow
    if seed.exists():
        done |= {ln.strip() for ln in seed.read_text().splitlines() if ln.strip()}
    q = ("SELECT youtube_video_id, tiktok_video_id, tiktok_url, tiktok_title FROM posted_videos "
         "WHERE channel_id=? AND format_type='longform' AND status='uploaded' "
         "AND youtube_video_id IS NOT NULL AND youtube_video_id NOT LIKE 'already%' ")
    if a.video:
        rows = con.execute(q + "AND youtube_video_id=?", (a.channel, a.video)).fetchall()
        done.discard(a.video)
    elif a.backfill:
        rows = con.execute(q + "ORDER BY posted_at DESC LIMIT ?", (a.channel, a.backfill)).fetchall()
    else:
        rows = con.execute(q + "AND posted_at LIKE ? ORDER BY posted_at", (a.channel, date.today().isoformat() + "%")).fetchall()
    todo = [r for r in rows if r["youtube_video_id"] not in done]
    print(f"thumbnails: {len(rows)} long video(s) in scope, {len(todo)} without an AI thumbnail")
    if not todo:
        return 0

    yt = youtube(channel)
    ok = 0
    for r in todo:
        vid = r["youtube_video_id"]
        work = Path(tempfile.mkdtemp(prefix=f"thumb_{vid}_"))
        try:
            url = r["tiktok_url"] or f"https://www.tiktok.com/@{channel['tiktok_username']}/video/{r['tiktok_video_id']}"
            src = download_video(url, r["tiktok_video_id"], work)
            if not src:
                raise RuntimeError("original TikTok could not be downloaded")
            fr = at.frames(Path(src), work / "frames")
            plan = at.plan(fr, r["tiktok_title"] or "", a.lang)
            base, how = at.build_base(plan, fr)
            thumb = at.add_text(base.copy(), plan.get("hook_lines") or [plan["hook_text"]],
                                plan["text_side"], plan["accent_color"])
            out = work / "thumb.jpg"
            thumb.save(out, quality=90)
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(out), mimetype="image/jpeg")).execute()
            with con:
                con.execute("INSERT OR REPLACE INTO thumbnails VALUES (?,?,?,?,?)",
                            (vid, a.channel, plan["hook_text"], how, datetime.utcnow().isoformat()))
            ok += 1
            print(f"thumbnails: OK {vid} '{plan['hook_text']}' ({how})")
        except Exception as exc:  # noqa: BLE001 - never fail the upload job over a thumbnail
            print(f"thumbnails: FAILED {vid}: {str(exc)[:300]}")
    print(f"thumbnails: {ok}/{len(todo)} done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
