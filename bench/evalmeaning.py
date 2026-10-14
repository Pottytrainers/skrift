"""Does the meaning check improve accuracy on videos with human captions?"""
import json, sys, time
sys.path.insert(0, "..")
from types import SimpleNamespace as NS
import jiwer
from score import vtt_text, norm
from fairscore import clean
from skrift.words import parakeet_words
from skrift.crosscheck import crosscheck
from skrift.vote import vote, youtube_words
from skrift.meaning import meaning_pass
only = sys.argv[1] if len(sys.argv) > 1 else None
for name, title, ref, pf, wf, yf in [
        ("Nintendo", "Nintendo Direct 9.9.2026", "nd.en.vtt", "out_parakeet-v2.json", "out_whisper-turbo.json", "nd_yt.en-orig.json3"),
        ("podcast", "We Respond To Your Hottest Gaming Takes | Assumptions", "pod.en.vtt", "pod_parakeet-v2.json", "pod_whisper-turbo.json", "pod_yt.en-orig.json3")]:
    if only and only != name:
        continue
    R = vtt_text(ref)
    pj, wj = json.load(open(pf)), json.load(open(wf))
    P = parakeet_words([NS(text=x["w"], start=x["s"], end=x["s"] + 0.08, confidence=x["p"]) for x in pj["words"]])
    W = [{"t": x["w"].strip(), "s": x["s"], "e": x["e"], "c": x["p"]} for x in wj["words"] if x["w"].strip()]
    Y = youtube_words(yf)
    revs = crosscheck(P, W); vote(P, W, revs, Y); revs = [r for r in revs if not r.get("weak")]
    before = " ".join(w["t"] for w in P)
    t = time.time()
    st = meaning_pass(P, revs, title, Y)
    after = " ".join(w["t"] for w in P)
    sc = lambda x: (jiwer.wer(norm(R), norm(x)) * 100, jiwer.wer(clean(R), clean(x)) * 100)
    b0, a0 = sc(before), sc(after)
    print(f"== {name}: WER {b0[0]:.2f}% -> {a0[0]:.2f}%   filler-insensitive {b0[1]:.2f}% -> {a0[1]:.2f}%   {st}  ({time.time()-t:.0f}s)")
    for r in revs:
        if r["kind"] == "ai":
            print(f"     {'[sound-alike] ' if r.get('homophone') else ''}{r['alt']!r} -> {r['ai']!r}   options {r['options']}")
