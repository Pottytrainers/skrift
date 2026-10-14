"""Third opinion: YouTube's own auto-captions (Google's recognizer), free for any YouTube video.

The two local engines are compared first (crosscheck.py). Wherever they disagree, YouTube's
captions break the tie:
    2 of 3 agree     -> settled automatically (shown underlined, one click to undo)
    all 3 disagree   -> stays in the review queue, with YouTube's version as an extra choice
Measured on videos with human-made captions: YouTube alone is a little worse than Parakeet on
studio audio but often better on noisy outdoor footage, and it fails in different places,
which is what makes the vote work.
"""
import json
import re
from difflib import SequenceMatcher

from .crosscheck import _FILLER, _windows
from .words import key, same_meaning

CENSORED = "…"  # YouTube writes swears as "[ __ ]"; kept as a wildcard that matches any one word
# Older YouTube captions drop swears entirely, so a swear only Parakeet heard proves nothing.
_SWEARS = {"fuck", "fucking", "fucked", "fucker", "shit", "shitty", "bullshit", "damn", "ass", "asshole",
           "bitch", "dick", "crap", "hell", "motherfucker", "goddamn", "piss", "pissed", "f"}


_USEFUL_SOUNDS = {"laughter", "music", "applause", "cheering", "screaming", "singing", "crowd cheering", "booing"}


def _is_repeat(new: list[str], before: list[dict], after: list[dict]) -> bool:
    """Is an inserted phrase just a stutter of the words next to it? ("his [his] whole", "I [I] love")"""
    nk = [key(t) for t in new if key(t) and key(t) not in _FILLER]
    if not nk:
        return True
    near = {key(w["t"]) for w in before[-4:] + after[:4]}
    near |= {k[:4] for k in near if len(k) > 4}  # "i love get" vs "I love getting"
    return sum(1 for k in nk if k in near or k[:4] in near) / len(nk) >= 0.6


def youtube_words(path) -> list[dict]:
    """Parse a YouTube json3 caption file into timed words (sound tags and >> marks dropped)."""
    d = json.load(open(path))
    raw = []
    for ev in d.get("events", []):
        t0 = ev.get("tStartMs", 0)
        for sg in ev.get("segs") or []:
            txt = sg.get("utf8", "").replace("\xa0", " ")  # YouTube writes its bleep as "[<nbsp>__<nbsp>]"
            if not txt.strip():
                continue
            txt = txt.replace("[ __ ]", f" {CENSORED} ")
            txt = re.sub(r"\[[^\]]*\]|>>", " ", txt)
            for part in txt.split():
                raw.append({"t": part, "s": (t0 + sg.get("tOffsetMs", 0)) / 1000})
    raw.sort(key=lambda w: w["s"])
    for i, w in enumerate(raw):
        nxt = raw[i + 1]["s"] if i + 1 < len(raw) else w["s"] + 0.6
        w["e"] = round(min(nxt, w["s"] + 1.5), 2)
        w["s"] = round(w["s"], 2)
        w["c"] = 1.0
    return raw


def youtube_events(path) -> tuple[list[float], list[dict]]:
    """Speaker changes (YouTube's ">>") and sound tags ("[laughter]", "[music]") with their times."""
    d = json.load(open(path))
    turns, sounds = [], []
    for ev in d.get("events", []):
        t0 = ev.get("tStartMs", 0)
        for sg in ev.get("segs") or []:
            txt = sg.get("utf8", "").replace("\xa0", " ")
            t = (t0 + sg.get("tOffsetMs", 0)) / 1000
            if ">>" in txt:
                turns.append(round(t, 2))
            for tag in re.findall(r"\[([a-z][a-z ]{2,20})\]", txt.lower()):
                if tag in _USEFUL_SOUNDS:
                    sounds.append({"t": f"[{tag}]", "s": round(t, 2)})
    return turns, sounds


def mark_turns(P: list[dict], turns: list[float]) -> int:
    """Flag the first word of each new speaker turn (w['sp']=1). YouTube's turn times are approximate,
    so a turn snaps to the nearest natural break (end of sentence or a pause) within a second, or is skipped."""
    import bisect
    starts = [w["s"] for w in P]
    n = 0
    for t in turns:
        lo, hi = bisect.bisect_left(starts, t - 1.0), bisect.bisect_right(starts, t + 1.0)
        breaks = [i for i in range(max(lo, 1), min(hi, len(P)))
                  if P[i - 1]["t"].endswith((".", "?", "!", ",")) or P[i]["s"] - P[i - 1]["e"] >= 0.25]
        if breaks:
            i = min(breaks, key=lambda i: abs(P[i]["s"] - t))
            P[i]["sp"] = 1
            n += 1
    return n


def _k(t):
    return "*" if t == CENSORED else key(t)


def _ak(t):
    """Alignment key: swears and YouTube's bleep both become "*", so "fucking" lines up with "[ __ ]"."""
    k = _k(t)
    return "*" if k in _SWEARS or k in ("fuckin", "motherfucking", "motherfuckers", "shits") else k


def _same(a_words: list[str], b_words: list[str]) -> bool:
    """Do two word lists say the same thing? ('gonna'='going to', 'Play Station'='PlayStation', swear wildcard)."""
    ka = [k for k in (_k(t) for t in a_words) if k]
    kb = [k for k in (_k(t) for t in b_words) if k]
    if not ka and not kb:
        return True
    if not ka or not kb:
        return False
    if "*" in ka or "*" in kb:
        return len(ka) == len(kb) and all(x == y or ("*" in (x, y) and (x in _SWEARS or y in _SWEARS or x == y))
                                          for x, y in zip(ka, kb))
    return "".join(ka) == "".join(kb) or same_meaning(" ".join(a_words), " ".join(b_words))


_NEG = {"not", "no", "dont", "didnt", "doesnt", "isnt", "wasnt", "arent", "werent", "cant", "couldnt", "wont",
        "wouldnt", "shouldnt", "never", "nothing", "none", "aint", "nope"}


def _content(words: list[str]) -> list[str]:
    """The words that carry meaning (small words and filler dropped, negations always kept)."""
    out = []
    for t in words:
        k = _ak(t)
        if k and (k not in _FILLER or k in _NEG):
            out.append(k)
    return out


def _same_content(a: list[str], b: list[str]) -> bool:
    """Same meaningful words: Whisper "in a box" and YouTube "on box" agree on "box"."""
    ca, cb = _content(a), _content(b)
    if not ca or len(ca) != len(cb):
        return False
    return all(x == y or "*" in (x, y) for x, y in zip(ca, cb))


def _word_majority(y: list[str], p: list[str], w: list[str]) -> list[str] | None:
    """Word-by-word vote for a stretch the engines split differently ("job. Yo," / "junk." / "junk. Yo,"):
    if every word of YouTube's version is backed by Parakeet or Whisper, and at least one is backed
    by Whisper against Parakeet, YouTube's version is a majority reading. Returns its words or None."""
    yk, pk, wk = [_ak(t) for t in y], [_ak(t) for t in p], [_ak(t) for t in w]
    if not y or len(y) > max(len(p), len(w)) + 1 or not all(yk):
        return None
    by_p, by_w = [False] * len(y), [False] * len(y)
    for src, flags in ((pk, by_p), (wk, by_w)):
        for tag, a1, a2, b1, b2 in SequenceMatcher(None, yk, src, autojunk=False).get_opcodes():
            if tag == "equal":
                for i in range(a1, a2):
                    flags[i] = True
    if not all(a or b for a, b in zip(by_p, by_w)) or not any(b and not a for a, b in zip(by_p, by_w)):
        return None
    out = list(y)
    for i, t in enumerate(y):  # YouTube's bleep: show the word the other engine wrote
        if t == CENSORED:
            src = p if by_p[i] else w
            out[i] = next((s for s in src if _ak(s) == "*"), t)
    return out


def _align(P, Y):
    """Map YouTube's words onto the primary transcript: eq[i] = Y word aligned to P[i],
    grp[i] = replace-group id, ins[i] = Y words inserted just before P[i]."""
    n = len(P)
    eq, grp, ins, groups = [None] * n, [None] * n, [[] for _ in range(n + 1)], []
    for (i1, i2), (j1, j2) in _windows(P, Y):
        pk = [x for x in range(i1, i2) if key(P[x]["t"])]
        yk = [x for x in range(j1, j2) if _k(Y[x]["t"])]
        sm = SequenceMatcher(None, [_ak(P[x]["t"]) for x in pk], [_ak(Y[x]["t"]) for x in yk], autojunk=False)
        for tag, a1, a2, b1, b2 in sm.get_opcodes():
            pidx, yidx = pk[a1:a2], [Y[x]["t"] for x in yk[b1:b2]]
            if tag == "equal":
                for x, t in zip(pidx, yidx):
                    eq[x] = t
            elif tag == "replace":
                groups.append(yidx)
                for x in pidx:
                    grp[x] = len(groups) - 1
            elif tag == "insert":
                at = pk[a1] if a1 < len(pk) else (pk[-1] + 1 if pk else i1)
                ins[min(at, n)].extend(yidx)
    return eq, grp, ins, groups


def _youtube_for(a, b, eq, grp, ins, groups):
    """Candidate readings of what YouTube heard for P[a:b] (with/without boundary insertions)."""
    core, seen = [], set()
    for i in range(a, b):
        if i > a:
            core += ins[i]
        if eq[i] is not None:
            core.append(eq[i])
        elif grp[i] is not None and grp[i] not in seen:
            seen.add(grp[i])
            core += groups[grp[i]]
    before, after = ins[a], ins[b] if b < len(ins) else []
    return [core, before + core, core + after, before + core + after]


def vote(P: list[dict], W: list[dict], reviews: list[dict], Y: list[dict]) -> dict:
    """Settle Parakeet-vs-Whisper disagreements with YouTube's captions. Edits P in place."""
    eq, grp, ins, groups = _align(P, Y)
    stats = {"fixed": 0, "kept": 0, "inserted": 0, "unresolved": 0, "lonely": 0, "youtube_only": 0}
    # phrases only YouTube heard (neither local engine has anything there): offered, never added silently
    yt_only = []
    for i, ws in enumerate(ins):
        words = [t for t in ws if t != CENSORED]
        if sum(1 for t in words if key(t) and key(t) not in _FILLER) < 2 or i >= len(P):
            continue
        t0, t1 = (P[i - 1]["e"] if i else 0.0), P[i]["s"]
        if not any(t0 - 0.2 <= w["s"] <= t1 + 0.2 for w in W):
            yt_only.append((P[i], " ".join(words), t0, t1))
    # work from the end so earlier word indices stay valid while words are swapped in
    for r in sorted(reviews, key=lambda r: (r["a"], r["b"]), reverse=True):
        a, b, kind = r["a"], r["b"], r["kind"]
        ptext = [w["t"] for w in P[a:b]]
        wtext = r["alt"].split()
        if kind == "differ" or kind == "missing":
            cands = _youtube_for(a, b, eq, grp, ins, groups) if kind == "differ" else [ins[a]]
            if kind == "missing":  # YouTube may wrap the same words in extra filler
                ys = ins[a]
                cands += [ys[i:j] for i in range(len(ys)) for j in range(i + 1, len(ys) + 1)
                          if len(ys) - (j - i) <= 3 and all(key(t) in _FILLER for t in ys[:i] + ys[j:])]
            if kind == "missing" and _is_repeat(wtext, P[max(0, a - 4):a], P[a:a + 4]):
                r["done"] = True                                # a stutter: real, but not worth adding
                if P and P[min(a, len(P) - 1)].get("g") == r["id"]:
                    P[min(a, len(P) - 1)].pop("g")
                continue
            match = next((c for c in cands if _same(c, wtext)), None)
            if match is None and kind == "differ" and not any(_same(c, ptext) for c in cands) \
                    and not _same_content(ptext, wtext):
                # no exact agreement: do Whisper and YouTube at least agree on the words that matter?
                match = next((c for c in cands if _same_content(c, wtext) and not _same_content(c, ptext)), None)
            if match is not None:                               # Whisper + YouTube vs Parakeet
                new = [dict(W[x], ok=True, r=r["id"]) for x in r["wi"]]
                if len(match) == len(new):  # Whisper sometimes lowercases names ("skyrim"); borrow YouTube's capitals
                    for w, yt in zip(new, match):
                        if key(w["t"]) == key(yt) and w["t"][:1].islower() and yt[:1].isupper():
                            w["t"] = yt[:1] + w["t"][1:]
                elif new and CENSORED not in match and any(t[:1].isupper() for t in match):
                    # same words, split differently ("FlightsOfFancy" vs "Flights of Fancy"): use YouTube's spacing
                    s0, e0 = new[0]["s"], new[-1]["e"]
                    new = [{"t": t, "s": round(s0 + (e0 - s0) * k / len(match), 2),
                            "e": round(s0 + (e0 - s0) * (k + 1) / len(match), 2), "c": 1.0, "ok": True, "r": r["id"]}
                           for k, t in enumerate(match)]
                # keep timestamps in order: Whisper's timing can start a hair before the previous word
                lo = P[a - 1]["s"] if a > 0 else 0.0
                hi = P[b]["s"] if b < len(P) else float("inf")
                for w in new:
                    w["s"] = round(min(max(w["s"], lo), hi), 2)
                    w["e"] = round(max(w["e"], w["s"] + 0.05), 2)
                    lo = w["s"]
                for w in new:
                    w.pop("g", None)
                g_old = P[a].get("g") if kind == "differ" and a < len(P) else None
                if g_old is not None and new:  # keep another review's "missed words" marker in place
                    new[0]["g"] = g_old
                if a < len(P) and P[a].get("p") and new:
                    new[0]["p"] = 1
                    if kind == "missing":
                        P[a].pop("p", None)
                if kind == "missing" and a < len(P) and P[a].get("g") == r["id"]:
                    P[a].pop("g")
                P[a:b] = new
                r.update(kind="voted", alt=" ".join(ptext), done=False, weak=False, was=kind)
                r.pop("weak")
                stats["inserted" if kind == "missing" else "fixed"] += 1
            elif kind == "differ" and not any(_same(c, ptext) for c in cands) and \
                    (wm := next((m for m in (_word_majority(c, ptext, wtext) for c in cands) if m), None)):
                s0, e0 = P[a]["s"], P[b - 1]["e"]
                new = [{"t": t, "s": round(s0 + (e0 - s0) * k / len(wm), 2), "e": round(s0 + (e0 - s0) * (k + 1) / len(wm), 2),
                        "c": 1.0, "ok": True, "r": r["id"]} for k, t in enumerate(wm)]
                for k_ in ("p", "g", "sp"):
                    if P[a].get(k_) is not None:
                        new[0][k_] = P[a][k_]
                P[a:b] = new
                r.update(kind="voted", alt=" ".join(ptext), done=False, was=kind)
                stats["fixed"] += 1
            elif kind == "differ" and any(_same(c, ptext) for c in cands):   # Parakeet + YouTube
                r["done"] = True
                r["voted_keep"] = True
                for w in P[a:b]:
                    w["ok"] = True
                stats["kept"] += 1
            elif kind == "differ":                              # three different answers
                yt = max(cands, key=len)
                if yt and not _same(yt, wtext) and not _same(yt, ptext):
                    r["alt2"] = " ".join(yt)
                stats["unresolved"] += 1
            elif kind == "missing" and not r.get("weak"):       # only Whisper heard it: probably not said
                r["done"] = True
                if a < len(P) and P[a].get("g") == r["id"]:
                    P[a].pop("g")
        elif kind == "single":
            cands = _youtube_for(a, b, eq, grp, ins, groups)
            if any(_same(c, ptext) for c in cands):             # Parakeet + YouTube: it was said
                r["done"] = True
                for w in P[a:b]:
                    w["ok"] = True
            elif not any(c for c in cands) and any(key(t) not in _FILLER and key(t) not in _SWEARS for t in ptext):
                r["kind"] = "lonely"                            # neither other engine heard real words here
                stats["lonely"] += 1
    next_id = max((r["id"] for r in reviews), default=-1) + 1
    where = {id(w): i for i, w in enumerate(P)}
    for anchor, text, t0, t1 in yt_only:
        i = where.get(id(anchor))
        if i is None or P[i].get("g") is not None:
            continue
        reviews.append({"id": next_id, "kind": "missing", "src": "youtube", "a": i, "b": i, "alt": text,
                        "s": round(t0, 2), "e": round(t1, 2), "conf": None, "done": False})
        P[i]["g"] = next_id
        next_id += 1
        stats["youtube_only"] += 1
    reviews[:] = [r for r in reviews if not r.get("weak")]
    return stats
