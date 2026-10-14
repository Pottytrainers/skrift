"""Gets audio from a YouTube / Twitch / any yt-dlp-supported link, or from an uploaded file,
and converts it to what the engines and the browser player need."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# Errors that usually mean "YouTube/Twitch changed something; a newer yt-dlp fixes it".
BREAKAGE = re.compile(r"unable to extract|nsig|signature|player response|http error 403|"
                      r"requested format is not available|failed to (decrypt|parse)|"
                      r"unsupported url|po token|jsinterp|challenge", re.I)
AUTH = re.compile(r"sign in|login|log in|subscriber|members[- ]only|private video|"
                  r"age[- ]restricted|confirm your age|not a bot", re.I)


class FetchError(Exception):
    def __init__(self, msg, kind="error"):
        super().__init__(msg)
        self.kind = kind  # error | breakage | auth | live


def _opts(cookies_browser: str | None, quiet=True):
    o = {"quiet": quiet, "no_warnings": True, "noplaylist": True, "noprogress": True,
         "socket_timeout": 30, "retries": 10, "fragment_retries": 10,
         "concurrent_fragment_downloads": 8}
    if cookies_browser:
        o["cookiesfrombrowser"] = (cookies_browser,)
    return o


def _classify(e: Exception) -> FetchError:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
    if AUTH.search(msg):
        return FetchError(msg, "auth")
    if BREAKAGE.search(msg):
        return FetchError(msg, "breakage")
    return FetchError(msg)


def probe(url: str, cookies_browser: str | None = None) -> dict:
    from yt_dlp import YoutubeDL
    try:
        with YoutubeDL({**_opts(cookies_browser), "skip_download": True}) as y:
            info = y.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001 - yt-dlp raises many types
        raise _classify(e) from e
    if info.get("_type") == "playlist":
        raise FetchError("That link is a playlist or channel. Paste a link to a single video or VOD.")
    if info.get("is_live") or info.get("live_status") == "is_live":
        raise FetchError("That stream is still live. Once it ends and the VOD is posted, paste the VOD link.", "live")
    up = info.get("upload_date") or ""
    return {
        "title": info.get("title") or "Untitled",
        "channel": info.get("channel") or info.get("uploader") or info.get("creator") or "",
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
        "webpage_url": info.get("webpage_url") or url,
        "site": info.get("extractor_key") or "",
        "upload_date": f"{up[:4]}-{up[4:6]}-{up[6:]}" if len(up) == 8 else "",
        "description": (info.get("description") or "")[:8000],
        "chapters": [c.get("title", "") for c in (info.get("chapters") or [])][:200],
        "tags": (info.get("tags") or [])[:60],
    }


def download(url: str, folder: Path, progress, cookies_browser: str | None = None) -> Path:
    from yt_dlp import YoutubeDL

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(d.get("downloaded_bytes", 0) / total, 1.0))
            elif d.get("fragment_count"):
                progress(d.get("fragment_index", 0) / d["fragment_count"])

    opts = {**_opts(cookies_browser), "format": "bestaudio/best",
            "outtmpl": str(folder / "source.%(ext)s"), "progress_hooks": [hook]}
    try:
        with YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
            path = Path(y.prepare_filename(info))
    except Exception as e:  # noqa: BLE001
        raise _classify(e) from e
    if not path.exists():
        found = sorted(folder.glob("source.*"))
        if not found:
            raise FetchError("The download finished but no audio file was saved.")
        path = found[0]
    return path


def youtube_captions(url: str, folder: Path, cookies_browser: str | None = None) -> Path | None:
    """Download YouTube's own speech-recognition captions (free, word-timed) to use as a third vote.
    Only the original-language English track: an auto-*translated* track would be useless as a vote."""
    from yt_dlp import YoutubeDL
    try:
        with YoutubeDL({**_opts(cookies_browser), "skip_download": True}) as y:
            info = y.extract_info(url, download=False)
        auto = info.get("automatic_captions") or {}
        lang = "en-orig" if "en-orig" in auto else \
            ("en" if "en" in auto and (info.get("language") or "en").startswith("en")
             and not any(k.endswith("-orig") for k in auto) else None)
        if not lang or not any(f.get("ext") == "json3" for f in auto[lang]):
            return None
        # subtitle choices are made while the video is looked up, so look it up again with them set
        with YoutubeDL({**_opts(cookies_browser), "skip_download": True, "writeautomaticsub": True,
                        "subtitleslangs": [lang], "subtitlesformat": "json3",
                        "outtmpl": str(folder / "youtube.%(ext)s")}) as y:
            y.extract_info(url, download=True)
    except Exception:  # noqa: BLE001 - the third vote is optional; never fail a job over it
        return None
    found = sorted(folder.glob("youtube*.json3"))
    return found[0] if found else None


def media_duration(path: Path) -> float:
    from .tools import duration
    return duration(path)


def convert(src: Path, folder: Path, progress, make_m4a: bool = True) -> tuple[Path, Path, Path, float]:
    """source -> audio.wav (engines), audio.pcm (raw, memory-mapped by Whisper), audio.m4a (player).
    make_m4a=False when the player copy already exists (re-encoding it on every redo degrades it)."""
    dur = media_duration(src)
    wav, pcm, m4a = folder / "audio.wav", folder / "audio.pcm", folder / "audio.m4a"
    from .tools import ffmpeg
    cmd = [ffmpeg(), "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-i", str(src), "-vn",
           "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
           "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm)]
    if make_m4a:
        cmd += ["-map", "0:a:0", "-ac", "1", "-c:a", "aac", "-b:a", "80k", "-movflags", "+faststart", str(m4a)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.startswith("out_time_us=") and dur:
            try:
                progress(min(int(line.split("=")[1]) / 1e6 / dur, 1.0))
            except ValueError:
                pass
    err = p.stderr.read()
    if p.wait() != 0:
        raise FetchError("Couldn't read audio from that file. " + err.strip()[-300:])
    return wav, pcm, m4a, dur or media_duration(m4a)


def update_ytdlp(log=print) -> bool:
    """Upgrade yt-dlp in place (YouTube changes break older versions every few weeks)."""
    t = time.time()
    uv = os.environ.get("SKRIFT_UV")  # installed copies: use the bundled uv (works whether or not pip is there)
    cmd = [uv, "pip", "install", "--python", sys.executable, "-q", "-U", "yt-dlp[default]"] if uv else \
        [sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default]"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    log(f"yt-dlp update: rc={r.returncode} in {time.time() - t:.0f}s {r.stderr.strip()[-200:]}")
    return r.returncode == 0
