"""Builds the free downloads from this checkout (no compiling, runs on any computer with Python 3):

    dist/Skrift-mac.zip        Skrift.app for Apple-chip Macs (drag to Applications)
    dist/Skrift-windows.zip    Skrift folder with "Start Skrift.bat" (Windows 10/11, 64-bit)
    dist/Skrift-linux.tar.gz   only with --linux: skrift folder with skrift.sh (+ add-to-menu.sh), x86_64 and arm64
                               (works and was tested in Oct 2026, but isn't released until there's demand)

Each one carries the app code and the official `uv` tool, which installs Python and Skrift's packages into the
user's own data folder the first time it starts.

    python3 deploy/build_release.py [--linux]
"""
from __future__ import annotations

import io
import os
import plistlib
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD, DIST = ROOT / "build", ROOT / "dist"
UV_VERSION = "0.12.24"
UV_URL = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
NAME = "Skrift"
APP_FILES = ["skrift", "web", "pyproject.toml", "uv.lock", "bootstrap.py", "LICENSE"]


def fetch_uv(target: str) -> bytes:
    """The uv binary for one platform, cached in build/."""
    exe = "uv.exe" if "windows" in target else "uv"
    cached = BUILD / "uv" / UV_VERSION / target / exe
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        archive = f"uv-{target}.zip" if "windows" in target else f"uv-{target}.tar.gz"
        data = urllib.request.urlopen(UV_URL + archive, timeout=120).read()
        if archive.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                cached.write_bytes(z.read(next(n for n in z.namelist() if n.endswith(exe))))
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                member = next(m for m in t.getmembers() if m.name.endswith("/" + exe) or m.name == exe)
                cached.write_bytes(t.extractfile(member).read())
    return cached.read_bytes()


def app_tree(dest: Path):
    """Copy the app code (no data, models, benchmarks or developer files)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in APP_FILES:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        else:
            shutil.copy2(src, dest / name)
    (dest / "README.txt").write_text(README)


README = f"""{NAME}: free, private, unlimited transcription.

Paste a YouTube or Twitch link (or drop in a video/audio file), and {NAME} writes out every word,
cross-checked by several speech engines, with subtitles (SRT/VTT), Word, PDF and text exports.

Everything runs on THIS computer. Nothing is uploaded anywhere, there are no accounts and no limits.
{NAME} is free software under the GNU GPL v3 (see the LICENSE file next to this one).
The first time you open it, it sets itself up (a few minutes, a few GB of downloads). After that it
opens in a couple of seconds.

Your transcripts and settings live in:
  Mac      ~/Library/Application Support/Skrift
  Windows  %LOCALAPPDATA%\\Skrift
  Linux    ~/.local/share/skrift
"""


def executable(info: zipfile.ZipInfo | tarfile.TarInfo):
    if isinstance(info, zipfile.ZipInfo):
        info.external_attr = (stat.S_IFREG | 0o755) << 16
    else:
        info.mode = 0o755
    return info


def zip_dir(src: Path, out: Path, exec_names=()):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            arc = p.relative_to(src.parent).as_posix()
            if p.is_dir():
                continue
            info = zipfile.ZipInfo.from_file(p, arc)
            if p.name in exec_names:
                executable(info)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())


def build_mac():
    stage = BUILD / "stage-mac"
    shutil.rmtree(stage, ignore_errors=True)
    app = stage / f"{NAME}.app" / "Contents"
    res = app / "Resources"
    app_tree(res / "app")
    (res / "bin").mkdir(parents=True)
    (res / "bin" / "uv").write_bytes(fetch_uv("aarch64-apple-darwin"))
    icns = ROOT / "deploy" / "mac" / "Skrift.icns"
    if icns.exists():
        shutil.copy2(icns, res / "Skrift.icns")
    (app / "MacOS").mkdir(parents=True)
    (app / "MacOS" / NAME).write_text(f"""#!/bin/bash
# Starts {NAME}. First launch sets itself up, then everything opens in the browser.
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
export SKRIFT_UV="$R/bin/uv"
mkdir -p "$HOME/Library/Logs"
if [ "$(uname -m)" != "arm64" ]; then
  osascript -e 'display alert "{NAME} needs a Mac with an Apple chip (M1 or newer)."' >/dev/null 2>&1
  exit 1
fi
exec "$R/bin/uv" run --no-project --python 3.12 --python-preference only-managed --quiet "$R/app/bootstrap.py" >> "$HOME/Library/Logs/{NAME}.log" 2>&1
""")
    with open(app / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleExecutable": NAME,
                       "CFBundleIdentifier": "app.skrift.transcriber", "CFBundleVersion": "1.0.0",
                       "CFBundleShortVersionString": "1.0.0", "CFBundlePackageType": "APPL",
                       "CFBundleIconFile": "Skrift", "LSMinimumSystemVersion": "14.0", "LSUIElement": True}, f)
    out = DIST / f"{NAME}-mac.zip"
    zip_dir(stage / f"{NAME}.app", out, exec_names={NAME, "uv"})
    return out


def build_windows():
    stage = BUILD / "stage-win" / NAME
    shutil.rmtree(stage.parent, ignore_errors=True)
    app_tree(stage / "app")
    (stage / "bin").mkdir(parents=True)
    (stage / "bin" / "uv.exe").write_bytes(fetch_uv("x86_64-pc-windows-msvc"))
    (stage / f"Start {NAME}.bat").write_text(f"""@echo off
title {NAME}
echo Starting {NAME}... it will open in your browser.
echo The first time, it sets itself up (a few minutes). Keep this window open while you use {NAME}.
echo.
set "SKRIFT_UV=%~dp0bin\\uv.exe"
"%~dp0bin\\uv.exe" run --no-project --python 3.12 --python-preference only-managed --quiet "%~dp0app\\bootstrap.py"
if errorlevel 1 pause
""".replace("\n", "\r\n"))
    out = DIST / f"{NAME}-windows.zip"
    zip_dir(stage, out)
    return out


def build_linux():
    stage = BUILD / "stage-linux" / NAME.lower()
    shutil.rmtree(stage.parent, ignore_errors=True)
    app_tree(stage / "app")
    (stage / "bin").mkdir(parents=True)
    (stage / "bin" / "uv-x86_64").write_bytes(fetch_uv("x86_64-unknown-linux-gnu"))
    (stage / "bin" / "uv-aarch64").write_bytes(fetch_uv("aarch64-unknown-linux-gnu"))
    (stage / "skrift.sh").write_text(f"""#!/bin/sh
# Starts {NAME}. First launch sets itself up, then everything opens in the browser.
HERE="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -m)" in aarch64|arm64) UV="$HERE/bin/uv-aarch64" ;; *) UV="$HERE/bin/uv-x86_64" ;; esac
export SKRIFT_UV="$UV"
exec "$UV" run --no-project --python 3.12 --python-preference only-managed --quiet "$HERE/app/bootstrap.py"
""")
    (stage / "add-to-menu.sh").write_text(f"""#!/bin/sh
# Adds {NAME} to your applications menu.
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/{NAME.lower()}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name={NAME}
Comment=Free, private transcription
Exec="$HERE/skrift.sh"
Icon=$HERE/app/web/icon-180.png
Terminal=false
Categories=AudioVideo;Utility;
EOF
echo "{NAME} is now in your applications menu."
""")
    out = DIST / f"{NAME}-linux.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        for p in sorted(stage.rglob("*")):
            info = t.gettarinfo(p, p.relative_to(stage.parent).as_posix())
            if p.name in ("skrift.sh", "add-to-menu.sh", "uv-x86_64", "uv-aarch64"):
                executable(info)
            if p.is_file():
                with open(p, "rb") as f:
                    t.addfile(info, f)
            else:
                t.addfile(info)
    return out


if __name__ == "__main__":
    DIST.mkdir(exist_ok=True)
    builds = [build_mac, build_windows] + ([build_linux] if "--linux" in sys.argv else [])
    for out in (b() for b in builds):
        print(f"{out.relative_to(ROOT)}  {out.stat().st_size / 1e6:.1f} MB")
