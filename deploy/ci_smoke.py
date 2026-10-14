"""End-to-end check of a built download, run the way a user runs it: start it from its folder, let it set itself
up from nothing, transcribe a few minutes of computer-generated speech, then check the words, every export,
the AI check, and that nothing outside this computer can reach it.

    python deploy/ci_smoke.py <extracted download folder>

Used by .github/workflows/release.yml on GitHub's free Windows and Linux machines. Standard library only.
"""
from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 5390
URL = f"http://127.0.0.1:{PORT}"

# original text, read slowly enough (about three minutes) to cross the engines' two-minute windows
TEXT = """\
The old lighthouse stood at the end of a narrow road that ran along the cliffs. Every summer my family drove out
to see it, and every summer my father told the same story about the keeper who lived there long ago. He said the
keeper climbed the stairs each night with a lantern in one hand and a book in the other, because the light had to
be watched until morning and the nights were very long. My sister always asked what kind of book it was, and my
father always gave a different answer. One year it was a book about ships, the next year it was a book of poems,
and once he claimed it was a cookbook, because the keeper got hungry at midnight and liked to plan his breakfast.

When we finally reached the top of the hill, we would sit on the grass and eat sandwiches while the wind pulled at
our jackets. The sea below was grey on cloudy days and bright green when the sun came out. Fishing boats moved
slowly across the water, and the gulls followed them, crying and diving whenever someone threw something over the
side. My mother liked to count the boats and give each one a name. The biggest one was always called the Queen,
and the smallest one was always called the Pebble.

Years later I went back on my own. The road had been repaved and there was a small parking lot where the grass used
to be, but the lighthouse looked exactly the same. A volunteer at the door told me that the light still turns on
every evening, even though no ships really need it anymore. I asked her if anyone still climbs the stairs at night.
She laughed and said that a computer does the watching now, but that she sometimes stays late anyway, just to see
the beam sweep across the water. Before I left, I bought a postcard of the lighthouse and sent it to my father.
On the back I wrote one question. What kind of book was it really? He never answered, but the next time I visited
him, the postcard was standing on his shelf, right next to an old cookbook.
"""


def say(*a):
    print(*a, flush=True)


def speak(text: str, folder: Path) -> Path:
    """Read the text aloud with the computer's own speech voice."""
    txt = folder / "speech.txt"
    # one line per paragraph: speech voices pause at line breaks, and the engines rightly hear those as sentence ends
    txt.write_text(re.sub(r"(?<!\n)\n(?!\n)", " ", text), encoding="utf-8")
    system = platform.system()
    if system == "Windows":
        out = folder / "speech.wav"
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.Rate = -2; $s.SetOutputToWaveFile('{out}'); $s.Speak([IO.File]::ReadAllText('{txt}')); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    elif system == "Darwin":
        out = folder / "speech.aiff"
        subprocess.run(["say", "-r", "140", "-f", str(txt), "-o", str(out)], check=True)
    else:
        out = folder / "speech.wav"
        subprocess.run(["espeak-ng", "-s", "130", "-f", str(txt), "-w", str(out)], check=True)
    return out


def launcher(folder: Path) -> list[str]:
    system = platform.system()
    if system == "Windows":
        return ["cmd", "/c", str(next(folder.glob("Start *.bat")))]
    if system == "Darwin":
        app = next(folder.glob("*.app"))
        return [str(next((app / "Contents" / "MacOS").iterdir()))]
    return [str(folder / "skrift.sh")]


def get(path: str, timeout=30):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def get_json(path: str):
    return json.loads(get(path)[2])


def words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", s.lower().replace("’", "'")).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / max(1, len(ref))


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outward-facing address
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def wait(what: str, check, minutes: float, every: float = 10):
    end = time.time() + minutes * 60
    last = None
    while time.time() < end:
        try:
            done, note = check()
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            done, note = False, "not answering yet"
        if note != last:
            say(f"  [{time.strftime('%H:%M:%S')}] {what}: {note}")
            last = note
        if done:
            return
        time.sleep(every)
    raise SystemExit(f"FAIL: {what} took longer than {minutes} minutes")


def main():
    folder = Path(sys.argv[1]).resolve()
    work = Path(tempfile.mkdtemp(prefix="skrift-smoke-"))
    data = work / "data"
    env = {**os.environ, "SKRIFT_PORT": str(PORT), "SKRIFT_NO_BROWSER": "1", "SKRIFT_DATA": str(data)}
    failures = []

    say("== making test speech")
    audio = speak(TEXT, work)
    say(f"  {audio.name}  {audio.stat().st_size / 1e6:.1f} MB")

    say("== starting the download the way a user would")
    try:
        get("/api/health", 3)
        raise SystemExit(f"FAIL: something is already answering on port {PORT}; stop it first")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        pass
    app_log = open(work / "app.log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(launcher(folder), env=env, stdin=subprocess.DEVNULL, stdout=app_log, stderr=subprocess.STDOUT,
                            start_new_session=os.name != "nt")
    t0 = time.time()
    try:
        wait("first-time setup (Python packages)", lambda: (b'"ok"' in get("/api/health", 5)[2], "app is up"), 25)
        say(f"  app answering after {time.time() - t0:.0f}s")

        ip = lan_ip()
        if ip:
            try:
                socket.create_connection((ip, PORT), timeout=3).close()
                failures.append(f"Skrift answered on the network address {ip}; it must only listen on this computer")
            except OSError:
                say(f"  good: not reachable from the network ({ip}:{PORT} refused)")

        say("== transcribing the test speech (accurate mode: Parakeet + Whisper + Whisper large-v3 + AI check)")
        req = urllib.request.Request(f"{URL}/api/upload?name=lighthouse{audio.suffix}&mode=accurate&language=en",
                                     data=audio.read_bytes(), method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            jid = json.loads(r.read())["jobs"][0]["id"]

        def job_done():
            get("/api/health", 5)  # what an open browser tab does; keeps the app from going to sleep
            j = get_json(f"/api/jobs/{jid}")
            if j["status"] == "error":
                raise SystemExit(f"FAIL: the job failed: {j.get('error')}")
            setup = get_json("/api/setup")
            pending = [i["label"] for i in setup.get("items", []) if not i.get("ready")]
            note = f"{j['status']} | {j.get('stage')}" + (f" | downloading: {', '.join(pending)}" if pending else "")
            return j["status"] == "done", note
        wait("transcription", job_done, 90, every=15)
        job = get_json(f"/api/jobs/{jid}")
        stats = job.get("stats", {})
        say(f"  done after {time.time() - t0:.0f}s total")
        say("  stats: " + json.dumps({k: v for k, v in stats.items() if not isinstance(v, (list, dict)) or k == "meaning"}))

        expect = {"primary_engine": "parakeet", "second_engine": "whisper", "third_engine": "whisper-large-v3"}
        for k, v in expect.items():
            if v not in str(stats.get(k, "")):
                failures.append(f"{k} was {stats.get(k)!r}, expected {v}")
        if "meaning_error" in stats:
            failures.append(f"AI check failed: {stats['meaning_error']}")

        say("== checking every export")
        for fmt in ("txt", "srt", "vtt", "docx", "pdf", "md", "json"):
            status, ctype, body = get(f"/api/jobs/{jid}/export/{fmt}?timestamps=0&sounds=0")
            looks_right = {"srt": lambda: b"-->" in body, "vtt": lambda: body.startswith(b"WEBVTT"),
                           "docx": lambda: body.startswith(b"PK"), "pdf": lambda: body.startswith(b"%PDF"),
                           "json": lambda: bool(json.loads(body))}.get(fmt, lambda: True)
            ok = status == 200 and len(body) > 200 and looks_right()
            say(f"  {fmt:5} {status} {len(body):>8} bytes  {'ok' if ok else 'BAD'}")
            if not ok:
                failures.append(f"{fmt} export looks wrong")
            if fmt == "txt":
                text = body.decode("utf-8")

        ref, hyp = words(TEXT), words(text)
        score = wer(ref, hyp)
        say(f"== word error rate against the script: {score:.1%} ({len(hyp)} words heard, {len(ref)} spoken)")
        say("  " + text[:600].replace("\n", " ") + " ...")
        limit = 0.15 if platform.system() != "Linux" else 0.25  # espeak's robotic voice is harder than Windows'
        if score > limit:
            failures.append(f"word error rate {score:.1%} is above {limit:.0%}")
        if " ".join(ref[-6:]) not in " ".join(hyp[-12:]):
            failures.append("the last sentence is missing from the end of the transcript")

        py = data / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        app_dir = folder / "app" if (folder / "app").exists() else next(folder.glob("*.app")) / "Contents" / "Resources" / "app"

        def in_app(code: str, timeout=1800):
            """Run Python inside the installed app, the way the transcriber does."""
            return subprocess.run([str(py), "-c", code], cwd=str(app_dir), env=env, capture_output=True, text=True,
                                  timeout=timeout)

        say("== asking the AI check a question directly")
        r = in_app("import time\n"
                   "from skrift import meaning\n"
                   "t = time.time()\n"
                   "i, p, _ = meaning.choose('Baking bread at home', 'then you add two cups of', "
                   "'and a pinch of salt to the bowl', ['flower', 'flour'])\n"
                   "print('AI_ANSWER', i, round(p, 2), f'first question incl. loading {time.time() - t:.0f}s')\n"
                   "for q in [('Car review', 'the engine makes about four hundred', 'which is a lot for a car this size', "
                   "['horsepower', 'whores power']), ('Cooking show', 'let the sauce', 'for ten minutes before serving', "
                   "['seep', 'steep', 'sleep'])]:\n"
                   "    t = time.time(); meaning.choose(*q); print(f'AI_NEXT {time.time() - t:.1f}s')\n"
                   "from skrift import llm_server; llm_server.shutdown()\n")
        m = re.search(r"AI_ANSWER (\d) ([\d.]+).*", r.stdout)
        say(f"  {m.group(0) if m else r.stdout[-400:] + r.stderr[-1500:]}")
        say("  per further question: " + ", ".join(re.findall(r"AI_NEXT (\S+)", r.stdout)))
        if not m or m.group(1) != "1":
            failures.append("the AI check did not pick 'flour' in a baking sentence")

        # Clean computer speech may leave nothing for the tie-breaker to do, so run it directly on three spots:
        # the words must come back at the right times, matching the transcript.
        say("== tie-breaker on three spots (Whisper large-v3)")
        spots = [(5.0, 14.0), (40.0, 52.0), (90.0, 99.0)]
        t_start = time.time()
        r = in_app("import json, subprocess\n"
                   "from skrift import tools\n"
                   "from skrift.engines import run_whisper\n"
                   f"pcm = {str(work / 'speech.pcm')!r}\n"
                   f"subprocess.run([tools.ffmpeg(), '-v', 'error', '-y', '-i', {str(audio)!r}, '-ac', '1', '-ar', '16000',"
                   " '-f', 's16le', pcm], check=True)\n"
                   f"Y, _ = run_whisper(pcm, 'large-v3', 'en', [], regions={spots!r})\n"
                   "print('SPOTS ' + json.dumps(Y))\n")
        m = re.search(r"^SPOTS (.*)$", r.stdout, re.M)
        if not m:
            say(r.stdout[-400:] + r.stderr[-1500:])
            failures.append("the tie-breaker did not run")
        else:
            Y = json.loads(m.group(1))
            T = job["transcript"]["words"]
            outside = [w for w in Y if not any(a - 0.5 <= w["s"] and w["e"] <= b + 0.5 for a, b in spots)]
            scores = []
            for a, b in spots:
                heard = words(" ".join(w["t"] for w in Y if a <= (w["s"] + w["e"]) / 2 <= b))
                said = words(" ".join(w["t"] for w in T if a + 1 <= (w["s"] + w["e"]) / 2 <= b - 1))
                # the clip edges cut words in half, so compare against the transcript's words well inside each spot
                missing = [x for x in said if x not in heard]
                scores.append(len(missing) / max(1, len(said)))
            say(f"  {len(Y)} words in {time.time() - t_start:.0f}s; words placed outside the spots: {len(outside)}; "
                f"transcript words missing per spot: {', '.join(f'{x:.0%}' for x in scores)}")
            if outside or max(scores) > 0.15:
                failures.append("the tie-breaker put words at the wrong times or missed words")

        if os.name == "nt":
            say("== Windows notification")
            r = in_app("from skrift.worker import windows_toast\n"
                       "print('TOAST', windows_toast('Smoke test: \"quotes\" & <symbols>', '0 spots to double-check', "
                       f"'http://localhost:{PORT}/'))\n", timeout=120)
            say("  " + (r.stdout.strip() or r.stderr[-800:]))
            if "TOAST (True" not in r.stdout:
                failures.append("Windows did not accept the notification")
    finally:
        if os.name == "nt":  # stop everything the launcher started (setup, app, transcriber, AI runtime)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(15)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for _ in range(30):  # wait until the app has really stopped (it shuts down gracefully)
            try:
                get("/api/health", 2)
                time.sleep(1)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                break
        else:
            failures.append("the app was still running 30 s after being stopped")
        app_log.close()
        for log in [work / "app.log", data / "setup.log", data / "worker.log", *data.rglob("llama-server.log")]:
            if log.exists():
                say(f"\n== {log.name} (last lines)")
                say("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))

    if failures:
        say("\nFAILED:\n  - " + "\n  - ".join(failures))
        sys.exit(1)
    say("\nALL GOOD")


if __name__ == "__main__":
    main()
