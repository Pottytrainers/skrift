"""Who's right where Skrift and another transcript disagree? An independent engine (not used by either) judges.
python judge.py <skrift transcript.json> <other.srt|youtube.json3> <judge words.json> [job_dir for diagnosis]"""
import json
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, "..")
from skrift.crosscheck import _FILLER  # noqa: E402
from skrift.vote import CENSORED, youtube_words  # noqa: E402
from skrift.words import key, same_meaning  # noqa: E402

SW = {"fuck", "fucking", "fucked", "shit", "shitty", "bullshit", "damn", "ass", "bitch", "motherfucker", "motherfucking"}


def srt_words(p):
    out = []
    for b in open(p, encoding="utf-8").read().strip().split("\n\n"):
        L = b.split("\n")
        h, m, s = L[1].split(" --> ")[0].replace(",", ".").split(":")
        t0 = int(h) * 3600 + int(m) * 60 + float(s)
        txt = re.sub(r"\[ __ \]", f" {CENSORED} ", " ".join(L[2:]).replace("\xa0", " "))
        for t in re.sub(r"\[[^\]]*\]|>>", " ", txt).split():
            out.append({"t": t, "s": t0})
    return out


S = [w for w in json.load(open(sys.argv[1]))["words"] if w.get("t")]
O = youtube_words(sys.argv[2]) if sys.argv[2].endswith(".json3") else srt_words(sys.argv[2])
L = json.load(open(sys.argv[3]))
job = sys.argv[4] if len(sys.argv) > 4 else None
P0 = json.load(open(f"{job}/primary.json")) if job else []
W0 = json.load(open(f"{job}/second.json")) if job else []
tr = json.load(open(sys.argv[1]))
R = {r["id"]: r for r in tr["reviews"]}


def k(t):
    if t == CENSORED:
        return "*"
    kk = key(t)
    return "*" if kk in SW else kk


sk = [i for i, w in enumerate(S) if k(w["t"])]
ok = [i for i, w in enumerate(O) if k(w["t"])]
sm = SequenceMatcher(None, [k(S[i]["t"]) for i in sk], [k(O[i]["t"]) for i in ok], autojunk=False)
res = {"Skrift right": [], "Other right": [], "unclear": []}
for tag, a1, a2, b1, b2 in sm.get_opcodes():
    if tag == "equal":
        continue
    si, oi = sk[a1:a2], ok[b1:b2]
    st = " ".join(S[i]["t"] for i in si)
    ot = " ".join(O[i]["t"] for i in oi)
    if tag == "replace" and same_meaning(st, ot):
        continue
    cs = [key(x) for x in st.split() if key(x) and key(x) not in _FILLER and key(x) not in SW]
    co = [key(x) for x in ot.split() if key(x) and key(x) not in _FILLER]
    if not cs and not co:
        continue  # filler / swear-bleep only
    t = S[si[0]]["s"] if si else O[oi[0]]["s"]
    span = "".join(key(w["t"]) for w in L if t - 2.5 <= w["s"] <= t + 3.5)
    js, jo = "".join(cs), "".join(co)
    s_ok = bool(js) and js in span
    o_ok = bool(jo) and jo in span
    row = {"t": round(t, 1), "skrift": st, "other": ot}
    if job:
        row["parakeet"] = " ".join(w["t"] for w in P0 if t - 0.6 <= w["s"] <= t + 1.6)
        row["whisper"] = " ".join(w["t"] for w in W0 if t - 0.6 <= w["s"] <= t + 1.6)
        revs = {S[i].get("r") for i in si if S[i].get("r") is not None} | {S[i].get("g") for i in si if S[i].get("g") is not None}
        if not si:  # an insertion point: any open "missing" review at that moment?
            revs |= {r["id"] for r in tr["reviews"] if r["kind"] == "missing" and abs(r["s"] - t) < 1.5}
        row["flag"] = ",".join(sorted({R[x]["kind"] + ("" if not R[x]["done"] else ":done") for x in revs if x in R})) or "-"
    res["Skrift right" if s_ok and not o_ok else "Other right" if o_ok and not s_ok else "unclear"].append(row)

for kname, v in res.items():
    print(f"{kname:<13} {len(v)}")
json.dump(res, open("judge_out.json", "w"), indent=1)
