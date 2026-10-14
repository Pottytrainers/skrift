"""Speech engines, both running on the Apple GPU through MLX.

primary  : NVIDIA Parakeet TDT 0.6B v2  - lowest English error rate we measured, never invents text
second   : OpenAI Whisper large-v3-turbo - independent model used to cross-check every word
fallback : Whisper is the primary for non-English audio (Parakeet v2 is English-only)
"""
import os
import time

import numpy as np

SR = 16000
PARAKEET = "mlx-community/parakeet-tdt-0.6b-v2"
WHISPER = {"turbo": "mlx-community/whisper-large-v3-turbo",
           "large-v3": "mlx-community/whisper-large-v3-mlx"}

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def go_offline_if_cached(repos) -> bool:
    """Once the models are downloaded, never touch the network for them again (works offline,
    no Hugging Face rate-limit warnings). Must run before huggingface_hub is imported."""
    from pathlib import Path
    from .store import MODELS
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"

    def cached(r):
        if (MODELS / r.split("/")[-1] / "tokens.txt").exists():  # downloaded as plain files (ONNX Parakeet)
            return True
        return any((hub / f"models--{r.replace('/', '--')}" / "snapshots").glob("*/config.json"))
    ok = all(cached(r) for r in repos)
    if ok:
        os.environ["HF_HUB_OFFLINE"] = "1"
    return ok


def load_pcm(path: str) -> np.ndarray:
    """16 kHz mono signed 16-bit raw PCM, memory-mapped so long streams don't fill RAM."""
    return np.memmap(path, dtype=np.int16, mode="r")


def _mlx_parakeet(wav_path: str, progress=lambda f: None) -> list[dict]:
    from parakeet_mlx import from_pretrained
    from .words import parakeet_words

    model = from_pretrained(PARAKEET)
    result = model.transcribe(wav_path, chunk_duration=120, overlap_duration=15,
                              chunk_callback=lambda cur, full: progress(cur / max(full, 1)))
    progress(1.0)
    return parakeet_words(result.tokens)


def _quiet_cut(pcm: np.ndarray, near: int, search: int = 45 * SR) -> int:
    """Sample index of the quietest 0.4 s within `search` samples before `near`."""
    lo = max(0, near - search)
    seg = np.abs(pcm[lo:near].astype(np.float32))
    win = int(0.4 * SR)
    if len(seg) < win * 2:
        return near
    hop = win // 4
    energy = np.array([seg[i:i + win].mean() for i in range(0, len(seg) - win, hop)])
    return lo + int(np.argmin(energy)) * hop + win // 2


def _install_hooks(T, vocab_tokens, on_frames):
    """mlx_whisper only uses `initial_prompt` for the first 30 s window; prepend the
    vocabulary to every window instead, and capture its progress bar."""
    if not getattr(T, "_scribe_patched", False):
        T._orig_DecodingOptions = T.DecodingOptions
        T._orig_tqdm = T.tqdm.tqdm
        T._scribe_patched = True
    orig = T._orig_DecodingOptions

    def options(**kw):
        if vocab_tokens:
            kw["prompt"] = list(vocab_tokens) + list(kw.get("prompt") or [])
        return orig(**kw)

    class Bar:
        def __init__(self, total=0, **_):
            self.n = 0

        def update(self, k):
            self.n += k
            on_frames(self.n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    T.DecodingOptions = options
    T.tqdm.tqdm = Bar


def speech_regions(words: list[dict], pad: float = 1.0, join: float = 4.0) -> list[tuple[float, float]]:
    """Where the first engine heard speech, padded and merged. Whisper only listens there:
    on music, ads and silence it is slow (it retries up to 6x) and invents phrases."""
    out = []
    for w in words:
        a, b = max(0.0, w["s"] - pad), w["e"] + pad
        if out and a - out[-1][1] <= join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def tiebreak_regions(reviews: list[dict], pad: float = 3.0, join: float = 3.0) -> list[tuple[float, float]]:
    """Where a third opinion can change anything: the spots the first two engines disagree on, with a few
    seconds of context either side (measured: same accuracy as tie-breaking the whole file)."""
    out = []
    for s, e in sorted((r["s"], r["e"]) for r in reviews if r["kind"] in ("differ", "missing", "single")):
        a, b = max(0.0, s - pad), e + pad
        if out and a - out[-1][1] <= join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _mlx_whisper(pcm_path: str, model: str = "turbo", language: str | None = "en",
                vocab: list[str] | None = None, progress=lambda f: None,
                regions: list[tuple[float, float]] | None = None, careful: bool = False) -> tuple[list[dict], str]:
    import importlib

    import mlx_whisper
    from mlx_whisper.tokenizer import get_tokenizer
    # the package re-exports a *function* named `transcribe`, so fetch the module itself
    T = importlib.import_module("mlx_whisper.transcribe")
    from .words import whisper_words

    repo = WHISPER.get(model, WHISPER["turbo"])
    pcm = load_pcm(pcm_path)
    total = len(pcm)
    vocab_tokens = []
    if vocab:
        prompt = "Names and terms: " + ", ".join(vocab) + "."
        tok = get_tokenizer(multilingual=True, language=language or "en", task="transcribe")
        vocab_tokens = tok.encode(" " + prompt)[:150]

    # What to transcribe: the speech regions (or everything), cut into pieces of <= ~10 minutes at
    # quiet moments so memory stays flat on multi-hour streams. (mlx_whisper's own clip_timestamps
    # doesn't skip the gaps between clips, so each piece is passed separately.)
    chunk = 10 * 60 * SR
    spans = [(0, total)] if regions is None else \
        [(int(a * SR), min(total, int(b * SR))) for a, b in regions if b - a > 0.3]
    pieces = []
    for a, b in spans:
        while b - a > chunk + 60 * SR:
            cut = _quiet_cut(pcm, a + chunk)
            pieces.append((a, cut))
            a = cut
        pieces.append((a, b))
    todo = sum(b - a for a, b in pieces) or 1
    done = 0
    words, detected = [], language
    import mlx.core as mx
    for a, b in pieces:
        mx.random.seed(0)  # Whisper samples randomly when unsure; a fixed seed makes redos give the same result
        audio = pcm[a:b].astype(np.float32) / 32768.0
        offset = a / SR
        _install_hooks(T, vocab_tokens, lambda frames, d=done, n=b - a: progress((d + min(frames * 160, n)) / todo))
        r = mlx_whisper.transcribe(
            audio, path_or_hf_repo=repo, verbose=False, language=detected,
            word_timestamps=True, condition_on_previous_text=False,
            # careful = second-chance pass over stretches Whisper skipped: no silence-skipping heuristic
            hallucination_silence_threshold=None if careful else 2.0, no_speech_threshold=0.6,
            compression_ratio_threshold=2.4, logprob_threshold=-1.0,
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8) if careful else (0.0, 0.3, 0.6),
        )
        detected = detected or r.get("language")
        for w in whisper_words(r["segments"]):
            w["s"] = round(w["s"] + offset, 2)
            w["e"] = round(w["e"] + offset, 2)
            words.append(w)
        done += b - a
    progress(1.0)
    return words, detected or "en"


# ---------------------------------------------------------------- which engines run on this computer

def backend() -> str:
    """"mlx" on Apple-chip Macs (fastest there), "portable" everywhere else (Windows, Linux, Intel Macs).
    SKRIFT_BACKEND=portable forces the cross-platform engines (used to test them on a Mac)."""
    import platform
    import sys
    forced = os.environ.get("SKRIFT_BACKEND")
    if forced in ("mlx", "portable"):
        return forced
    return "mlx" if sys.platform == "darwin" and platform.machine() == "arm64" else "portable"


def run_parakeet(wav_path: str, progress=lambda f: None) -> list[dict]:
    if backend() == "mlx":
        return _mlx_parakeet(wav_path, progress)
    from . import portable
    pcm = os.path.splitext(wav_path)[0] + ".pcm"
    return portable.run_parakeet(pcm if os.path.exists(pcm) else wav_path, progress)


def run_whisper(pcm_path: str, model: str = "turbo", language: str | None = "en", vocab: list[str] | None = None,
                progress=lambda f: None, regions=None, careful: bool = False) -> tuple[list[dict], str]:
    if backend() == "mlx":
        return _mlx_whisper(pcm_path, model, language, vocab, progress, regions=regions, careful=careful)
    from . import portable
    return portable.run_whisper(pcm_path, model, language, vocab, progress, regions=regions, careful=careful)


def required_repos(second: str = "turbo", english: bool = True, third: bool = False) -> list[str]:
    """Model downloads this computer needs for a job (third: the Whisper tie-breaker used when there are
    no YouTube captions)."""
    other = "large-v3" if second != "large-v3" else "turbo"
    if backend() == "mlx":
        if not english:
            return [WHISPER["large-v3"]]
        return [PARAKEET, WHISPER[second]] + ([WHISPER[other]] if third else [])
    from . import portable
    if not english:
        return [portable.WHISPER_REPOS["large-v3"]]
    return portable.model_repos(second) + ([portable.WHISPER_REPOS[portable.WHISPER[other]]] if third else [])


def warm(timer=time.time):
    """Download all models ahead of the first job (used by the installer)."""
    from huggingface_hub import snapshot_download
    from .meaning import LLM
    for repo in (required_repos() + ([LLM] if backend() == "mlx" else [])):
        t = timer()
        snapshot_download(repo)
        print(f"model ready: {repo} ({timer() - t:.0f}s)")
