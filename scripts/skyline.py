#!/usr/bin/env python3
"""
skyline.py - a year of GitHub contributions as an isometric skyline. Stdlib only.

    python scripts/skyline.py --user willdannaltairr --out assets
    python scripts/skyline.py --data assets/contributions.json --out assets

Writes <out>/skyline-{dark,light}.svg so the README can swap them with
<picture> + prefers-color-scheme, the same way the banners and the cards do.

Every day of the past year is a box on a week x weekday grid. Seen from the
corner that grid becomes a skyline: a day's contribution count is its height.
The flat tiles land first, then the towers rise in a wave from the oldest week
to the newest, so the heat map visibly turns into the city. The rise is SMIL,
so it runs inside an <img> tag - no JavaScript, no external file, nothing to
host.

Contribution counts come from the GraphQL contribution calendar, which needs a
token ($GITHUB_TOKEN / $GH_TOKEN). Pass --data to read a cached JSON instead:
the section then still renders with no token and no network.
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = {"User-Agent": "skyline.py"}
FONT = "ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif"

# --------------------------------------------------------------------------- #
# theme - the same split the rest of the repo uses: purple on dark, green on
# light. `tiles` is the empty day, then activity levels 1..4.
# --------------------------------------------------------------------------- #

THEMES = {
    "dark": {
        "bg": "#090A16", "border": "#30363d", "title": "#aa9bef",
        "text": "#ffffff", "muted": "#8b949e", "accent": "#aa9bef",
        "tiles": ["#171a2e", "#2d1f4f", "#553c9a", "#8b5cf6", "#aa9bef"],
    },
    "light": {
        "bg": "#ffffff", "border": "#d0d7de", "title": "#1a7f37",
        "text": "#1f2328", "muted": "#57606a", "accent": "#1a7f37",
        "tiles": ["#ebedf0", "#c6e8cf", "#7cc98f", "#2da44e", "#1a7f37"],
    },
}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# --------------------------------------------------------------------------- #
# geometry - the camera and the projection, lifted from the React original
# --------------------------------------------------------------------------- #

YAW = math.pi / 4              # 45 deg, the isometric corner
ELEV = math.radians(34)        # 34 deg above the horizon
CELL = 0.9                     # box size in grid units; the rest is the gap
SLAB = 0.12                    # height of an empty day, in grid units
TOP_H = 6.2                    # height of the busiest day, in grid units
RISE = 0.42                    # share of the loop one bar spends rising
START = 0.42                   # when the newest week starts rising; the rest holds

_CS, _SN = math.cos(YAW), math.sin(YAW)
_SE, _CE = math.sin(ELEV), math.cos(ELEV)


def project(x: float, y: float, z: float) -> tuple[float, float]:
    """Grid (x = week, y = weekday, z = up) -> screen, before scale and offset."""
    return (x * _CS - y * _SN, (x * _SN + y * _CS) * _SE - z * _CE)


def bar_height(count: int, busiest: int) -> float:
    """Empty days are thin slabs; the busiest day is TOP_H cells tall."""
    if count <= 0 or busiest <= 0:
        return SLAB
    return 0.4 + (count / busiest) ** 0.85 * (TOP_H - 0.4)


def rise_at(week: int, weeks: int, day: int) -> float:
    """0 -> 1 as the wave sweeps oldest week -> newest. Mirrors riseAt()."""
    return (week / (weeks - 1) if weeks > 1 else 0.0) * 0.9 + (day / 6) * 0.1


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #

CONTRIB_QUERY = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        totalContributions
        weeks{ contributionDays{ date contributionCount } }
      }
    }
  }
}
"""


def graphql(query: str, variables: dict, token: str):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={**UA, "Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_days(user: str, token: str | None):
    """[(date, count)] for the last year, or None if the token can't read it."""
    if not token:
        return None
    try:
        data = graphql(CONTRIB_QUERY, {"login": user}, token)
    except urllib.error.HTTPError as e:
        print(f"  contributions unavailable (HTTP {e.code})", file=sys.stderr)
        return None
    if data.get("errors"):
        print(f"  contributions unavailable: {data['errors'][0].get('message')}",
              file=sys.stderr)
        return None
    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [(d["date"], int(d["contributionCount"]))
            for w in cal["weeks"] for d in w["contributionDays"]]
    days.sort()
    return days


def load_days(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw["days"] if isinstance(raw, dict) else raw
    return sorted((e["date"], int(e["count"])) for e in entries)


def build_grid(days, week_start: int = 0):
    """
    Columns are weeks, rows are weekdays (row 0 = `week_start`). The grid ends on
    the newest day present and starts on the week containing the day one year
    earlier, so the newest column is always the current week.
    """
    counts: dict[str, int] = {}
    for date, count in days:
        if count > 0:
            counts[date] = counts.get(date, 0) + count
    if not counts:
        return [], 0, 0

    last = max(dt.date.fromisoformat(d) for d, _ in days)
    start = last - dt.timedelta(days=364)
    # Python's weekday() is Monday=0; GitHub's is Sunday=0.
    dow = (start.weekday() + 1) % 7
    start -= dt.timedelta(days=(dow - week_start) % 7)

    cells = []
    cur = start
    while cur <= last:
        idx = (cur - start).days
        iso = cur.isoformat()
        cells.append({"date": iso, "count": counts.get(iso, 0),
                      "week": idx // 7, "day": idx % 7, "level": 0})
        cur += dt.timedelta(days=1)

    # Levels 1-4 split the non-zero days by their share of a busy day - the 95th
    # percentile, so one freak day can't wash every other day out to level 1.
    nz = sorted(c["count"] for c in cells if c["count"] > 0)
    busy = nz[int(0.95 * (len(nz) - 1))] if nz else 0
    for c in cells:
        c["level"] = (0 if c["count"] <= 0 else 4 if busy <= 0
                      else 1 + min(3, int(c["count"] / busy * 4)))

    return cells, cells[-1]["week"] + 1, (nz[-1] if nz else 0)


def compute_stats(cells):
    """Total, busiest day, longest run, and the run reaching today (or yesterday)."""
    total = sum(c["count"] for c in cells)
    busiest = max(cells, key=lambda c: c["count"], default=None)

    longest = run = longest_end = 0
    for i, c in enumerate(cells):
        run = run + 1 if c["count"] > 0 else 0
        if run > longest:
            longest, longest_end = run, i

    # today is not over, so a zero today does not break the current streak
    j = len(cells) - 1
    if j >= 0 and cells[j]["count"] == 0:
        j -= 1
    end_at = j
    while j >= 0 and cells[j]["count"] > 0:
        j -= 1
    current = end_at - j

    return {
        "total": total,
        "first": cells[0]["date"] if cells else None,
        "last": cells[-1]["date"] if cells else None,
        "busiest": (busiest["count"], busiest["date"]) if busiest else (0, None),
        "longest": (longest,
                    cells[longest_end - longest + 1]["date"] if longest else None,
                    cells[longest_end]["date"] if longest else None),
        "current": (current,
                    cells[j + 1]["date"] if current else None,
                    cells[end_at]["date"] if current else None),
    }


# --------------------------------------------------------------------------- #
# svg helpers
# --------------------------------------------------------------------------- #

def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def shade(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


def nf(n: int) -> str:
    return f"{n:,}"


def d_short(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}"


def d_long(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def poly(pts) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


# Written as real characters, never as HTML entities: esc() would turn "&#8212;"
# into "&amp;#8212;" and the reader would see the entity itself.
DASH = "\u2014"
DOT = "\u00b7"


# --------------------------------------------------------------------------- #
# renderer
# --------------------------------------------------------------------------- #

W = 880
PAD = 24
HEAD = 58           # title row
STATS = 76          # the four-number row under the chart
FOOT = 42           # legend row
SPLINE = "0 0 1 1;0.215 0.61 0.355 1;0 0 1 1"
UNIT = "0,0 1,0 1,1 0,1"


def render(cells, weeks, busiest, stats, theme: str, *, animate: bool = True,
           user: str = "", loop: float = 9.0) -> str:
    c = THEMES[theme]
    tiles = c["tiles"]

    # ---- layout: fit the projected scene into the chart box ----------------- #
    chart_w = W - 2 * PAD
    heights = [bar_height(x["count"], busiest) for x in cells]
    off = (1 - CELL) / 2

    xs, ys = [], []
    for cell, h in zip(cells, heights):
        for dx in (0, CELL):
            for dy in (0, CELL):
                for z in (0, h):
                    px, py = project(cell["week"] + off + dx, cell["day"] + off + dy, z)
                    xs.append(px)
                    ys.append(py)
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    scale = chart_w / (maxx - minx)
    chart_top = HEAD
    chart_bot = chart_top + (maxy - miny) * scale
    H = int(round(chart_bot + STATS + FOOT))

    ox = PAD - minx * scale
    oy = chart_top - miny * scale

    def sx(x, y):
        return ox + (x * _CS - y * _SN) * scale

    def sy(x, y, z):
        return oy + ((x * _SN + y * _CS) * _SE - z * _CE) * scale

    # basis vectors: one cell along each floor axis, and one unit of height,
    # which on screen is straight up by _CE * scale.
    pvx, pvy = sx(CELL, 0) - sx(0, 0), sy(CELL, 0, 0) - sy(0, 0, 0)
    qvx, qvy = sx(0, CELL) - sx(0, 0), sy(0, CELL, 0) - sy(0, 0, 0)
    uy = -_CE * scale

    def anim_transform(kind, values, times):
        return (f'<animateTransform attributeName="transform" type="{kind}" '
                f'values="{values}" keyTimes="{times}" calcMode="spline" '
                f'keySplines="{SPLINE}" dur="{loop}s" repeatCount="indefinite"/>')

    def floor_svg(cell):
        """An empty day: just its tile on the grid, nothing above it."""
        x0, y0 = cell["week"] + off, cell["day"] + off
        fx, fy = sx(x0, y0), sy(x0, y0, 0)
        return (f'<g transform="translate({fx:.1f},{fy:.1f})">'
                f'<g transform="matrix({pvx:.2f},{pvy:.2f},{qvx:.2f},{qvy:.2f},0,0)">'
                f'<polygon points="{UNIT}" fill="{tiles[0]}"/></g></g>')

    def tower_svg(cell, h):
        """A day with activity: two visible sides and a top face."""
        x0, y0 = cell["week"] + off, cell["day"] + off
        base = tiles[cell["level"]] or tiles[1]
        progress = rise_at(cell["week"], weeks, cell["day"])

        # each box rises inside its own window of the loop, in wave order, and the
        # whole city is left standing for the last stretch before the loop restarts
        t0 = progress * START
        kt = f"0;{t0:.4f};{t0 + RISE:.4f};1"
        # a side is the unit square squashed flat, then grown to full height
        grow = "1 0.001;1 0.001;1 1;1 1"
        # the cap rides the same clock, lifted straight up the screen
        lift = f"0,0;0,0;0,{uy * h:.2f};0,{uy * h:.2f}"

        fx, fy = sx(x0, y0), sy(x0, y0, 0)

        def face(origin, matrix, fill):
            # the innermost group carries no transform of its own, so the
            # animateTransform replaces it cleanly instead of fighting it
            return (f'<g transform="translate({origin[0]:.1f},{origin[1]:.1f})">'
                    f'<g transform="matrix({matrix})">'
                    f'<g><polygon points="{UNIT}" fill="{fill}"/>'
                    + (anim_transform("scale", grow, kt) if animate else "") +
                    f'</g></g></g>')

        # v runs up the side, so the matrix's second column is one box of height
        left = face((sx(x0, y0 + CELL), sy(x0, y0 + CELL, 0)),
                    f"{pvx:.2f},{pvy:.2f},0,{uy * h:.2f}", shade(base, 0.84))
        right = face((sx(x0 + CELL, y0), sy(x0 + CELL, y0, 0)),
                     f"{qvx:.2f},{qvy:.2f},0,{uy * h:.2f}", shade(base, 0.68))
        cap = (f'<g transform="translate({fx:.1f},{fy:.1f})">'
               f'<g>'
               f'<g transform="matrix({pvx:.2f},{pvy:.2f},{qvx:.2f},{qvy:.2f},0,0)">'
               f'<polygon points="{UNIT}" fill="{base}"/></g>'
               + (anim_transform("translate", lift, kt) if animate else "") +
               f'</g></g>')
        return left + right + cap

    # ---- the city, back to front -------------------------------------------- #
    # depth for yaw in [0, 90]: larger is nearer the viewer, so draw ascending.
    order = sorted(range(len(cells)),
                   key=lambda i: (cells[i]["week"] + off) + (cells[i]["day"] + off))

    body = []
    if animate:
        # the whole grid fades in before the first tower rises
        body.append('<g opacity="0"><animate attributeName="opacity" values="0;1" '
                    'dur="0.6s" fill="freeze"/>')
    body += [(tower_svg(cells[i], heights[i]) if cells[i]["count"] > 0
              else floor_svg(cells[i])) for i in order]
    if animate:
        body.append("</g>")

    # ---- month labels along the front edge ---------------------------------- #
    labels, prev = [], -1
    by_week = {}
    for cell in cells:
        by_week.setdefault(cell["week"], cell)
    for w in range(weeks):
        cell = by_week.get(w)
        if not cell:
            break
        m = int(cell["date"][5:7])
        if m != prev:
            labels.append((w, MONTHS[m - 1]))
        prev = m
    if len(labels) > 1 and labels[1][0] - labels[0][0] < 3:
        labels.pop(0)

    month_svg, edge = [], -1e9
    for w, label in labels:
        x = sx(w + 0.5, 7 + off + CELL)
        y = sy(w + 0.5, 7 + off + CELL, 0)
        width = len(label) * 6.4
        if x < edge or x + width > W - PAD or y + 16 > chart_bot + 10:
            continue
        month_svg.append(f'<text x="{x:.1f}" y="{y + 15:.1f}" font-size="10.5" '
                         f'fill="{c["muted"]}">{label}</text>')
        edge = x + width + 10

    # ---- header -------------------------------------------------------------- #
    head = [
        f'<text x="{PAD}" y="36" font-size="15" fill="{c["text"]}">'
        f'<tspan font-weight="700" fill="{c["accent"]}">{nf(stats["total"])}</tspan>'
        f' contributions in the last year</text>',
        f'<text x="{W - PAD}" y="36" font-size="11" text-anchor="end" '
        f'fill="{c["muted"]}">{esc(user)} {DOT} isometric skyline</text>',
        f'<line x1="{PAD}" y1="48" x2="{W - PAD}" y2="48" stroke="{c["border"]}"/>',
    ]

    # ---- the four numbers, in a row so they can never sit on the towers ----- #
    b_count, b_date = stats["busiest"]
    l_days, l_from, l_to = stats["longest"]
    c_days, c_from, c_to = stats["current"]
    span = (f'{d_short(stats["first"])} {DASH} {d_short(stats["last"])}, '
            f'{stats["last"][:4]}') if stats["first"] else DASH
    blocks = [
        ("1 year total", nf(stats["total"]), "contributions", span),
        ("Busiest day", nf(b_count), "on one day",
         d_long(b_date) if b_date else DASH),
        ("Longest streak", nf(l_days), "days" if l_days != 1 else "day",
         f'{d_short(l_from)} {DASH} {d_short(l_to)}' if l_days else DASH),
        ("Current streak", nf(c_days), "days" if c_days != 1 else "day",
         f'{d_short(c_from)} {DASH} {d_short(c_to)}' if c_days else DASH),
    ]
    y = int(round(chart_bot)) + 16
    col = chart_w / len(blocks)
    stat_svg = []
    for i, (label, value, unit, sub) in enumerate(blocks):
        x = PAD + i * col
        stat_svg.append(
            f'<text x="{x:.0f}" y="{y}" font-size="11" fill="{c["muted"]}">'
            f'{esc(label)}</text>'
            f'<text x="{x:.0f}" y="{y + 30}" font-size="27" font-weight="700" '
            f'fill="{c["accent"]}">{esc(value)}</text>'
            f'<text x="{x:.0f}" y="{y + 30}" font-size="12.5" fill="{c["text"]}" '
            f'dx="{len(value) * 17 + 6}">{esc(unit)}</text>'
            f'<text x="{x:.0f}" y="{y + 47}" font-size="10.5" fill="{c["muted"]}">'
            f'{esc(sub)}</text>')

    # ---- legend -------------------------------------------------------------- #
    legend = [
        f'<text x="{PAD}" y="{H - 18}" font-size="11" fill="{c["muted"]}">Less</text>',
        "".join(f'<rect x="{PAD + 38 + i * 15}" y="{H - 28}" width="11" height="11" '
                f'rx="2" fill="{t}"/>' for i, t in enumerate(tiles)),
        f'<text x="{PAD + 38 + len(tiles) * 15 + 4}" y="{H - 18}" font-size="11" '
        f'fill="{c["muted"]}">More</text>',
        f'<text x="{W - PAD}" y="{H - 18}" font-size="11" text-anchor="end" '
        f'fill="{c["muted"]}">each box is one day {DOT} its height is that '
        f'day\u2019s contributions</text>',
    ]

    label = (f'{nf(stats["total"])} contributions between {d_long(stats["first"])} '
             f'and {d_long(stats["last"])}, drawn as an isometric skyline where each '
             f'box is one day and its height is that day\u2019s contribution count.')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
        f'height="{H}" role="img" aria-label="{esc(label)}" font-family="{FONT}">'
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" '
        f'fill="{c["bg"]}" stroke="{c["border"]}"/>'
        + "".join(head) + "".join(body) + "".join(month_svg)
        + "".join(stat_svg) + "".join(legend) + '</svg>'
    )


# --------------------------------------------------------------------------- #

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", help="GitHub login; fetches the live calendar")
    p.add_argument("--data", type=Path,
                   help="cached contributions JSON, used when --user has no token")
    p.add_argument("--save-data", type=Path,
                   help="write the fetched calendar here, refreshing the cache")
    p.add_argument("--out", type=Path, default=Path("assets"))
    p.add_argument("--static", action="store_true",
                   help="no SMIL: one flat skyline, no rise")
    args = p.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    days = user = None

    # Live data first: --data is the offline fallback, not an override. Passing
    # both used to mean the cache always won, so the skyline never refreshed.
    if args.user:
        days = fetch_days(args.user, token)
        user = args.user
        if days:
            print(f"fetched {len(days)} days from the API for {args.user}")
        elif args.data and args.data.exists():
            print("  falling back to the cached calendar", file=sys.stderr)

    if not days and args.data and args.data.exists():
        days = load_days(args.data)
        raw = json.loads(args.data.read_text(encoding="utf-8"))
        user = user or (raw.get("user") if isinstance(raw, dict) else None)
        print(f"read {len(days)} days from {args.data}")

    if not days:
        print("need --user with a token, or --data pointing at a cache",
              file=sys.stderr)
        return 1

    # keep the cache current so a token-less run still has this year to draw
    if args.save_data and args.user:
        args.save_data.write_text(
            json.dumps({"_comment": "Cached contribution calendar, written by "
                                    "scripts/skyline.py. Used when no token is "
                                    "available.",
                        "user": args.user,
                        "total": sum(c for _, c in days),
                        "days": [{"date": d, "count": c} for d, c in days]},
                       indent=2) + "\n", encoding="utf-8")
        print(f"refreshed {args.save_data}")

    cells, weeks, busiest = build_grid(days)
    if not cells:
        print("no contribution days to draw", file=sys.stderr)
        return 1
    stats = compute_stats(cells)
    print(f"  {stats['total']} contributions over {weeks} weeks, "
          f"busiest {stats['busiest'][0]} on {stats['busiest'][1]}")

    args.out.mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        svg = render(cells, weeks, busiest, stats, theme,
                     animate=not args.static, user=user or "")
        dest = args.out / f"skyline-{theme}.svg"
        dest.write_text(svg, encoding="utf-8")
        print(f"wrote {dest}  ({len(svg) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
