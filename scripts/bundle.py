#!/usr/bin/env python3
"""Build a reproducible source ZIP containing the CLI, skill, docs, and tests."""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "README.md", "pyproject.toml", "MANIFEST.in", "video-digest", "video_digest.py",
    "flake.nix", "flake.lock", "scripts/bundle.py", "scripts/check_install.py",
)


def bundle(destination):
    version = re.search(r'^version = "([^"]+)"',
                        (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"video-digest-{version}.zip"
    files = [ROOT / name for name in FILES]
    files += list((ROOT / "skills").rglob("*.md"))
    files += list((ROOT / "tests").rglob("*.py"))
    files += list((ROOT / ".github" / "workflows").glob("*.yml"))
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(ROOT).as_posix()
            entry = zipfile.ZipInfo(f"video-digest/{relative}", (2020, 1, 1, 0, 0, 0))
            entry.create_system = 3
            mode = 0o755 if relative == "video-digest" else 0o644
            entry.external_attr = (0o100000 | mode) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            # Normalize checkout line endings for the same ZIP on Windows and Unix.
            archive.writestr(entry, path.read_bytes().replace(b"\r\n", b"\n"))
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(f"{checksum}  {target.name}\n", encoding="utf-8")
    print(f"{target}\nSHA256 {checksum}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    bundle(parser.parse_args().out)
