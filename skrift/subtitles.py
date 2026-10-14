"""Builds broadcast-style subtitle cues and readable paragraphs from timed words.

Cue rules (Netflix/BBC-style): <= 42 characters per line, <= 2 lines,
<= 7 s on screen, >= 0.8 s minimum, break after sentence ends and long pauses,
prefer line breaks after punctuation and before conjunctions.
"""
MAX_LINE = 42
MAX_LINES = 2
MAX_DUR = 7.0
MIN_DUR = 0.8
PAUSE_BREAK = 0.8
_ENDS = (".", "?", "!", "…")
_SOFT = (",", ";", ":", "—", "–")
_WEAK = {"the", "a", "an", "and", "of", "to", "in", "for", "with", "my", "your", "his", "her", "their", "our",
         "its", "at", "on", "from", "by", "but", "or", "that", "this", "is", "was", "i", "we", "you", "new"}
_LEAD = {"and", "but", "or", "so", "because", "that", "which", "who", "when", "if", "to", "the", "a"}


def _text(words):
    return " ".join(w["t"] for w in words)


def _split_lines(words):
    """Best 2-line split of a cue: balanced, preferring punctuation/conjunction boundaries."""
    text = _text(words)
    if len(text) <= MAX_LINE:
        return [text]
    best, best_cost = None, 1e9
    for i in range(1, len(words)):
        a, b = _text(words[:i]), _text(words[i:])
        if len(a) > MAX_LINE or len(b) > MAX_LINE:
            continue
        cost = abs(len(a) - len(b))
        if words[i - 1]["t"].endswith(_SOFT + _ENDS):
            cost -= 15
        elif words[i]["t"].lower() in _LEAD:
            cost -= 6
        if cost < best_cost:
            best, best_cost = [a, b], cost
    return best or [text]


def cues(words: list[dict]) -> list[dict]:
    out, cur = [], []

    def flush():
        if cur:
            out.append({"s": cur[0]["s"], "e": cur[-1]["e"], "lines": _split_lines(cur), "w": list(cur)})
            cur.clear()

    for i, w in enumerate(words):
        if cur and w.get("sp"):  # one speaker per subtitle
            flush()
        if cur:
            gap = w["s"] - cur[-1]["e"]
            lines = _split_lines(cur + [w])
            too_long = len(lines) > MAX_LINES or any(len(ln) > MAX_LINE for ln in lines)
            too_slow = w["e"] - cur[0]["s"] > MAX_DUR
            sentence_done = cur[-1]["t"].endswith(_ENDS) and len(_text(cur)) > 12
            if too_long or too_slow or gap > PAUSE_BREAK or sentence_done:
                # don't end a cue on "the" / "and" / "of": carry those words into the next cue
                if (too_long or too_slow) and len(cur) > 4 and cur[-1]["t"].lower() in _WEAK:
                    k = len(cur) - 1
                    while k > 2 and cur[k - 1]["t"].lower() in _WEAK:
                        k -= 1
                    carry = cur[k:]
                    del cur[k:]
                    flush()
                    cur.extend(carry)
                    cur.append(w)
                    continue
                # avoid leaving one dangling word: back off to the last soft break if it's close
                if too_long and len(cur) > 4:
                    for k in range(len(cur) - 1, len(cur) - 4, -1):
                        if cur[k - 1]["t"].endswith(_SOFT):
                            carry = cur[k:]
                            del cur[k:]
                            flush()
                            cur.extend(carry)
                            break
                    else:
                        flush()
                else:
                    flush()
        cur.append(w)
    flush()
    out = _merge_fragments(out)
    # timing polish: minimum duration, small lead-out, never overlap the next cue
    for i, c in enumerate(out):
        nxt = out[i + 1]["s"] if i + 1 < len(out) else c["e"] + 2
        c["e"] = min(max(c["e"] + 0.15, c["s"] + MIN_DUR), nxt - 0.02)
        c["e"] = max(c["e"], min(c["s"] + 0.3, nxt - 0.02))  # never run into the next cue
        c.pop("w", None)
    return out


def _merge_fragments(out: list[dict]) -> list[dict]:
    """A one- or two-word subtitle ("Yeah.", "And") flashes by too fast to read. Merge it into its
    neighbour: same speaker -> one subtitle; different speakers -> two lines with dialogue dashes."""
    def short(c):
        return len(c["w"]) <= 2 or c["w"][-1]["e"] - c["w"][0]["s"] < 0.8

    def join(c1, c2):
        if c2["w"][0]["s"] - c1["w"][-1]["e"] > 1.0 or c2["w"][-1]["e"] - c1["w"][0]["s"] > MAX_DUR:
            return None
        if any(len(ln) > MAX_LINE for ln in c1["lines"] + c2["lines"]) or \
                any(ln.startswith("- ") for ln in c1["lines"] + c2["lines"]):
            return None
        words = c1["w"] + c2["w"]
        if c2["w"][0].get("sp"):  # two speakers: one line each, broadcast-style dashes
            t1, t2 = "- " + _text(c1["w"]), "- " + _text(c2["w"])
            if len(c1["lines"]) > 1 or len(c2["lines"]) > 1 or len(t1) > MAX_LINE or len(t2) > MAX_LINE:
                return None
            lines = [t1, t2]
        else:
            lines = _split_lines(words)
            if len(lines) > MAX_LINES or any(len(ln) > MAX_LINE for ln in lines):
                return None
        return {"s": c1["s"], "e": c2["e"], "lines": lines, "w": words}

    i = 0
    while i < len(out):
        c = out[i]
        if short(c):
            if i + 1 < len(out) and (m := join(c, out[i + 1])):
                out[i:i + 2] = [m]
                continue
            if i > 0 and (m := join(out[i - 1], c)):
                out[i - 1:i + 1] = [m]
                i -= 1
                continue
        i += 1
    return out


def paragraphs(words: list[dict], max_words: int = 90, pause: float = 1.6) -> list[tuple[int, int]]:
    """Group words into reading paragraphs: new paragraph after a long pause, or after a
    sentence end once the paragraph is getting long. Returns (start, end) word indices."""
    out, start = [], 0
    for i in range(1, len(words) + 1):
        if i == len(words):
            out.append((start, i))
            break
        n = i - start
        gap = words[i]["s"] - words[i - 1]["e"]
        ended = words[i - 1]["t"].endswith(_ENDS)
        if (words[i].get("sp") and n >= 8 and ended) or (gap >= pause and n >= 8) or (ended and n >= max_words * 0.6 and gap > 0.35) or (ended and n >= max_words) or n >= max_words * 1.6:
            out.append((start, i))
            start = i
    return out


def add_sounds(cs: list[dict], sounds: list[dict]) -> list[dict]:
    """Put sound tags ("[laughter]") into the subtitles: inside a cue if there's room, else as their own cue."""
    out = [dict(c, lines=list(c["lines"])) for c in cs]
    for snd in sounds:
        t, tag = snd["s"], snd["t"]
        host = next((c for c in out if c["s"] - 0.2 <= t <= c["e"]), None)
        if host:
            if tag in " ".join(host["lines"]):
                continue
            if len(host["lines"][-1]) + len(tag) + 1 <= MAX_LINE:
                host["lines"][-1] += " " + tag
            elif len(host["lines"]) < MAX_LINES:
                host["lines"].append(tag)
            continue
        nxt = min((c["s"] for c in out if c["s"] > t), default=t + 2)
        if nxt - t >= 0.5:
            out.append({"s": t, "e": min(t + 1.5, nxt - 0.05), "lines": [tag]})
    return sorted(out, key=lambda c: c["s"])


def ts(sec: float, sep: str = ",", hours: bool = True) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}" if hours else f"{m:02d}:{s:02d}{sep}{ms:03d}"


def clock(sec: float) -> str:
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
