"""WER that ignores filler words the human captioner deleted (clean-verbatim reference)."""
import json, re, sys, jiwer
from score import vtt_text, norm
FILL = r"\b(like|um+|uh+|you know|i mean|yeah|yes|so|just|kind of|sort of|oh|okay|ok|right|and|but|well|actually|literally)\b"
def clean(t): return re.sub(r"\s+", " ", re.sub(FILL, " ", norm(t))).strip()
if __name__ == "__main__":
    ref = clean(vtt_text(sys.argv[1]))
    for p in sys.argv[2:]:
        d = json.load(open(p)); hyp = clean(d["text"])
        o = jiwer.process_words(ref, hyp)
        print(f"{d['engine']:<15} fair-WER {o.wer*100:5.2f}%  (sub {o.substitutions} del {o.deletions} ins {o.insertions} / {len(ref.split())})  time {d['elapsed']:.0f}s")
