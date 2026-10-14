"""python evalflags.py ref.vtt parakeet.json whisper.json [vocab-source.txt]
Measures: primary WER, how many of its errors get flagged, and flag precision."""
import json, sys, time
from difflib import SequenceMatcher
from types import SimpleNamespace as NS

sys.path.insert(0, "..")
from score import vtt_text  # noqa
from skrift.words import key, same_meaning, parakeet_words  # noqa
from skrift.crosscheck import crosscheck, vocab_terms, apply_vocab  # noqa
import jiwer  # noqa
from skrift.words import _norm  # noqa

ref_words = [w for w in vtt_text(sys.argv[1]).split() if key(w)]
pj, wj = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
P = parakeet_words([NS(text=x["w"], start=x["s"], end=x["s"] + 0.08, confidence=x["p"]) for x in pj["words"]])
W = [{"t": x["w"].strip(), "s": x["s"], "e": x["e"], "c": x["p"]} for x in wj["words"] if x["w"].strip()]
t0 = time.time()
reviews = crosscheck(P, W)
vocab = vocab_terms([open(sys.argv[4]).read()]) if len(sys.argv) > 4 else {}
nfix = apply_vocab(P, reviews, vocab)
t_cc = time.time() - t0


def wer(words):
    return jiwer.wer(_norm(" ".join(ref_words)), _norm(" ".join(words)))


print(f"crosscheck {t_cc:.2f}s, vocab terms {len(vocab)}, spelling fixes {nfix}")
print(f"WER primary-after-fixes {wer([w['t'] for w in P])*100:.2f}%")

# label each P word right/wrong against the reference
pk = [i for i, w in enumerate(P) if key(w["t"])]
sm = SequenceMatcher(None, [key(P[i]["t"]) for i in pk], [key(w) for w in ref_words], autojunk=False)
wrong = set()
missed_at = []  # reference words the primary dropped, located at a P index
for tag, a1, a2, b1, b2 in sm.get_opcodes():
    if tag == "equal":
        continue
    pt = " ".join(P[i]["t"] for i in pk[a1:a2]); rt = " ".join(ref_words[b1:b2])
    if tag == "replace" and same_meaning(pt, rt):
        continue
    if tag in ("replace", "delete"):
        wrong.update(pk[a1:a2])
    else:
        missed_at.append(pk[a1] if a1 < len(pk) else len(P) - 1)

flagged = {i for r in reviews if not r["done"] and r["kind"] != "single" for i in range(r["a"], r["b"])}
gaps = {r["a"] for r in reviews if r["kind"] == "missing" and not r["done"]}
near = lambda i: any(abs(i - g) <= 1 for g in gaps) or any(abs(i - f) <= 1 for f in flagged)  # noqa
by_kind = {}
for r in reviews:
    if not r["done"]:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
print("review items:", by_kind, " words highlighted:", len(flagged), f"({len(flagged)/len(P)*100:.1f}% of {len(P)})")
print(f"wrong words {len(wrong)}: flagged {len(wrong & flagged)} ({len(wrong & flagged)/max(1,len(wrong))*100:.0f}%)")
print(f"dropped ref words {len(missed_at)}: near a flag {sum(near(i) for i in missed_at)}")
print(f"precision: {len(wrong & flagged)/max(1,len(flagged))*100:.0f}% of highlighted words are actually wrong")
for kind in ("differ", "single", "spelling"):
    ws = {i for r in reviews if r["kind"] == kind and not r["done"] for i in range(r["a"], r["b"])}
    if ws:
        print(f"  {kind:<8} words {len(ws):4d} actually-wrong {len(ws & wrong)/len(ws)*100:3.0f}%")
lc = {i for i, w in enumerate(P) if w["c"] < 0.5}
print(f"low-conf(<0.5) words {len(lc)}: wrong {len(lc & wrong)}, of which unflagged {len((lc & wrong) - flagged)}")
unflagged_wrong = sorted(wrong - flagged)
print("UNFLAGGED ERRORS (sample):")
for i in unflagged_wrong[:25]:
    print(f"   {P[i]['s']:7.1f}s  '{P[i]['t']}'  conf {P[i]['c']}  ctx: {' '.join(w['t'] for w in P[max(0,i-4):i+3])}")
