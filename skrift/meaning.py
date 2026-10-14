"""The meaning check: a local language model reads the transcript around each spot where the speech
engines disagree and picks the version that makes sense ("not even an EDITION, it's a collection").

It only ever chooses between versions the engines actually heard (it never invents words), it runs
on this Mac (free, private), and it reports how sure it is so unsure picks stay in the review list.
"""
import os

# Gemma 4 E4B: 14/17 on real disputed spots from test videos (bench/meaning_cases.py), ~2 s per spot,
# ~4 GB. Gemma 4 12B scored 16/17 but needs 11 GB, which swaps on a 16 GB Mac.
LLM = os.environ.get("SKRIFT_LLM", "mlx-community/gemma-4-e4b-it-4bit")
MODELS = {
    "standard": "mlx-community/gemma-4-e4b-it-4bit",      # ~4 GB, ~5 s per spot
    "thorough": "mlx-community/gemma-4-12B-it-qat-4bit",  # ~11 GB, ~10 s per spot on 16 GB; best results
}
LETTERS = "ABCD"
_model = None


def load(repo: str = LLM):
    global _model
    if _model is None or _model[0] != repo:
        from mlx_lm import load as mlx_load
        model, tok = mlx_load(repo)
        _model = (repo, model, tok)
    return _model[1], _model[2]


def _prompt(tok, title, before, after, options):
    opts = "\n".join(f"{LETTERS[i]}) {o}" for i, o in enumerate(options))
    msg = (f'This is part of an automatic transcript of a spoken video titled "{title}". '
           "Speech-recognition engines disagreed about the words at [___].\n\n"
           f"...{before} [___] {after}...\n\n"
           "Which option is what the speaker most likely said? Use the meaning of the conversation, grammar, "
           "and real names (games, people, companies). Reply with only the letter.\n\n" + opts)
    messages = [{"role": "user", "content": msg}]
    kw = {"add_generation_prompt": True, "tokenize": False}
    try:
        text = tok.apply_chat_template(messages, enable_thinking=False, **kw)
    except TypeError:
        text = tok.apply_chat_template(messages, **kw)
    return text


def _reason_messages(title, before, after, options):
    opts = "\n".join(f"{LETTERS[i]}) {o}" for i, o in enumerate(options))
    msg = (f'This is part of an automatic transcript of a spoken video titled "{title}". '
           "Speech-recognition engines disagreed about the words at [___]; the options sound alike.\n\n"
           f"...{before} [___] {after}...\n\n{opts}\n\n"
           "Which option did the speaker most likely say? Think about what the conversation is about, grammar, "
           "and real names (games, people, companies). Write at most two short sentences of reasoning, "
           "then a final line exactly like: Answer: X")
    return [{"role": "user", "content": msg}]


def _reason_prompt(tok, title, before, after, options):
    messages = _reason_messages(title, before, after, options)
    kw = {"add_generation_prompt": True, "tokenize": False}
    try:
        return tok.apply_chat_template(messages, enable_thinking=False, **kw)
    except TypeError:
        return tok.apply_chat_template(messages, **kw)


def _letter_probs(model, tok, text, n):
    import math
    import mlx.core as mx
    ids = tok.encode(text, add_special_tokens=False)
    logits = model(mx.array([ids]))[0, -1].astype(mx.float32)
    cand = []
    for i in range(n):
        ids_i = {t for form in (LETTERS[i], " " + LETTERS[i]) for t in tok.encode(form, add_special_tokens=False)[:1]}
        cand.append(max(logits[t].item() for t in ids_i))
    m = max(cand)
    ex = [math.exp(c - m) for c in cand]
    return [e / sum(ex) for e in ex]


def choose(title: str, before: str, after: str, options: list[str], repo: str = LLM,
           reason: bool = False) -> tuple[int, float, list[float]]:
    """Returns (index of the most likely option, its probability, all probabilities).
    reason=True lets the model write a sentence or two first (slower, better on tricky spots)."""
    from .engines import backend
    if backend() != "mlx":  # Windows / Linux / Intel Macs: llama.cpp's llama-server with the same Gemma model
        from .llm_server import letter_choice
        probs = letter_choice(_reason_messages(title, before, after, options), len(options))
        best = max(range(len(options)), key=lambda i: probs[i])
        return best, probs[best], probs
    model, tok = load(repo)
    if reason:
        # one pass: stream the short reasoning and read the letter's probabilities right where it's written
        import math
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        prompt = _reason_prompt(tok, title, before, after, options)
        letter_ids = [{t for form in (LETTERS[i], " " + LETTERS[i]) for t in tok.encode(form, add_special_tokens=False)[:1]}
                      for i in range(len(options))]
        text, probs = "", None
        for resp in stream_generate(model, tok, prompt, max_tokens=110, sampler=make_sampler(temp=0.0)):
            if text.rstrip().endswith("Answer:"):
                lp = resp.logprobs
                cand = [max(lp[t].item() for t in ids) for ids in letter_ids]
                m = max(cand)
                ex = [math.exp(c - m) for c in cand]
                probs = [e / sum(ex) for e in ex]
                break
            text += resp.text
        if probs is None:  # the model never wrote "Answer:"; fall back to asking for the letter directly
            probs = _letter_probs(model, tok, prompt + text.rstrip() + "\nAnswer:", len(options))
        best = max(range(len(options)), key=lambda i: probs[i])
        return best, probs[best], probs
    import mlx.core as mx
    ids = tok.encode(_prompt(tok, title, before, after, options), add_special_tokens=False)
    logits = model(mx.array([ids]))[0, -1].astype(mx.float32)
    cand = []
    for i in range(len(options)):
        ids_i = {t for form in (LETTERS[i], " " + LETTERS[i]) for t in tok.encode(form, add_special_tokens=False)[:1]}
        cand.append(max(logits[t].item() for t in ids_i))
    m = max(cand)
    import math
    ex = [math.exp(c - m) for c in cand]
    probs = [e / sum(ex) for e in ex]
    best = max(range(len(options)), key=lambda i: probs[i])
    return best, probs[best], probs


# ---------------------------------------------------------------- the pass over a transcript

def sound_code(text: str) -> str:
    """Phonetic fingerprint: "addition" and "edition", "Don" and "Dawn" get the same code."""
    import re
    import jellyfish
    c = jellyfish.metaphone(re.sub(r"[^A-Za-z]", "", text))
    return re.sub(r"^[AEIOU]", "A", c)


def _recase(new: str, old: str, prev: str) -> str:
    """YouTube's older captions are all lowercase ("i was", "soulsborne"): fix the obvious capitals."""
    import re
    if not new or any(c.isupper() for c in new):
        return new
    out = re.sub(r"\bi\b", "I", new)
    if (not prev or prev.rstrip()[-1:] in ".?!") or (old[:1].isupper() and not old.startswith("I ")):
        out = out[:1].upper() + out[1:]
    return out


def _uncaps(text):
    """YouTube writes shouting in capitals ("ALL RIGHT."): use normal case."""
    if not text or sum(c.isupper() for c in text) < 3 or not text.replace("I", "").isupper():
        return text
    out = text.lower()
    return out[:1].upper() + out[1:]


def _dictionary(text: str) -> bool:
    """Every word is an ordinary English word (so a sound-alike swap is a real homophone question)."""
    from wordfreq import zipf_frequency
    from .words import base_key
    ks = [base_key(t) for t in text.split() if base_key(t)]
    return bool(ks) and all(zipf_frequency(k, "en") >= 2.0 for k in ks)


def _ctx(P, a, b, n_before=60, n_after=40):
    return " ".join(w["t"] for w in P[max(0, a - n_before):a]), " ".join(w["t"] for w in P[b:b + n_after])


def _swap(P, a, b, text, rid):
    toks = text.split()
    s0, e0 = (P[a]["s"], P[b - 1]["e"]) if b > a else (P[a]["s"], P[a]["s"])
    new = [{"t": t, "s": round(s0 + (e0 - s0) * k / len(toks), 2), "e": round(s0 + (e0 - s0) * (k + 1) / len(toks), 2),
            "c": 1.0, "ok": True, "r": rid} for k, t in enumerate(toks)]
    for k_ in ("p", "g", "sp"):
        if P[a].get(k_) is not None and new:
            new[0][k_] = P[a][k_]
    P[a:b] = new


def meaning_pass(P: list[dict], reviews: list[dict], title: str, Y: list[dict] | None = None,
                 progress=lambda f: None, repo: str = LLM, homophones: bool = True,
                 third_name: str | None = "YouTube") -> dict:
    """1) Spots where all engines disagree: the model picks the version that makes sense.
       2) Words two engines agreed on but YouTube heard differently, when both versions SOUND THE SAME
          ("addition"/"edition"): the model may overrule the majority. Words that sound different
          ("Resonant"/"Resident") are never overruled, because there the engines' ears beat the model's guess.
    The model only ever chooses between versions an engine actually heard."""
    from .words import key, same_meaning
    BLEEP = "\u2026"
    jobs = []
    for r in reviews:
        if r["done"] or r["kind"] != "differ":
            continue
        idx = [i for i, w in enumerate(P) if w.get("r") == r["id"]]
        if not idx or idx[-1] - idx[0] + 1 != len(idx):
            continue
        cur = " ".join(P[i]["t"] for i in idx)
        opts, srcs, seen = [cur], ["Parakeet"], {"".join(key(t) for t in cur.split())}
        for o, src in ((r.get("alt"), r.get("alt_from") or "Whisper"), (_uncaps(r.get("alt2")), third_name)):
            k = "".join(key(t) for t in (o or "").split())
            if o and k and k not in seen and BLEEP not in o and not same_meaning(o, cur):
                opts.append(o)
                srcs.append(src or "Engine 3")
                seen.add(k)
        if len(opts) > 1:
            jobs.append((idx[0], idx[-1] + 1, opts, srcs, r))
    if Y and homophones:
        from .vote import _align
        eq, grp, ins, groups = _align(P, Y)
        spans = {}
        for i, g in enumerate(grp):
            if g is not None:
                spans.setdefault(g, []).append(i)
        for g, idx in spans.items():
            ytoks = [t for t in groups[g] if t != BLEEP]
            if idx[-1] - idx[0] + 1 != len(idx) or not ytoks or len(idx) > 3 or len(ytoks) > 3:
                continue
            ws = P[idx[0]:idx[-1] + 1]
            if not all(w.get("ok") and w.get("r") is None and w.get("g") is None for w in ws):
                continue
            ptext, ytext = " ".join(w["t"] for w in ws), _uncaps(" ".join(ytoks))
            if same_meaning(ptext, ytext) or sound_code(ptext) != sound_code(ytext) or len(sound_code(ptext)) < 2:
                continue
            # names ("Townfall", "Geppetto"): the model tends to prefer a more familiar spelling, so for these it
            # may only SUGGEST (shown in the review list, text unchanged)
            suggest_only = not (_dictionary(ptext) and _dictionary(ytext))
            jobs.append((idx[0], idx[-1] + 1, [ptext, ytext], ["Parakeet + Whisper", third_name or "Engine 3"],
                         "suggest" if suggest_only else None))
    stats = {"asked": len(jobs), "changed": 0, "homophones_changed": 0, "suggested": 0}
    next_id = max((r["id"] for r in reviews), default=-1) + 1
    for n, (a, b, opts, srcs, r) in enumerate(sorted(jobs, key=lambda j: j[0], reverse=True)):
        before, after = _ctx(P, a, b)
        pick, prob, _ = choose(title, before, after, opts, repo=repo, reason=True)
        progress((n + 1) / max(len(jobs), 1))
        if not isinstance(r, dict) and pick == 0:
            continue  # the majority stands; nothing to show
        if r == "suggest":  # a name: leave the majority's spelling in place, offer the other one
            reviews.append({"id": next_id, "kind": "ai", "suggest": True, "ai": opts[0], "ai_from": srcs[0],
                            "alt": _recase(opts[pick], opts[0], P[a - 1]["t"] if a else ""), "alt_from": srcs[pick],
                            "options": opts, "s": P[a]["s"], "e": P[b - 1]["e"], "conf": None, "done": False,
                            "homophone": True})
            for w in P[a:b]:
                w["r"] = next_id
            next_id += 1
            stats["suggested"] += 1
            continue
        # review card: the AI's pick is in the transcript; "A" puts back the first engine's version, "Y" the other one
        rest = [k for k in range(len(opts)) if k != pick]
        card = {"kind": "ai", "ai": opts[pick], "ai_from": srcs[pick], "options": opts,
                "alt": opts[rest[0]], "alt_from": srcs[rest[0]],
                "alt2": opts[rest[1]] if len(rest) > 1 else None, "alt2_from": srcs[rest[1]] if len(rest) > 1 else None}
        if isinstance(r, dict):
            if pick != 0:
                opts[pick] = _recase(opts[pick], opts[0], P[a - 1]["t"] if a else "")
                card["ai"] = opts[pick]
                _swap(P, a, b, opts[pick], r["id"])
                stats["changed"] += 1
            r.update(card)
        else:
            opts[pick] = card["ai"] = _recase(opts[pick], opts[0], P[a - 1]["t"] if a else "")
            reviews.append({"id": next_id, **card, "s": P[a]["s"], "e": P[b - 1]["e"], "conf": None,
                            "done": False, "homophone": True})
            _swap(P, a, b, opts[pick], next_id)
            next_id += 1
            stats["homophones_changed"] += 1
            stats["changed"] += 1
    return stats
