#!/usr/bin/env python3
"""
profilecard.py - render the biodata card as a terminal window that types itself.

    python scripts/profilecard.py
    python scripts/profilecard.py --json assets/profile.json --out assets

Writes <out>/card-profile-dark.svg and <out>/card-profile-light.svg from
assets/profile.json. Stdlib only, no network: the card is static geometry plus
CSS keyframes, so it animates anywhere GitHub renders an SVG (including as an
<img> in a README, where script is the only thing that is blocked).

The typing is done character by character: every glyph is its own <tspan> with
an animation-delay, so the code arrives in reading order instead of all at once.
A block caret blinks at the end and the window itself fades in, which is the
only thing that plays on repeat.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

# --------------------------------------------------------------------------- #
# themes
# --------------------------------------------------------------------------- #

THEMES = {
    "dark": {
        "win": "#090a16", "bar": "#10132a", "border": "#1c2142",
        "title": "#c9c9da", "shadow": "#000000", "shadow_op": "0.5",
        "kw": "#c08cff", "cls": "#f5c56b", "str": "#ff7bac",
        "fn": "#57d3f0", "key": "#e8e8f2", "pn": "#9d9db4",
        "brk": "#57d3f0", "num": "#8ce99a", "cmt": "#79879d",
        "caret": "#aa9bef", "glow": "#aa9bef",
        "red": "#ff5f57", "amber": "#febc2e", "green": "#28c840",
    },
    "light": {
        "win": "#ffffff", "bar": "#f6f7fb", "border": "#d7dae3",
        "title": "#4b4b5a", "shadow": "#1f2328", "shadow_op": "0.13",
        "kw": "#8250df", "cls": "#9a6700", "str": "#cf222e",
        "fn": "#0550ae", "key": "#1f2328", "pn": "#6e7781",
        "brk": "#0550ae", "num": "#116329", "cmt": "#6e7781",
        "caret": "#7c3aed", "glow": "#7c3aed",
        "red": "#ff5f57", "amber": "#febc2e", "green": "#28c840",
    },
}

MONO = ("ui-monospace,'JetBrains Mono','SFMono-Regular',Menlo,Consolas,"
        "'DejaVu Sans Mono','Liberation Mono',monospace")

# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #

WIDTH = 520
BAR = 42          # title bar height
PAD_X = 24
SIZE = 13         # code font size
LH = 21           # code line height
TOP = 26          # first baseline, measured from the top of the window
BOTTOM = 22       # padding under the last baseline
ADV = 0.575       # monospace advance width, as a fraction of the font size
MAX_COLS = 57     # wrap long lists at this many columns
ENTRY = 0.45      # how long the window takes to land before typing starts


def width_of(text: str, size: float = SIZE) -> float:
    return len(text) * size * ADV


# --------------------------------------------------------------------------- #
# the little bit of javascript
# --------------------------------------------------------------------------- #

def q(s) -> str:
    """A quoted JS string literal."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_code(p: dict) -> list[str]:
    """The card body as plain lines of JS, ready to be colourised."""
    var, cls, pkg = p["variable"], p["class"], p["package"]

    def field(key: str, value: str, comma: bool = True) -> list[str]:
        return [f"  {key}: {value}{',' if comma else ''}"]

    def list_field(key: str, items: list[str], comma: bool = True) -> list[str]:
        head = f"  {key}: ["
        cont = " " * len(head)
        tail = "]" + ("," if comma else "")
        chunks, cur = [], ""
        for i, item in enumerate(items):
            piece = q(item) + ("," if i < len(items) - 1 else "")
            if cur and len(cur) + 2 + len(piece) + len(tail) > MAX_COLS - len(head):
                chunks.append(cur)
                cur = ""
            cur += (" " if cur else "") + piece
        chunks.append(cur)
        return [(head if i == 0 else cont) + ch
                + (tail if i == len(chunks) - 1 else "")
                for i, ch in enumerate(chunks)]

    code = [
        f"import {{ {cls} }} from {q(pkg)};",
        "",
        f"const {var} = new {cls}({{",
        *field("name", q(p["name"])),
        *field("handle", q(p["handle"])),
        *field("role", q(p["role"])),
        *field("age", str(p["age"])),
        *field("base", q(p["base"])),
        *list_field("stack", p["stack"]),
        *list_field("tools", p["tools"]),
        *field("focus", q(p["focus"])),
        *field("goal", q(p["goal"])),
        *field("status", q(p["status"]), comma=False),
        "});",
        "",
        f"await {var}.buildPortfolio();",
        "",
        *[f"// {r}" for chunk in wrap(p["repos"], 52) for r in
          (chunk if isinstance(chunk, list) else [chunk])],
        "",
        f"{var}.connect();",
        f"console.log('hello, world');",
    ]
    return code


def wrap(items: list[str], cols: int) -> list[list[str]]:
    """Split items into lines of at most `cols` characters, commas included."""
    lines, cur = [], ""
    for i, item in enumerate(items):
        piece = item + ("," if i < len(items) - 1 else "")
        if cur and len(cur) + 1 + len(piece) > cols:
            lines.append(cur)
            cur = piece
        else:
            cur += (" " if cur else "") + piece
    if cur:
        lines.append(cur)
    return lines or [[]]


# --------------------------------------------------------------------------- #
# colouriser
# --------------------------------------------------------------------------- #

KEYWORDS = {"import", "from", "const", "new", "await", "export", "class",
            "function", "return", "async", "let", "var"}
BRACKETS = "[](){}<>"

import re

LEXER = re.compile(r"(?P<cmt>//[^\n]*)"
                   r"|(?P<str>'(?:[^'\\]|\\.)*')"
                   r"|(?P<num>\b\d+\b)"
                   r"|(?P<word>[A-Za-z_$][\w$]*)"
                   r"|(?P<ws>\s+)"
                   r"|(?P<op>[^\s])")


def tokenize(line: str, cls: str) -> list[tuple[str, str]]:
    """Split one line into (text, role) pairs for colouring."""
    out = []
    for m in LEXER.finditer(line):
        kind = m.lastgroup
        text = m.group()
        rest = line[m.end():]
        nxt = rest.lstrip()[:1]
        if kind == "cmt":
            role = "cmt"
        elif kind == "str":
            role = "str"
        elif kind == "num":
            role = "num"
        elif kind == "word":
            if text in KEYWORDS:
                role = "kw"
            elif text == cls:
                role = "cls"
            elif nxt == ":":
                role = "key"
            else:
                role = "fn"
        elif kind == "op":
            role = "brk" if text in BRACKETS else "pn"
        else:
            role = "pn"
        if out and out[-1][1] == role:
            out[-1] = (out[-1][0] + text, role)
        else:
            out.append((text, role))
    return out


# --------------------------------------------------------------------------- #
# typing schedule
# --------------------------------------------------------------------------- #

def schedule(lines: list[str], step: float, jitter: float, pause: float,
             seed: int = 7) -> list[list[float]]:
    """Reveal time per character, line by line. Returns [line][char]."""
    rnd = random.Random(seed)
    out, t = [], 0.0
    for line in lines:
        row = []
        for _ in line:
            t += step * rnd.uniform(1 - jitter, 1 + jitter)
            row.append(t)
        t += pause
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# svg
# --------------------------------------------------------------------------- #

def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render(code: list[str], filename: str, cls: str, label: str, theme: str,
           step: float, jitter: float, pause: float) -> str:
    c = THEMES[theme]
    delays = schedule(code, step, jitter, pause)
    # The window lands first, then the typing starts. Without this the text
    # appears while the box is still sliding in, so the box reads as lagging
    # behind its own text.
    delays = [[t + ENTRY for t in row] for row in delays]
    total = max((max(r) for r in delays if r), default=0.0)  # when the last glyph lands
    height = BAR + TOP + (len(code) - 1) * LH + BOTTOM

    css = [
        # one keyframe set for every character reveal; `both` holds a glyph at
        # opacity 0 through its delay, then pops it in for a single frame
        "@keyframes tp{from{opacity:0}to{opacity:1}}",
        ".r{animation:tp 1ms linear both}",
        "@keyframes in{from{opacity:0;transform:translateY(6px)}"
        "to{opacity:1;transform:none}}",
        f".win{{animation:in {ENTRY}s cubic-bezier(.2,.8,.3,1) both}}",
        # The caret is a solid block while the text is still arriving and only
        # starts blinking once the last glyph has landed. No fill-mode, so
        # before the delay the caret simply sits there at full opacity.
        "@keyframes bl{0%,49%{opacity:1}50%,100%{opacity:0}}",
        f".caret{{animation:bl 1.1s steps(1,end) {total:.2f}s infinite}}",
        # leading indentation is a run of spaces inside a tspan, which browsers
        # drop unless the text content is told to keep them
        ".code text{white-space:pre}",
    ]

    body = [
        f'<defs><filter id="sh" x="-25%" y="-25%" width="150%" height="160%">'
        f'<feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="{c["shadow"]}" '
        f'flood-opacity="{c["shadow_op"]}"/></filter></defs>',
        f'<g class="win">',
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="13" '
        f'fill="{c["win"]}" filter="url(#sh)"/>',
        # title bar, rounded only at the top
        f'<path d="M.5,13.5 A13,13 0 0 1 13.5,.5 H{WIDTH - 13.5} '
        f'A13,13 0 0 1 {WIDTH - .5},13.5 V{BAR} H.5 Z" fill="{c["bar"]}"/>',
        f'<rect x=".5" y="{BAR}" width="{WIDTH - 1}" height=".75" fill="{c["border"]}"/>',
        f'<rect x=".5" y=".5" width="{WIDTH - 1}" height="{height - 1}" rx="13" '
        f'fill="none" stroke="{c["border"]}"/>',
    ]

    # traffic lights
    for i, tone in enumerate(("red", "amber", "green")):
        body.append(f'<circle cx="{22 + i * 17}" cy="{BAR / 2:.0f}" r="5.5" '
                    f'fill="{c[tone]}"/>')

    body.append(f'<text x="{WIDTH / 2:.0f}" y="{BAR / 2 + 4.5:.1f}" text-anchor="middle" '
                f'font-family="{MONO}" font-size="12.5" font-weight="600" '
                f'letter-spacing=".3" fill="{c["title"]}">{esc(filename)}</text>')

    # code, one tspan per character so it can arrive in reading order
    n = 0
    lines = []
    # Caret track: where the block cursor sits after every character, and
    # where it sits at the start of each line. The caret is stepped through
    # these with SMIL, on the same clock as the glyph reveals, so it never
    # lags behind the text it belongs to.
    caret_x: list[tuple[float, float]] = []
    caret_y: list[tuple[float, float]] = []
    for i, (line, times) in enumerate(zip(code, delays)):
        if not line:
            lines.append("")
            continue
        spans = []
        lead = len(line) - len(line.lstrip(" "))
        if lead:
            spans.append(f'<tspan class="r" fill="none" opacity="0">'
                         f'{" " * lead}</tspan>')
        line_y = BAR + TOP + i * LH - SIZE + 3
        k = lead  # indentation is typed too, it just has nothing to draw
        for text, role in tokenize(line[lead:], cls):
            for ch in text:
                t = times[k]
                k += 1
                n += 1
                style = f' font-style="italic"' if role == "cmt" else ""
                spans.append(f'<tspan class="r d{n}" fill="{c[role]}"{style}>{esc(ch)}</tspan>')
                css.append(f".d{n}{{animation-delay:{t:.3f}s}}")
                # The block sits just after the character that just landed.
                caret_x.append((t, PAD_X + width_of(line[:k])))
                if len(caret_y) == 0 or caret_y[-1][1] != line_y:
                    caret_y.append((t, line_y))
        lines.append("".join(spans))

    # SMIL with calcMode="discrete" requires the first keyTime to be exactly
    # 0, otherwise the whole animation is treated as an error and silently
    # ignored — the caret then sits at its initial x for the entire run.
    # So each track gets a leading stop at t=0 holding the start position.
    if caret_x:
        caret_x.insert(0, (0.0, caret_x[0][1]))
        caret_y.insert(0, (0.0, caret_y[0][1]))

    # textLength pins every line to the same advance the caret is placed with, so
    # the block lands on the last glyph whatever monospace the viewer has
    body.append(f'<g class="code" font-family="{MONO}" font-size="{SIZE}" '
                f'xml:space="preserve" fill="{c["key"]}">')
    for i, spans in enumerate(lines):
        y = BAR + TOP + i * LH
        if spans:
            body.append(f'<text x="{PAD_X}" y="{y}" textLength="{width_of(code[i]):.1f}" '
                        f'lengthAdjust="spacing">{spans}</text>')
    body.append("</g>")

    # Block caret that rides the typing head. Two discrete SMIL animations
    # move it in x (one stop per character) and y (one stop per line), on
    # the same clock as the glyph reveals, so the caret is never left
    # behind at the end of the file while the text is still arriving.
    if caret_x:
        span = max(t for t, _ in caret_x) or 1.0
        kt_x = ";".join(f"{t / span:.4f}" for t, _ in caret_x)
        v_x = ";".join(f"{x:.1f}" for _, x in caret_x)
        span_y = max(t for t, _ in caret_y) or 1.0
        kt_y = ";".join(f"{t / span_y:.4f}" for t, _ in caret_y)
        v_y = ";".join(f"{y:.1f}" for _, y in caret_y)
        body.append(
            f'<rect class="caret" x="{caret_x[0][1]:.1f}" y="{caret_y[0][1]:.1f}" '
            f'width="{SIZE * ADV:.1f}" height="{SIZE + 2}" rx="2.5" '
            f'fill="{c["caret"]}">'
            f'<animate attributeName="x" begin="0s" dur="{span:.2f}s" '
            f'calcMode="discrete" keyTimes="{kt_x}" values="{v_x}"/>'
            f'<animate attributeName="y" begin="0s" dur="{span_y:.2f}s" '
            f'calcMode="discrete" keyTimes="{kt_y}" values="{v_y}"/>'
            f'</rect>'
        )
    body.append("</g>")

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
            f'viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{esc(label)}">'
            f'<style>{"".join(css)}</style>{"".join(body)}</svg>')


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", type=Path, default=Path("assets/profile.json"),
                   help="biodata to render (default: assets/profile.json)")
    p.add_argument("--out", type=Path, default=Path("assets"))
    p.add_argument("--step", type=float, default=0.020,
                   help="seconds per character (default 0.020)")
    p.add_argument("--jitter", type=float, default=0.45,
                   help="per-character speed variation, 0-1 (default 0.45)")
    p.add_argument("--pause", type=float, default=0.16,
                   help="extra beat at the end of each line (default 0.16)")
    p.add_argument("--dump", action="store_true",
                   help="print the generated JS instead of writing the SVGs")
    args = p.parse_args(argv)

    if not args.json.exists():
        raise SystemExit(f"no such file: {args.json}")
    profile = json.loads(args.json.read_text(encoding="utf-8"))

    code = build_code(profile)
    if args.dump:
        print("\n".join(code))
        return

    widest = max(width_of(line) for line in code)
    if PAD_X * 2 + widest > WIDTH:
        print(f"  !! longest line is {widest:.0f}px, card is {WIDTH - PAD_X * 2}px "
              f"wide â€” shorten a field in {args.json}")

    label = (f"{profile['name']} â€” {profile['role']}, {profile['age']}, "
             f"{profile['base']}. Stack: {', '.join(profile['stack'])}. "
             f"{profile['status']}.")
    filename = profile.get("filename", "profile.js")
    print(f"{len(code)} lines, {widest:.0f}px wide, {sum(len(l) for l in code)} chars")

    args.out.mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        svg = render(code, filename, profile["class"], label, theme,
                     args.step, args.jitter, args.pause)
        dest = args.out / f"card-profile-{theme}.svg"
        dest.write_text(svg, encoding="utf-8")
        print(f"wrote {dest}  ({len(svg) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()