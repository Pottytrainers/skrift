"""python diffs.py ref.vtt out.json [n] -> show the error regions"""
import json, sys, jiwer
from score import vtt_text, norm  # noqa

ref = norm(vtt_text(sys.argv[1])).split()
hyp = norm(json.load(open(sys.argv[2]))["text"]).split()
n = int(sys.argv[3]) if len(sys.argv) > 3 else 60
o = jiwer.process_words(" ".join(ref), " ".join(hyp))
shown = 0
for a in o.alignments[0]:
    if a.type == "equal":
        continue
    r = " ".join(ref[a.ref_start_idx:a.ref_end_idx])
    h = " ".join(hyp[a.hyp_start_idx:a.hyp_end_idx])
    ctx = " ".join(ref[max(0, a.ref_start_idx - 4):a.ref_start_idx])
    print(f"{a.type[:3]:<4} ...{ctx[-30:]:>30} | REF: {r[:60]:<40} | HYP: {h[:60]}")
    shown += 1
    if shown >= n:
        break
