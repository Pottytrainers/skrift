"""First-run setup: download the engines and models this computer needs, with progress for the home page.
On later runs everything is already there and this finishes instantly."""
import os
import threading
import time
from pathlib import Path

_state = {"running": False, "ready": False, "items": [], "error": None}
_lock = threading.Lock()


def _hf_size(repo: str, filename: str | None = None) -> int:
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo, files_metadata=True)
        return sum((s.size or 0) for s in info.siblings if filename is None or s.rfilename == filename)
    except Exception:  # noqa: BLE001 - offline or rate-limited: progress just won't show a total
        return 0


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _hub_cache() -> Path:
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def plan() -> list[dict]:
    """What this computer needs: (label, how to fetch, where it lands, size)."""
    from . import store
    from .engines import backend, required_repos
    s = store.settings()
    items = []
    if backend() == "mlx":
        from .meaning import MODELS as LLMS
        repos = required_repos(s.get("second_model", "turbo"))
        if s.get("meaning_check", True):
            repos.append(LLMS.get(s.get("meaning_model", "standard"), LLMS["standard"]))
        for r in repos:
            items.append({"label": r.split("/")[-1], "kind": "hub", "repo": r,
                          "where": _hub_cache() / f"models--{r.replace('/', '--')}"})
    else:
        from . import llm_server, portable
        items.append({"label": "Parakeet speech model", "kind": "local", "repo": portable.PARAKEET,
                      "where": store.MODELS / portable.PARAKEET.split("/")[-1]})
        w = portable.WHISPER_REPOS[portable.WHISPER.get(s.get("second_model", "turbo"), "large-v3-turbo")]
        items.append({"label": "Whisper speech model", "kind": "hub", "repo": w,
                      "where": _hub_cache() / f"models--{w.replace('/', '--')}"})
        if s.get("meaning_check", True):
            items.append({"label": "AI model (Gemma 4)", "kind": "gguf", "repo": llm_server.GGUF[0],
                          "file": llm_server.GGUF[1], "where": store.MODELS / "gemma-4-E4B-gguf"})
            items.append({"label": "AI runtime (llama.cpp)", "kind": "llama", "repo": None,
                          "where": store.MODELS / "llama.cpp", "size": 40 << 20})
    items.append({"label": "YouTube helper (Deno)", "kind": "deno", "repo": None, "size": 40 << 20,
                  "where": store.DATA / "bin"})
    return items


def _ready(item) -> bool:
    from . import store
    if item["kind"] == "hub":
        return any((item["where"] / "snapshots").glob("*/*")) and not any(item["where"].rglob("*.incomplete"))
    if item["kind"] == "local":
        return (item["where"] / "tokens.txt").exists() and not any(item["where"].rglob("*.incomplete"))
    if item["kind"] == "gguf":
        return any(item["where"].glob("*.gguf"))
    if item["kind"] == "llama":
        return any(item["where"].rglob("llama-server*"))
    if item["kind"] == "deno":
        import shutil
        return bool(shutil.which("deno") or shutil.which("node") or (store.DATA / "bin" / "deno").exists()
                    or (store.DATA / "bin" / "deno.exe").exists())
    return True


def _fetch(item):
    from huggingface_hub import snapshot_download
    if item["kind"] == "hub":
        snapshot_download(item["repo"])
    elif item["kind"] == "local":
        from .portable import model_dir
        model_dir(item["repo"])
    elif item["kind"] == "gguf":
        from .llm_server import model_path
        model_path()
    elif item["kind"] == "llama":
        from .llm_server import _assets, _install
        _install(_assets()[0])
    elif item["kind"] == "deno":
        from .tools import js_runtime
        js_runtime()


def status() -> dict:
    with _lock:
        out = {k: v for k, v in _state.items() if k != "items"}
        out["items"] = [{k: v for k, v in i.items() if k in ("label", "done", "total", "ready")} for i in _state["items"]]
    return out


def is_ready() -> bool:
    if _state["ready"]:
        return True
    try:
        if all(_ready(i) for i in plan()):
            _state["ready"] = True
    except Exception:  # noqa: BLE001
        return False
    return _state["ready"]


def start():
    """Download whatever is missing, in the background. Safe to call repeatedly."""
    with _lock:
        if _state["running"] or _state["ready"]:
            return
        _state.update(running=True, error=None)
    threading.Thread(target=_run, daemon=True).start()


def _run():
    try:
        items = plan()
        for i in items:
            i["ready"] = _ready(i)
            i["total"] = 0 if i["ready"] else (i.get("size") or (_hf_size(i["repo"], i.get("file")) if i["repo"] else 0))
            i["done"] = i["total"] if i["ready"] else 0
        with _lock:
            _state["items"] = items
        for i in items:
            if i["ready"]:
                continue
            base = _dir_size(i["where"])
            t = threading.Thread(target=_fetch, args=(i,), daemon=True)
            t.start()
            while t.is_alive():
                i["done"] = max(0, _dir_size(i["where"]) - base)
                time.sleep(1)
            i["ready"] = _ready(i)
            if not i["ready"]:
                raise RuntimeError(f"Couldn't download {i['label']}. Check your internet connection and reopen Skrift.")
            i["done"] = i["total"] or i["done"]
        _state["ready"] = True
    except Exception as e:  # noqa: BLE001 - shown on the home page
        _state["error"] = str(e)
    finally:
        _state["running"] = False
