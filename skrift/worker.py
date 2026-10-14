"""One transcription job, run as its own process:  python -m skrift.worker <job-id>

Every finished step leaves a file behind (audio.m4a, primary.json, second.json),
so a job that gets interrupted picks up where it stopped."""
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request

from . import store
from .fetch import FetchError, convert, download, probe, update_ytdlp, youtube_captions
from .store import job_dir, read_json, write_json

# Share of the progress bar each step gets (roughly how long each takes).
WEIGHTS = {
    "accurate": {"download": 0.05, "convert": 0.03, "primary": 0.18, "second": 0.60, "check": 0.03, "third": 0.01, "meaning": 0.10},
    "accurate3": {"download": 0.05, "convert": 0.03, "primary": 0.16, "second": 0.50, "check": 0.02, "third": 0.14, "meaning": 0.10},
    "fast": {"download": 0.20, "convert": 0.08, "primary": 0.68, "second": 0.0, "check": 0.04, "third": 0.0, "meaning": 0.0},
}
LABELS = {"download": "Downloading audio", "convert": "Preparing audio",
          "primary": "Transcribing (pass 1: Parakeet)", "second": "Cross-checking (pass 2: Whisper)",
          "third": "Tie-breaking (pass 3)", "check": "Comparing the passes word by word",
          "meaning": "Reading for meaning (AI check)"}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Job:
    def __init__(self, jid):
        self.id, self.dir = jid, job_dir(jid)
        self.job = store.load(jid)
        self.mode = self.job["options"].get("mode", "accurate")
        if self.job["options"].get("language", "en") != "en":
            self.mode = "fast"  # Parakeet is English-only, so other languages get one Whisper pass
        self.w = WEIGHTS[self.mode]
        self.t0 = time.time()
        self._last = 0.0

    def stage(self, key, status, frac=0.0, label=None):
        order = list(self.w)
        done = sum(self.w[k] for k in order[:order.index(key)])
        p = min(done + self.w[key] * frac, 0.999)
        now = time.time()
        if frac not in (0.0, 1.0) and now - self._last < 0.7:
            return
        self._last = now
        self.job.update(status=status, stage=label or LABELS[key], progress=round(p, 4))
        el = now - self.t0
        self.job["eta"] = round(el * (1 - p) / p) if p > 0.04 and el > 8 else None
        store.save(self.job)

    def fail(self, msg, kind="error"):
        self.job.update(status="error", error=msg, error_kind=kind, eta=None)
        store.save(self.job)
        log(f"[{self.id}] FAILED ({kind}): {msg}")

    # ------------------------------------------------------------------ steps
    def fetch(self, settings):
        src = self.job["source"]
        have = [p for p in self.dir.glob("source.*") if not p.name.endswith((".part", ".ytdl"))]
        if src["kind"] == "url":
            cookies = settings.get("cookies_browser") or None
            if not self.job["meta"].get("probed"):
                self.stage("download", "downloading", 0, "Looking up the video")
                meta = probe(src["url"], cookies)
                meta["probed"] = True
                self.job["meta"] = meta
                store.save(self.job)
                self._thumb(meta.get("thumbnail"))
            if not have:
                self.stage("download", "downloading", 0)
                return download(src["url"], self.dir, lambda f: self.stage("download", "downloading", f), cookies)
        if not have:
            raise FetchError("The uploaded file is missing.")
        return have[0]

    def _thumb(self, url):
        if not url:
            return
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                (self.dir / "thumb.jpg").write_bytes(r.read())
        except Exception as e:  # noqa: BLE001 - a thumbnail is optional
            log(f"thumbnail skipped: {e}")

    def run(self, retried=False):
        from .crosscheck import (apply_phrases, apply_vocab, crosscheck, low_confidence, merge_second_chance,
                                 names_break_ties, skipped_spans, vocab_terms)
        from .vote import mark_turns, vote, youtube_events, youtube_words
        from .engines import run_parakeet, run_whisper, speech_regions, tiebreak_regions
        from .subtitles import paragraphs

        settings = store.settings()
        opts = self.job["options"]
        log(f"[{self.id}] start mode={self.mode} {self.job['source']}")
        m4a, pcm, wav = self.dir / "audio.m4a", self.dir / "audio.pcm", self.dir / "audio.wav"
        try:
            if not (m4a.exists() and pcm.exists() and wav.exists()):
                # Redos must hear exactly the same audio as the first run: links keep their original
                # download, uploads reuse the player copy without re-compressing it.
                fresh = [p for p in self.dir.glob("source.*") if not p.name.endswith((".part", ".ytdl"))]
                original = sorted(self.dir.glob("original.*"))
                url = self.job["source"]["kind"] == "url"
                if fresh or (url and not original):
                    src = self.fetch(settings)
                elif original:
                    src = original[0]
                elif m4a.exists():
                    src = m4a
                else:
                    raise FetchError("The original audio is gone, so this one can't be redone.")
                self.stage("convert", "converting", 0)
                _, _, _, dur = convert(src, self.dir, lambda f: self.stage("convert", "converting", f),
                                       make_m4a=not m4a.exists() or src.name.startswith("source."))
                self.job["meta"]["duration"] = round(dur, 2)
                for p in self.dir.glob("source.*"):
                    if url:  # audio-only downloads are small: keep for identical redos
                        for old in self.dir.glob("original.*"):
                            old.unlink()
                        p.rename(self.dir / ("original" + p.suffix))
                    else:  # uploads can be multi-GB videos; the player copy is enough
                        p.unlink()
        except FetchError as e:
            if e.kind == "breakage" and not retried:
                self.stage("download", "downloading", 0, "Updating the downloader (YouTube/Twitch changed something)…")
                if update_ytdlp(log):
                    store.save_settings({"last_ytdlp_update": time.time()})
                    # run again in a fresh process so the updated downloader is the one that loads
                    sys.exit(subprocess.call([sys.executable, "-m", "skrift.worker", self.id, "--retried"]))
            hint = {"auth": " This video needs a login. Turn on “Use my browser login” in Settings and try again.",
                    "breakage": " The downloader was updated but still couldn't get it. Try again in a bit."}.get(e.kind, "")
            return self.fail(str(e) + hint, e.kind)

        meta = self.job["meta"]
        english = opts.get("language", "en") == "en"
        # third vote: YouTube's own captions when the video is on YouTube, otherwise (optionally) Whisper large-v3
        yt_caps = sorted(self.dir.glob("youtube*.json3"))
        if self.mode == "accurate" and english and not yt_caps and meta.get("site") == "Youtube":
            self.stage("convert", "converting", 1.0, "Getting YouTube's captions for a third vote")
            got = youtube_captions(self.job["source"]["url"], self.dir, settings.get("cookies_browser") or None)
            yt_caps = [got] if got else []
        third_whisper = self.mode == "accurate" and english and not yt_caps and settings.get("third_vote", True)
        if third_whisper:
            self.w = WEIGHTS["accurate3"]
        user_vocab = [v.strip() for v in settings.get("vocab", []) + opts.get("vocab", []) if v.strip()]
        auto = vocab_terms([meta.get("title", ""), meta.get("description", ""),
                            " ".join(meta.get("chapters", [])), " ".join(meta.get("tags", []))])
        vocab = {**auto, **vocab_terms(user_vocab)}
        prompt_terms = user_vocab + [v for k, v in auto.items() if v not in user_vocab]

        P = read_json(self.dir / "primary.json")
        if P is None:
            label = ("Transcribing (pass 1: Parakeet)" if self.mode == "accurate" else "Transcribing (Parakeet)") \
                if english else "Transcribing (Whisper large-v3)"
            self.stage("primary", "transcribing", 0, label)
            if english:
                P = run_parakeet(str(wav), lambda f: self.stage("primary", "transcribing", f, label))
                engine = "parakeet-tdt-0.6b-v2"
            else:
                P, lang = run_whisper(str(pcm), "large-v3", None, prompt_terms[:40],
                                      lambda f: self.stage("primary", "transcribing", f, "Transcribing (Whisper large-v3)"))
                self.job["stats"]["language"] = lang
                engine = "whisper-large-v3"
            self.job["stats"]["primary_engine"] = engine
            write_json(self.dir / "primary.json", P)

        reviews, vstats = [], None
        if self.mode == "accurate":
            model = settings.get("second_model", "turbo")
            W = read_json(self.dir / "second.json")
            if W is None:
                self.stage("second", "transcribing", 0)
                W, _ = run_whisper(str(pcm), model, "en", prompt_terms[:40],
                                   lambda f: self.stage("second", "transcribing", f * 0.97), regions=speech_regions(P))
                spans = skipped_spans(P, W)
                if spans:  # Whisper sometimes skips whole sentences; give those stretches a careful second try
                    self.stage("second", "transcribing", 0.97, f"Re-checking {len(spans)} skipped part{'s' if len(spans) != 1 else ''}")
                    W2, _ = run_whisper(str(pcm), model, "en", prompt_terms[:40], regions=spans, careful=True)
                    W = merge_second_chance(W, spans, W2)
                    self.job["stats"]["second_chance_spans"] = len(spans)
                self.job["stats"]["second_engine"] = f"whisper-{model}"
                write_json(self.dir / "second.json", W)
            self.stage("check", "checking", 0)
            reviews = crosscheck(P, W, user_vocab)
            Y = None
            if yt_caps:
                Y = youtube_words(yt_caps[0])
                self.job["stats"]["third_engine"] = "youtube-captions"
            elif third_whisper:
                other = "large-v3" if model != "large-v3" else "turbo"
                Y = read_json(self.dir / "third.json")
                if Y is None:
                    # the tie-breaker only listens where the first two engines disagree, with context either side:
                    # measured as accurate as tie-breaking the whole file, for a fraction of the work
                    spots = tiebreak_regions(reviews)
                    Y = []
                    if spots:
                        label = f"Tie-breaking (pass 3: Whisper {other})"
                        self.stage("third", "transcribing", 0, label)
                        Y, _ = run_whisper(str(pcm), other, "en", prompt_terms[:40],
                                           lambda f: self.stage("third", "transcribing", f, label), regions=spots)
                    write_json(self.dir / "third.json", Y)
                    self.job["stats"]["tiebreak_audio_s"] = round(sum(b - a for a, b in spots))
                self.job["stats"]["third_engine"] = f"whisper-{other}"
            if Y is not None:
                vstats = vote(P, W, reviews, Y)  # 2-of-3 agreement settles a disagreement by itself
                self.job["stats"]["vote"] = vstats
            reviews = [r for r in reviews if not r.get("weak")]
        elif not english:
            reviews = low_confidence(P)
        phrases = [v for v in user_vocab if " " in v or len(v) >= 6]
        fixed = names_break_ties(P, reviews, phrases, "YouTube" if yt_caps else "Whisper large-v3")
        fixed += apply_phrases(P, reviews, phrases)
        fixed += apply_vocab(P, reviews, vocab)

        if self.mode == "accurate" and english and settings.get("meaning_check", True):
            # free the speech models first: the language model needs the memory
            import gc
            import importlib
            from .engines import backend
            if backend() == "mlx":
                import mlx.core as mx
                importlib.import_module("mlx_whisper.transcribe").ModelHolder.model = None
                gc.collect()
                mx.clear_cache()
            else:
                from . import portable
                portable.release()
            from .meaning import MODELS, meaning_pass
            self.stage("meaning", "checking", 0)
            third_name = "YouTube" if yt_caps else ("Whisper large-v3" if third_whisper else None)
            try:
                self.job["stats"]["meaning"] = meaning_pass(
                    P, reviews, meta.get("title", ""), Y, lambda f: self.stage("meaning", "checking", f),
                    third_name=third_name, repo=MODELS.get(settings.get("meaning_model", "standard"), MODELS["standard"]))
            except Exception as e:  # noqa: BLE001 - the AI check is a bonus; never lose the transcript over it
                log(f"[{self.id}] meaning check skipped: {e}")
                self.job["stats"]["meaning_error"] = str(e)[:200]
            finally:
                from .engines import backend
                if backend() != "mlx":
                    from .llm_server import shutdown
                    shutdown()

        from .words import tidy
        tidy(P)
        for i in range(1, len(P)):  # safety net: timestamps must never run backwards
            if P[i]["s"] < P[i - 1]["s"]:
                P[i]["s"] = P[i - 1]["s"]
            if P[i]["e"] < P[i]["s"]:
                P[i]["e"] = round(P[i]["s"] + 0.05, 2)
        sounds = []
        if yt_caps and english:  # speaker changes and [laughter]/[music] tags come free with YouTube's captions
            turns, sounds = youtube_events(yt_caps[0])
            self.job["stats"]["speaker_turns"] = mark_turns(P, turns)
        for a, _ in paragraphs(P):
            P[a]["p"] = 1
        for r in reviews:
            for k in ("a", "b", "wi", "weak"):
                r.pop(k, None)
        open_items = sum(1 for r in reviews if not r["done"] and r["kind"] not in ("single", "voted"))
        confirmed = sum(1 for w in P if w.get("ok"))
        rare = _names(P)
        store.save_transcript(self.id, {"version": 1, "words": P, "reviews": reviews, "rare": rare, "sounds": sounds})
        self.job["stats"].update(words=len(P), reviews=open_items, spelling_fixes=fixed,
                                 confirmed=round(confirmed / max(len(P), 1), 4) if self.mode == "accurate" else None,
                                 seconds=round(time.time() - self.t0))
        self.job.update(status="done", stage="Done", progress=1.0, eta=None, finished=time.time())
        store.save(self.job)
        for p in (wav, pcm):
            p.unlink(missing_ok=True)
        log(f"[{self.id}] done: {len(P)} words, {open_items} to review, {time.time() - self.t0:.0f}s")
        if settings.get("notify", True):
            self._notify(meta.get("title", "Transcript"), open_items)

    def _notify(self, title, n):
        msg = f"{n} spot{'s' if n != 1 else ''} to double-check" if self.mode == "accurate" else "Transcript ready"
        t = title.replace('"', "'")[:80]
        try:
            if sys.platform == "darwin":
                subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Transcript ready" '
                                f'subtitle "{t}" sound name "Glass"'], capture_output=True)
            elif sys.platform.startswith("linux"):
                subprocess.run(["notify-send", "Transcript ready", f"{t}: {msg}"], capture_output=True)
            elif os.name == "nt":
                windows_toast(title[:120], msg, f"http://localhost:{os.environ.get('SKRIFT_PORT', '5200')}/t/{self.id}")
        except OSError:
            pass


# Windows' own notification system, reached through the PowerShell every Windows 10/11 PC has (Windows PowerShell
# 5.1: the newer pwsh can't load these Windows types). The texts travel in environment variables and are set as
# plain text, so a video title can never break the script. Clicking the notification opens the transcript.
_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('<toast activationType="protocol"><visual><binding template="ToastGeneric"><text/><text/><text/></binding></visual></toast>')
$t = $x.GetElementsByTagName('text')
$t.Item(0).InnerText = 'Transcript ready'
$t.Item(1).InnerText = $env:SKRIFT_TOAST_TITLE
$t.Item(2).InnerText = $env:SKRIFT_TOAST_TEXT
$x.DocumentElement.SetAttribute('launch', $env:SKRIFT_TOAST_URL)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def windows_toast(title: str, text: str, url: str) -> tuple[bool, str]:
    env = {**os.environ, "SKRIFT_TOAST_TITLE": title, "SKRIFT_TOAST_TEXT": text, "SKRIFT_TOAST_URL": url}
    import base64
    script = base64.b64encode(_TOAST.encode("utf-16-le")).decode()  # no quoting rules to trip over
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", script],
                       env=env, capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-400:]


def _names(P):
    """Words worth a spelling glance: names (capitalized mid-sentence) and very rare words."""
    from wordfreq import zipf_frequency
    from .words import base_key
    out = set()
    for i, w in enumerate(P):
        mid = i > 0 and not P[i - 1]["t"].endswith((".", "?", "!"))
        parts = [x for x in re.split(r"[-–—/]", w["t"]) if x]
        for n, part in enumerate(parts):  # "ham-fisted" is two ordinary words, not a name
            k = base_key(part)
            if len(k) < 3 or k.isdigit() or k in out:
                continue
            z = zipf_frequency(k, "en")
            if z < 2.0 or ((mid or n > 0) and part[:1].isupper() and z < 3.5):
                out.add(k)
    return sorted(out)


def main():
    from .engines import go_offline_if_cached, required_repos
    from .tools import js_runtime, setup_path
    setup_path()  # ffmpeg + a JavaScript runtime (for YouTube) without anyone installing them
    js_runtime()
    if os.name == "nt":  # keep Windows from sleeping mid-transcription (a Mac uses caffeinate)
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS | SYSTEM_REQUIRED
    jid = sys.argv[1]
    opts, settings = store.load(jid)["options"], store.settings()
    english = opts.get("language", "en") == "en"
    # a file (or a video without YouTube captions) also needs the Whisper tie-breaker, which may not be downloaded yet
    third = english and opts.get("mode", "accurate") == "accurate" and settings.get("third_vote", True)
    go_offline_if_cached(required_repos(settings.get("second_model", "turbo"), english, third))
    job = Job(jid)
    try:
        job.run(retried="--retried" in sys.argv)
    except Exception as e:  # noqa: BLE001 - report anything unexpected on the job card
        traceback.print_exc()
        job.fail(f"Something went wrong: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
