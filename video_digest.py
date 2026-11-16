#!/usr/bin/env python3
"""video-digest: turn a YouTube video/playlist or TikTok video into Markdown that pairs
screenshots with the time-aligned transcript, for an LLM to read.

Needs yt-dlp and ffmpeg on PATH. Python stdlib only.
"""
import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_ROOT = Path.home() / "video-digests"
SETTLE = 0.5          # seconds after a scene cut to grab the frame (skip crossfades)
SCAN_FPS = 5          # frame rate used for the scene-score pass
THUMB_W, THUMB_H = 128, 72   # grayscale thumbnail used for near-duplicate checks
PIXEL_DELTA = 16      # a thumbnail pixel "changed" if it moved by more than this
JPEG_QSCALE = 5       # ffmpeg mjpeg qscale; ~quality 80-85
MIN_WPM = 20          # below this many words/minute, captions are ASR noise over music
NON_SPEECH = re.compile(r"^\[[^\]]*\]$|^\([^)]*\)$|^♪+$")


def log(msg, indent=0):
    print("  " * indent + msg, flush=True)


def slugify(text, maxlen=60):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    text = (text[:maxlen].rstrip("-")) or "video"
    # These names are reserved even with an extension on Windows.
    if re.fullmatch(r"con|prn|aux|nul|com[1-9]|lpt[1-9]", text):
        text = "video-" + text
    return text


def fmt_ts(sec, force_hours=False):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h or force_hours:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def youtube_id(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0] or None
    if host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] in ("shorts", "embed", "live"):
            return parts[1]
    return None


def source_url(info, fallback):
    """Use the extractor's page URL, never turn a non-YouTube ID into YouTube."""
    url = info.get("webpage_url") or info.get("original_url") or fallback
    extractor = info.get("extractor_key") or info.get("extractor") or info.get("ie_key") or ""
    if extractor.lower().startswith("youtube") or youtube_id(url):
        return f"https://www.youtube.com/watch?v={info['id']}"
    return url


def timestamp_url(url, seconds):
    vid = youtube_id(url)
    return f"https://youtu.be/{vid}?t={int(seconds)}" if vid else url


def playlist_entry_url(entry, playlist):
    for key in ("webpage_url", "url"):
        url = entry.get(key)
        if url and urlparse(url).scheme in ("http", "https"):
            return url
    extractor = (entry.get("ie_key") or playlist.get("extractor_key")
                 or playlist.get("extractor") or "")
    if extractor.lower().startswith("youtube"):
        return f"https://www.youtube.com/watch?v={entry['id']}"
    raise ValueError("playlist entry has no video page URL")


def run(cmd, cwd=None, retries=1, what=None):
    """Run a command, return stdout. Retries (for network calls) then raises."""
    # Resolve before changing cwd, including when PATH contains relative entries.
    executable = shutil.which(cmd[0])
    if executable:
        cmd = [str(Path(executable).absolute()), *cmd[1:]]
    for attempt in range(1, retries + 1):
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if p.returncode == 0:
            return p.stdout
        err = (p.stderr or "").strip().splitlines()[-5:]
        if attempt < retries:
            log(f"! {what or cmd[0]} failed (attempt {attempt}), retrying: {err[-1] if err else ''}", 2)
            time.sleep(10 * attempt)
    raise RuntimeError(f"{what or cmd[0]} failed: " + " | ".join(err))


def ytdlp(args, **kw):
    return run(["yt-dlp", "--encoding", "utf-8", "--no-warnings", "--no-progress", *args], retries=3, **kw)


# ---------------------------------------------------------------- subtitles

def choose_subs(info):
    """Return (lang, ext, kind) for the best English track, or None.
    Manual subs first, then the original-language ASR track, then any auto 'en'."""
    def pick_ext(tracks):
        exts = [t.get("ext") for t in tracks]
        for e in ("json3", "vtt", "srt", "json"):
            if e in exts:
                return e
        return None

    manual = info.get("subtitles") or {}
    keys = [k for k in manual if k == "en" or k.startswith("en-") or k.startswith("en_")]
    keys.sort(key=lambda k: (k != "en", k))
    for k in keys:
        ext = pick_ext(manual[k])
        if ext:
            extractor = info.get("extractor_key") or info.get("extractor") or ""
            kind = "site-provided" if extractor.lower().startswith("tiktok") else "manual"
            return k, ext, kind
    auto = info.get("automatic_captions") or {}
    for k, kind in (("en-orig", "auto"), ("en", "auto")):
        if k in auto:
            ext = pick_ext(auto[k])
            if ext:
                if k == "en" and any(x.endswith("-orig") and x != "en-orig" for x in auto):
                    kind = "auto (machine-translated)"
                return k, ext, kind
    return None


def parse_json3(path):
    """Return (segments, tokens). segments: [(start, text)] one per caption event.
    tokens: [(time, text, starts_event)] at word level when available."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segments, tokens = [], []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs or ev.get("aAppend"):
            continue
        start = ev.get("tStartMs", 0) / 1000
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        segments.append((start, text))
        first = True
        for s in segs:
            t = s.get("utf8", "").replace("\n", " ")
            if not t.strip():
                continue
            tokens.append((start + s.get("tOffsetMs", 0) / 1000, t, first))
            first = False
    return segments, tokens


VTT_TIME = re.compile(r"(?:(\d+):)?(\d\d):(\d\d)[.,](\d{3})\s+-->")


def parse_vtt(path, rolling=True):
    """Parse WebVTT, or SRT with rolling=False to preserve repeated cues."""
    segments, prev_lines = [], []
    blocks = re.split(r"\n\s*\n", Path(path).read_text(encoding="utf-8"))
    for b in blocks:
        lines = b.strip().splitlines()
        idx = next((i for i, l in enumerate(lines) if VTT_TIME.search(l)), None)
        if idx is None:
            continue
        h, m, s, ms = (int(x or 0) for x in VTT_TIME.search(lines[idx]).groups())
        start = h * 3600 + m * 60 + s + ms / 1000
        text_lines = []
        for l in lines[idx + 1:]:
            l = re.sub(r"<[^>]+>", "", l)
            l = l.replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
            l = re.sub(r"\s+", " ", l).strip()
            if l:
                text_lines.append(l)
        new = [l for l in text_lines if l not in prev_lines] if rolling else text_lines
        prev_lines = text_lines
        text = " ".join(new).strip()
        if text and not (rolling and segments and segments[-1][1] == text):
            segments.append((start, text))
    tokens = [(t, " " + x, True) for t, x in segments]
    return segments, tokens


def parse_tiktok_json(path):
    """TikTok caption utterances use start_time in milliseconds, unlike json3."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("utterances"), list):
        return [], []
    segments = []
    for cue in data.get("utterances", []):
        if not isinstance(cue, dict):
            continue
        text = re.sub(r"\s+", " ", cue.get("text") or "").strip()
        start = cue.get("start_time")
        if text and isinstance(start, (int, float)):
            segments.append((start / 1000, text))
    segments.sort()
    return segments, [(t, " " + text, True) for t, text in segments]


def parse_subtitles(path, ext):
    if ext == "json3":
        return parse_json3(path)
    if ext == "json":
        return parse_tiktok_json(path)
    return parse_vtt(path, rolling=ext == "vtt")


def is_speech(text):
    return any(not NON_SPEECH.match(w) for w in text.split())


def word_count(segments):
    return sum(1 for _, t in segments for w in t.split() if not NON_SPEECH.match(w))


# ---------------------------------------------------------------- frames

def scene_scores(video, workdir):
    video = Path(video).resolve()
    out = Path(workdir) / "scene_scores.txt"
    vf = (f"fps={SCAN_FPS},scale=320:-2,select='gte(scene\\,0)',"
          f"metadata=print:key=lavfi.scene_score:file={out.name}")
    run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(video),
         "-an", "-sn", "-dn", "-vf", vf, "-f", "null", "-"], cwd=workdir, what="ffmpeg scene scan")
    scores, t = [], None
    for line in out.read_text(encoding="utf-8").splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        if line.startswith("lavfi.scene_score=") and t is not None:
            scores.append((t, float(line.split("=", 1)[1])))
    out.unlink(missing_ok=True)
    return scores


def pick_times(scores, duration, chapters, scene, min_gap, max_gap):
    """Return sorted [(time, reason)]. Reasons: start, chapter, scene, gap."""
    end = max(duration - 1.0, 0.0)
    events = [(min(1.0, end), "start")]
    events += [(max(float(c["start_time"]), 0.0) + SETTLE, "chapter") for c in chapters
               if c.get("start_time", 0) > 1.0]
    events += [(t + SETTLE, "scene") for t, s in scores if s >= scene]
    events = [(min(t, end), r) for t, r in events]
    events.sort()

    picks = []
    for t, r in events:
        if picks and t - picks[-1][0] < min_gap:
            if r == "chapter" and picks[-1][1] != "chapter":
                picks[-1] = (t, r)  # a chapter boundary beats a nearby scene cut
            continue
        picks.append((t, r))

    filled = []
    bounds = picks + [(end, "end")]
    for (t0, r0), (t1, _) in zip(bounds, bounds[1:]):
        filled.append((t0, r0))
        gap = t1 - t0
        if gap > max_gap:
            n = math.ceil(gap / max_gap) - 1
            filled += [(t0 + gap * i / (n + 1), "gap") for i in range(1, n + 1)]
    return filled


def extract_frame(video, t, jpg, gray, width):
    fc = (f"[0:v]split=2[a][b];[a]scale='min({width},iw)':-2[o1];"
          f"[b]scale={THUMB_W}:{THUMB_H},format=gray[o2]")
    run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
         "-ss", f"{t:.3f}", "-i", str(video), "-filter_complex", fc,
         "-map", "[o1]", "-frames:v", "1", "-q:v", str(JPEG_QSCALE), str(jpg),
         "-map", "[o2]", "-frames:v", "1", "-f", "rawvideo", str(gray)],
        what=f"ffmpeg frame @{t:.1f}s")
    return Path(gray).read_bytes()


def thumb_diff(a, b):
    """Fraction of thumbnail pixels that changed noticeably."""
    if not a or not b or len(a) != len(b):
        return 1.0
    changed = sum(1 for x, y in zip(a, b) if abs(x - y) > PIXEL_DELTA)
    return changed / len(a)


def build_frames(video, outdir, picks, width, dedup):
    frames_dir = outdir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    tmp = outdir / ".frames_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    def job(i):
        t, _ = picks[i]
        return extract_frame(video, t, tmp / f"{i:05d}.jpg", tmp / f"{i:05d}.gray", width)

    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        thumbs = list(ex.map(job, range(len(picks))))

    frames_dir.mkdir()
    kept, last = [], None
    for i, ((t, reason), th) in enumerate(zip(picks, thumbs)):
        src = tmp / f"{i:05d}.jpg"
        if not src.exists():
            continue
        change = 1.0 if last is None else thumb_diff(th, last)
        if reason != "chapter" and change < dedup:
            continue
        last = th
        name = f"{len(kept) + 1:04d}.jpg"
        src.rename(frames_dir / name)
        kept.append({"file": f"frames/{name}", "t": round(t, 2), "reason": reason,
                     "change": round(change, 3)})
    shutil.rmtree(tmp)
    return kept


# ---------------------------------------------------------------- output

def assign_text(frames, tokens):
    """Text spoken from each frame's timestamp until the next frame."""
    buckets = [[] for _ in frames]
    starts = [f["t"] for f in frames]
    j = 0
    for t, text, first in tokens:
        while j + 1 < len(starts) and t >= starts[j + 1]:
            j += 1
        buckets[j].append((" " if first else "") + text)
    return [re.sub(r"\s+", " ", "".join(b)).strip() for b in buckets]


def write_outputs(outdir, info, url, frames, segments, tokens, sub_kind, args):
    duration = info.get("duration") or 0
    hours = duration >= 3600
    chapters = info.get("chapters") or []
    words = word_count(segments)
    # music-only videos get auto-captions like "[Music] hey hey [Music]"
    has_speech = words > 0 and words / max(duration / 60, 1 / 60) >= MIN_WPM
    music_marks = sum(1 for _, t in segments if "[Music]" in t)
    lyrics_hint = has_speech and music_marks >= 0.2 * len(segments)
    texts = assign_text(frames, tokens) if frames else []

    date = info.get("upload_date") or ""
    if len(date) == 8:
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"

    L = [f"# {info.get('title', info['id'])}", ""]
    L.append(f"- URL: {url}")
    L.append(f"- Channel: {info.get('channel') or info.get('uploader') or 'unknown'}")
    L.append(f"- Uploaded: {date or 'unknown'}")
    L.append(f"- Duration: {fmt_ts(duration, hours)}")
    L.append(f"- Frames: {len(frames)}")
    if has_speech:
        L.append(f"- Transcript: {words} words ({sub_kind} English subtitles)"
                 + ("; frequent [Music] markers, so this may be song lyrics" if lyrics_hint else ""))
    else:
        why = (f"captions hold only {words} stray words, likely ASR noise over music" if words else
               "subtitles contain no speech" if segments else
               sub_kind if sub_kind.startswith("none (") else "no English subtitles")
        L.append(f"- Transcript: none. **No usable English speech captions** ({why}); "
                 "frames are marked (no speech).")
    if not youtube_id(url):
        L.append("- Timecodes are offsets in the video; source links open the video, not a guaranteed seek position.")
    if chapters:
        L += ["", "## Chapters", ""]
        for c in chapters:
            s = int(c.get("start_time", 0))
            L.append(f"- [{fmt_ts(s, hours)}]({timestamp_url(url, s)}) {c.get('title', '')}")
    desc = (info.get("description") or "").strip()
    if desc:
        if len(desc) > 1500:
            desc = desc[:1500].rstrip() + " ..."
        L += ["", "## Description", ""] + ["> " + l if l.strip() else ">" for l in desc.splitlines()]
    L += ["", "## Frames and transcript", ""]

    def chapter_at(t):
        name = None
        for c in chapters:
            if c.get("start_time", 0) <= t + 0.01:
                name = c.get("title")
        return name

    current = None
    for f, text in zip(frames, texts):
        ch = chapter_at(f["t"])
        if ch and ch != current:
            L += [f"## Chapter: {ch}", ""]
            current = ch
        s = int(f["t"])
        L += [f"### [{fmt_ts(s, hours)}]({timestamp_url(url, s)})", "",
              f"![]({f['file']})", "",
              text if has_speech and text and is_speech(text) else "(no speech)", ""]

    (outdir / "transcript.txt").write_text(
        "".join(f"[{fmt_ts(t, hours)}] {x}\n" for t, x in segments) if segments else "(no speech)\n",
        encoding="utf-8")

    meta = {
        "id": info["id"], "url": url, "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "upload_date": date, "duration": duration,
        "description": info.get("description"), "chapters": chapters,
        "transcript": {"source": sub_kind, "words": words, "has_speech": has_speech},
        "frames": frames,
        "settings": {k: getattr(args, k) for k in ("scene", "max_gap", "min_gap", "min_frames", "width", "dedup")},
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (outdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp = outdir / "digest.md.tmp"
    tmp.write_text("\n".join(L), encoding="utf-8")
    tmp.replace(outdir / "digest.md")  # atomic overwrite also works on Windows
    return meta


# ---------------------------------------------------------------- per video

def digest_video(url, outdir, args, info=None):
    outdir = Path(outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    # A failed forced rerun must not leave a stale success marker over new frames.
    (outdir / "digest.md").unlink(missing_ok=True)
    t0 = time.time()
    if info is None or "formats" not in info:
        log("fetching metadata", 1)
        info = json.loads(ytdlp(["-J", "--no-playlist", url], what="yt-dlp metadata"))
    url = source_url(info, url)
    log(f"{info.get('title')} ({fmt_ts(info.get('duration') or 0)})", 1)

    segments, tokens, sub_kind = [], [], "none"
    choice = choose_subs(info)
    if choice:
        lang, ext, sub_kind = choice
        log(f"subtitles: {lang} {ext} ({sub_kind})", 1)
        flag = "--write-subs" if sub_kind in ("manual", "site-provided") else "--write-auto-subs"
        for old in outdir.glob("subs.*"):
            old.unlink()
        try:
            ytdlp(["--skip-download", flag, "--sub-langs", lang, "--sub-format", ext,
                   "-o", "subs.%(ext)s", url], cwd=outdir, what="yt-dlp subtitles")
        except RuntimeError as ex:
            # YouTube rate-limits machine-translated tracks hard (HTTP 429); they are
            # a last resort anyway, so carry on without a transcript.
            if "translated" not in sub_kind:
                raise
            log(f"! translated subtitles unavailable, continuing without: {ex}", 1)
            sub_kind = "none (machine-translated English track failed to download)"
        files = sorted(outdir.glob(f"subs.*.{ext}"))
        if files:
            segments, tokens = parse_subtitles(files[0], ext)
            if not segments:
                sub_kind = "none (downloaded captions contain no supported timed text)"
            for f in files:
                f.unlink()
    else:
        log("subtitles: none in English", 1)

    video = next(iter(sorted(outdir.glob("video.*"))), None)
    if video is None or video.suffix in (".part", ".ytdl"):
        log("downloading video (prefer <=720p; combined-format fallback)", 1)
        ytdlp(["-f", "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]/b",
               "-o", "video.%(ext)s", url], cwd=outdir, what="yt-dlp video")
        video = next(p for p in sorted(outdir.glob("video.*")) if p.suffix not in (".part", ".ytdl"))

    log("scanning scene changes", 1)
    scores = scene_scores(video, outdir)
    duration = info.get("duration") or (scores[-1][0] if scores else 0)
    # short videos (timelapses, trailers) get a tighter gap so they still yield
    # about --min-frames frames; long talks are governed by --max-gap
    max_gap = max(args.min_gap, min(args.max_gap, duration / max(args.min_frames, 1)))
    picks = pick_times(scores, duration, info.get("chapters") or [],
                       args.scene, args.min_gap, max_gap)
    log(f"extracting {len(picks)} candidate frames", 1)
    frames = build_frames(video, outdir, picks, args.width, args.dedup)
    log(f"kept {len(frames)} frames after near-duplicate removal", 1)

    meta = write_outputs(outdir, info, url, frames, segments, tokens, sub_kind, args)
    if not args.keep_video:
        video.unlink()
    log(f"done in {time.time() - t0:.0f}s -> {outdir / 'digest.md'}", 1)
    return meta


# ---------------------------------------------------------------- playlist

def write_index(root, pl, rows):
    total_frames = sum(r["frames"] for r in rows if r.get("frames") is not None)
    total_dur = sum(r["duration"] or 0 for r in rows)
    L = [f"# {pl.get('title', 'Playlist')}", "",
         f"- URL: {pl.get('webpage_url') or pl.get('original_url', '')}",
         f"- Channel: {pl.get('channel') or pl.get('uploader') or 'unknown'}",
         f"- Videos: {len(rows)}, total {fmt_ts(total_dur, True)}, {total_frames} frames",
         "", "| # | Video | Duration | Frames | Speech |", "|---|---|---|---|---|"]
    for r in rows:
        if r.get("error"):
            L.append(f"| {r['n']} | {r['title']} (failed: {r['error'][:80]}) | "
                     f"{fmt_ts(r['duration'] or 0)} | - | - |")
        else:
            L.append(f"| {r['n']} | [{r['title']}]({r['dir']}/digest.md) | "
                     f"{fmt_ts(r['duration'] or 0)} | {r['frames']} | {'yes' if r['speech'] else 'no'} |")
    (root / "index.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    # Redirected Windows output may otherwise use a legacy code page that cannot
    # print video titles or Unicode output paths. Files and subprocesses use UTF-8 too.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="video-digest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="YouTube video/playlist or TikTok video URL (including share links)")
    ap.add_argument("--out", type=Path, help=f"output directory (default {DEFAULT_ROOT}/<slug>)")
    ap.add_argument("--scene", type=float, default=0.3, help="ffmpeg scene-change threshold, 0-1 (default 0.3)")
    ap.add_argument("--max-gap", type=float, default=20, help="force a frame after this many seconds (default 20)")
    ap.add_argument("--min-gap", type=float, default=3, help="never two frames closer than this (default 3)")
    ap.add_argument("--min-frames", type=int, default=24,
                    help="shrink the max gap on short videos to aim for this many frames (default 24)")
    ap.add_argument("--width", type=int, default=1280, help="frame width in pixels (default 1280)")
    ap.add_argument("--dedup", type=float, default=0.005,
                    help="drop a frame if under this fraction of its thumbnail changed (default 0.005)")
    ap.add_argument("--keep-video", action="store_true", help="keep the downloaded video file")
    ap.add_argument("--force", action="store_true", help="redo videos that already have a digest")
    args = ap.parse_args()
    if args.out is not None:
        args.out = args.out.expanduser().resolve()

    for tool in ("yt-dlp", "ffmpeg"):
        if not shutil.which(tool):
            sys.exit(f"error: {tool} not on PATH. Install it and restart your terminal "
                     "(see README.md prerequisites); Nix users can run: nix develop")

    log(f"resolving {args.url}")
    info = json.loads(ytdlp(["-J", "--flat-playlist", args.url], what="yt-dlp resolve"))

    if info.get("_type") != "playlist":
        outdir = args.out or DEFAULT_ROOT / slugify(info.get("title", info["id"]))
        if (outdir / "digest.md").exists() and not args.force:
            log(f"skip: {outdir / 'digest.md'} exists (use --force)")
            return
        digest_video(args.url, outdir, args, info)
        return

    entries = [e for e in info.get("entries") or [] if e]
    root = args.out or DEFAULT_ROOT / slugify(info.get("title", info["id"]))
    root.mkdir(parents=True, exist_ok=True)
    log(f"playlist: {info.get('title')} ({len(entries)} videos) -> {root}")
    rows, failures = [], 0
    for n, e in enumerate(entries, 1):
        title = e.get("title") or e["id"]
        d = f"{n:02d}-{slugify(title)}"
        row = {"n": n, "title": title, "dir": d, "duration": e.get("duration")}
        log(f"[{n}/{len(entries)}] {title}")
        outdir = root / d
        try:
            if (outdir / "digest.md").exists() and not args.force:
                log("skip: digest exists", 1)
                meta = json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
            else:
                meta = digest_video(playlist_entry_url(e, info), outdir, args)
            row.update(title=meta["title"], duration=meta["duration"], frames=len(meta["frames"]),
                       speech=meta["transcript"]["has_speech"])
        except Exception as ex:  # keep going; record the failure in the index
            failures += 1
            row["error"] = str(ex).replace("|", "/").replace("\n", " ")
            log(f"! failed: {ex}", 1)
        rows.append(row)
        write_index(root, info, rows)
    log(f"index: {root / 'index.md'} ({failures} failed)")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
