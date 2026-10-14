"""Skrift web server: API + the single-page app + the job runner.

    python -m skrift.server --port 5201        (development)
    launched by launchd with sockets             (deploy/mac, see launcher.py)
"""
import argparse
import bisect
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import store
from .export import FORMATS, filename, render

WEB = store.ROOT / "web"
IDLE_MINUTES = float(os.environ.get("IDLE_EXIT_MINUTES", "0"))  # 0 = never sleep
URL_RE = re.compile(r"https?://\S+")

app = FastAPI(title="Skrift", docs_url=None, redoc_url=None)
_last_request = time.time()


@app.middleware("http")
async def touch(request: Request, call_next):
    global _last_request
    _last_request = time.time()
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # revalidate, so app updates show up right away
    return response


# ------------------------------------------------------------------ runner
class Runner(threading.Thread):
    """Runs queued jobs one at a time (they all share the GPU), each in its own process."""

    def __init__(self):
        super().__init__(daemon=True)
        self.wake = threading.Event()
        self.proc = None
        self.current = None
        self.cancelled = set()

    def busy(self):
        return self.current is not None or any(j["status"] == "queued" for j in store.all_jobs())

    def recover(self):
        """After a restart: re-attach to a worker that's still going, re-queue ones that died."""
        for j in store.all_jobs():
            if j["status"] == "uploading":
                j.update(status="error", error="The upload was interrupted. Drop the file in again.")
                store.save(j)
            if j["status"] in store.RUNNING:
                pid = store.read_json(store.job_dir(j["id"]) / "pid.json", {}).get("pid")
                if pid and _alive(pid):
                    self.current = j["id"]
                    while _alive(pid):
                        time.sleep(2)
                    self.current = None
                    j = store.load(j["id"])
                if j["status"] in store.RUNNING:
                    j.update(status="queued", stage="Waiting in line (resuming)")
                    store.save(j)

    def run(self):
        try:
            self.recover()
        except Exception as e:  # noqa: BLE001
            print("recover failed:", e, flush=True)
        while True:
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - never let the runner die silently
                import traceback
                traceback.print_exc()
                print("runner error:", e, flush=True)
                self.proc, self.current = None, None
                time.sleep(5)

    def step(self):
        queued = [j for j in store.all_jobs() if j["status"] == "queued"]
        if not queued:
            self.wake.wait(10)
            self.wake.clear()
            return
        from . import setup
        if not setup.is_ready():  # first run: models are still downloading
            setup.start()
            for j in queued:
                if j.get("stage") != "Waiting for setup to finish":
                    j["stage"] = "Waiting for setup to finish"
                    store.save(j)
            time.sleep(3)
            return
        job = min(queued, key=lambda j: j["created"])
        self.current = job["id"]
        with open(store.LOG, "a") as logf:
            self.proc = _launch([sys.executable, "-m", "skrift.worker", job["id"]], logf)
        store.write_json(store.job_dir(job["id"]) / "pid.json", {"pid": self.proc.pid})
        rc = self.proc.wait()
        try:
            j = store.load(job["id"])
            if job["id"] in self.cancelled:  # the worker may have saved progress after we marked it
                self.cancelled.discard(job["id"])
                if store.transcript(job["id"]):
                    j.update(status="done", stage="Done", progress=1.0, eta=None)
                else:
                    j.update(status="cancelled", stage="Cancelled", eta=None)
                store.save(j)
            elif j["status"] in store.ACTIVE and j["status"] != "queued":
                j.update(status="error", error=f"The transcriber stopped unexpectedly (code {rc}). "
                         "Press Retry; it resumes where it left off.", eta=None)
                store.save(j)
        except KeyError:
            pass  # deleted while running
        self.proc, self.current = None, None

    def cancel(self, jid):
        if self.current == jid and self.proc:
            self.cancelled.add(jid)
            _kill_tree(self.proc.pid)


def _launch(cmd, logf):
    """Start a job in its own process group, at low priority: it uses every core the computer isn't using,
    while games, OBS and the browser stay smooth. On a Mac, caffeinate keeps it from idle-sleeping mid-job
    (the worker does the same on Windows itself)."""
    if sys.platform == "darwin" and os.path.exists("/usr/bin/caffeinate"):
        cmd = ["/usr/bin/caffeinate", "-i"] + cmd
    if os.name == "nt":  # its helpers (ffmpeg, the AI runtime) inherit the low priority
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        return subprocess.Popen(cmd, cwd=store.ROOT, stdout=logf, stderr=logf, creationflags=flags)
    cmd = ["/usr/bin/nice", "-n", "10"] + cmd if os.path.exists("/usr/bin/nice") else cmd
    return subprocess.Popen(cmd, cwd=store.ROOT, stdout=logf, stderr=logf, start_new_session=True)


def _kill_tree(pid):
    """Stop a job and everything it started (e.g. the AI model's server)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def _alive(pid):
    """Is a process still running? (On Windows, os.kill(pid, 0) would KILL it, so ask the OS properly.)"""
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


runner = Runner()


# ------------------------------------------------------------------ API
def summary(j):
    keys = ("id", "status", "stage", "progress", "eta", "created", "finished", "error", "error_kind", "stats", "options")
    out = {k: j.get(k) for k in keys}
    m = j.get("meta", {})
    out["meta"] = {k: m.get(k) for k in ("title", "channel", "duration", "webpage_url", "site", "upload_date")}
    out["source"] = j.get("source", {}).get("kind")
    out["thumb"] = (store.job_dir(j["id"]) / "thumb.jpg").exists()
    return out


def _options(body):
    s = store.settings()
    mode = body.get("mode") or s.get("default_mode", "accurate")
    lang = body.get("language") or "en"
    vocab = [v.strip() for v in re.split(r"[,\n]", body.get("vocab") or "") if v.strip()]
    return {"mode": mode if mode in ("accurate", "fast") else "accurate",
            "language": lang if lang in ("en", "auto") else "en", "vocab": vocab[:100]}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [summary(j) for j in store.all_jobs()], "busy": runner.current}


@app.post("/api/jobs")
async def create(request: Request):
    body = await request.json()
    urls = list(dict.fromkeys(URL_RE.findall(body.get("url", ""))))
    if not urls:
        raise HTTPException(400, "Paste a YouTube or Twitch link (starting with https://).")
    opts = _options(body)
    jobs = [store.new_job({"kind": "url", "url": u.rstrip(".,)")}, opts) for u in urls[:50]]
    runner.wake.set()
    return {"jobs": [summary(j) for j in jobs]}


@app.post("/api/upload")
async def upload(request: Request, name: str, mode: str = "", language: str = "en", vocab: str = ""):
    name = Path(name).name or "upload"
    ext = Path(name).suffix.lower()[:8] or ".bin"
    title = Path(name).stem
    job = store.new_job({"kind": "file", "filename": name}, _options({"mode": mode, "language": language, "vocab": vocab}),
                        {"title": title, "channel": "", "duration": 0})
    job["status"] = "uploading"
    store.save(job)
    dest = store.job_dir(job["id"]) / f"source{ext}"
    try:
        with open(dest, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
    except Exception:
        store.delete(job["id"])
        raise
    job["status"] = "queued"
    store.save(job)
    runner.wake.set()
    return {"jobs": [summary(job)]}


def _job(jid):
    try:
        return store.load(jid)
    except KeyError:
        raise HTTPException(404, "Not found") from None


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = _job(jid)
    out = summary(j)
    out["meta"] = {k: v for k, v in j.get("meta", {}).items() if k != "description"}
    if j["status"] == "done":
        out["transcript"] = store.transcript(jid)
    return out


@app.put("/api/jobs/{jid}/transcript")
async def put_transcript(jid: str, request: Request):
    j = _job(jid)
    if j["status"] != "done":
        raise HTTPException(409, "Still transcribing")
    tr = await request.json()
    if not isinstance(tr.get("words"), list) or not isinstance(tr.get("reviews"), list):
        raise HTTPException(400, "Bad transcript")
    old = store.transcript(jid) or {}
    if tr.get("version", 0) < old.get("version", 0):
        raise HTTPException(409, "This transcript was changed in another tab. Reload to get the latest.")
    tr = {**old, "words": tr["words"], "reviews": tr["reviews"], "version": old.get("version", 0) + 1}
    store.save_transcript(jid, tr)
    j["stats"]["reviews"] = sum(1 for r in tr["reviews"] if not r.get("done") and r.get("kind") not in ("single", "voted"))
    j["stats"]["words"] = len(tr["words"])
    j["edited"] = time.time()
    store.save(j)
    return {"version": tr["version"]}


@app.patch("/api/jobs/{jid}")
async def rename(jid: str, request: Request):
    j = _job(jid)
    body = await request.json()
    if body.get("title"):
        j["meta"]["title"] = str(body["title"])[:300]
        store.save(j)
    return summary(j)


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    j = _job(jid)
    if j["status"] in store.ACTIVE:
        if store.transcript(jid):  # a cancelled redo keeps the last finished transcript
            j.update(status="done", stage="Done", progress=1.0, eta=None)
        else:
            j.update(status="cancelled", stage="Cancelled", eta=None)
        store.save(j)
        runner.cancel(jid)
    return summary(j)


@app.post("/api/jobs/{jid}/retry")
def retry(jid: str):
    j = _job(jid)
    if j["status"] in ("error", "cancelled"):
        j.update(status="queued", stage="Waiting in line", error=None, error_kind=None, progress=0)
        store.save(j)
        runner.wake.set()
    return summary(j)


@app.post("/api/jobs/{jid}/recheck")
def recheck(jid: str):
    """Re-run the whole job (e.g. after adding words to the vocabulary)."""
    j = _job(jid)
    if j["status"] in store.ACTIVE:
        raise HTTPException(409, "Already running")
    d = store.job_dir(jid)
    if not (d / "audio.m4a").exists() and j["source"]["kind"] != "url":
        raise HTTPException(409, "The original audio is gone, so this one can't be redone.")
    for name in ("primary.json", "second.json", "third.json", "audio.wav", "audio.pcm"):
        (d / name).unlink(missing_ok=True)  # the worker rebuilds these from the original audio
    j.update(status="queued", stage="Waiting in line", progress=0, error=None, eta=None)
    store.save(j)
    runner.wake.set()
    return summary(j)


@app.delete("/api/jobs/{jid}")
def delete(jid: str):
    j = _job(jid)
    if j["status"] in store.ACTIVE:
        j.update(status="cancelled")
        store.save(j)
        runner.cancel(jid)
        time.sleep(0.5)
    store.delete(jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/audio")
def audio(jid: str):
    p = store.job_dir(jid) / "audio.m4a"
    if not p.exists():
        raise HTTPException(404, "No audio")
    return FileResponse(p, media_type="audio/mp4")


@app.get("/api/jobs/{jid}/thumb")
def thumb(jid: str):
    p = store.job_dir(jid) / "thumb.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/jobs/{jid}/export/{fmt}")
def export(jid: str, fmt: str, timestamps: int = 1, sounds: int = 1, bleep: int = 0, download: int = 1):
    if fmt not in FORMATS:
        raise HTTPException(404, "Unknown format")
    j = _job(jid)
    tr = store.transcript(jid)
    if not tr:
        raise HTTPException(409, "Not transcribed yet")
    data = render(fmt, j, tr, bool(timestamps), bool(sounds), bool(bleep))
    name = filename(j, fmt)
    disp = "attachment" if download else "inline"
    return Response(data, media_type=FORMATS[fmt][1] + ("; charset=utf-8" if fmt in ("srt", "vtt", "txt", "md", "json") else ""),
                    headers={"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(name)}"})


@app.get("/api/search")
def search(q: str):
    """Find a phrase across every transcript."""
    q = q.strip()
    if len(q) < 2:
        return {"results": []}
    rx = re.compile(r"\b" + re.escape(q) + r"\b", re.I)
    results = []
    for j in store.all_jobs():
        if j["status"] != "done":
            continue
        tr = store.transcript(j["id"]) or {}
        words = tr.get("words", [])
        text, starts = [], []
        pos = 0
        for w in words:
            starts.append(pos)
            text.append(w["t"])
            pos += len(w["t"]) + 1
        full = " ".join(text)
        hits = []
        for m in rx.finditer(full):
            i = bisect.bisect_right(starts, m.start()) - 1
            a, b = max(0, i - 10), min(len(words), i + 14)
            hits.append({"t": words[i]["s"], "i": i, "context": " ".join(w["t"] for w in words[a:b]),
                         "lead": a > 0})
            if len(hits) >= 30:
                break
        if hits:
            results.append({"id": j["id"], "title": j["meta"].get("title"), "count": len(rx.findall(full)), "hits": hits})
    return {"results": results}


@app.get("/api/settings")
def get_settings():
    from .engines import backend
    # which options apply here: the Thorough AI model and Safari logins exist on Apple-chip Macs only
    return {**store.settings(), "device": "mac" if backend() == "mlx" else "pc"}


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    if "vocab" in body and isinstance(body["vocab"], str):
        body["vocab"] = [v.strip() for v in re.split(r"[,\n]", body["vocab"]) if v.strip()]
    return store.save_settings(body)


@app.get("/api/health")
def health():
    return {"ok": True, "busy": runner.current}


@app.get("/api/setup")
def setup_status():
    from . import setup
    ready = setup.is_ready()
    if not ready:
        setup.start()
    return {**setup.status(), "ready": ready}


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
@app.get("/search")
@app.get("/settings")
@app.get("/t/{jid}")
def index(jid: str | None = None):
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ housekeeping
def _ytdlp_daily():
    """Keep yt-dlp fresh so YouTube/Twitch changes don't break downloads."""
    from .fetch import update_ytdlp
    s = store.settings()
    if time.time() - s.get("last_ytdlp_update", 0) > 86400 and runner.current is None:
        if update_ytdlp(lambda m: print(m, flush=True)):
            store.save_settings({"last_ytdlp_update": time.time()})


def _idle_watch(server):
    while IDLE_MINUTES > 0:
        time.sleep(30)
        if time.time() - _last_request > IDLE_MINUTES * 60 and not runner.busy():
            print("idle, sleeping until the next visit", flush=True)
            server.should_exit = True
            return


def serve(sockets=None, port=5201, host=None):
    if host is None:  # only this computer, unless the user allowed their home network
        host = "0.0.0.0" if store.settings().get("allow_lan") else "127.0.0.1"
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    os.environ["SKRIFT_PORT"] = str(port)  # transcription workers link their "done" notification here
    runner.start()
    threading.Thread(target=_ytdlp_daily, daemon=True).start()
    threading.Thread(target=_idle_watch, args=(server,), daemon=True).start()
    server.run(sockets=sockets)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("SKRIFT_PORT", 5201)))
    ap.add_argument("--fds", default=os.environ.get("SKRIFT_FDS", ""))
    a = ap.parse_args()
    socks = [socket.socket(fileno=int(fd)) for fd in a.fds.split(",") if fd] or None
    serve(socks, a.port)
