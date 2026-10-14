"""The AI meaning check on Windows, Linux and Intel Macs: llama.cpp's official prebuilt `llama-server`
running Google's Gemma 4 E4B, on this computer only (bound to 127.0.0.1, started and stopped by Skrift).

The Vulkan builds use NVIDIA, AMD or Intel graphics when there are any; if that build can't start,
Skrift falls back to the plain-processor build.
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

TAG = "b11551"  # pinned so every install behaves the same; bump deliberately
RELEASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/"
GGUF = ("google/gemma-4-E4B-it-qat-q4_0-gguf", "gemma-4-E4B_q4_0-it.gguf")


def _assets() -> list[str]:
    """Builds to try for this computer, best first."""
    system, mach = platform.system(), platform.machine().lower()
    x64 = mach in ("x86_64", "amd64")
    if system == "Windows":
        return [f"llama-{TAG}-bin-win-vulkan-x64.zip", f"llama-{TAG}-bin-win-cpu-x64.zip"] if x64 else \
               [f"llama-{TAG}-bin-win-cpu-arm64.zip"]
    if system == "Linux":
        return [f"llama-{TAG}-bin-ubuntu-vulkan-x64.tar.gz", f"llama-{TAG}-bin-ubuntu-x64.tar.gz"] if x64 else \
               [f"llama-{TAG}-bin-ubuntu-vulkan-arm64.tar.gz", f"llama-{TAG}-bin-ubuntu-arm64.tar.gz"]
    return [f"llama-{TAG}-bin-macos-{'x64' if x64 else 'arm64'}.tar.gz"]


def _install(asset: str, progress=lambda f: None) -> Path:
    """Download and unpack one llama.cpp build; returns the llama-server executable."""
    from .store import MODELS
    home = MODELS / "llama.cpp" / asset.rsplit(".", 2 if asset.endswith(".tar.gz") else 1)[0]
    exe_name = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    found = list(home.rglob(exe_name)) if home.exists() else []
    if found:
        return found[0]
    home.mkdir(parents=True, exist_ok=True)
    archive = home / asset
    _download(RELEASE + asset, archive, progress)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(home)
    else:
        with tarfile.open(archive) as t:
            t.extractall(home, filter="data")
    archive.unlink()
    exe = next(home.rglob(exe_name))
    exe.chmod(0o755)
    return exe


def _download(url: str, dest: Path, progress=lambda f: None):
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Skrift"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            if total:
                progress(got / total)
    os.replace(tmp, dest)


def model_path() -> str:
    from huggingface_hub import hf_hub_download
    from .store import MODELS
    return hf_hub_download(GGUF[0], GGUF[1], local_dir=str(MODELS / "gemma-4-E4B-gguf"))


def _env() -> dict:
    """llama.cpp's Linux builds need the GNU OpenMP library (libgomp). Minimal systems may not have it, but the
    Whisper runtime (ctranslate2) ships its own copy, so point llama-server at that one."""
    env = dict(os.environ)
    if platform.system() != "Linux":
        return env
    import ctypes.util
    if ctypes.util.find_library("gomp"):
        return env
    import glob
    import sysconfig
    from .store import MODELS
    found = sorted(glob.glob(os.path.join(sysconfig.get_paths()["purelib"], "*.libs", "libgomp*.so*")))
    if found:
        lib = MODELS / "llama.cpp" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        link = lib / "libgomp.so.1"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(found[0])
        env["LD_LIBRARY_PATH"] = os.pathsep.join(p for p in (str(lib), env.get("LD_LIBRARY_PATH")) if p)
    return env


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """One llama-server for the duration of a job's meaning check."""

    def __init__(self):
        self.proc, self.port = None, None

    def start(self):
        model = model_path()
        from .portable import threads as cores
        threads = str(cores())
        last_err, env = "", _env()
        for asset in _assets():
            exe = _install(asset)
            self.port = _free_port()
            log_path = Path(model).parent / "llama-server.log"
            log = open(log_path, "ab")
            self.proc = subprocess.Popen(
                [str(exe), "-m", model, "--host", "127.0.0.1", "--port", str(self.port), "-c", "4096",
                 "-ngl", "99", "-t", threads, "--no-webui"],
                stdout=log, stderr=log, cwd=str(exe.parent), env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.time() + 180
            while time.time() < deadline and self.proc.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                        if r.status == 200:
                            return self
                except Exception:  # noqa: BLE001 - still loading
                    time.sleep(1)
            self.stop()
            log.close()
            tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] or [""]
            last_err = f"{asset} did not start: {tail[0][-200:]}"
        raise RuntimeError(f"The AI model couldn't start ({last_err}).")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def chat(self, messages: list[dict], max_tokens: int = 110) -> dict:
        body = json.dumps({"messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": 0,
                           "logprobs": True, "top_logprobs": 10,
                           "chat_template_kwargs": {"enable_thinking": False}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())


_server = None


def server() -> Server:
    global _server
    if _server is None:
        _server = Server().start()
    return _server


def shutdown():
    global _server
    if _server:
        _server.stop()
        _server = None


def letter_choice(messages: list[dict], n: int, letters: str = "ABCD") -> list[float]:
    """Ask, let the model reason briefly, and read the probabilities of the answer letters."""
    import math
    import re
    out = server().chat(messages)
    choice = out["choices"][0]
    text = choice["message"].get("content") or ""
    toks = (choice.get("logprobs") or {}).get("content") or []
    acc = ""
    for t in toks:
        if acc.rstrip().endswith("Answer:"):
            cand = {}
            for alt in t.get("top_logprobs", []):
                k = alt["token"].strip()
                if k in letters[:n] and k not in cand:
                    cand[k] = alt["logprob"]
            if cand:
                m = max(cand.values())
                ex = [math.exp(cand[letters[i]] - m) if letters[i] in cand else 0.0 for i in range(n)]
                return [e / sum(ex) for e in ex]
        acc += t["token"]
    m = re.search(r"Answer:\s*([A-D])", text)  # no logprobs: trust the written answer
    probs = [0.0] * n
    probs[letters.index(m.group(1)) if m and letters.index(m.group(1)) < n else 0] = 1.0
    return probs


def remove_builds():
    """Delete downloaded llama.cpp builds (e.g. to force a fresh download)."""
    from .store import MODELS
    shutil.rmtree(MODELS / "llama.cpp", ignore_errors=True)
