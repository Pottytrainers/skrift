"""Jobs live in data/jobs/<id>/ as plain JSON files, so nothing needs a database."""
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _default_data() -> Path:
    """A developer checkout keeps data next to the code; installed copies use the normal per-user folder."""
    if (ROOT / "data").exists():
        return ROOT / "data"
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Skrift"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Skrift"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "skrift"


DATA = Path(os.environ.get("SKRIFT_DATA", _default_data()))
JOBS = DATA / "jobs"
SETTINGS = DATA / "settings.json"
MODELS = Path(os.environ.get("SKRIFT_MODELS", DATA / "models"))  # models that must be plain files (ONNX, llama.cpp)
LOG = DATA / "worker.log"
JOBS.mkdir(parents=True, exist_ok=True)

ACTIVE = ("queued", "downloading", "converting", "transcribing", "checking")
RUNNING = ACTIVE[1:]
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{4}$")

DEFAULT_SETTINGS = {
    "vocab": [],
    "cookies_browser": "",     # "", "chrome", "safari", "firefox", "brave", "edge"
    "second_model": "turbo",   # turbo | large-v3
    "third_vote": True,        # Twitch/files: run Whisper large-v3 as a tie-breaker (YouTube uses its own captions)
    "meaning_check": True,     # local AI reads each disputed spot in context and picks the version that makes sense
    "meaning_model": "standard",  # standard (Gemma 4 E4B, ~4 GB) | thorough (Gemma 4 12B, ~11 GB, better)
    "default_mode": "accurate",
    "notify": True,
    "allow_lan": False,        # only this computer can open Skrift unless the user turns this on
    "last_ytdlp_update": 0,
}


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def settings() -> dict:
    return {**DEFAULT_SETTINGS, **(read_json(SETTINGS, {}) or {})}


def save_settings(patch: dict) -> dict:
    s = settings()
    for k in DEFAULT_SETTINGS:
        if k in patch:
            s[k] = patch[k]
    write_json(SETTINGS, s)
    return s


def job_dir(jid: str) -> Path:
    if not _ID.match(jid or ""):
        raise KeyError(jid)
    return JOBS / jid


def new_job(source: dict, options: dict, meta: dict | None = None) -> dict:
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    d = JOBS / jid
    d.mkdir(parents=True)
    job = {"id": jid, "status": "queued", "stage": "Waiting in line", "progress": 0.0,
           "created": time.time(), "updated": time.time(), "source": source,
           "options": options, "meta": meta or {"title": source.get("url") or source.get("filename") or "Untitled"},
           "error": None, "error_kind": None, "stats": {}}
    write_json(d / "job.json", job)
    return job


def load(jid: str) -> dict:
    job = read_json(job_dir(jid) / "job.json")
    if job is None:
        raise KeyError(jid)
    return job


def save(job: dict) -> None:
    job["updated"] = time.time()
    write_json(job_dir(job["id"]) / "job.json", job)


def all_jobs() -> list[dict]:
    out = []
    for d in JOBS.iterdir():
        j = read_json(d / "job.json") if _ID.match(d.name) else None
        if j:
            out.append(j)
    return sorted(out, key=lambda j: j["created"], reverse=True)


def delete(jid: str) -> None:
    d = job_dir(jid)
    if d.parent == JOBS and d.exists():
        shutil.rmtree(d)


def transcript(jid: str):
    return read_json(job_dir(jid) / "transcript.json")


def save_transcript(jid: str, tr: dict) -> None:
    write_json(job_dir(jid) / "transcript.json", tr)
