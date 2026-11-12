#!/usr/bin/env python3
"""Merged 3 min+ long videos (owner rule 2026-11-12, test on ch3 + ch8).

One upload per day, every upload longer than 3 minutes, original 9:16 (no 4:3 editing):
  1. Anchor = the newest TikTok clip never posted on the channel.
  2. Anchor already 3 min+  -> it goes up on its own.
  3. Otherwise Claude picks 1-3 more clips on the SAME topic so the total passes 3 min:
     other unposted clips, or clips last used on the channel 15+ days ago (owner rule).
     No sensible match -> no upload today (owner: a skipped day is fine; the anchor waits).
  4. Clips come from the original TikToks, are joined in order (same size/fps/audio), and the
     video gets a Claude title + description and an AI thumbnail, then uploads.
Every clip's use is recorded, so the 15-day rule holds for later merges.

  python merge_upload.py --channel channel_8 --slot 1            # normal run
  python merge_upload.py --channel channel_8 --dry-run           # pick + join + texts, no upload

Channel config:  merge_long: {max_clips: 4}   (sources: tiktok_username [+ tiktok_username_slot2])
Env: ANTHROPIC_API_KEY, VERTEX_API_KEY, VERTEX_PROJECT, DB_PAGE_ID, TIKTOK_COOKIES_FILE
"""
import argparse
import json
import logging
import os
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.config import load_config  # noqa: E402
from src.tiktok_downloader import download_video, get_profile_videos  # noqa: E402

log = logging.getLogger("merge")
MIN_TOTAL = 185          # a little over 180 s so YouTube never treats it as a Short
OLD_DAYS = 15            # owner: an already-used clip may be used again only after 15 days
MIN_CLIP = 15            # ignore tiny clips
MODEL = "claude-sonnet-5-5"

PICK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["possible", "topic", "clip_ids", "reason"],
    "properties": {
        "possible": {"type": "boolean"},
        "topic": {"type": "string"},
        "clip_ids": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
}

PICK_SYSTEM = """You build compilation videos (Japanese "まとめ") for a YouTube channel that reposts one
creator's TikTok clips. Viewers must feel they are watching ONE coherent video about one topic.

You get the ANCHOR clip (must be used) and a list of candidate clips with id, length in seconds,
views, date and the creator's caption (with hashtags). Choose clips that fit the anchor's topic
so the total length of all chosen clips (anchor included) is at least the minimum given.

Rules:
- Same topic / series / situation as the anchor (e.g. the same #...シリーズ hashtag, the same kind
  of moment: waking up, bath time, siblings, a type of food, a restaurant chain...). Similar
  hashtags and words in the captions are the best signal. Prefer clips with more views.
- Use as FEW clips as possible (but never more than the maximum given) to pass the minimum.
- Put the anchor FIRST unless another order is clearly better for the story; return clip_ids
  in playing order, anchor included.
- If no set of clips makes a sensible single-topic video that passes the minimum, return
  possible=false (it is fine to skip a day). Never pad with unrelated clips.
- topic: a few words describing the shared topic, in English."""


def is_ad(caption):
    """Sponsored posts (#PR, タイアップ ...) are adverts, not channel content - never merged."""
    c = (caption or "").upper()
    return bool(re.search(r"(?:^|[\s#＃])PR(?![A-Z])|タイアップ|#AD(?![A-Z])|#SPONSORED|提供でお送り", c))


def setup_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS merges (
        youtube_video_id TEXT PRIMARY KEY, channel_id TEXT, parts TEXT, topic TEXT,
        title TEXT, seconds REAL, made_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS merge_parts (
        channel_id TEXT, tiktok_video_id TEXT, youtube_video_id TEXT, used_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS thumbnails (
        youtube_video_id TEXT PRIMARY KEY, channel_id TEXT, hook TEXT, identity TEXT, made_at TEXT)""")
    con.commit()


def last_use(con, channel_id):
    """tiktok id -> latest time it went up on the channel (alone, in a merge, or as a repost)."""
    use = {}

    def note(tid, when):
        if tid and when and (tid not in use or when > use[tid]):
            use[tid] = when
    yt_to_tt = {}
    for tid, yt, when, fmt in con.execute(
            "SELECT tiktok_video_id, youtube_video_id, posted_at, format_type FROM posted_videos "
            "WHERE channel_id=? AND status='uploaded'", (channel_id,)):
        if str(tid).startswith("repost:"):
            continue
        note(str(tid), when)
        if yt:
            yt_to_tt[yt] = str(tid)
    for tid, when in con.execute(
            "SELECT tiktok_video_id, posted_at FROM posted_videos WHERE channel_id=? AND status='uploaded' "
            "AND tiktok_video_id LIKE 'repost:%'", (channel_id,)):
        note(yt_to_tt.get(str(tid).split(":")[1]), when)        # repost of an earlier upload
    for tid, when in con.execute("SELECT tiktok_video_id, used_at FROM merge_parts WHERE channel_id=?",
                                 (channel_id,)):
        note(str(tid), when)
    return use


def pick(anchor, cands, max_clips, retry_note=""):
    import anthropic
    lines = [f"{c['id']} | {int(c['duration'])}s | {c.get('view_count') or 0} views | "
             f"{datetime.utcfromtimestamp(c.get('timestamp') or 0).date()} | "
             f"{(c.get('title') or '').replace(chr(10), ' ')[:90]}" for c in cands]
    text = (f"Minimum total length: {MIN_TOTAL} s. Maximum number of clips: {max_clips}.\n"
            f"ANCHOR: {anchor['id']} | {int(anchor['duration'])}s | {(anchor.get('title') or '')[:200]}\n\n"
            "CANDIDATES (id | length | views | date | caption):\n" + "\n".join(lines))
    if retry_note:
        text += "\n\n" + retry_note
    resp = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=4000, system=PICK_SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
        messages=[{"role": "user", "content": text}])
    u = resp.usage
    log.info("pick: %d in / %d out tokens", u.input_tokens, u.output_tokens)
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"pick stop_reason={resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                          "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    return {"w": v.get("width") or 0, "h": v.get("height") or 0, "audio": a,
            "dur": float(d.get("format", {}).get("duration") or 0)}


def join(files, out):
    """Join clips in order: same 9:16 frame (letterbox only if a clip has another shape),
    30 fps, 48 kHz stereo audio. Quality: x264 CRF 18."""
    infos = [probe(f) for f in files]
    h = max(i["h"] for i in infos if i["w"] < i["h"]) if any(i["w"] < i["h"] for i in infos) else 1280
    h = min(1920, h - h % 2)
    w = (h * 9 // 16) // 2 * 2
    args, filt, n = ["ffmpeg", "-v", "error", "-y"], [], 0
    for f, i in zip(files, infos):
        args += ["-i", str(f)]
    k = len(files)
    for idx, i in enumerate(infos):
        if not i["audio"]:
            args += ["-f", "lavfi", "-t", f"{i['dur']:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
    silent = k
    for idx, i in enumerate(infos):
        filt.append(f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,format=yuv420p[v{idx}]")
        src = f"{idx}:a" if i["audio"] else f"{silent}:a"
        if not i["audio"]:
            silent += 1
        filt.append(f"[{src}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{idx}]")
        n += 1
    filt.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    args += ["-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(out)]
    subprocess.run(args, check=True)
    return probe(out)["dur"]


def upload(channel, path, title, desc):
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from src.youtube_uploader import get_authenticated_client
    yt = get_authenticated_client(credentials_file=channel["google_credentials_file"],
                                  token_file=channel["oauth_token_file"])
    footer = (channel.get("description_footer") or "").strip()
    body = {"snippet": {"title": title.replace("<", "‹").replace(">", "›")[:100],
                        "description": (desc + ("\n\n" + footer if footer else "")).replace("<", "‹").replace(">", "›")[:4900],
                        "tags": list(channel.get("default_tags") or []),
                        "categoryId": str(channel.get("youtube_category_id", "22"))},
            "status": {"privacyStatus": channel.get("privacy_status") or "public",
                       "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), mimetype="video/mp4", resumable=True,
                                                        chunksize=8 * 1024 * 1024))
    resp, tries = None, 0
    while resp is None:
        try:
            _, resp = req.next_chunk()
        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504) and tries < 6:
                tries += 1
                time.sleep(min(60, 2 ** tries + random.random()))
            else:
                raise
    return yt, resp["id"]


def thumbnail(yt, con, channel_id, vid, video, caption, lang, title):
    from googleapiclient.http import MediaFileUpload
    from src import ai_thumbnail as at
    with tempfile.TemporaryDirectory(prefix="mthumb_") as tmp:
        fr = at.frames(Path(video), Path(tmp) / "frames")
        recent = [r[0] for r in con.execute("SELECT hook FROM thumbnails WHERE channel_id=? AND hook IS NOT NULL "
                                            "ORDER BY made_at DESC LIMIT 60", (channel_id,))]
        p = at.plan(fr, caption, lang, avoid=recent + [title])
        base, how = at.build_base(p, fr)
        img = at.add_text(base.copy(), p.get("hook_lines") or [p["hook_text"]], p["text_side"], p["accent_color"])
        out = Path(tmp) / "thumb.jpg"
        img.save(out, quality=90)
        if yt is not None:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(out), mimetype="image/jpeg")).execute()
            with con:
                con.execute("INSERT OR REPLACE INTO thumbnails VALUES (?,?,?,?,?)",
                            (vid, channel_id, p["hook_text"], how, datetime.utcnow().isoformat()))
        else:
            keep = ROOT / "logs" / f"dryrun_thumb_{channel_id}.jpg"
            keep.parent.mkdir(exist_ok=True)
            img.save(keep, quality=90)
        log.info("thumbnail: '%s' (%s) = %s", p["hook_text"], how, p.get("hook_meaning_en"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-thumb", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    for n in ("httpx", "urllib3", "googleapiclient"):
        logging.getLogger(n).setLevel(logging.WARNING)

    cfg = load_config()
    channel = next(c for c in cfg["channels"] if c["id"] == a.channel)
    mcfg = channel.get("merge_long") or {}
    max_clips = int(mcfg.get("max_clips", 3))
    lang = channel.get("ai_longform_metadata") or "Japanese"
    db.init_db()
    con = sqlite3.connect(str(db._get_db_path()))
    setup_tables(con)
    today = datetime.utcnow().date().isoformat()
    if con.execute("SELECT 1 FROM merges WHERE channel_id=? AND substr(made_at,1,10)=?",
                   (a.channel, today)).fetchone() and not a.dry_run:
        log.info("[%s] today's merged upload is already done - nothing to do", a.channel)
        return 0
    run_id = None if a.dry_run else db.start_run(a.channel, a.slot)

    def finish(status, err=None, n=0):
        if run_id:
            db.finish_run(run_id, status, videos_uploaded=n, error_message=err)

    try:
        # every clip of the source account(s), newest first
        users = [u for u in [channel.get("tiktok_username"), channel.get("tiktok_username_slot2")] if u]
        clips = {}
        for u in dict.fromkeys(users):
            vids = get_profile_videos(u, end=None)
            if vids is None:
                raise RuntimeError(f"TikTok listing failed for @{u}")
            for v in vids:
                if v.get("id") and (v.get("duration") or 0) >= MIN_CLIP and not is_ad(v.get("title")):
                    v["user"] = u
                    v["url"] = v.get("url") or f"https://www.tiktok.com/@{u}/video/{v['id']}"
                    clips[v["id"]] = v
        use = last_use(con, a.channel)
        cutoff = (datetime.utcnow() - timedelta(days=OLD_DAYS)).isoformat()
        new = sorted([c for c in clips.values() if c["id"] not in use], key=lambda c: c.get("timestamp") or 0,
                     reverse=True)
        old = [c for c in clips.values() if c["id"] in use and use[c["id"]] <= cutoff]
        log.info("[%s] %d clips on TikTok: %d never posted, %d posted 15+ days ago", a.channel, len(clips),
                 len(new), len(old))
        if not new:
            log.info("[%s] no new TikTok clip - no upload today (owner: skipping a day is fine)", a.channel)
            finish("skipped", "no new clip")
            return 0
        work = Path(tempfile.mkdtemp(prefix="merge_"))
        chosen, topic = None, ""
        old_pool = sorted(old, key=lambda c: c.get("view_count") or 0, reverse=True)[:200]
        for anchor in new[:5]:          # newest first; an anchor with no match simply waits
            log.info("[%s] anchor %s %ss '%s'", a.channel, anchor["id"], anchor["duration"],
                     (anchor.get("title") or "")[:80])
            if anchor["duration"] >= MIN_TOTAL:
                chosen, topic = [anchor], "single clip"
                break
            pool = [c for c in new[:81] if c["id"] != anchor["id"]] + old_pool
            note = ""
            for attempt in range(2):
                res = pick(anchor, pool, max_clips, note)
                ids = [i for i in res.get("clip_ids") or [] if i in clips]
                if res.get("possible") and anchor["id"] not in ids:
                    ids = [anchor["id"]] + ids
                total = sum(clips[i]["duration"] for i in ids)
                log.info("[%s] pick: possible=%s topic=%s ids=%s total=%ss (%s)", a.channel, res.get("possible"),
                         res.get("topic"), ids, int(total), (res.get("reason") or "")[:200])
                if not res.get("possible"):
                    break
                if total < MIN_TOTAL or len(ids) > max_clips or len(set(ids)) != len(ids):
                    note = (f"Your last choice {ids} was invalid (total {int(total)} s, {len(ids)} clips). "
                            f"It must reach {MIN_TOTAL} s with at most {max_clips} clips, no repeats.")
                    continue
                chosen, topic = [clips[i] for i in ids], res.get("topic") or ""
                break
            if chosen:
                break
        if not chosen:
            log.info("[%s] no sensible same-topic merge reaching 3 min - no upload today", a.channel)
            finish("skipped", "no same-topic merge")
            return 0

        # download the originals (TikTok); a clip that cannot be downloaded is dropped
        files = []
        for c in chosen:
            f = download_video(c["url"], c["id"], work)
            if not f:
                raise RuntimeError(f"TikTok download failed for {c['id']}")
            files.append(Path(f))
        merged = work / "merged.mp4"
        if len(files) == 1:
            merged = files[0]
            seconds = probe(merged)["dur"]
        else:
            seconds = join(files, merged)
        log.info("[%s] video ready: %d clip(s), %.0fs", a.channel, len(files), seconds)
        if seconds < 181:
            raise RuntimeError(f"joined video only {seconds:.0f}s")

        if len(chosen) == 1:
            caption = anchor.get("title") or ""
        else:
            caption = (f"This video is a compilation (まとめ) of {len(chosen)} clips about: {topic}. "
                       "Write a compilation-style title that covers the whole video (for example starting "
                       "with 【まとめ】 or naming the shared moment), not just the first clip. "
                       "Captions of the clips in playing order: "
                       + " / ".join(f"{n + 1}) {c.get('title') or ''}" for n, c in enumerate(chosen)))
        from src import ai_metadata
        meta = ai_metadata.write(merged, caption, lang, a.channel, anchor["id"], db._get_db_path())
        if not meta:
            meta = {"title": (("【まとめ】" if len(chosen) > 1 else "") + (anchor.get("title") or channel["id"]))[:95],
                    "description": "\n".join(c.get("title") or "" for c in chosen)}
        log.info("[%s] title: %s", a.channel, meta["title"])

        if a.dry_run:
            log.info("[%s] DRY RUN - parts %s, not uploading", a.channel, [c["id"] for c in chosen])
            if not a.no_thumb:
                thumbnail(None, con, a.channel, "dry", merged, caption, lang, meta["title"])
            return 0

        yt, vid = upload(channel, merged, meta["title"], meta["description"])
        log.info("[%s] UPLOADED https://www.youtube.com/watch?v=%s", a.channel, vid)
        now = datetime.utcnow().isoformat()
        with con:
            con.execute("INSERT OR REPLACE INTO merges VALUES (?,?,?,?,?,?,?)",
                        (vid, a.channel, json.dumps([c["id"] for c in chosen]), topic, meta["title"], seconds, now))
            for c in chosen:
                con.execute("INSERT INTO merge_parts VALUES (?,?,?,?)", (a.channel, c["id"], vid, now))
        from src import catalog
        catalog.record(db._get_db_path(), vid, a.channel, "merge" if len(chosen) > 1 else "single", meta["title"],
                       [{"id": c["id"], "caption": c.get("title"), "url": c["url"], "seconds": c.get("duration")}
                        for c in chosen])
        for c in chosen:
            if c["id"] not in use:      # first time on the channel
                db.mark_uploaded(a.channel, c["id"], vid, format_type="merge", tiktok_url=c["url"],
                                 tiktok_title=c.get("title"), tiktok_timestamp=c.get("timestamp"))
        finish("success", n=1)
        if not a.no_thumb and os.environ.get("VERTEX_API_KEY"):
            try:
                thumbnail(yt, con, a.channel, vid, merged, caption, lang, meta["title"])
            except Exception as exc:  # noqa: BLE001 - the upload itself is done
                log.warning("[%s] thumbnail failed (the thumbnail step retries): %s", a.channel, str(exc)[:200])
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("[%s] merged upload failed: %s", a.channel, exc)
        finish("failed", str(exc)[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
