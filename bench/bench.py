"""Benchmark harness: python bench.py <engine> <wav> <out.json>"""
import json, sys, time

engine, wav, out = sys.argv[1], sys.argv[2], sys.argv[3]
extra = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
t0 = time.time()
words = []
if engine.startswith("whisper"):
    import mlx_whisper
    repo = {"whisper-v3": "mlx-community/whisper-large-v3-mlx",
            "whisper-turbo": "mlx-community/whisper-large-v3-turbo"}[engine]
    opts = dict(word_timestamps=True, condition_on_previous_text=False,
                hallucination_silence_threshold=2.0, language="en")
    opts.update(extra)
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=repo, verbose=None, **opts)
    for seg in r["segments"]:
        for w in seg.get("words", []):
            words.append({"w": w["word"], "s": w["start"], "e": w["end"], "p": w["probability"]})
    text = r["text"]
else:
    from parakeet_mlx import from_pretrained
    repo = {"parakeet-v2": "mlx-community/parakeet-tdt-0.6b-v2",
            "parakeet-v3": "mlx-community/parakeet-tdt-0.6b-v3"}[engine]
    m = from_pretrained(repo)
    r = m.transcribe(wav, chunk_duration=120, overlap_duration=15)
    text = r.text
    for t in r.tokens:
        words.append({"w": t.text, "s": t.start, "e": t.end, "p": t.confidence})
el = time.time() - t0
json.dump({"engine": engine, "elapsed": el, "text": text, "words": words, "opts": extra}, open(out, "w"))
print(f"{engine}: {el:.1f}s")
