"""Word-level helpers shared by the engines, the cross-check and the exporters.

A transcript is a flat list of word dicts:
    {"t": display text incl. punctuation, "s": start sec, "e": end sec, "c": confidence 0..1}
plus optional review fields added by crosscheck.py.
"""
import re
import unicodedata
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from whisper_normalizer.english import EnglishTextNormalizer

_norm = EnglishTextNormalizer()
_PUNCT_ONLY = re.compile(r"^[^\w]+$", re.UNICODE)
_OPENERS = tuple("£$€¥₹([“\"")  # Parakeet often emits these without the space before them
_CLITIC = re.compile(r"(['’](s|ll|re|ve|d|m)|n['’]t)$", re.I)


def key(text: str) -> str:
    """Loose comparison key for one word: lowercase letters/digits only."""
    t = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    return re.sub(r"[^a-z0-9]", "", t)


def base_key(text: str) -> str:
    """key() without possessive/contraction endings: "Kaya's" -> "kaya", "they'll" -> "they"."""
    t = re.sub(r"[^\w'’]+$", "", unicodedata.normalize("NFKC", text))
    return key(_CLITIC.sub("", t))


_SPOKEN = re.compile(r"\b(cuz|cause|coz|bc)\b")


def same_meaning(a: str, b: str) -> bool:
    """True when two spans differ only in spelling convention:
    'gonna'/'going to', '12th'/'twelfth', 'ok'/'okay', 'touch screen'/'touchscreen', 'cuz'/'because'."""
    na, nb = _SPOKEN.sub("because", _norm(a)), _SPOKEN.sub("because", _norm(b))
    return na == nb or na.replace(" ", "") == nb.replace(" ", "")


def parakeet_words(tokens) -> list[dict]:
    """Parakeet emits sub-word pieces (' don', "'", 't', '.'); a leading space starts a new word."""
    words: list[dict] = []
    pending_space = False  # a bare ' ' token (e.g. before a digit) starts the next word
    for tok in tokens:
        txt = tok.text
        if not txt.strip():
            pending_space = True
            continue
        if pending_space:
            txt, pending_space = " " + txt.lstrip(), False
        core = txt.strip()
        starts_word = (txt.startswith(" ") and (not _PUNCT_ONLY.match(core) or core.startswith(_OPENERS))) or \
            (core.startswith(_OPENERS) and words and words[-1]["t"][-1:].isalnum())
        if starts_word or not words:
            words.append({"t": txt.strip(), "s": tok.start, "e": tok.end, "_c": [tok.confidence]})
        else:
            w = words[-1]
            w["t"] += txt.strip() if txt.startswith(" ") else txt
            w["e"] = max(w["e"], tok.end)
            if not _PUNCT_ONLY.match(txt.strip()):  # a '.' token shouldn't lower the word's confidence
                w["_c"].append(tok.confidence)
    for w in words:
        w["c"] = round(min(w.pop("_c")), 3)
        w["s"], w["e"] = round(w["s"], 2), round(w["e"], 2)
    return words


def whisper_words(segments) -> list[dict]:
    out = []
    for seg in segments:
        for w in seg.get("words", []):
            t = w["word"].strip()
            if t:
                out.append({"t": t, "s": round(w["start"], 2), "e": round(w["end"], 2),
                            "c": round(w["probability"], 3)})
    return out


def tidy(words: list[dict]) -> None:
    """Small written-style fixes: "$10 bucks" -> "10 bucks" (engines write the $ and say "bucks")."""
    for i, w in enumerate(words[:-1]):
        if re.match(r"^\$\d", w["t"]) and key(words[i + 1]["t"]) in ("bucks", "dollars", "buck", "dollar"):
            w["t"] = w["t"][1:]


def plain_text(words: list[dict]) -> str:
    return " ".join(w["t"] for w in words)
