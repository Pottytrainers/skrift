"""Turns a saved transcript into downloadable files."""
import io
import json
import re
from datetime import datetime

from .subtitles import add_sounds, clock, cues, paragraphs, ts

FORMATS = {
    "srt": ("SubRip subtitles", "application/x-subrip"),
    "vtt": ("WebVTT subtitles", "text/vtt"),
    "txt": ("Plain text", "text/plain"),
    "docx": ("Word document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": ("PDF", "application/pdf"),
    "md": ("Markdown", "text/markdown"),
    "json": ("JSON (word timings)", "application/json"),
}


def _paras(tr):
    """Paragraphs as stored by the editor (word['p'] marks a paragraph start)."""
    words = tr["words"]
    if not words:
        return []
    if not any(w.get("p") for w in words):
        return [(words[a:b]) for a, b in paragraphs(words)]
    out, cur = [], []
    for w in words:
        if w.get("p") and cur:
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def _ptext(ws):
    return " ".join(w["t"] for w in ws if w["t"])


def _meta_lines(job):
    m = job.get("meta", {})
    lines = []
    if m.get("channel"):
        lines.append(m["channel"])
    if m.get("webpage_url"):
        lines.append(m["webpage_url"])
    bits = []
    if m.get("duration"):
        bits.append(f"Length {clock(m['duration'])}")
    if m.get("upload_date"):
        bits.append(f"Published {m['upload_date']}")
    bits.append("Transcribed " + datetime.fromtimestamp(job.get("finished") or job["created"]).strftime("%Y-%m-%d"))
    lines.append(" · ".join(bits))
    return lines


_SWEAR = re.compile(r"^(\W*)((?:mother)?fuck\w*|shit\w*|bullshit\w*|bitch\w*|asshole\w*|dick|dicks|cunt\w*|"
                    r"goddamn\w*|damn\w*|pussy)(\W*)$", re.I)


def bleep(t: str) -> str:
    """"fucking," -> "f***ing,"-style: keeps the first letter so it still reads naturally."""
    m = _SWEAR.match(t)
    if not m:
        return t
    word = m.group(2)
    return m.group(1) + word[0] + "*" * (len(word) - 1) + m.group(3)


def render(fmt: str, job: dict, tr: dict, timestamps: bool = True, sounds: bool = True, bleeped: bool = False) -> bytes:
    if bleeped:
        tr = {**tr, "words": [dict(w, t=bleep(w["t"])) for w in tr["words"]]}
    words = [w for w in tr["words"] if w.get("t")]
    title = job.get("meta", {}).get("title", "Transcript")
    subs = cues(words) if fmt in ("srt", "vtt") else []
    if subs and sounds and tr.get("sounds"):
        subs = add_sounds(subs, tr["sounds"])
    if fmt == "srt":
        out = []
        for i, c in enumerate(subs, 1):
            out.append(f"{i}\n{ts(c['s'])} --> {ts(c['e'])}\n" + "\n".join(c["lines"]) + "\n")
        return "\n".join(out).encode("utf-8")
    if fmt == "vtt":
        out = ["WEBVTT", ""]
        for c in subs:
            out.append(f"{ts(c['s'], '.')} --> {ts(c['e'], '.')}\n" + "\n".join(c["lines"]) + "\n")
        return "\n".join(out).encode("utf-8")
    paras = _paras(tr)
    if fmt == "txt":
        body = [title, *_meta_lines(job), ""]
        for p in paras:
            body.append((f"[{clock(p[0]['s'])}] " if timestamps else "") + _ptext(p))
            body.append("")
        return "\n".join(body).encode("utf-8")
    if fmt == "md":
        body = [f"# {title}", "", *[f"_{ln}_  " for ln in _meta_lines(job)], ""]
        for p in paras:
            body.append((f"**[{clock(p[0]['s'])}]** " if timestamps else "") + _ptext(p))
            body.append("")
        return "\n".join(body).encode("utf-8")
    if fmt == "json":
        return json.dumps({"title": title, "meta": job.get("meta", {}),
                           "paragraphs": [{"start": p[0]["s"], "end": p[-1]["e"], "text": _ptext(p)} for p in paras],
                           "words": [{"text": w["t"], "start": w["s"], "end": w["e"], "confidence": w.get("c")}
                                     for w in words]}, ensure_ascii=False, indent=1).encode("utf-8")
    if fmt == "docx":
        return _docx(title, job, paras, timestamps)
    if fmt == "pdf":
        return _pdf(title, job, paras, timestamps)
    raise ValueError(f"unknown format {fmt}")


def _docx(title, job, paras, timestamps):
    from docx import Document
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt, RGBColor

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(11)
    doc.add_heading(title, level=1)
    for ln in _meta_lines(job):
        r = doc.add_paragraph().add_run(ln)
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    doc.add_paragraph()
    for p in paras:
        par = doc.add_paragraph()
        par.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
        par.paragraph_format.space_after = Pt(8)
        if timestamps:
            r = par.add_run(f"[{clock(p[0]['s'])}]  ")
            r.font.size = Pt(9)
            r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
            r.bold = True
        par.add_run(_ptext(p))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


_FONTS = None


def _fonts():
    """Arial ships with macOS and covers accents, curly quotes, Greek and Cyrillic."""
    global _FONTS
    if _FONTS is None:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        base = "/System/Library/Fonts/Supplemental/"
        try:
            pdfmetrics.registerFont(TTFont("Body", base + "Arial.ttf"))
            pdfmetrics.registerFont(TTFont("Body-Bold", base + "Arial Bold.ttf"))
            _FONTS = ("Body", "Body-Bold")
        except Exception:  # noqa: BLE001 - fall back to the built-in PDF fonts
            _FONTS = ("Helvetica", "Helvetica-Bold")
    return _FONTS


def _pdf(title, job, paras, timestamps):
    from xml.sax.saxutils import escape

    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    body, bold = _fonts()
    h = ParagraphStyle("h", fontName=bold, fontSize=18, leading=23, spaceAfter=6)
    meta = ParagraphStyle("m", fontName=body, fontSize=8.5, leading=12, textColor=HexColor("#666666"))
    txt = ParagraphStyle("t", fontName=body, fontSize=10.5, leading=16, spaceAfter=9)
    story = [Paragraph(escape(title), h)]
    story += [Paragraph(escape(ln), meta) for ln in _meta_lines(job)]
    story.append(Spacer(1, 14))
    for p in paras:
        stamp = f'<font name="{bold}" size="8" color="#8a8a8a">[{clock(p[0]["s"])}]</font>&nbsp;&nbsp;' if timestamps else ""
        story.append(Paragraph(stamp + escape(_ptext(p)), txt))

    def footer(c, d):
        c.saveState()
        c.setFont(body, 8)
        c.setFillColor(HexColor("#999999"))
        c.drawRightString(LETTER[0] - 0.9 * inch, 0.55 * inch, f"{d.page}")
        c.drawString(0.9 * inch, 0.55 * inch, title[:90])
        c.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                      topMargin=0.85 * inch, bottomMargin=0.85 * inch, title=title,
                      author=job.get("meta", {}).get("channel", "")).build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def filename(job, fmt):
    t = job.get("meta", {}).get("title", "transcript")
    safe = "".join(ch for ch in t if ch.isalnum() or ch in " -_().,'&").strip()[:120] or "transcript"
    return f"{safe}.{fmt}"

