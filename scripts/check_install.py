#!/usr/bin/env python3
"""Validate a source ZIP through a wheel and a fresh, non-editable install.

Requires Python with venv/ensurepip, and FFmpeg/ffprobe on PATH for the media smoke test.
Build dependencies are fetched by pip; installing the resulting wheel and all
media tests are offline. No YouTube request is made.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile


def check(archive, with_downloader=False):
    archive = archive.resolve()
    with tempfile.TemporaryDirectory(prefix="video digest install ") as directory:
        root = Path(directory)
        with zipfile.ZipFile(archive) as source:
            source.extractall(root)
        source = root / "video-digest"
        environment = root / "clean env"
        venv.EnvBuilder(with_pip=True).create(environment)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        cli = binaries / ("video-digest.exe" if os.name == "nt" else "video-digest")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONUTF8"] = "1"
        env["PATH"] = str(binaries) + os.pathsep + env.get("PATH", "")
        env["VIDEO_DIGEST_REQUIRE_FFMPEG"] = "1"

        def run(*command):
            subprocess.run([str(item) for item in command], cwd=root, env=env, check=True)

        wheels = root / "wheels"
        run(python, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", wheels, source)
        wheel, = wheels.glob("*.whl")
        run(python, "-m", "pip", "install", "--no-index", "--no-deps", wheel)
        # Confirm the import cannot be satisfied from the checkout/extracted source.
        run(python, "-c", "import pathlib, sys, video_digest; "
            "assert pathlib.Path(video_digest.__file__).is_relative_to(pathlib.Path(sys.prefix)); "
            "print('Installed module:', video_digest.__file__)")
        skill = environment / "share" / "video-digest" / "skills" / "video-digest"
        assert (skill / "SKILL.md").is_file(), "Wheel missing the skill"
        assert (skill / "references" / "notes-template.md").is_file(), "Wheel missing notes template"
        run(cli, "--help")
        run(python, "-m", "video_digest", "--help")
        if with_downloader:
            # Exercise the friend-facing optional dependencies on each CI OS.
            run(python, "-m", "pip", "install", str(wheel) + "[download]")
            run(python, "-c", "import curl_cffi, yt_dlp; print('Downloader dependencies import successfully')")
            run(binaries / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp"), "--version")
        # Discover tests by absolute path, with cwd outside the source tree.
        run(python, "-m", "unittest", "discover", "-s", source / "tests", "-v")
        print("Clean wheel install, bundled skill, and offline media checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--with-downloader", action="store_true",
                        help="also fetch/install the download extra and verify its imports and CLI")
    args = parser.parse_args()
    check(args.archive, args.with_downloader)
