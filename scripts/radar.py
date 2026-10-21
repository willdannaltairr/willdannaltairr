#!/usr/bin/env python3
"""
radar.py - render a spider / radar chart as a standalone SVG. Stdlib only.

The chart is drawn as an orb web: radial spokes with concentric threads that
sag toward the centre, and a spider resting on the hub. Colours follow the
parchment-and-ink palette of the profile banner, so the two read as one set.

Two sources of data:

  1. a JSON file you control (default)
        python scripts/radar.py --data assets/skills.json -o assets/radar

  2. live language stats from the GitHub API
        python scripts/radar.py --github YOUR_USERNAME -o assets/radar-langs

Writes <out>-dark.svg and <out>-light.svg so the README can swap them with
<picture> + prefers-color-scheme.

skills.json shape:
    {
      "title": "Skill Radar",
      "axes": [ {"label": "Python", "value": 88}, ... ]      // value 0-100
    }
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Parchment and ink, lifted straight from the banner (scripts/banner/
# generate.py) so the webs sit on the same paper as the WANTED poster.
# One palette for both themes, exactly like the banner: the sheet is the
# same sheet whichever colour scheme the reader's GitHub is in.
PALETTE = {
    "bg": "#E8BF93",
    # Gradien cahaya yang jatuh di tengah lembar, disalin dari banner.
    "wash": ("#FFFEFA", "#FCF5E7", "#F9EBD4", "#F0D4AC"),
    "paper": "#FDF8EC",
    "edge": "#8A5A2B",
    "line": "#4a3b2a",
    # Benang jaring memakai tinta yang sama dengan garis bingkai; jaring
    # radial lebih ringan supaya benangnyataknik terlihat di atasnya.
    "grid": "#4a3b2a",
    "spoke": "#77603f",
    "muted": "#77603f",
    "label": "#2b1e10",
    "value": "#77603f",
    "title": "#2b1e10",
    "fill": "#77603f",
    "stroke": "#2b1e10",
    "ink": "#2b1e10",
    "shadow": "#b09060",
}

THEMES = {"dark": dict(PALETTE), "light": dict(PALETTE)}

# Semua card signals memakai kanvas yang sama persis, jadi dua chart di
# README duduk sejajar dan jaring di dalamnya sama besar. RING_R adalah
# radius cincin untuk semua chart; label yang lebih panjang hanya membuat
# isinya diperkecil sedikit, bukan mengubah ukuran cardnya.
CARD_W = 660
CARD_H = 560
RING_R = 200

UA = {"User-Agent": "radar.py"}


# --------------------------------------------------------------------------- #
# data sources
# --------------------------------------------------------------------------- #


def from_json(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    axes = [(a["label"], float(a["value"])) for a in d["axes"]]
    return d.get("title", "Skill Radar"), axes


def _api(url, token):
    req = urllib.request.Request(url, headers=dict(UA))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def from_github(user: str, token: str | None, limit: int, exclude: set[str],
                curve: float):
    """Sum language bytes across the user's non-fork public repos."""
    totals: dict[str, int] = {}
    page = 1
    while True:
        repos = _api(
            f"https://api.github.com/users/{user}/repos"
            f"?per_page=100&page={page}&type=owner&sort=pushed",
            token,
        )
        if not repos:
            break
        for repo in repos:
            if repo.get("fork") or repo.get("archived"):
                continue
            try:
                langs = _api(repo["languages_url"], token)
            except urllib.error.HTTPError:
                continue
            for name, count in langs.items():
                if name.lower() in exclude:
                    continue
                totals[name] = totals.get(name, 0) + count
        if len(repos) < 100:
            break
        page += 1

    if not totals:
        sys.exit(f"no language data found for '{user}' (private repos need a token)")

    top = sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
    peak = top[0][1]
    # Raw byte ratios are brutally lopsided — one dominant language leaves every
    # other axis pinned near the centre and the shape reads as a spike. `curve`
    # compresses that: 1.0 is linear, 0.5 (default) is sqrt, lower spreads more.
    axes = [(n, round(100 * (c / peak) ** curve, 1)) for n, c in top]
    return f"{user} · language mix", axes


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


FONT = "ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman','Nimbus Roman',serif"  # matches the banner masthead
LBL, VAL, TTL = 13, 11, 15  # font sizes: axis label, axis value, title


def ring(radius, n, start=-math.pi / 2):
    """Vertices of a regular n-gon centred on (0, 0), first one straight up."""
    return [
        (radius * math.cos(start + i * 2 * math.pi / n),
         radius * math.sin(start + i * 2 * math.pi / n))
        for i in range(n)
    ]


def text_width(s, font_size):
    """Rough advance width for a humanist sans. Deliberately generous —
    it only feeds the bounding box, so over-estimating just adds padding."""
    return len(s) * font_size * 0.62


def web(n: int, r: float, rings: int, sag: float = 0.14):
    """Concentric threads that bow toward the centre, the way an orb web does.

    A straight n-gon reads as a chart grid. Pulling each segment's midpoint
    toward the hub turns the same geometry into silk: the threads visibly sag
    between the spokes instead of cutting a hard corner. Yields (k, path).
    """
    out = []
    for k in range(1, rings + 1):
        pts = ring(r * k / rings, n)
        d = []
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            mx = (x1 + x2) / 2 * (1 - sag)
            my = (y1 + y2) / 2 * (1 - sag)
            d.append(f"M{x1:.1f} {y1:.1f}Q{mx:.1f} {my:.1f} {x2:.1f} {y2:.1f}")
        out.append((k, "".join(d)))
    return out


def spider(c, scale: float = 0.9) -> str:
    """A spider resting on the hub: eight jointed legs behind body and head.

    Drawn last so the measured shape never hides it, and scaled from the
    chart size so it keeps the same weight at any --size.
    """
    ink, paper = c["ink"], c["paper"]
    legs = []
    for side in (-1, 1):
        for (x0, y0), (cx, cy), (x1, y1) in (
            ((5.5, -9.0), (16.0, -19.0), (27.0, -24.0)),
            ((6.5, -5.0), (19.5, -9.5), (32.0, -7.5)),
            ((6.5, 0.0), (20.0, 3.5), (31.0, 11.0)),
            ((5.5, 5.0), (16.0, 14.0), (25.0, 24.0)),
        ):
            d = (f"M{side * x0:.1f} {y0:.1f}"
                 f"Q{side * cx:.1f} {cy:.1f} {side * x1:.1f} {y1:.1f}")
            legs.append(
                f'<path d="{d}" fill="none" stroke="{ink}" '
                f'stroke-width="2.4" stroke-linecap="round"/>'
            )
    body = (
        # abdomen, cephalothorax, and two eyes catching the parchment
        f'<ellipse cx="0" cy="5.5" rx="10.5" ry="13.5" fill="{ink}"/>'
        f'<ellipse cx="0" cy="-7.5" rx="7.6" ry="7" fill="{ink}"/>'
        f'<circle cx="-3.2" cy="-9.2" r="1.5" fill="{paper}"/>'
        f'<circle cx="3.2" cy="-9.2" r="1.5" fill="{paper}"/>'
    )
    # A slow bob: enough to feel alive inside a GitHub <img>, small enough
    # that it never reads as the chart itself moving.
    bob = (
        '<animateTransform attributeName="transform" type="translate" '
        'values="0 -2.2;0 2.2;0 -2.2" dur="5.5s" repeatCount="indefinite" '
        'calcMode="spline" keyTimes="0;0.5;1" '
        'keySplines="0.42 0 0.58 1;0.42 0 0.58 1"/>'
    )
    return f'<g>{bob}<g transform="scale({scale:.2f})">{"".join(legs)}{body}</g></g>'


def render(title, axes, theme: str, size: int, rings: int, show_values: bool,
           animate: bool) -> str:
    c = THEMES[theme]
    n = len(axes)
    r = size / 2 - 8
    gap = 20  # how far the labels sit beyond the outer ring

    vals = [max(0.0, min(100.0, v)) for _, v in axes]
    outer = ring(r, n)

    # Lay the labels out first, in centre-relative coordinates, so the viewBox
    # can be sized around whatever they actually occupy. A fixed viewBox clips
    # long labels on the left/right spokes.
    labels = []
    for i, (label, _) in enumerate(axes):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        cosv, sinv = math.cos(ang), math.sin(ang)
        lx, ly = (r + gap) * cosv, (r + gap) * sinv
        anchor = "middle" if abs(cosv) < 0.25 else ("start" if cosv > 0 else "end")
        dy = 4 if abs(sinv) < 0.25 else (14 if sinv > 0 else -5)
        labels.append((lx, ly + dy, anchor, label, vals[i]))

    minx, maxx, miny, maxy = -r, r, -r, r
    for lx, ly, anchor, label, v in labels:
        w = max(text_width(label, LBL),
                text_width(f"{v:g}", VAL) if show_values else 0.0)
        if anchor == "start":
            x0, x1 = lx, lx + w
        elif anchor == "end":
            x0, x1 = lx - w, lx
        else:
            x0, x1 = lx - w / 2, lx + w / 2
        y0 = ly - LBL
        y1 = ly + 4 + (VAL + 4 if show_values else 0)
        minx, maxx = min(minx, x0), max(maxx, x1)
        miny, maxy = min(miny, y0), max(maxy, y1)

    pad = 18
    title_h = TTL + 18 if title else 0
    # Kanvas tetap untuk semua card: keempatnya berukuran sama persis,
    # jadi dua chart di README duduk sejajar dan ukurannya bisa
    # dibandingkan langsung. Isi yang melebihi card diperkecil, bukan
    # dipotong.
    W, H = CARD_W, CARD_H
    avail_w = W - 2 * pad
    avail_h = H - title_h - 2 * pad
    content_w = maxx - minx
    content_h = maxy - miny
    s = min(1.0, avail_w / content_w, avail_h / content_h)
    # Konten diskalakan terhadap pusat jaring, lalu dipusatkan di ruang
    # yang tersisa di bawah judul.
    ox = pad + (avail_w - s * content_w) / 2 - minx * s
    oy = title_h + pad + (avail_h - s * content_h) / 2 - miny * s

    # A long title ("<user> · language mix") can be wider than the chart
    # itself. Since the canvas is fixed, shrink the title instead of
    # widening the card. Upper-cased because the plate is set like the
    # banner masthead.
    title_size = TTL
    if title:
        need = text_width(title.upper(), title_size) * 1.1 + 2 * pad
        if need > W:
            title_size = max(9.0, TTL * (W - 2 * pad) / (need - 2 * pad))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" '
        f'aria-label="{esc(title) or "radar chart"}" font-family="{FONT}">',
        # Filter dan gradien kertas, disalin apa adanya dari banner
        # (scripts/banner/generate.py) supaya kartu signals duduk di
        # lembar yang sama: butiran halus, serat panjang, bercak besar,
        # dan vignette tepi.
        '<defs>',
        '<filter id="grain" x="0" y="0" width="100%" height="100%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="3" '
        'seed="4" result="n"/>'
        '<feColorMatrix in="n" type="saturate" values="0"/>'
        '<feComponentTransfer><feFuncA type="linear" slope=".55"/></feComponentTransfer>'
        '</filter>',
        '<filter id="fiber" x="0" y="0" width="100%" height="100%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".004 .62" numOctaves="4" '
        'seed="23" result="n"/>'
        '<feColorMatrix in="n" type="saturate" values="0"/>'
        '<feComponentTransfer><feFuncA type="linear" slope=".5"/></feComponentTransfer>'
        '</filter>',
        '<filter id="stain" x="-5%" y="-5%" width="110%" height="110%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".0045 .006" '
        'numOctaves="4" seed="91" result="n"/>'
        '<feColorMatrix in="n" type="matrix" '
        'values="0 0 0 0 .38  0 0 0 0 .25  0 0 0 0 .11  0 0 0 1.7 -.62"/>'
        '</filter>',
        # Cahaya jatuh di tengah lembar, sama seperti banner.
        '<radialGradient id="paperWash" cx=".5" cy=".42" r=".8">'
        f'<stop offset="0" stop-color="{c["wash"][0]}"/>'
        f'<stop offset=".52" stop-color="{c["wash"][1]}"/>'
        f'<stop offset=".84" stop-color="{c["wash"][2]}"/>'
        f'<stop offset="1" stop-color="{c["wash"][3]}"/>'
        '</radialGradient>',
        # Vignette tepi amber, hangat seperti banner.
        '<radialGradient id="age" cx=".5" cy=".46" r=".8">'
        '<stop offset=".60" stop-color="#C97A1C" stop-opacity="0"/>'
        '<stop offset=".78" stop-color="#CB7C1E" stop-opacity=".07"/>'
        '<stop offset=".91" stop-color="#CD7E20" stop-opacity=".18"/>'
        '<stop offset="1" stop-color="#CF8022" stop-opacity=".34"/>'
        '</radialGradient>',
        f'<clipPath id="paperClip"><rect x="6.5" y="6.5" width="{W - 13}" '
        f'height="{H - 13}" rx="9"/></clipPath>',
        '</defs>',
        # Parchment sheet, then the same double rule the poster uses.
        f'<rect width="{W}" height="{H}" rx="12" fill="{c["bg"]}"/>',
        f'<rect x="6.5" y="6.5" width="{W - 13}" height="{H - 13}" rx="9" '
        f'fill="url(#paperWash)" stroke="{c["line"]}" stroke-width="1.4"/>',
        f'<rect x="12" y="12" width="{W - 24}" height="{H - 24}" rx="7" '
        f'fill="none" stroke="{c["line"]}" stroke-width="0.7" opacity=".5"/>',
    ]
    if title:
        parts.append(
            f'<text x="{W / 2:.1f}" y="{pad + title_size - 3:.0f}" text-anchor="middle" '
            f'font-family="{SERIF}" font-size="{title_size:.1f}" font-weight="700" '
            f'letter-spacing="1.8" fill="{c["title"]}">'
            f'{esc(title.upper())}</text>'
        )
    parts.append(f'<g transform="translate({ox:.1f},{oy:.1f}) scale({s:.4f})">')

    # Spokes first: the silk lines the rest of the web is spun onto.
    for x, y in outer:
        parts.append(
            f'<line x1="0" y1="0" x2="{x:.1f}" y2="{y:.1f}" '
            f'stroke="{c["spoke"]}" stroke-width="1" opacity=".9"/>'
        )

    # Concentric threads, sagging between the spokes. The outermost is drawn
    # heaviest, which is how a real web anchors itself to its frame.
    for k, d in web(n, r, rings):
        parts.append(
            f'<path d="{d}" fill="none" stroke="{c["grid"]}" '
            f'stroke-width="{1.0 + 0.3 * k / rings:.2f}" '
            f'opacity="{0.4 + 0.45 * k / rings:.2f}" stroke-linecap="round"/>'
        )

    # The measured shape. SMIL rather than CSS: animateTransform scales about
    # the group's own origin, which is already the centre of the chart here.
    shape = [(px * v / 100, py * v / 100) for (px, py), v in zip(outer, vals)]
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in shape)
    parts.append("<g>")
    if animate:
        parts.append(
            '<animateTransform attributeName="transform" type="scale" '
            'values="0.04;1" dur="1.1s" calcMode="spline" keyTimes="0;1" '
            'keySplines="0.22 1 0.36 1" fill="freeze"/>'
        )
    parts.append(
        f'<polygon points="{d}" fill="{c["fill"]}" fill-opacity="0.20" '
        f'stroke="{c["stroke"]}" stroke-width="2.4" stroke-linejoin="round"/>'
    )
    for x, y in shape:
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="{c["paper"]}" '
            f'stroke="{c["stroke"]}" stroke-width="1.6"/>'
        )
    parts.append("</g>")

    # The spider sits on the hub, drawn last so no shape can cover it.
    parts.append(spider(c, scale=size / 440 * 0.9))

    # axis labels
    for lx, ly, anchor, label, v in labels:
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="{LBL}" font-weight="600" fill="{c["label"]}">'
            f'{esc(label)}</text>'
        )
        if show_values:
            parts.append(
                f'<text x="{lx:.1f}" y="{ly + VAL + 4:.1f}" text-anchor="{anchor}" '
                f'font-size="{VAL}" fill="{c["value"]}">{v:g}</text>'
            )

    parts.append("</g>")

    # Lapisan kertas terakhir: butiran halus, serat panjang, bercak besar,
    # dan vignette tepi. Filter hanya berlaku pada elemen yang disisipkan,
    # jadi lapisan ini harus paling akhir — kalau tidak, konten di
    # atasnya akan menutupi tekstur dan kertasnya terlihat datar.
    parts.append(
        f'<g clip-path="url(#paperClip)">'
        f'<rect x="6.5" y="6.5" width="{W - 13}" height="{H - 13}" '
        f'filter="url(#grain)" opacity=".10"/>'
        f'<rect x="6.5" y="6.5" width="{W - 13}" height="{H - 13}" '
        f'filter="url(#fiber)" opacity=".05"/>'
        f'<rect x="6.5" y="6.5" width="{W - 13}" height="{H - 13}" '
        f'filter="url(#stain)" opacity=".07"/>'
        f'<rect x="6.5" y="6.5" width="{W - 13}" height="{H - 13}" '
        f'fill="url(#age)"/>'
        f'</g>'
    )
    parts.append("</svg>")
    return "".join(parts)


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------- #


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--data", type=Path, default=Path("assets/skills.json"))
    src.add_argument("--github", metavar="USER",
                     help="build the radar from GitHub language stats instead")
    p.add_argument("-o", "--out", type=Path, default=Path("assets/radar"),
                   help="output path WITHOUT extension")
    p.add_argument("--title", help="override the chart title ('' for none)")
    p.add_argument("--size", type=int, default=440)
    p.add_argument("--rings", type=int, default=4)
    p.add_argument("--limit", type=int, default=7,
                   help="max axes when using --github")
    p.add_argument("--exclude", default="html,css,shell,makefile,dockerfile,batchfile",
                   help="comma-separated languages to skip in --github mode")
    p.add_argument("--curve", type=float, default=0.5,
                   help="--github axis scaling: 1.0 linear, 0.5 sqrt (default), "
                        "0.3 flattens a one-language-dominant profile")
    p.add_argument("--values", action="store_true", help="print the number per axis")
    p.add_argument("--no-animate", dest="animate", action="store_false",
                   help="disable the grow-in animation")
    args = p.parse_args(argv)

    if args.github:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        excl = {s.strip().lower() for s in args.exclude.split(",") if s.strip()}
        title, axes = from_github(args.github, token, args.limit, excl, args.curve)
    else:
        if not args.data.exists():
            sys.exit(f"no data file: {args.data}")
        title, axes = from_json(args.data)

    if args.title is not None:
        title = args.title
    if len(axes) < 3:
        sys.exit("a radar chart needs at least 3 axes")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        svg = render(title, axes, theme, args.size, args.rings, args.values,
                     args.animate)
        dest = args.out.with_name(f"{args.out.name}-{theme}.svg")
        dest.write_text(svg, encoding="utf-8")
        print(f"wrote {dest}  ({len(axes)} axes)")


if __name__ == "__main__":
    main()
