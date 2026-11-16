"""Offline regressions; these also run against the wheel outside the checkout."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import video_digest as vd


@contextmanager
def working_directory(directory):
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


class PortabilityTests(unittest.TestCase):
    def test_windows_safe_slugs(self):
        for title in ("CON", "prn", "AUX.", "nul", "COM1", "lpt9"):
            self.assertTrue(vd.slugify(title).startswith("video-"))
        self.assertEqual(vd.slugify("Café: slides / demo?"), "cafe-slides-demo")
        self.assertEqual(vd.slugify("..."), "video")

    def test_utf8_and_shell_metacharacters_with_changed_cwd(self):
        with tempfile.TemporaryDirectory(prefix="digest spaces ") as directory:
            root = Path(directory)
            script = root / "echo ü.py"
            script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:], ensure_ascii=False))\n",
                              encoding="utf-8")
            arguments = ["café 演示", "a & b; $(echo no)", 'a"b', r"C:\a folder\video.mp4"]
            env = {"PYTHONIOENCODING": "utf-8"}
            with patch.dict(os.environ, env):
                result = vd.run([sys.executable, str(script), *arguments], cwd=root)
            self.assertEqual(json.loads(result), arguments)

    def test_resolve_executable_before_cwd_and_retry(self):
        failure = subprocess.CompletedProcess([], 1, "", "first\nlast failure\n")
        success = subprocess.CompletedProcess([], 0, "ok", "")
        with patch.object(vd.shutil, "which", return_value="relative bin/tool.exe"), \
             patch.object(vd.subprocess, "run", side_effect=[failure, success]) as run, \
             patch.object(vd.time, "sleep") as sleep:
            self.assertEqual(vd.run(["tool", "argument"], cwd="different", retries=2), "ok")
            self.assertEqual(run.call_args.args[0][0], str(Path("relative bin/tool.exe").resolve()))
            self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
            sleep.assert_called_once_with(10)
        with patch.object(vd.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "last failure"):
                vd.run([sys.executable])

    def test_missing_dependency_has_portable_help(self):
        with patch.object(sys, "argv", ["video-digest", "https://youtu.be/fixture"]), \
             patch.object(vd.shutil, "which", return_value=None):
            with self.assertRaisesRegex(SystemExit, "yt-dlp not on PATH.*README"):
                vd.main()

    def test_cli_logging_under_legacy_console_encoding(self):
        with tempfile.TemporaryDirectory(prefix="digest café ") as directory:
            root = Path(directory)
            (root / "digest.md").write_text("cached", encoding="utf-8")
            script = root / "cached.py"
            script.write_text("import sys\nsys.path.insert(0, " + repr(str(Path(vd.__file__).parent)) + ")\n" + '''import json, sys
from unittest.mock import patch
import video_digest as vd
with patch.object(vd.shutil, "which", return_value=sys.executable), \\
     patch.object(vd, "ytdlp", return_value=json.dumps({"id": "fixture"})):
    vd.main()
''', encoding="utf-8")
            with patch.dict(os.environ, {"PYTHONIOENCODING": "ascii"}):
                result = vd.run([sys.executable, str(script), "https://example.test/fixture",
                                 "--out", str(root)])
            self.assertIn(str(root), result)
            self.assertIn("skip:", result)

    def test_subtitle_selection_and_parsing(self):
        self.assertEqual(vd.choose_subs({"subtitles": {"en": [{"ext": "vtt"}]},
                                       "automatic_captions": {"en-orig": [{"ext": "json3"}]}}),
                         ("en", "vtt", "manual"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "subs.vtt"
            path.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello\n\n"
                            "00:00:03.000 --> 00:00:05.000\nhello\nworld\n", encoding="utf-8")
            segments, _ = vd.parse_vtt(path)
            self.assertEqual(segments, [(1, "hello"), (3, "world")])
            path = Path(directory) / "subs.json3"
            path.write_text(json.dumps({"events": [{"tStartMs": 1000, "segs": [
                {"utf8": "hello"}, {"utf8": " world", "tOffsetMs": 1200}]}]}), encoding="utf-8")
            segments, tokens = vd.parse_json3(path)
            self.assertEqual(segments, [(1, "hello world")])
            self.assertEqual(vd.assign_text([{"t": 1}, {"t": 2}], tokens), ["hello", "world"])

    def test_selection_and_dedup(self):
        picks = vd.pick_times([(3, 0.9), (3.5, 0.8)], 25,
                              [{"start_time": 4, "title": "chapter"}], 0.3, 3, 6)
        self.assertIn((4.5, "chapter"), picks)
        self.assertFalse(any(reason == "scene" for _, reason in picks))
        self.assertTrue(all(b[0] - a[0] <= 6 for a, b in zip(picks, picks[1:])))
        self.assertEqual(vd.thumb_diff(b"\x00\x00", b"\x00\x20"), 0.5)


class MediaTests(unittest.TestCase):
    """Real FFmpeg with a deterministic external downloader fixture, no network."""
    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg"):
            if os.environ.get("VIDEO_DIGEST_REQUIRE_FFMPEG"):
                raise RuntimeError("FFmpeg is required for the clean-install/CI media tests")
            raise unittest.SkipTest("ffmpeg not on PATH; media smoke tests skipped")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="video digest é ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "fixture clip.mkv"
        vd.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                "-i", "testsrc2=size=160x90:rate=5:duration=8", "-c:v", "ffv1", str(self.video)])
        self.downloader = self.root / "offline downloader.py"
        # Behave like yt-dlp at its subprocess boundary: emit UTF-8 metadata,
        # copy media and write captions in cwd, or fail one playlist item.
        self.downloader.write_text('''import json, os, pathlib, shutil, sys
sys.stdout.reconfigure(encoding="utf-8")
a = sys.argv[1:]
url = a[-1]
vid = "bad" if "bad" in url else "silent" if "silent" in url else "speech"
if vid == "bad":
    sys.stderr.write("fixture unavailable")
    sys.exit(1)
info = {"id": vid, "title": "Café 演示 " + vid, "duration": 8,
        "formats": [{}], "channel": "Fixture", "upload_date": "20260101",
        "chapters": [{"start_time": 0, "title": "Demo"}],
        "subtitles": {"en": [{"ext": "json3"}]} if vid == "speech" else {}}
if "-J" in a:
    if "playlist" in url:
        info = {"_type": "playlist", "id": "list", "title": "Playlist café",
                "entries": [{"id": key, "title": "Café 演示 " + key} for key in
                            (["speech", "bad", "silent"] if "failure" in url else ["speech", "silent"])]}
    print(json.dumps(info, ensure_ascii=False))
elif "--skip-download" in a:
    pathlib.Path("subs.en.json3").write_text(json.dumps({"events": [
        {"tStartMs": 1000, "segs": [{"utf8": "café " + "a useful spoken word " * 10}]},
        {"tStartMs": 5000, "segs": [{"utf8": "later spoken evidence"}]}]}), encoding="utf-8")
else:
    shutil.copyfile(os.environ["VIDEO_DIGEST_FIXTURE"], "video.mkv")
''', encoding="utf-8")

    def invoke(self, url="https://example.test/speech", *flags):
        def offline_ytdlp(args, **kwargs):
            return vd.run([sys.executable, str(self.downloader), *args], **kwargs)
        # The downloader itself is a Python fixture, so only dependency discovery
        # is stubbed; subprocess execution and every FFmpeg operation are real.
        which = shutil.which
        with patch.dict(os.environ, {"VIDEO_DIGEST_FIXTURE": str(self.video)}), \
             patch.object(vd, "ytdlp", side_effect=offline_ytdlp), \
             patch.object(vd.shutil, "which", side_effect=lambda tool: sys.executable
                          if tool == "yt-dlp" else which(tool)), \
             patch.object(sys, "argv", ["video-digest", url, *flags]), \
             working_directory(self.root):
            vd.main()

    def test_video_relative_output_cache_force_and_keep(self):
        output = self.root / "relative output é"
        self.invoke("https://example.test/speech", "--out", output.name, "--min-gap", "2",
                    "--max-gap", "4", "--min-frames", "3", "--scene", "0.9", "--width", "120",
                    "--dedup", "0", "--keep-video")
        meta = json.loads((output / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["transcript"]["has_speech"])
        self.assertEqual(meta["settings"]["width"], 120)
        self.assertGreater(len(meta["frames"]), 1)
        self.assertTrue((output / "video.mkv").exists())
        self.assertTrue((output / meta["frames"][0]["file"]).read_bytes().startswith(b"\xff\xd8"))
        digest = output / "digest.md"
        self.assertIn("https://youtu.be/speech?t=", digest.read_text(encoding="utf-8"))
        self.assertIn("café", (output / "transcript.txt").read_text(encoding="utf-8"))
        original = digest.stat().st_mtime_ns
        self.invoke("https://example.test/speech", "--out", output.name)
        self.assertEqual(digest.stat().st_mtime_ns, original)
        self.invoke("https://example.test/speech", "--out", output.name, "--force")
        self.assertFalse((output / "video.mkv").exists())
        self.assertNotEqual(digest.stat().st_mtime_ns, original)
        # Replace an already existing digest, exercising Windows overwrite semantics.
        args = vd.argparse.Namespace(scene=.3, max_gap=20, min_gap=3, min_frames=24, width=1280, dedup=.005)
        vd.write_outputs(output, {"id": "silent", "duration": 8}, "url", [], [], [], "none", args)
        self.assertIn("no speech", digest.read_text(encoding="utf-8"))

    def test_playlist_cache_silent_and_partial_failure(self):
        self.invoke("https://example.test/playlist", "--out", "playlist output")
        output = self.root / "playlist output"
        speech = output / "01-cafe-speech" / "digest.md"
        self.assertTrue(speech.is_file())
        silent = output / "02-cafe-silent" / "meta.json"
        self.assertFalse(json.loads(silent.read_text(encoding="utf-8"))["transcript"]["has_speech"])
        original = speech.stat().st_mtime_ns
        self.invoke("https://example.test/playlist", "--out", "playlist output")
        self.assertEqual(speech.stat().st_mtime_ns, original)
        self.assertIn("01-cafe-speech/digest.md", (output / "index.md").read_text(encoding="utf-8"))
        with self.assertRaises(SystemExit) as failure:
            self.invoke("https://example.test/playlist-failure", "--out", "partial")
        self.assertEqual(failure.exception.code, 1)
        index = (self.root / "partial" / "index.md").read_text(encoding="utf-8")
        self.assertIn("fixture unavailable", index)
        self.assertIn("03-cafe-silent/digest.md", index)

    def test_failed_force_does_not_leave_success_marker(self):
        self.invoke("https://example.test/speech", "--out", "forced")
        with patch.object(vd, "scene_scores", side_effect=RuntimeError("scan failed")):
            with self.assertRaisesRegex(RuntimeError, "scan failed"):
                self.invoke("https://example.test/speech", "--out", "forced", "--force")
        self.assertFalse((self.root / "forced" / "digest.md").exists())


if __name__ == "__main__":
    unittest.main()
