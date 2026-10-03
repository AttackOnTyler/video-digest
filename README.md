# video-digest

Turns a YouTube video or playlist into Markdown that interleaves screenshots with
the time-aligned transcript, so a model like Claude can read a talk the way a
person watches it: the slide or demo frame, then what was said while it was up.

One Python script, stdlib only. Needs `yt-dlp` and `ffmpeg`.

## Run

```sh
nix run . -- <url>                 # packaged, deps included
nix develop                        # or: shell with python3, yt-dlp, ffmpeg; ./video-digest on PATH
video-digest <url> [--out DIR] [--scene 0.3] [--max-gap 20] [--min-gap 3]
             [--min-frames 24] [--width 1280] [--dedup 0.01] [--keep-video] [--force]
```

Output goes to `~/video-digests/<slug>/` unless `--out` is given. Re-running skips
videos that already have a `digest.md`; `--force` redoes them.

## Output

Single video:

```
<slug>/digest.md        header (title, URL, channel, date, duration, chapters, counts,
                        description) then one section per frame: timestamp link,
                        chapter heading where it changes, image, transcript until next frame
<slug>/transcript.txt   [mm:ss] caption lines
<slug>/meta.json        metadata, transcript source, every frame's time/reason/change
<slug>/frames/0001.jpg  JPEG frames
```

Playlist: one `NN-<slug>/` directory per video as above, plus `index.md` linking
each digest with duration, frame count and whether it has speech.

## How frames are chosen

1. ffmpeg computes a scene-change score for every frame at 5 fps (320 px wide).
2. A frame is taken 0.5 s after each cut scoring at least `--scene` (to skip
   crossfades), and at each chapter start, never closer than `--min-gap` seconds.
3. Gaps longer than `--max-gap` are filled with evenly spaced frames. Short
   videos use a tighter gap (`duration / --min-frames`) so a 2-minute timelapse
   still gets about 24 frames.
4. Each candidate is compared with the last kept frame as a 64x36 grayscale
   thumbnail; if under `--dedup` (1%) of pixels changed noticeably it is dropped,
   so a slide held for two minutes yields one frame, not six.

On a 32-minute live-demo talk the defaults give about 110 frames (about 6 MB).

## Subtitles

English manual subtitles first, then YouTube's original-language auto captions
(`en-orig`), then any auto `en` track. json3 is preferred (word-level timing, so
text is split between frames at the word); vtt is the fallback and its rolling
duplicate lines are removed. Videos without speech keep their frames, marked
`(no speech)`, and the header says so.

## Notes

- The video is downloaded video-only at <=720p and deleted afterwards unless
  `--keep-video`.
- Both the flake package and dev shell unset `LD_LIBRARY_PATH`; a stray value
  pointing at another glibc's libraries makes Nix's ffmpeg fail to start.
