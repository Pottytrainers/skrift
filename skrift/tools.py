"""Helper programs Skrift needs, provided without asking anyone to install anything:

ffmpeg : audio conversion (also used by parakeet-mlx and yt-dlp). Uses one already installed, otherwise the
         copy that ships inside the imageio-ffmpeg Python package.
deno   : the JavaScript runtime yt-dlp needs for YouTube. Uses one already installed (deno or node), otherwise
         downloads the official build from GitHub once.
Both are put on PATH (in a private bin folder) for this process and the programs it starts.
"""
import os
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

DENO_TAG = "v2.9.7"


def bin_dir() -> Path:
    from .store import DATA
    d = DATA / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exe(name: str) -> str:
    return name + (".exe" if platform.system() == "Windows" else "")


def ffmpeg() -> str:
    found = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if found:
        return found
    local = bin_dir() / _exe("ffmpeg")
    if not local.exists():
        import imageio_ffmpeg
        src = imageio_ffmpeg.get_ffmpeg_exe()
        try:
            local.symlink_to(src)
        except OSError:  # Windows without symlink rights
            shutil.copy2(src, local)
    return str(local)


def ffprobe() -> str | None:
    return os.environ.get("FFPROBE") or shutil.which("ffprobe")


def duration(path: Path) -> float:
    """Seconds of media, via ffprobe if present, else by reading ffmpeg's banner."""
    probe = ffprobe()
    if probe:
        import json
        r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True)
        try:
            return float(json.loads(r.stdout)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _deno_asset() -> str:
    system, mach = platform.system(), platform.machine().lower()
    arch = "aarch64" if mach in ("arm64", "aarch64") else "x86_64"
    target = {"Windows": "pc-windows-msvc", "Darwin": "apple-darwin"}.get(system, "unknown-linux-gnu")
    return f"deno-{arch}-{target}.zip"


def js_runtime() -> str | None:
    """deno (or node) for yt-dlp's YouTube support; downloads deno once if neither is installed."""
    found = shutil.which("deno") or shutil.which("node")
    if found:
        return found
    local = bin_dir() / _exe("deno")
    if local.exists():
        return str(local)
    try:
        from .llm_server import _download
        zpath = bin_dir() / "deno.zip"
        _download(f"https://github.com/denoland/deno/releases/download/{DENO_TAG}/{_deno_asset()}", zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(bin_dir())
        zpath.unlink()
        local.chmod(0o755)
        return str(local)
    except Exception:  # noqa: BLE001 - YouTube may still work for many videos without it
        return None


def setup_path():
    """Make ffmpeg and the JS runtime findable by name for everything this process starts."""
    ff = ffmpeg()
    paths = [str(bin_dir()), str(Path(ff).parent)] + os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(p for p in paths if p))
