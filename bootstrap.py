"""The first thing the Skrift icon runs (through the bundled `uv`, which also brings Python itself).
Only Python's standard library is used here, so it works before anything is installed.

First launch : shows a "Setting up Skrift" page in the browser while it installs Skrift's packages into the
               user's own data folder, then starts the app (the page switches over by itself).
Later        : starts the app straight away. If Skrift is already running, it just opens it.
"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP = Path(__file__).resolve().parent
PORT = int(os.environ.get("SKRIFT_PORT", "5200"))
URL = f"http://localhost:{PORT}"


def data_dir() -> Path:
    if os.environ.get("SKRIFT_DATA"):
        return Path(os.environ["SKRIFT_DATA"])
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Skrift"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Skrift"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "skrift"


DATA = data_dir()
ENV = DATA / "env"
PY = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MARK = ENV / ".skrift-installed"
UV = os.environ.get("SKRIFT_UV") or str(next((p for p in [APP.parent / "bin" / ("uv.exe" if os.name == "nt" else "uv")] if p.exists()), "uv"))
LOG = DATA / "setup.log"


def deps_hash() -> str:
    h = hashlib.sha256()
    for f in ("pyproject.toml", "uv.lock"):
        p = APP / f
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()


def installed() -> bool:
    return PY.exists() and MARK.exists() and MARK.read_text() == deps_hash()


def running() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


STATE = {"lines": [], "done": False, "error": None}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Setting up Skrift</title><style>
:root{color-scheme:dark}body{margin:0;background:#0a1120;color:#eef3fb;font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;
display:grid;place-items:center;min-height:100vh}main{width:min(560px,calc(100vw - 32px))}
@font-face{font-family:'Young Serif';src:url(/font.woff2) format('woff2')}h1{font:400 27px/1.2 'Young Serif',Georgia,serif;margin:0 0 6px}p{color:#a8b5c9}.bar{height:6px;border-radius:6px;background:#ffffff14;overflow:hidden;margin:18px 0}
.bar i{display:block;height:100%;width:35%;background:linear-gradient(100deg,#8fd6ff,#3f8cff,#2c58d8);
animation:slide 1.4s ease-in-out infinite;border-radius:6px}@keyframes slide{0%{margin-left:-35%}100%{margin-left:100%}}
pre{background:#00000055;border:1px solid #ffffff17;border-radius:12px;padding:12px;font-size:12px;color:#a8b5c9;
white-space:pre-wrap;max-height:220px;overflow:auto}.err{color:#ff8a96}</style></head><body><main>
<img src="/icon.svg" width="64" height="64" alt="" style="display:block;margin-bottom:14px"><h1>Setting up Skrift</h1><p id="msg">First time only: installing the transcription engines on this computer.
This usually takes a few minutes. Nothing is sent anywhere; everything stays on your computer.</p>
<div class="bar"><i></i></div><pre id="log"></pre></main><script>
async function tick(){try{const s=await (await fetch('/status')).json();document.getElementById('log').textContent=s.lines.join('\\n');
if(s.error){document.getElementById('msg').innerHTML='<span class="err">'+s.error+'</span>';return}
if(s.done){document.getElementById('msg').textContent='Done! Opening Skrift…';setTimeout(()=>location.reload(),2500);return}}
catch(e){setTimeout(()=>location.reload(),2500);return}setTimeout(tick,1000)}tick()</script></body></html>"""


class Setup(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/icon.svg":
            body, kind = (APP / "web" / "icon.svg").read_bytes(), "image/svg+xml"
        elif self.path == "/font.woff2":
            body, kind = (APP / "web" / "fonts" / "young-serif-latin.woff2").read_bytes(), "font/woff2"
        elif self.path.startswith("/status"):
            body, kind = json.dumps(STATE).encode(), "application/json"
        else:
            body, kind = PAGE.encode(), "text/html"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def install() -> bool:
    DATA.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(ENV), "UV_PYTHON_PREFERENCE": "only-managed"}
    cmd = [UV, "sync", "--project", str(APP), "--frozen", "--python", "3.12", "--no-progress"]
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.ctime()} {' '.join(cmd)}\n")
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            line = line.rstrip()
            log.write(line + "\n")
            if line:
                STATE["lines"] = (STATE["lines"] + [line])[-14:]
        ok = p.wait() == 0
    if ok:
        MARK.write_text(deps_hash())
    else:
        STATE["error"] = f"Setup didn't finish. Check your internet connection and open Skrift again. (Details: {LOG})"
    return ok


def start_app(open_browser: bool):
    env = {**os.environ, "SKRIFT_DATA": str(DATA)}
    if not open_browser:
        env["SKRIFT_NO_BROWSER"] = "1"
    if os.environ.get("SKRIFT_UV") is None and Path(UV).exists():
        env["SKRIFT_UV"] = UV
    code = subprocess.call([str(PY), "-m", "skrift.app"], cwd=str(APP), env=env)
    sys.exit(code)


def main():
    if running():
        if not os.environ.get("SKRIFT_NO_BROWSER"):
            webbrowser.open(URL)
        return
    if installed():
        return start_app(open_browser=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Setup)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not os.environ.get("SKRIFT_NO_BROWSER"):
        threading.Timer(0.8, webbrowser.open, [URL]).start()
    ok = install()
    STATE["done"] = ok
    if not ok:
        time.sleep(600)  # leave the error on screen for a while
        return
    time.sleep(1.5)  # let the page see "done"
    server.shutdown()
    server.server_close()
    start_app(open_browser=False)  # the setup page reloads into the app by itself


if __name__ == "__main__":
    main()
