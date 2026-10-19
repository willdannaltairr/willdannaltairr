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
THEMES = {
    "dark": {
        "bg": "#e5d7b8",
        "paper": "#ede1c4",
        "line": "#4a3a26",
        "grid": "#4a3a26",
        "spoke": "#6b5a40",
        "label": "#2b2117",
        "value": "#6b5a40",
        "title": "#2b2117",
        "fill": "#6b5a40",
        "stroke": "#2b2117",
        "ink": "#2b2117",
    },
    "light": {
        "bg": "#f4eacc",
        "paper": "#faf1dc",
        "line": "#5b4a32",
        "grid": "#5b4a32",
        "spoke": "#77644a",
        "label": "#2b2117",
        "value": "#77644a",
        "title": "#2b2117",
        "fill": "#77644a",
        "stroke": "#2b2117",
        "ink": "#2b2117",
    },
}

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
    W = round((maxx - minx) + 2 * pad)
    H = round((maxy - miny) + 2 * pad + title_h)
    ox, oy = -minx + pad, -miny + pad + title_h

    # A long title ("<user> · language mix") can be wider than the chart itself.
    # Widen the canvas and re-centre the chart inside it rather than clipping.
    # Upper-cased because the plate is set like the banner masthead.
    if title:
        need = round(text_width(title.upper(), TTL) * 1.1 + 2 * pad)
        if need > W:
            ox += (need - W) / 2
            W = need

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" '
        f'aria-label="{esc(title) or "radar chart"}" font-family="{FONT}">',
        # Parchment sheet, then the same double rule the poster uses.
        f'<rect width="{W}" height="{H}" rx="12" fill="{c["bg"]}"/>',
        f'<rect x="6.5" y="6.5" width="{W - 13}" height="{H - 13}" rx="9" '
        f'fill="{c["paper"]}" stroke="{c["line"]}" stroke-width="1.4"/>',
        f'<rect x="12" y="12" width="{W - 24}" height="{H - 24}" rx="7" '
        f'fill="none" stroke="{c["line"]}" stroke-width="0.7" opacity=".5"/>',
    ]
    if title:
        parts.append(
            f'<text x="{W / 2:.1f}" y="{pad + TTL - 3:.0f}" text-anchor="middle" '
            f'font-family="{SERIF}" font-size="{TTL}" font-weight="700" '
            f'letter-spacing="1.8" fill="{c["title"]}">'
            f'{esc(title.upper())}</text>'
        )
    parts.append(f'<g transform="translate({ox:.1f},{oy:.1f})">')

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

    parts.append("</g></svg>")
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
