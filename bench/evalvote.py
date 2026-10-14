"""Does adding YouTube's auto-captions as a third vote beat each engine alone?  (human captions = answer key)"""
import json, sys
sys.path.insert(0, "..")
from types import SimpleNamespace as NS
import jiwer
from score import vtt_text, norm
from fairscore import clean
from skrift.words import parakeet_words
from skrift.crosscheck import crosscheck
from skrift.vote import vote, youtube_words
for name, ref, pf, wf, yf in [("Nintendo Direct", "nd.en.vtt", "out_parakeet-v2.json", "out_whisper-turbo.json", "nd_yt.en-orig.json3"),
                              ("4-person podcast", "pod.en.vtt", "pod_parakeet-v2.json", "pod_whisper-turbo.json", "pod_yt.en-orig.json3")]:
    R = vtt_text(ref)
    pj, wj = json.load(open(pf)), json.load(open(wf))
    P = parakeet_words([NS(text=x["w"], start=x["s"], end=x["s"] + 0.08, confidence=x["p"]) for x in pj["words"]])
    W = [{"t": x["w"].strip(), "s": x["s"], "e": x["e"], "c": x["p"]} for x in wj["words"] if x["w"].strip()]
    Y = youtube_words(yf)
    def score(words):
        t = " ".join(w["t"] for w in words)
        return jiwer.wer(norm(R), norm(t)) * 100, jiwer.wer(clean(R), clean(t)) * 100
    p0 = score(P)
    revs = crosscheck(P, W)
    open0 = sum(1 for r in revs if not r["done"] and r["kind"] not in ("single",))
    st = vote(P, W, revs, Y)
    p1 = score(P)
    open1 = sum(1 for r in revs if not r["done"] and r["kind"] not in ("single", "voted"))
    print(f"== {name}")
    print(f"   Parakeet alone       WER {p0[0]:.2f}%  filler-insensitive {p0[1]:.2f}%   spots to check: {open0}")
    print(f"   + 3-way vote         WER {p1[0]:.2f}%  filler-insensitive {p1[1]:.2f}%   spots to check: {open1}")
    print(f"   vote: {st}")
