"""Speech engines for Windows, Linux and Intel Macs (the same models as the Apple-chip path, different runtimes):

primary : NVIDIA Parakeet TDT 0.6B v2 (int8 ONNX) through sherpa-onnx; on a plain processor it does ~30x real time
second  : OpenAI Whisper large-v3-turbo through faster-whisper (CTranslate2); uses an NVIDIA GPU when present

Both return the same timed-word lists as engines.py, so the cross-check, vote and editor don't care which ran.
"""
import os

import numpy as np

SR = 16000
# full precision: the int8 version dropped whole sentences (4.5% errors vs 3.9% on the Nintendo Direct benchmark)
PARAKEET = os.environ.get("SKRIFT_PARAKEET_ONNX", "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2")
WHISPER = {"turbo": "large-v3-turbo", "large-v3": "large-v3"}  # faster-whisper names (Systran / mobiuslabs repos)
WHISPER_REPOS = {"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "large-v3": "Systran/faster-whisper-large-v3"}


def model_dir(repo: str) -> str:
    """Download a model as plain files (ONNX refuses weights that live behind the Hugging Face cache's symlinks)."""
    from huggingface_hub import snapshot_download
    from .store import MODELS
    return snapshot_download(repo, local_dir=str(MODELS / repo.split("/")[-1]))


def threads() -> int:
    """All cores (up to 8): transcription runs at low priority, so it only gets what nothing else needs.
    SKRIFT_THREADS overrides it."""
    if os.environ.get("SKRIFT_THREADS", "").isdigit():
        return max(1, int(os.environ["SKRIFT_THREADS"]))
    return max(1, min(8, os.cpu_count() or 4))


def gpu() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - no CUDA runtime at all
        return False


def _load_whisper(name: str):
    """An NVIDIA card is only used if it really works: it also needs NVIDIA's CUDA 12 + cuDNN 9 libraries, which
    most PCs don't have. Try it once on a second of silence; anything missing means the processor (works everywhere)."""
    from faster_whisper import WhisperModel
    if gpu():
        try:
            m = WhisperModel(name, device="cuda", compute_type="float16")
            list(m.transcribe(np.zeros(SR, dtype=np.float32), language="en", beam_size=1)[0])
            return m
        except Exception:  # noqa: BLE001
            import gc
            gc.collect()
    return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads())


def _pieces(pcm, spans, chunk_s):
    """Cut spans into <= chunk_s pieces at quiet moments (no word gets split)."""
    from .engines import _quiet_cut
    chunk = int(chunk_s * SR)
    out = []
    for a, b in spans:
        while b - a > chunk + 10 * SR:
            cut = _quiet_cut(pcm, a + chunk, search=min(20 * SR, chunk // 3))
            out.append((a, cut))
            a = cut
        out.append((a, b))
    return out


_parakeet = None
_whisper = {}


def release(keep: str | None = None):
    """Keep at most one speech model in memory: Parakeet + two Whispers at once don't fit in 8 GB, and loading
    one takes seconds next to the minutes each pass runs."""
    global _parakeet
    if keep != "parakeet":
        _parakeet = None
    for k in [k for k in _whisper if k != keep]:
        del _whisper[k]
    import gc
    gc.collect()


def run_parakeet(pcm_path: str, progress=lambda f: None) -> list[dict]:
    import glob
    from types import SimpleNamespace as NS

    import sherpa_onnx
    from huggingface_hub import snapshot_download

    from .engines import load_pcm
    from .words import parakeet_words

    global _parakeet
    if _parakeet is None:
        release("parakeet")
        d = PARAKEET if os.path.isdir(PARAKEET) else model_dir(PARAKEET)
        f = lambda pat: glob.glob(os.path.join(d, pat))[0]  # noqa: E731
        _parakeet = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=f("encoder*.onnx"), decoder=f("decoder*.onnx"), joiner=f("joiner*.onnx"), tokens=f("tokens.txt"),
            model_type="nemo_transducer", num_threads=threads(), decoding_method="greedy_search")
    pcm = load_pcm(pcm_path)
    # Overlapping 2-minute windows (like the Apple-chip path): Parakeet sometimes skips a sentence depending
    # on where a window starts, and seeing it twice with different context fixes most of that.
    win, hop = 120 * SR, 105 * SR
    starts = list(range(0, max(len(pcm) - 15 * SR, 1), hop))
    toks = []
    for n, a in enumerate(starts):
        b = min(len(pcm), a + win)
        s = _parakeet.create_stream()
        s.accept_waveform(SR, pcm[a:b].astype(np.float32) / 32768.0)
        _parakeet.decode_stream(s)
        r = s.result
        off = a / SR
        lps = list(r.ys_log_probs) if r.ys_log_probs else [0.0] * len(r.tokens)
        durs = list(r.durations) if r.durations else [0.08] * len(r.tokens)
        cur = [NS(text=t, start=off + ts, end=off + ts + max(du, 0.04), confidence=float(np.exp(lp)))
               for t, ts, du, lp in zip(r.tokens, r.timestamps, durs, lps)]
        toks = _merge(toks, cur, overlap_start=off, overlap_end=(off + win / SR) if n + 1 < len(starts) else None)
        progress((n + 1) / len(starts))
    return parakeet_words(toks)


def _merge(prev, cur, overlap_start, overlap_end):
    """Join two decodes of overlapping windows: inside the overlap, keep whichever window heard MORE there
    (a skipped sentence is the failure we're guarding against), cutting at a word boundary."""
    if not prev:
        return cur
    ov_end = prev[-1].end
    a_ov = [t for t in prev if t.start >= overlap_start]
    b_ov = [t for t in cur if t.start < ov_end]
    keep_prev = len(a_ov) >= len(b_ov)
    if keep_prev:  # previous window wins the overlap: take cur only after it, starting at a word
        rest = [t for t in cur if t.start > ov_end]
        while rest and not rest[0].text.startswith(" "):
            rest.pop(0)
        return prev + rest
    head = [t for t in prev if t.start < overlap_start]
    while head and cur and head[-1].start >= cur[0].start:
        head.pop()
    tail = [t for t in cur if not head or t.start > head[-1].start]
    while tail and not tail[0].text.startswith(" ") and len(tail) < len(cur):
        tail.pop(0)
    return head + tail


def run_whisper(pcm_path: str, model: str = "turbo", language: str | None = "en",
                vocab: list[str] | None = None, progress=lambda f: None,
                regions: list[tuple[float, float]] | None = None, careful: bool = False) -> tuple[list[dict], str]:
    import ctranslate2

    from .engines import load_pcm

    name = WHISPER.get(model, WHISPER["turbo"])
    if name not in _whisper:
        release(name)
        _whisper[name] = _load_whisper(name)
    m = _whisper[name]
    pcm = load_pcm(pcm_path)
    total = len(pcm)
    spans = [(0, total)] if regions is None else \
        [(int(a * SR), min(total, int(b * SR))) for a, b in regions if b - a > 0.3]
    pieces = _pieces(pcm, spans, 600)
    todo = sum(b - a for a, b in pieces) or 1
    done, words, detected = 0, [], language
    hot = ", ".join(vocab) if vocab else None
    for a, b in pieces:
        ctranslate2.set_random_seed(0)  # temperature fallback samples randomly; a fixed seed makes redos identical
        segs, info = m.transcribe(
            pcm[a:b].astype(np.float32) / 32768.0, language=detected, word_timestamps=True,
            condition_on_previous_text=False, hotwords=hot, vad_filter=False, beam_size=5,
            hallucination_silence_threshold=None if careful else 2.0, no_speech_threshold=0.6,
            compression_ratio_threshold=2.4, log_prob_threshold=-1.0,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8] if careful else [0.0, 0.3, 0.6])
        detected = detected or info.language
        off = a / SR
        for seg in segs:  # a generator: progress follows the decoding
            for w in seg.words or []:
                t = w.word.strip()
                if t:
                    words.append({"t": t, "s": round(w.start + off, 2), "e": round(w.end + off, 2),
                                  "c": round(w.probability, 3)})
            progress((done + min(seg.end * SR, b - a)) / todo)
        done += b - a
    progress(1.0)
    return words, detected or "en"


def model_repos(second: str = "turbo") -> list[str]:
    return [PARAKEET, WHISPER_REPOS[WHISPER.get(second, "large-v3-turbo")]]
