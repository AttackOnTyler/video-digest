# video-digest

Turn a YouTube video or playlist into Markdown pairing screenshots with the
aligned transcript. The included **video-digest skill** helps an assistant turn
that evidence into timestamped takeaways and visual observations. It works in an
ordinary folder, with no repository or issue tracker required.

The extraction code uses Python's standard library. It calls `yt-dlp` to download
media and English captions, and FFmpeg to sample frames. An assistant with local
file access (and image access for visual review) writes the notes; the CLI does
not call a model or require an API key.

## Install prerequisites

Use **Python 3.10 or newer**, including `pip` and `venv`. Install the **FFmpeg
executables** (`ffmpeg` and `ffprobe`), not a similarly named Python package.
Install **Deno** for YouTube's JavaScript challenges. The pip commands below
install `yt-dlp[default]`, including its challenge solver. See the upstream
[yt-dlp dependencies](https://github.com/yt-dlp/yt-dlp#dependencies),
[FFmpeg downloads](https://ffmpeg.org/download.html), and
[Deno installation guide](https://docs.deno.com/runtime/getting_started/installation/).
These third-party programs are downloaded separately; they are not in the bundle.

### Windows / PowerShell

Install Python from [python.org](https://www.python.org/downloads/windows/),
then use `py` in the commands below. With Windows Package Manager:

```powershell
winget install --id Gyan.FFmpeg --exact
winget install --id DenoLand.Deno --exact
```

Open a **new terminal** so updated PATH settings apply. Verify:

```powershell
py --version
ffmpeg -version
ffprobe -version
deno --version
```

Without `winget`, use the Windows builds linked by FFmpeg and the Deno guide.
Add their executable folders to PATH, then restart the terminal.

### macOS / Linux

On macOS with [Homebrew](https://brew.sh/):

```sh
brew install python ffmpeg deno
```

On Debian/Ubuntu Linux:

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip ffmpeg
```

On Linux, install Deno using its [official guide](https://docs.deno.com/runtime/getting_started/installation/)
(the shell installer is `curl -fsSL https://deno.land/install.sh | sh`), then open
a new terminal. Other distributions can use their package manager for Python and
FFmpeg. Verify `python3 --version`, `ffmpeg -version`, `ffprobe -version`, and
`deno --version`.

## Quick start from the shared ZIP

Extract `video-digest-0.1.0.zip`; it contains a `video-digest` folder with this
README, the tool, skill, template, and validation scripts. No Git or Nix needed.
Commands below start in the directory **containing** that extracted folder.
Quote URLs, especially playlist URLs containing `&`.

Windows / PowerShell (no activation script or execution-policy change needed):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install ".\video-digest[youtube]"
$env:PATH = "$(Resolve-Path .\.venv\Scripts);$env:PATH"
video-digest --help
yt-dlp --version
video-digest "https://www.youtube.com/watch?v=VIDEO_ID" --out ".\my digest"
```

macOS / Linux:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "./video-digest[youtube]"
video-digest --help
yt-dlp --version
video-digest "https://www.youtube.com/watch?v=VIDEO_ID" --out "./my digest"
```

Replace `VIDEO_ID` with the real video ID, or pass a playlist URL. Keep the
virtual environment on PATH when running the tool, including from an assistant.
In a later terminal, repeat the PowerShell PATH line or the Unix activation line.
An assistant started before this setup may need restarting to inherit PATH.

From a source checkout, install with `python -m pip install ".[youtube]"` instead.
If you already manage `yt-dlp` separately, omit `[youtube]`. You can also run
`python video_digest.py "<url>"` without installing the tool, with prerequisites
on PATH. `python -m video_digest` is available after installation.

## Use or install the skill

The portable skill is [skills/video-digest/SKILL.md](skills/video-digest/SKILL.md).
Copy the **whole `skills/video-digest` folder**, including `references/`, into
your assistant's documented skills directory, then reload its skills. Choose a
location where no existing personal skill will be overwritten. Skill discovery
locations vary by assistant; no particular provider is required.

You can use it immediately without configuring discovery. Give a local-file
capable assistant this request, replacing the paths with your extracted folder
and output location:

> Read `video-digest/skills/video-digest/SKILL.md` and follow it for this URL:
> `<url>`. The CLI is installed in my `.venv` environment. Save the digest and
> notes in `my digest`. Inspect the frame images, cite timestamps, and distinguish
> the speaker's claims from your own inferences. Do not create project issues.

If the CLI was already run, ask it to read `my digest/digest.md` and write
`my digest/notes.md` using the bundled skill. The
[notes template](skills/video-digest/references/notes-template.md) is included.
An assistant without image access can summarize the transcript but must mark
visual review incomplete. Direct processing and available delegation are both
supported; no fixed model, private notes, project maps, or services are needed.
The wheel also carries the skill under the environment's
`share/video-digest/skills/video-digest/` directory.

## Flags and outputs

```text
video-digest <url> [--out DIR] [--scene 0.3] [--max-gap 20] [--min-gap 3]
             [--min-frames 24] [--width 1280] [--dedup 0.005] [--keep-video] [--force]
```

Without `--out`, output goes to `video-digests/<title-slug>/` under your home
folder (on Windows, typically `%USERPROFILE%\video-digests`). Relative paths,
spaces, and Unicode output folders are supported. Titles become short ASCII
slugs, with reserved Windows names prefixed by `video-`.

```text
<output>/digest.md        source details, chapters, timestamp links, frames + transcript
<output>/transcript.txt   [mm:ss] caption lines
<output>/meta.json        metadata, caption source, settings, frame times/reasons/change
<output>/frames/0001.jpg  sampled JPEGs
<output>/notes.md         written by the assistant using the skill, not by the CLI
```

A playlist gets numbered `NN-<slug>/` video directories and an `index.md` linking
their digests, durations, frame counts, and caption status. Failed videos are
listed in the index; other videos continue and the command exits with status 1.
Reruns skip videos with a completed `digest.md`; `--force` regenerates them.
A failed forced rerun removes the old completion marker so the next run retries.
Existing `notes.md` files are not changed by the tool; refresh outdated notes
with your assistant. Downloads are video-only at <=720p and deleted after
success unless `--keep-video` is set. Incomplete downloads may remain for retry.

### How frames and captions are selected

FFmpeg scores scene changes at 5 fps and 320 px width. Frames are sampled 0.5 s
after cuts above `--scene` and at chapter starts, respecting `--min-gap`.
Gaps above `--max-gap` are filled; short videos tighten that gap using
`duration / --min-frames`. Candidates are compared as 128x72 grayscale thumbnails;
under `--dedup` changed pixels drops a near-duplicate (chapter frames are kept).
Defaults yield roughly 100–300 frames for many talks. Lower `--dedup` or use
`--dedup 0` to retain more gradual changes. This is sampling, not every frame.

English manual captions take precedence over original English auto captions,
then other auto English tracks. json3 word timing is preferred; VTT is a fallback
with rolling duplicates removed. No transcription service is used. Fewer than
20 caption words/minute is classified as no speech; frequent `[Music]` markers
flag possible lyrics. The header's “no speech” is a heuristic and may actually
mean no usable English captions. The skill keeps that distinction in its notes.

## Nix workflow (retained)

From a checkout or the extracted bundle:

```sh
nix run . -- "<url>"              # Python, yt-dlp, FFmpeg, and Deno included
nix develop                       # dependencies and ./video-digest on PATH
video-digest "<url>"
```

The package and dev shell unset `LD_LIBRARY_PATH` to prevent foreign glibc
libraries from breaking Nix's FFmpeg. No Nix is needed for the pip workflow.

## Update

With the environment on PATH, update the downloader independently:

```sh
python -m pip install --upgrade "yt-dlp[default]"
```

PowerShell can use `.\.venv\Scripts\python.exe` instead of `python`.
For tool/skill updates, extract a newer bundle and repeat the local pip install
with `--upgrade --force-reinstall`; recopy the skill folder after preserving any
local edits. Existing digests stay cached unless you pass `--force`. In a Git
checkout, `git pull` followed by reinstalling `".[youtube]"` updates the tool;
Nix uses the checked-in lock until you explicitly update it. Update FFmpeg/Deno
through the same method used to install them.

## Common failures

- **Missing command / missing dependency:** ensure the environment's `Scripts`
  (Windows) or `bin` directory is on PATH. Check `yt-dlp --version`,
  `ffmpeg -version`, `ffprobe -version`, and `deno --version` in that terminal.
  Use the exact environment's Python for pip; restart the assistant if needed.
- **PowerShell blocks activation:** the commands above use Python directly and
  set PATH; no activation policy change is needed.
- **No `venv` or `ensurepip`:** install your distribution's `python3-venv`
  package, or use an official Python installation with pip support.
- **403 / signature challenge / no formats:** update `yt-dlp[default]` and Deno.
  YouTube changes frequently. An account-restricted or private video may need
  user-managed authentication through yt-dlp's own configuration; the bundle
  includes no credentials and does not automate sign-in.
- **429 / caption download failed:** wait and retry the affected video once
  with `--force`. A failed machine-translated track allows a frames-only digest;
  failures of other caption tracks are reported as failures. Check the header
  before assuming notes include speech.
- **No captions / music-only:** read the frames; do not invent dialogue. If
  captions exist in a language other than English, this tool may not select them.
- **Path too long / access denied:** use a short writable folder with `--out`
  and close viewers locking output files before a forced rerun on Windows.
- **Sparse or blurry frames:** try `--dedup 0`, a smaller `--max-gap`, or a larger
  `--width`. Greater width cannot recover detail absent from the <=720p download.

## Build a bundle and validate

From the project folder (PowerShell: use `py` in place of `python` if needed):

```sh
python scripts/bundle.py
python scripts/check_install.py dist/video-digest-0.1.0.zip
```

Share `dist/video-digest-0.1.0.zip` and its `.zip.sha256` checksum. The builder
uses an explicit file list plus skill, test, and workflow files; it excludes
Git history, environments, caches, local output, and installed personal skills.
ZIP ordering, timestamps, permissions, and text line endings are normalized.
It includes source rather than Python/FFmpeg binaries, so recipients still need
the prerequisites and Internet access for installation and YouTube downloads.
Nothing is published to a package registry.

The validator extracts into a temporary folder with spaces, builds a wheel,
installs it non-editably into a fresh environment, checks CLI/module entry points
and installed skill resources, and runs the offline tests from outside the
source tree. Building may fetch setuptools from PyPI; the wheel install and
media tests use no network. FFmpeg is required for this validation. Tests use
synthetic video with real FFmpeg and an external mock downloader, covering
Unicode/relative paths, subtitles, video/playlist output, skip/force caching,
partial failures, and Windows-safe filenames. For a quick source test:
`python -m unittest discover -s tests -v` (media tests skip if FFmpeg is missing).

GitHub Actions runs the clean-install checks on Linux, macOS, and Windows with
Python 3.10 and 3.14. Configuring this matrix alone is not proof those OSes passed;
consult the actual run. No live YouTube network test is part of these checks.
