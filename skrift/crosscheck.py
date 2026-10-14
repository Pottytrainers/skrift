"""Cross-checks the primary transcript (Parakeet) against an independent second
engine (Whisper) and turns every disagreement into a review item.

Where both engines heard the same word it is marked confirmed. Anything else
becomes a review item the editor highlights:
    differ   - the engines heard different words here        (alt = Whisper's words)
    single   - only the primary engine heard these words
    missing  - only Whisper heard words here (not inserted)    (alt = Whisper's words)
    spelling - auto-corrected to a name from the vocabulary   (alt = original text)
"""
from difflib import SequenceMatcher

from wordfreq import zipf_frequency

from .words import _CLITIC, base_key, key, same_meaning

WINDOW = 120.0  # seconds; alignment runs window by window so 8-hour streams stay fast


def _windows(P, W):
    """Yield (p_slice, w_slice) chunks split at moments of silence in both transcripts."""
    i = j = 0
    while i < len(P) or j < len(W):
        start = P[i]["s"] if i < len(P) else W[j]["s"]
        target = start + WINDOW
        # first gap of >= 0.6 s in the primary transcript after the target time
        k = i
        while k < len(P) and not (P[k]["s"] >= target and k > i and P[k]["s"] - P[k - 1]["e"] >= 0.6):
            k += 1
        cut = (P[k - 1]["e"] + P[k]["s"]) / 2 if k < len(P) else float("inf")
        m = j
        while m < len(W) and W[m]["s"] < cut:
            m += 1
        yield (i, k), (j, m)
        i, j = k, m


def crosscheck(P: list[dict], W: list[dict], names: list[str] = ()) -> list[dict]:
    """Annotates P in place and returns review items.
    w['ok']=True  both engines heard this word
    w['r']=id     word belongs to review item `id`
    w['g']=id     review item `id` (Whisper-only words) sits just before this word"""
    reviews = []
    name_keys = {key(t) for n in names for t in n.split() if key(t)}

    def add(kind, a, b, alt="", conf=None, wi=(), weak=False):
        rid = len(reviews)
        s = P[a]["s"] if a < len(P) else (P[-1]["e"] if P else 0)
        e = P[b - 1]["e"] if b > a else s
        reviews.append({"id": rid, "kind": kind, "a": a, "b": b, "alt": alt,
                        "s": s, "e": e, "conf": conf, "done": weak, "wi": list(wi)})
        if weak:  # kept only so a third engine can vote for it; never shown on its own
            reviews[-1]["weak"] = True
            return rid
        for x in range(a, b):
            P[x]["r"] = rid
        if kind == "missing" and P:
            P[min(a, len(P) - 1)]["g"] = rid
        return rid

    for (i1, i2), (j1, j2) in _windows(P, W):
        pk = [x for x in range(i1, i2) if key(P[x]["t"])]
        wk = [x for x in range(j1, j2) if key(W[x]["t"])]
        sm = SequenceMatcher(None, [key(P[x]["t"]) for x in pk], [key(W[x]["t"]) for x in wk], autojunk=False)
        for tag, a1, a2, b1, b2 in sm.get_opcodes():
            pidx, widx = pk[a1:a2], wk[b1:b2]
            ptext = " ".join(P[x]["t"] for x in pidx)
            wtext = " ".join(W[x]["t"] for x in widx)
            if tag == "equal" or (tag == "replace" and same_meaning(ptext, wtext)):
                for x in pidx:
                    P[x]["ok"] = True
                continue
            wconf = round(min(W[x]["c"] for x in widx), 3) if widx else None
            if tag == "replace":
                add("differ", pidx[0], pidx[-1] + 1, wtext, wconf, widx)
            elif tag == "delete":
                add("single", pidx[0], pidx[-1] + 1)
            else:  # insert: Whisper heard something the primary didn't
                at = pk[a1] if a1 < len(pk) else (pk[-1] + 1 if pk else i1)
                # Whisper is hinted with the names list and sometimes just says the names back
                only_names = bool(name_keys) and all(key(W[x]["t"]) in name_keys for x in widx if key(W[x]["t"]))
                add("missing", at, at, wtext, wconf, widx, weak=only_names or not _plausible(W, widx))
    return reviews


def low_confidence(P: list[dict], threshold: float = 0.35) -> list[dict]:
    """Single-engine (Whisper) transcripts: flag runs of words Whisper itself was unsure of.
    (Parakeet's confidence isn't used: its mistakes were usually still 0.9+.)"""
    reviews, i = [], 0
    while i < len(P):
        if P[i]["c"] < threshold and key(P[i]["t"]):
            j = i
            while j + 1 < len(P) and P[j + 1]["c"] < threshold:
                j += 1
            rid = len(reviews)
            reviews.append({"id": rid, "kind": "unsure", "a": i, "b": j + 1, "alt": "",
                            "s": P[i]["s"], "e": P[j]["e"], "conf": P[i]["c"], "done": False})
            for x in range(i, j + 1):
                P[x]["r"] = rid
            i = j + 1
        else:
            i += 1
    return reviews


# Whisper's well-known phantom phrases on music/silence (it learned them from YouTube credits).
_PHANTOMS = ("thank you", "thanks for watching", "subscribe", "to be continued", "zzz",
             "subtitles by", "please like", "see you next time", "bye")


# Filler and small function words. A Whisper-only span made of nothing but these
# ("um", "like", "I I") turned out to be a real omission only ~4% of the time.
_FILLER = set("""a an the and or but so of to in on at for with from by it its is was were be been
are am i im ive ill id you youre he she we they theyre that thats this there do did dont have has had
not no yes yeah yep yup oh ah uh um uhm hmm mm mhm like just really kinda gonna okay ok right well
then now what cool wow man dude me my your if as all would will can could cause because one""".split())


def _plausible(W, widx) -> bool:
    """Is a Whisper-only span worth showing? Drops filler, low confidence and stock hallucinations."""
    text = " ".join(W[x]["t"] for x in widx).lower()
    mean = sum(W[x]["c"] for x in widx) / len(widx)
    keys = [key(W[x]["t"]) for x in widx]
    if all(k in _FILLER for k in keys if k):
        return False
    return mean >= 0.5 and not any(p in text for p in _PHANTOMS)


def skipped_spans(P: list[dict], W: list[dict], min_words: int = 6, pad: float = 1.5) -> list[tuple[float, float]]:
    """Stretches the primary engine heard but Whisper skipped entirely (a known Whisper habit).
    These get a second, careful Whisper pass so they can be cross-checked too."""
    Pc = [dict(w) for w in P]
    spans = []
    for r in crosscheck(Pc, W):
        if r["kind"] == "single" and r["b"] - r["a"] >= min_words:
            spans.append((max(0.0, r["s"] - pad), r["e"] + pad))
    return spans


def merge_second_chance(W: list[dict], spans, W2: list[dict]) -> list[dict]:
    """Swap Whisper's words inside the re-run spans for the second-chance words."""
    inside = lambda w: any(a <= w["s"] < b for a, b in spans)  # noqa: E731
    return sorted([w for w in W if not inside(w)] + [w for w in W2 if inside(w)], key=lambda w: w["s"])


# ---------------------------------------------------------------- vocabulary

def _common(word: str) -> float:
    return zipf_frequency(word, "en")


def vocab_terms(texts: list[str]) -> dict[str, str]:
    """Pull rare words (names, game titles) out of the title, description,
    chapter names and the user's own word list. Returns {key: display spelling}."""
    import re
    out = {}
    for text in texts:
        text = re.sub(r"(https?://|www\.)\S+|\S+@\S+|\S+\.(com|net|org|tv|gg|io|ly|co)\b\S*", " ", text or "")  # links aren't speech
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9'’\-]*[A-Za-z0-9]", text or ""):
            for part in raw.split("-"):
                part = re.sub(r"['’]s$", "", part)  # Kaya's -> Kaya
                k = key(part)
                if len(k) >= 4 and _common(k) < 3.5 and not k.startswith("http"):
                    # keep the nicest-looking spelling (Title Case beats ALL CAPS)
                    if k not in out or (out[k].isupper() and not part.isupper()):
                        out[k] = part
    return out


def _next_id(reviews):
    return max((r["id"] for r in reviews), default=-1) + 1


def names_break_ties(P: list[dict], reviews: list[dict], phrases: list[str], third: str = "YouTube") -> int:
    """Unresolved disagreements involving a name from the user's list.
    - An engine's version that contains the name once wins, if its other words are backed by another
      engine ("This is ACME" + "TV" when YouTube heard "This is Acne TV") -> "This is ACME TV".
    - Whisper is given the names list as a hint, and sometimes just repeats it ("ACME TV, ACME TV,
      ACME TV,"): that's a hallucination, so that version is dropped from the choices."""
    targets = {"".join(base_key(t) for t in ph.split()): ph for ph in phrases}
    targets.pop("", None)
    fixed = 0
    for r in reviews:
        if r["done"] or r["kind"] != "differ":
            continue
        idx = [i for i, w in enumerate(P) if w.get("r") == r["id"]]
        if not idx or idx[-1] - idx[0] + 1 != len(idx):
            continue
        a, b = idx[0], idx[-1] + 1
        ptoks = [w["t"] for w in P[a:b]]
        versions = [("alt", r.get("alt")), ("alt2", r.get("alt2"))]
        for field, alt in versions:  # drop "ACME TV, ACME TV, ACME TV,"
            joined = "".join(base_key(t) for t in (alt or "").split())
            if alt and any(joined.count(tg) >= 2 for tg in targets):
                if field == "alt":
                    alt2 = r.pop("alt2", None)
                    r["alt"] = alt2 or ""
                    if alt2:
                        r["alt_from"] = third
                else:
                    r.pop("alt2", None)
        found = None
        for field, other in (("alt", "alt2"), ("alt2", "alt")):
            toks = (r.get(field) or "").split()
            if not toks:
                continue
            backing = {base_key(t) for t in (r.get(other) or "").split() + ptoks}
            for kl in range(0, 3):
                for kr in range(0, 3):
                    if a - kl < 0 or b + kr > len(P) or any(w.get("r") is not None for w in P[a - kl:a] + P[b:b + kr]):
                        continue
                    ext = [w["t"] for w in P[a - kl:a]] + toks + [w["t"] for w in P[b:b + kr]]
                    keys = [base_key(t) for t in ext]
                    for i in range(len(ext)):
                        for j in range(i + 1, min(len(ext), i + 5) + 1):
                            tg = "".join(keys[i:j])
                            if tg not in targets or j <= kl or i >= kl + len(toks):
                                continue  # the name must use at least one of this engine's words
                            rest = [k for n, k in enumerate(keys) if (n < i or n >= j) and kl <= n < kl + len(toks) and k]
                            if all(k in backing for k in rest):  # the engine's other words are backed up
                                found = (a - kl, b + kr, ext, i, j, tg)
                                break
                        if found:
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if not found:
            found = _hybrid_name(P, r, a, b, targets)
        if not found:
            continue
        a2, b2, ext, i, j, tg = found
        tail = ext[j - 1][len(ext[j - 1].rstrip(".,!?;:\"')”")):]
        toks = ext[:i] + targets[tg].split() + ext[j:]
        if j - 1 < len(ext) and tail:
            k = i + len(targets[tg].split()) - 1
            toks[k] += tail
        s0, e0 = P[a2]["s"], P[b2 - 1]["e"]
        new = [{"t": t, "s": round(s0 + (e0 - s0) * k / len(toks), 2), "e": round(s0 + (e0 - s0) * (k + 1) / len(toks), 2),
                "c": 1.0, "ok": True, "r": r["id"]} for k, t in enumerate(toks)]
        for k_ in ("p", "g", "sp"):
            if P[a2].get(k_) is not None:
                new[0][k_] = P[a2][k_]
        r.update(kind="spelling", alt=" ".join(w["t"] for w in P[a2:b2]))
        r.pop("alt2", None)
        P[a2:b2] = new
        fixed += 1
    return fixed


def _hybrid_name(P, r, a, b, targets):
    """Whisper (given the names list) spelled the name right but misheard the words around it;
    YouTube got the words but mangled the name: "Nets is ACME TV" + "This is Acne TV" -> "This is ACME TV"."""
    w_toks, y_toks = (r.get("alt") or "").split(), (r.get("alt2") or "").split()
    if not w_toks or not y_toks or r.get("alt_from"):
        return None
    for kl in range(0, 3):
        for kr in range(0, 3):
            if a - kl < 0 or b + kr > len(P) or any(w.get("r") is not None for w in P[a - kl:a] + P[b:b + kr]):
                continue
            left, right = [w["t"] for w in P[a - kl:a]], [w["t"] for w in P[b:b + kr]]
            ew = left + w_toks + right
            kw = [base_key(t) for t in ew]
            tg = next(("".join(kw[i:j]) for i in range(len(ew)) for j in range(i + 1, min(len(ew), i + 4) + 1)
                       if "".join(kw[i:j]) in targets and j > kl and i < kl + len(w_toks)), None)
            if not tg:
                continue
            name = targets[tg].split()
            first, last = base_key(name[0]), base_key(name[-1])
            ey = left + y_toks + right
            ky = [base_key(t) for t in ey]
            best = None
            for i in range(len(ey)):
                for j in range(i + 1, min(len(ey), i + 4) + 1):
                    run = "".join(ky[i:j])
                    if not run or (ky[i] != first and ky[j - 1] != last and ky[i][:1] != first[:1]):
                        continue
                    sim = SequenceMatcher(None, run, tg).ratio()
                    if sim >= 0.7 and (best is None or sim > best[0]):
                        best = (sim, i, j)
            if best:
                return (a - kl, b + kr, ey, best[1], best[2], tg)
    return None


def apply_phrases(P: list[dict], reviews: list[dict], phrases: list[str]) -> int:
    """Multi-word names from the user's names list: "Acne TV" / "Ack Me TV" -> "ACME TV".
    Needs ~85%+ letter similarity AND one word matching exactly ("TV"), so "acne cream" is left alone."""
    fixed = 0
    for phrase in phrases:
        toks = phrase.split()
        pkeys = [key(t) for t in toks if key(t)]
        target = "".join(pkeys)
        if len(target) < 5 or (len(toks) == 1 and len(target) < 6):
            continue
        # also try a single squashed word ("AckMeTV") for multi-word names
        sizes = sorted({max(2, len(toks) - 1), len(toks), len(toks) + 1} | ({1} if len(toks) > 1 else set()), reverse=True)
        i = 0
        while i < len(P):
            done = False
            best = None  # (similarity, n): the closest-matching word run starting here
            for n in sizes:
                gram = P[i:i + n]
                if len(gram) < n or any(w["t"].endswith((".", "?", "!")) for w in gram[:-1]):
                    continue
                gkeys = [base_key(w["t"]) for w in gram]
                joined = "".join(gkeys)
                if not gkeys[0] or not gkeys[-1] or gkeys[0][0] != target[0] or gkeys[-1][-1] != target[-1]:
                    continue  # must start and end like the name ("on ACME TV" must not eat "on")
                anchored = gkeys[0] == pkeys[0] or gkeys[-1] == pkeys[-1] or joined == target
                sim = SequenceMatcher(None, joined, target).ratio()
                if anchored and sim >= 0.85 and (best is None or sim > best[0]):
                    best = (sim, n)
            for n in ([best[1]] if best else []):
                gram = P[i:i + n]
                core = " ".join(_CLITIC.sub("", w["t"].strip("\"'(“.,!?;:)”")) for w in gram)
                if core == phrase:
                    continue
                last = gram[-1]["t"]
                tail = last[len(last.rstrip(".,!?;:\"')”")):]
                clitic = _CLITIC.search(last.rstrip(".,!?;:\"')”"))
                lead = gram[0]["t"][:len(gram[0]["t"]) - len(gram[0]["t"].lstrip("\"'(“"))]
                s, e = gram[0]["s"], gram[-1]["e"]
                rid = _next_id(reviews)
                reviews.append({"id": rid, "kind": "spelling", "a": i, "b": i + len(toks),
                                "alt": " ".join(w["t"] for w in gram), "s": s, "e": e, "conf": None, "done": False})
                new = []
                for k, t in enumerate(toks):
                    w = {"t": t, "s": round(s + (e - s) * k / len(toks), 2),
                         "e": round(s + (e - s) * (k + 1) / len(toks), 2), "c": 1.0, "r": rid}
                    new.append(w)
                new[0]["t"] = lead + new[0]["t"]
                new[-1]["t"] += (clitic.group(0) if clitic else "") + tail
                for key_ in ("p", "g"):
                    if gram[0].get(key_) is not None:
                        new[0][key_] = gram[0][key_]
                if all(w.get("ok") for w in gram):
                    for w in new:
                        w["ok"] = True
                P[i:i + n] = new
                fixed += 1
                i += len(new)
                done = True
                break
            if not done:
                i += 1
    return fixed


def apply_vocab(P: list[dict], reviews: list[dict], vocab: dict[str, str], W_alt=None) -> int:
    """Fix near-miss spellings of vocabulary names ('Dondori' -> 'Dandori').
    Only touches words that are not normal English, so real words are never changed."""
    if not vocab:
        return 0
    keys = list(vocab)
    fixed = 0
    for i, w in enumerate(P):
        k = base_key(w["t"])  # "Ugreen's" is checked as "ugreen"
        if len(k) < 4 or k in vocab or _common(k) >= 2.1:  # real English words are never "corrected"
            continue
        best, score = None, 0.0
        for v in keys:
            if abs(len(v) - len(k)) > 2 or v[0] != k[0]:
                continue
            r = SequenceMatcher(None, k, v).ratio()
            if r > score:
                best, score = v, r
        if best and score >= 0.8:
            spelled = vocab[best]
            if spelled.isupper() and len(spelled) > 3:   # MECCHA -> Meccha
                spelled = spelled.capitalize()
            core = w["t"].strip("\"'(“.,!?;:)”")
            if spelled.islower() and core[:1].isupper():  # keep the transcript's capital letter
                spelled = spelled.capitalize()
            lead = len(w["t"]) - len(w["t"].lstrip("\"'(“"))
            trail = len(w["t"].rstrip(".,!?;:\"')”"))
            clitic = _CLITIC.search(w["t"][lead:trail])  # keep "'s", "'ll" … on the end
            new = w["t"][:lead] + spelled + (clitic.group(0) if clitic else "") + w["t"][trail:]
            if new != w["t"]:
                prev_f = w.get("r")
                prev = next((r for r in reviews if r["id"] == prev_f), None)
                if prev is not None and prev["kind"] == "differ" and sum(1 for x in P if x.get("r") == prev_f) == 1:
                    prev["done"] = True  # the vocabulary settles a one-word disagreement
                rid = _next_id(reviews)
                reviews.append({"id": rid, "kind": "spelling", "a": i, "b": i + 1, "alt": w["t"],
                                "s": w["s"], "e": w["e"], "conf": None, "done": False})
                w["t"], w["r"] = new, rid
                fixed += 1
    return fixed
