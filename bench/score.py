"""python score.py ref.vtt out1.json [out2.json ...] -> WER per engine"""
import json, re, sys, html
import jiwer
from whisper_normalizer.english import EnglishTextNormalizer

norm = EnglishTextNormalizer()


def vtt_text(path):
    lines, prev = [], None
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or "-->" in ln or ln.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or ln.isdigit():
            continue
        ln = html.unescape(re.sub(r"<[^>]+>", "", ln))
        ln = re.sub(r"\[[^\]]*\]|\([^)]*\)|♪", " ", ln)   # [music], (laughs)
        ln = re.sub(r"^[A-Z][A-Za-z .']{0,25}:\s", "", ln)  # speaker labels
        lines.append(ln)
    return " ".join(lines)


def text_of(path):
    return vtt_text(path) if path.endswith(".vtt") else json.load(open(path))["text"]


if __name__ == "__main__":
    ref = norm(text_of(sys.argv[1]))
    for p in sys.argv[2:]:
        d = json.load(open(p)) if p.endswith(".json") else {"engine": p, "elapsed": 0}
        hyp = norm(text_of(p))
        o = jiwer.process_words(ref, hyp)
        print(f"{d['engine']:<16} {json.dumps(d.get('opts', {})):<40} WER {o.wer*100:5.2f}%  "
              f"(sub {o.substitutions} del {o.deletions} ins {o.insertions} / {len(ref.split())} ref words)  "
              f"time {d['elapsed']:.0f}s")
