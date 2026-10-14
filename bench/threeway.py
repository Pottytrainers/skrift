"""Compare Skrift's transcript with YouTube's auto-captions (and Whisper) on one job.
python threeway.py <job_dir> <yt_words.json> [extra_engine.json]  ->  prints every disagreement region"""
import json
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, "..")
from skrift.words import key, same_meaning  # noqa: E402

job, ytp = sys.argv[1], sys.argv[2]
extra = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None
tr = json.load(open(f"{job}/transcript.json"))
P = tr["words"]                      # what Skrift exported (Parakeet + spelling fixes)
W = json.load(open(f"{job}/second.json"))
rev = {r["id"]: r for r in tr["reviews"]}

Y = []
for w in json.load(open(ytp)):
    t = re.sub(r"\[[^\]]*\]|>>", " ", w["t"]).strip()   # drop [laughter], [ __ ], >> speaker marks
    for part in t.split():
        Y.append({"t": part, "s": w["s"]})
for i, w in enumerate(Y):
    w["e"] = Y[i + 1]["s"] if i + 1 < len(Y) else w["s"] + 0.5


def span(words, a, b):
    return " ".join(x["t"] for x in words if a - 0.25 <= x["s"] < b + 0.25)


pk = [i for i, w in enumerate(P) if key(w["t"])]
yk = [i for i, w in enumerate(Y) if key(w["t"])]
sm = SequenceMatcher(None, [key(P[i]["t"]) for i in pk], [key(Y[i]["t"]) for i in yk], autojunk=False)
rows = []
for tag, a1, a2, b1, b2 in sm.get_opcodes():
    if tag == "equal":
        continue
    pi, yi = pk[a1:a2], yk[b1:b2]
    pt = " ".join(P[i]["t"] for i in pi)
    yt = " ".join(Y[i]["t"] for i in yi)
    if tag == "replace" and same_meaning(pt, yt):
        continue
    if pi:
        s, e = P[pi[0]]["s"], P[pi[-1]]["e"]
    else:
        s = Y[yi[0]]["s"]
        e = Y[yi[-1]]["e"]
    # flagged = a review covers these words, or a "missing" review sits at this moment
    flagged = any(P[i].get("r") is not None and rev[P[i]["r"]]["kind"] != "single" for i in pi) or \
        any(r["kind"] == "missing" and s - 1.5 <= r["s"] <= e + 1.5 for r in tr["reviews"])
    rows.append({"s": s, "e": e, "tag": tag, "skrift": pt, "youtube": yt, "whisper": span(W, s, e),
                 "extra": span(extra, s, e) if extra else None, "flagged": flagged})

json.dump(rows, open("threeway_rows.json", "w"), indent=1)
for r in rows:
    m, sec = divmod(int(r["s"]), 60)
    print(f"{m}:{sec:02d} {'F' if r['flagged'] else ' '} S: {r['skrift'][:38]:<38} | YT: {r['youtube'][:38]:<38} | W: {r['whisper'][:38]}"
          + (f" | L: {r['extra'][:38]}" if extra else ""))
print(len(rows), "disagreement regions;", sum(r["flagged"] for r in rows), "flagged in Skrift")
