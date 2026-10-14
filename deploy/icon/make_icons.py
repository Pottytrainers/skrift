"""Draws Skrift's icon and writes every file the app needs (macOS + Windows + the web page):

    deploy/icon/skrift.svg        full icon, macOS grid (824 px tile on a 1024 canvas)
    deploy/icon/skrift-small.svg  simplified version for 16-32 px (the nib alone, bolder)
    deploy/icon/skrift-512.png    for the README
    deploy/mac/Skrift.icns        Mac app icon (small art at 16/32 px, full art above)
    web/icon.svg                  browser tab
    web/icon-180.png              phone home screen (full-bleed; the phone rounds the corners)

A fountain pen (Midnight Ink: gold nib, black resin, navy paper) whose ink line starts as a sound wave and calms
into handwriting at the tip. The pen is drawn along its own axis (tip at 0,0, pointing down), lit from the top left.
Needs rsvg-convert (brew install librsvg) and iconutil (macOS).

    python3 deploy/icon/make_icons.py
"""
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = ROOT / "deploy" / "icon"
TILE = '<rect x="100" y="100" width="824" height="824" rx="185"/>'
SQUARE = '<rect x="100" y="100" width="824" height="824"/>'
TIP = (574, 626)
PEN_XF = f"translate({TIP[0]} {TIP[1]}) rotate(40) scale(1.18)"


# ---------------------------------------------------------------- the ink: a sound wave that becomes handwriting

def ribbon(points, widths):
    """A filled calligraphic stroke around a centreline, with a given width at each point."""
    left, right = [], []
    n = len(points)
    for i, ((x, y), w) in enumerate(zip(points, widths)):
        (x0, y0), (x1, y1) = points[max(i - 1, 0)], points[min(i + 1, n - 1)]
        d = math.hypot(x1 - x0, y1 - y0) or 1
        nx, ny = -(y1 - y0) / d, (x1 - x0) / d
        left.append((x + nx * w / 2, y + ny * w / 2))
        right.append((x - nx * w / 2, y - ny * w / 2))
    return "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in left + right[::-1]) + " Z"


def ink_stroke(x0, y0, tip):
    """A burst of sound (swelling, then fading on a calm zero crossing) that eases into a cursive line ending
    at the pen's tip. Width follows a broad nib held at a fixed angle, so it is thick and thin like real ink."""
    tx, ty = tip
    n, wave_end, nib_angle = 420, .66, math.radians(-35)
    pts = []
    for i in range(n + 1):
        t = i / n
        x = x0 + (tx - x0) * t
        base = y0 + (ty - y0) * t ** 2.2
        if t < wave_end:
            u = t / wave_end
            env = math.sin(math.pi * u) ** 2 * (1 - .25 * u)
            y = base - 96 * env * math.sin(2 * math.pi * (3.5 * u + .5 * u * u))
        else:
            v = (t - wave_end) / (1 - wave_end)
            y = base - 16 * math.sin(math.pi * v) ** 2 * (1 - v)
        pts.append((x, y))
    widths = []
    for i in range(n + 1):
        t = i / n
        (xa, ya), (xb, yb) = pts[max(i - 1, 0)], pts[min(i + 1, n)]
        w = 5 + 13 * abs(math.sin(math.atan2(yb - ya, xb - xa) - nib_angle))
        w *= min(1, t / .05) ** .6                  # tapered start
        w *= .45 + .55 * min(1, (1 - t) / .08)      # narrows into the tip
        widths.append(w)
    return ribbon(pts, widths)


# ---------------------------------------------------------------- the pen

# nib outline, right half: tip -> flank swelling to the shoulder -> gentle waist -> into the grip
NIB_R = [("C", [(9, -26), (38, -104), (55, -168)]),
         ("C", [(61, -192), (66, -214), (63, -236)]),
         ("C", [(61, -256), (58, -280), (60, -306)])]
NIB_BORDER = [("C", [(7, -40), (30, -110), (44, -166)]),
              ("C", [(49, -190), (52, -212), (50, -232)]),
              ("C", [(48, -252), (46, -272), (47, -292)])]


def mirror(right):
    """A symmetric closed outline from its right half."""
    d, segs, cur = "M 0 0 ", [], (0, 0)
    for cmd, pts in right:
        d += cmd + " " + " ".join(f"{x:g} {y:g}" for x, y in pts) + " "
        segs.append((cur, pts)); cur = pts[-1]
    d += f"L {-cur[0]:g} {cur[1]:g} "
    for start, (c1, c2, _) in reversed(segs):
        d += f"C {-c2[0]:g} {c2[1]:g} {-c1[0]:g} {c1[1]:g} {-start[0]:g} {start[1]:g} "
    return d + "Z"


def open_path(right, start, flip=False):
    s = -1 if flip else 1
    d = f"M {start[0]:g} {start[1]:g} "
    for cmd, pts in right:
        d += cmd + " " + " ".join(f"{s * x:g} {y:g}" for x, y in pts) + " "
    return d


def cylinder(x0, x1, y0, y1, ry, fill):
    """A cylinder segment seen slightly from above: straight sides, elliptical ends."""
    return f'<path d="M {-x0} {y0} A {x0} {ry} 0 0 0 {x0} {y0} L {x1} {y1} A {x1} {ry} 0 0 1 {-x1} {y1} Z" fill="{fill}"/>'


PEN_DEFS = '''
<linearGradient id="gold" gradientUnits="userSpaceOnUse" x1="-64" y1="0" x2="64" y2="0">
  <stop offset="0" stop-color="#7a4f16"/><stop offset=".16" stop-color="#c8963f"/><stop offset=".33" stop-color="#ffefb8"/>
  <stop offset=".44" stop-color="#f3cf7c"/><stop offset=".58" stop-color="#d6a14b"/><stop offset=".8" stop-color="#a9742a"/>
  <stop offset="1" stop-color="#5d3a0c"/></linearGradient>
<linearGradient id="goldShade" gradientUnits="userSpaceOnUse" x1="0" y1="-306" x2="0" y2="0">
  <stop offset="0" stop-color="#3a2206" stop-opacity=".55"/><stop offset=".22" stop-color="#3a2206" stop-opacity="0"/>
  <stop offset=".85" stop-color="#fff6d8" stop-opacity="0"/><stop offset="1" stop-color="#fff6d8" stop-opacity=".35"/></linearGradient>
<radialGradient id="tipping" cx=".38" cy=".35" r=".7"><stop offset="0" stop-color="#ffffff"/><stop offset=".45" stop-color="#c9ced6"/>
  <stop offset="1" stop-color="#5d636e"/></radialGradient>
<linearGradient id="resin" gradientUnits="userSpaceOnUse" x1="-90" y1="0" x2="90" y2="0">
  <stop offset="0" stop-color="#030405"/><stop offset=".12" stop-color="#1c2028"/><stop offset=".24" stop-color="#5b6372"/>
  <stop offset=".285" stop-color="#f4f7fb"/><stop offset=".31" stop-color="#7b8494"/><stop offset=".42" stop-color="#20242c"/>
  <stop offset=".7" stop-color="#090a0d"/><stop offset=".9" stop-color="#2b3140"/><stop offset="1" stop-color="#050608"/></linearGradient>
<linearGradient id="ringGold" gradientUnits="userSpaceOnUse" x1="-92" y1="0" x2="92" y2="0">
  <stop offset="0" stop-color="#5d3a0c"/><stop offset=".2" stop-color="#d9a74e"/><stop offset=".3" stop-color="#fff3c4"/>
  <stop offset=".42" stop-color="#e8bd62"/><stop offset=".75" stop-color="#9c6a24"/><stop offset="1" stop-color="#4a2e08"/></linearGradient>
<mask id="nibCut" maskUnits="userSpaceOnUse" x="-120" y="-340" width="240" height="360">
  <rect x="-120" y="-340" width="240" height="360" fill="#fff"/>
  <circle cx="0" cy="-152" r="10.5" fill="#000"/>
  <path d="M -1.3 -142 L 1.3 -142 L .5 -4 L -.5 -4 Z" fill="#000"/></mask>'''


def pen():
    nib = mirror(NIB_R)
    br, bl = open_path(NIB_BORDER, (0, -22)), open_path(NIB_BORDER, (0, -22), flip=True)
    cut = 'stroke="#5a360a" stroke-opacity=".65"'
    barrel = (cylinder(88, 92, -504, -1200, 14, "url(#resin)")
              + '<path d="M -58 -520 L -62 -1150" stroke="#fff" stroke-opacity=".85" stroke-width="4" stroke-linecap="round"/>'
              + '<path d="M 70 -520 L 74 -1150" stroke="#7f8aa0" stroke-opacity=".35" stroke-width="3" stroke-linecap="round"/>')
    ring = (cylinder(84, 84, -468, -506, 13, "url(#ringGold)")
            + '<path d="M -84 -480 A 84 13 0 0 0 84 -480 M -84 -494 A 84 13 0 0 0 84 -494" fill="none" stroke="#5d3a0c" stroke-opacity=".55" stroke-width="2"/>')
    grip = (cylinder(66, 78, -296, -470, 13, "url(#resin)")
            + '<path d="M -52 -301 A 66 13 0 0 0 -8 -284" fill="none" stroke="#c3cad6" stroke-opacity=".45" stroke-width="2" stroke-linecap="round"/>'
            + '<path d="M -48 -312 L -56 -458" stroke="#fff" stroke-opacity=".9" stroke-width="3.5" stroke-linecap="round"/>')
    feed = ('<path d="M 6 -40 C 30 -110 52 -170 60 -214 C 64 -240 64 -270 66 -306 L 72 -306 C 72 -270 72 -238 67 -212 '
            'C 60 -168 38 -104 12 -36 Z" fill="#0b0d12"/>')
    body = (f'<g mask="url(#nibCut)"><path d="{nib}" fill="url(#gold)"/><path d="{nib}" fill="url(#goldShade)"/></g>'
            '<g fill="none" stroke-linecap="round">'
            f'<path d="{br}" stroke="#5a360a" stroke-width="2.4" stroke-opacity=".7"/>'
            f'<path d="{bl}" stroke="#5a360a" stroke-width="2.4" stroke-opacity=".7"/>'
            f'<path d="{bl}" stroke="#fff4cf" stroke-width="1.4" stroke-opacity=".6" transform="translate(1.8 1.2)"/>'
            f'<path d="{br}" stroke="#fff4cf" stroke-width="1.4" stroke-opacity=".35" transform="translate(1.8 1.2)"/>'
            f'<path d="M -34 -262 Q 0 -276 34 -262" {cut} stroke-width="2.2"/>'
            f'<path d="M -26 -244 Q 0 -255 26 -244" {cut} stroke-width="2.2"/>'
            f'<path d="M 0 -232 L 7 -218 L 0 -204 L -7 -218 Z" {cut} stroke-width="2"/></g>'
            '<path d="M -7.5 -146 A 10.5 10.5 0 0 0 9 -147" fill="none" stroke="#fff3c8" stroke-width="2" stroke-opacity=".7"/>'
            '<path d="M 2.4 -140 L 1.2 -8" stroke="#fff3c8" stroke-width="1.2" stroke-opacity=".55"/>'
            '<ellipse cx="0" cy="-5" rx="6.5" ry="7.5" fill="url(#tipping)"/>'
            '<path d="M 0 -12 L 0 -1" stroke="#2a2d33" stroke-width="1.2"/>')
    return barrel + ring + grip + feed + body


# ---------------------------------------------------------------- the icons

COMMON_DEFS = '''
<linearGradient id="tile" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e2f52"/><stop offset="1" stop-color="#090f1d"/></linearGradient>
<filter color-interpolation-filters="sRGB" id="blur16" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="16"/></filter>'''


def full_icon(full_bleed=False):
    shape = SQUARE if full_bleed else TILE
    s = shape[:-2]
    ink = ink_stroke(196, 712, (TIP[0] - 2, TIP[1] - 1))
    defs = (COMMON_DEFS + PEN_DEFS
            + '<radialGradient id="light" gradientUnits="userSpaceOnUse" cx="320" cy="200" r="760"><stop offset="0" stop-color="#9fc2ff" stop-opacity=".16"/><stop offset=".6" stop-color="#9fc2ff" stop-opacity="0"/></radialGradient>'
            + '<radialGradient id="vignette" gradientUnits="userSpaceOnUse" cx="512" cy="470" r="560"><stop offset=".6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".35"/></radialGradient>'
            + f'<linearGradient id="ink" gradientUnits="userSpaceOnUse" x1="196" y1="0" x2="{TIP[0]}" y2="0"><stop offset="0" stop-color="#a6e4ff"/><stop offset="1" stop-color="#3f8cff"/></linearGradient>'
            + '<filter color-interpolation-filters="sRGB" id="grain" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="7"/>'
              '<feColorMatrix values="0 0 0 0 .5  0 0 0 0 .5  0 0 0 0 .5  0 0 0 .3 -.13"/><feComposite in2="SourceGraphic" operator="in"/></filter>'
            + '<filter color-interpolation-filters="sRGB" id="glow" x="-30%" y="-60%" width="160%" height="220%"><feGaussianBlur stdDeviation="15"/></filter>'
            + '<filter color-interpolation-filters="sRGB" id="penShadow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="13"/></filter>'
            + f'<clipPath id="clip">{shape}</clipPath>')
    shadow = "" if full_bleed else f'<g transform="translate(0 14)" opacity=".32">{s} fill="#000" filter="url(#blur16)"/></g>'
    edge = "" if full_bleed else '<rect x="101.5" y="101.5" width="821" height="821" rx="183.5" fill="none" stroke="#fff" stroke-opacity=".10" stroke-width="3"/>'
    body = (shadow + f'{s} fill="url(#tile)"/>'
            f'<g clip-path="url(#clip)">{s} fill="url(#light)"/>{s} fill="url(#vignette)"/>{s} fill="#000" filter="url(#grain)"/>'
            f'<path d="{ink}" fill="#3f8cff" opacity=".55" filter="url(#glow)"/><path d="{ink}" fill="url(#ink)"/>'
            f'<g transform="translate(16 26)" opacity=".55" filter="url(#penShadow)"><g transform="{PEN_XF}">'
            f'<path d="{mirror(NIB_R)}" fill="#000"/><rect x="-92" y="-1200" width="184" height="905" fill="#000"/></g></g>'
            f'<g transform="{PEN_XF}">{pen()}</g></g>' + edge)
    view = "100 100 824 824" if full_bleed else "0 0 1024 1024"
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"><defs>{defs}</defs>{body}</svg>'


def small_icon(crop=False):
    """For 16-32 px: the gold nib alone, bigger and bolder, writing a short stroke of blue ink."""
    view = "100 100 824 824" if crop else "0 0 1024 1024"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"><defs>
<linearGradient id="tile" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#22355c"/><stop offset="1" stop-color="#0b1222"/></linearGradient>
<linearGradient id="gold" gradientUnits="userSpaceOnUse" x1="-64" y1="0" x2="64" y2="0"><stop offset="0" stop-color="#b07a2c"/>
<stop offset=".35" stop-color="#ffe9a8"/><stop offset=".55" stop-color="#e9b85c"/><stop offset="1" stop-color="#8a5b1c"/></linearGradient>
<mask id="cut" maskUnits="userSpaceOnUse" x="-120" y="-340" width="240" height="360"><rect x="-120" y="-340" width="240" height="360" fill="#fff"/>
<circle cx="0" cy="-150" r="15" fill="#000"/><path d="M -3.5 -138 L 3.5 -138 L 1 -6 L -1 -6 Z" fill="#000"/></mask>
<clipPath id="clip">{TILE}</clipPath></defs>
{TILE[:-2]} fill="url(#tile)"/><g clip-path="url(#clip)">
<path d="M 170 712 C 260 700 330 690 420 664" fill="none" stroke="#5aa7ff" stroke-width="58" stroke-linecap="round"/>
<g transform="translate(436 660) rotate(40) scale(1.75)"><rect x="-70" y="-560" width="140" height="262" rx="10" fill="#0d1018"/>
<path d="{mirror(NIB_R)}" fill="url(#gold)" mask="url(#cut)"/></g></g></svg>'''


def png(svg_path, px, out):
    subprocess.run(["rsvg-convert", "-w", str(px), "-h", str(px), str(svg_path), "-o", str(out)], check=True)


def main():
    full, small = HERE / "skrift.svg", HERE / "skrift-small.svg"
    full.write_text(full_icon())
    small.write_text(small_icon())
    (ROOT / "web" / "icon.svg").write_text(small_icon(crop=True))
    png(HERE / "skrift.svg", 512, HERE / "skrift-512.png")
    with tempfile.TemporaryDirectory() as tmp:
        bleed = Path(tmp) / "bleed.svg"
        bleed.write_text(full_icon(full_bleed=True))
        png(bleed, 180, ROOT / "web" / "icon-180.png")
        iconset = Path(tmp) / "Skrift.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                png(small if px <= 32 else full, px, iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "deploy" / "mac" / "Skrift.icns")], check=True)
    print("icons written:", ", ".join(str(p.relative_to(ROOT)) for p in (
        full, small, HERE / "skrift-512.png", ROOT / "deploy/mac/Skrift.icns", ROOT / "web/icon.svg", ROOT / "web/icon-180.png")))


if __name__ == "__main__":
    if not shutil.which("rsvg-convert"):
        raise SystemExit("needs rsvg-convert: brew install librsvg")
    main()
