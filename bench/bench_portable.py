"""python bench_portable.py <parakeet|whisper> <pcm> <out.json>  -- runs the cross-platform engines (CPU)"""
import json, os, sys, time
os.environ["SKRIFT_BACKEND"] = "portable"
sys.path.insert(0, "..")
from skrift import portable
eng, pcm, out = sys.argv[1:4]
t = time.time()
if eng == "parakeet":
    W = portable.run_parakeet(pcm)
else:
    W, _ = portable.run_whisper(pcm, "turbo", "en", None)
el = time.time() - t
json.dump({"engine": f"portable-{eng}", "elapsed": el, "text": " ".join(w["t"] for w in W),
           "words": [{"w": " " + w["t"], "s": w["s"], "e": w["e"], "p": w["c"]} for w in W]}, open(out, "w"))
print(f"portable {eng}: {el:.0f}s for {len(W)} words")
