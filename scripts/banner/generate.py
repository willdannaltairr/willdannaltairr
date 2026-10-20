#!/usr/bin/env python3
"""Generate the animated GitHub profile banners.

Run from the repository root:
    python scripts/banner/generate.py
"""

from __future__ import annotations

import json
import math
import random
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from scipy import ndimage
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from profilecard import esc, width_of  # noqa: E402

SOURCE = ROOT / "assets/source/siganteng.jpeg"
ASSETS = ROOT / "assets"
PROFILE = ASSETS / "profile.json"
LOGOS = Path(__file__).resolve().parent / "logos"
DATA = Path(__file__).resolve().parent / "data"
SOURCE_DIR = ROOT / "assets" / "source"

# Urutan morphing: portrait -> tb -> rpl -> altair -> code -> stellar ->
# sheriff -> portrait. Badge sheriff menutup rantai, jadi video kembali
# ke foto setelah lencana.
LOGO_ORDER = ["tb", "rpl", "altair", "code", "stellar", "sheriff"]
# Tiap logo punya karakter geraknya sendiri: seberapa jauh partikel
# mengembang saat morph (bloom), seberapa kecil layer titiknya mendarat
# (skala), dan seberapa tebal tinta yang mekar saat bentuk itu mengunci
# (lebar garis). Nilai berbeda-beda bikin tiap perpindahan punya
# cirinya sendiri, bukan satu pola yang diulang.
LOGO_BLOOM = (12.0, 14.0, 10.5, 13.0, 15.0, 11.5)
LOGO_ARRIVE = (0.86, 0.90, 0.83, 0.88, 0.92, 0.85)
LOGO_SETTLE = (2.4, 2.1, 2.7, 2.3, 2.0, 2.6)
# Semua bentuk (foto + 5 logo) tampil sama lama: satu slot PHASE_SECONDS
# dengan morph seragam MORPH_SECONDS, sisanya diam (hold). Morph dipecah
# dua babak dengan titik tengah yang mengembang keluar (bloom) supaya
# transisinya halus, lembut, dan tidak terasa patah-patah.
PHASE_SECONDS = 3.4
MORPH_SECONDS = 1.7
MORPH_BLOOM = 12.0

W, H = 1180, 786
# Poster masthead (ala koran WANTED) mendorong panel ke bawah agar muat
# headline besar + papan nama. Semua koordinat animasi ikut digeser.
MASTHEAD_SHIFT = 140.0
# Pusat logo di frame visual map. Semua logo diletakkan di bidang 350x350
# yang berpusat di titik ini, jadi skala animasi harus terhadap titik
# tersebut supaya bentuknya melayang masuk, bukan menjauh dari asal.
LOGO_CX = 69.0 + 200.0 * 0.875
LOGO_CY = 154.0 + MASTHEAD_SHIFT + 200.0 * 0.875
INTRO_SECONDS = 3.2
# 2000 partikel terbang + layer titik padat per logo (kisi 2px, cap 55%)
# supaya siluet tb/rpl/altair/code/stellar tampil padat dan jelas.
TRAVELLER_COUNT = 2000
LOGO_DOT_STEP = 2
LOGO_DOT_CAP = 0.55
SEED = 314159

# Foto di-dither pada kisi internal 2x (langkah 0.5px poster) supaya
# detail wajah/tas/sepatu tampil jauh lebih halus; titiknya tetap
# digambar sebagai bar kecil selebar satu sel kisi internal.
PORTRAIT_GRID = 2
PORTRAIT_STEP = 1.0 / PORTRAIT_GRID
PORTRAIT_WHITE = 190.0
PORTRAIT_STROKE = 1.5 * PORTRAIT_STEP

BIO_X = 496.0
BIO_SIZE = 15.5
BIO_LH = 22.0
BIO_TOP = 146.0 + MASTHEAD_SHIFT
# Ritme ketikan: jeda dasar per karakter + variasi (jitter) supaya terasa
# seperti diketik manusia, plus jeda ekstra setelah spasi/tanda baca dan
# jeda lebih panjang saat pindah baris. Tidak terlalu cepat, tidak terlalu
# lama: total satu putaran ketikan dibuat sekitar 10-11 detik.
BIO_STEP = 0.021
BIO_JITTER = 0.55
BIO_SPACE_PAUSE = 0.018
BIO_PUNCT_PAUSE = 0.055
BIO_LINE_PAUSE = 0.26
# Jeda setelah ketikan selesai sebelum animasi mengetik diulang dari kosong.
BIO_LOOP_PAUSE = 4.0

# Vintage western/newspaper stacks. Disengaja system-fonts saja supaya
# tampil sama di GitHub <img> tanpa webfont.
MASTHEAD_SERIF = "Georgia,'Times New Roman','Nimbus Roman',serif"
WANTED_SLAB = "Impact,Haettenschweiler,'Arial Narrow Bold','Franklin Gothic Heavy',sans-serif"
TYPEWRITER = "'Courier New',Courier,'Nimbus Mono PS',monospace"
MONO_UI = "ui-monospace,SFMono-Regular,Consolas,monospace"

# Satu palet untuk dua tema. GitHub memilih berkas lewat
# prefers-color-scheme, tapi isi posternya sama persis, jadi tidak ada
# jarring antara mode terang dan gelap.
_PALETTE = {
    "bg": "#E8BF93",
    # Lembar kertas tua: terang di tengah, makin amber ke tepi. Empat
    # stop ini yang dipakai gradien paperWash, jadi bukan warna rata.
    "wash": ("#FFFEFA", "#FCF5E7", "#F9EBD4", "#F0D4AC"),
    # Kotak VISUAL MAP dan kotak DESCRIPTIVE INFORMATION (yang memuat
    # animasi typing) memakai panel yang sama, jadi keduanya terbaca
    # sebagai satu keluarga di poster.
    "panel": "#FDF8EC",
    # Pinggiran kedua kotak: amber tua yang lebih gelap dari bg kertas,
    # satu nada dengan kertasnya tapi cukup pekat untuk memisahkan kotak
    # dari bidang di sekitarnya.
    "edge": "#8A5A2B",
    "line": "#4a3b2a",
    "muted": "#77603f",
    "text": "#2b1e10",
    "portrait": "#000000",
    "chrome": "#2b1e10",
    "accent": "#7a1f1f",
    "shadow": "#b09060",
}

THEMES = {"dark": dict(_PALETTE), "light": dict(_PALETTE)}

# BIO panel sekarang plain-text satu tinta (gaya ketikan koran),
# bukan syntax-color code. Kuncinya satu warna tinta untuk semua peran.
# Kartu biodata di dalam poster juga satu palet untuk dua tema, sama
# seperti kertasnya. Dua tema memakai palet identik, jadi teks, caret, dan
# kotak-kotaknya tidak boleh berbeda warna sedikit pun.
BANNER_CARD_PALETTE = {
    "kw": "#2b2117", "cls": "#2b2117", "str": "#2b2117",
    "fn": "#2b2117", "key": "#2b2117", "pn": "#2b2117",
    "brk": "#2b2117", "num": "#2b2117", "cmt": "#77644a",
    "caret": "#7a1f1f",
}

BANNER_CARD_THEMES = {
    "dark": dict(BANNER_CARD_PALETTE),
    "light": dict(BANNER_CARD_PALETTE),
}


def photo_logo_to_silhouette(path: Path, size: int = 400, threshold: int = 200) -> Image.Image:
    """Convert a JPEG logo (white background) to black-on-transparent silhouette.

    Dark pixels (luminance < threshold) become the opaque shape so the result
    matches the silhouette format used by sample_logo_points().
    """
    photo = Image.open(path).convert("RGB")
    fitted = photo.copy()
    fitted.thumbnail((350, 350), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    gray = np.asarray(ImageOps.grayscale(canvas))
    mask = gray < threshold
    silhouette = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    black = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    alpha = Image.fromarray(np.uint8(mask * 255), "L")
    silhouette.paste(black, mask=alpha)
    return silhouette


def make_logos() -> dict[str, Image.Image]:
    """Create clean 400px black-on-transparent silhouette sources."""
    LOGOS.mkdir(parents=True, exist_ok=True)
    size = 400
    logos: dict[str, Image.Image] = {}

    # Rust-inspired gear: twelve teeth, heavy annulus, and hub cutout.
    rust = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(rust)
    center = np.array([200.0, 200.0])
    outer: list[tuple[float, float]] = []
    for tooth in range(12):
        base = tooth * math.tau / 12
        for offset, radius in [
            (-0.42, 150), (-0.30, 178), (0.30, 178), (0.42, 150)
        ]:
            a = base + offset * math.tau / 12
            outer.append(tuple(center + radius * np.array([math.cos(a), math.sin(a)])))
    d.polygon(outer, fill="black")
    d.ellipse((70, 70, 330, 330), fill="black")
    d.ellipse((128, 128, 272, 272), fill=(0, 0, 0, 0))
    d.ellipse((174, 174, 226, 226), fill="black")
    logos["rust"] = rust

    # Sheriff badge from the reference photo: a five-pointed star inside
    # the banded ring, with a round seal at the hub. Drawn as a filled
    # silhouette so it can be sampled into dots like the other marks.
    sheriff = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(sheriff)
    cx, cy = 200.0, 200.0
    d.ellipse(
        (cx - 186, cy - 186, cx + 186, cy + 186), outline="black", width=26
    )
    star_pts: list[tuple[float, float]] = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        r = 150 if i % 2 == 0 else 62
        star_pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(star_pts, fill="black")
    d.ellipse((cx - 46, cy - 46, cx + 46, cy + 46), fill="black")
    logos["sheriff"] = sheriff

    # </> mark built from broad, rounded strokes.
    code = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(code)
    stroke = 42
    d.line([(154, 95), (66, 200), (154, 305)], fill="black", width=stroke, joint="curve")
    d.line([(246, 95), (334, 200), (246, 305)], fill="black", width=stroke, joint="curve")
    d.line([(225, 72), (174, 328)], fill="black", width=stroke)
    logos["code"] = code

    # Stellar-inspired four-point star with a small connected node network.
    stellar = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(stellar)
    star: list[tuple[float, float]] = []
    for i in range(16):
        a = -math.pi / 2 + i * math.pi / 8
        radius = 137 if i % 4 == 0 else (45 if i % 2 == 0 else 25)
        star.append((200 + math.cos(a) * radius, 200 + math.sin(a) * radius))
    d.polygon(star, fill="black")
    nodes = [(82, 112), (322, 106), (326, 300), (88, 316)]
    for a, b in zip(nodes, nodes[1:] + nodes[:1]):
        d.line([a, b], fill="black", width=12)
    for x, y in nodes:
        d.ellipse((x - 19, y - 19, x + 19, y + 19), fill="black")
    logos["stellar"] = stellar

    # Photo logos (tb/rpl/altair) + SVG code/stellar dipertahankan.
    photo_sources = {
        "tb": SOURCE_DIR / "logo tb.jpg",
        "rpl": SOURCE_DIR / "logo rpl.jpg",
        "altair": SOURCE_DIR / "logo altair.jpg",
    }
    for name, path in photo_sources.items():
        if not path.exists():
            raise SystemExit(f"Missing logo source: {path}")
        logos[name] = photo_logo_to_silhouette(path, size=size)

    for name, image in logos.items():
        image.save(LOGOS / f"{name}.png", optimize=True)
    return logos


@lru_cache(maxsize=None)
def blue_noise_ranks(height: int, width: int) -> np.ndarray:
    """Rank tiap sel kisi (0..1) memakai urutan low-discrepancy R2.

    Dipakai sebagai ambang dithering: sel ber-rank kecil lebih dulu
    menjadi tinta, sehingga sebaran titik selalu rata di semua nada —
    tidak membentuk garis/gelombang seperti error-diffusion.
    """
    total = height * width
    ranks = np.full((height, width), -1, dtype=np.int64)
    alpha1, alpha2 = 0.7548776662466927, 0.5698402909980532
    filled = 0
    order = 0
    n = 0
    limit = total * 3
    while filled < total and n < limit:
        px = (0.5 + alpha1 * n) % 1.0
        py = (0.5 + alpha2 * n) % 1.0
        cx = min(width - 1, int(px * width))
        cy = min(height - 1, int(py * height))
        if ranks[cy, cx] < 0:
            ranks[cy, cx] = order
            order += 1
            filled += 1
        n += 1
    # Sisa lubang (sangat sedikit) diisi urutan raster di akhir.
    for cy in range(height):
        row = ranks[cy]
        for cx in range(width):
            if row[cx] < 0:
                row[cx] = order
                order += 1
    return ranks / float(total)


# Zona 370x380 foto yang NADANYA mirip dinding (tetap bagian orang):
# ubun-ubun kena lampu, wajah, tangan, kaos kaki, dan tutup atas tas
# (lipatan kulit terang yang nadanya mirip bangku). Ambang tiap zona
# dipilih dari pengukuran piksel supaya dinding di sekitarnya tetap di luar.
PORTRAIT_CORE = 60.0
PORTRAIT_ZONES = (
    ((182, 84, 216, 106), 140.0),
    ((172, 102, 208, 154), 150.0),
    ((148, 160, 192, 208), 150.0),
    ((170, 264, 208, 298), 150.0),
    ((98, 166, 154, 246), 125.0),
)


def subject_mask(v: np.ndarray) -> np.ndarray:
    """Mask "orang saja" untuk foto ini (koordinat khusus foto 370x380).

    Dinding kuning, bangku, celah, dan bayangannya dibuang supaya foto
    tidak tampil sebagai kotak bertitik; badan, wajah, tangan, tas
    (termasuk lipatan atasnya), dan sepatu (sampai ujung sol) dipertahankan.
    Caranya: ambang inti gelap + zona terang subjek, lalu morfologi supaya
    bagian terang yang nadanya mirip latar tetap utuh.
    """
    m = v < PORTRAIT_CORE
    for (x0, y0, x1, y1), limit in PORTRAIT_ZONES:
        zone = np.zeros_like(m)
        zone[y0:y1, x0:x1] = True
        m |= (v < limit) & zone
    m = ndimage.binary_opening(m, iterations=1)
    # Buang bingkai: tepi foto (cincin gelap JPEG), kolom tepi foto, dan
    # sudut kiri-bawah (bayangan lantai/bangku). Baris bawah dipotong di
    # bawah ujung sol sepatu supaya sepatu tampil utuh (tidak terpotong).
    m[:3, :] = False
    m[375:, :] = False
    m[:, :3] = False
    m[:, -3:] = False
    m[:, 40:47] = False
    m[:, 322:329] = False
    m[342:, :140] = False
    # Buang komponen yang menyentuh tepi gambar atau terlalu kecil (bintik
    # dinding + garis celah vertikal di kanan).
    labels, count = ndimage.label(m)
    border = (
        set(labels[0, :].tolist())
        | set(labels[-1, :].tolist())
        | set(labels[:, 0].tolist())
        | set(labels[:, -1].tolist())
    )
    border.discard(0)
    sizes = ndimage.sum(m, labels, range(1, count + 1))
    small = {i + 1 for i, size in enumerate(sizes) if size < 40}
    keep = [i for i in range(1, count + 1) if i not in border and i not in small]
    m = np.isin(labels, keep)
    # Rapatkan wajah/tangan ke badan lalu isi rongga terang di dalam subjek.
    m = ndimage.binary_closing(m, iterations=3)
    m = ndimage.binary_fill_holes(m)
    m = ndimage.binary_opening(m, iterations=1)
    return ndimage.binary_dilation(m, iterations=1)


def portrait_points() -> np.ndarray:
    """Return x/y banner coordinates from the dithered portrait grid.

    Foto ditempel di kertas putih, dinding/bangku dibuang lewat mask
    "orang saja", nada dinaikkan (high-key) supaya wajah terang, lalu sel
    GELAP di-dither dengan mask R2 (blue-noise) sehingga semua titik rata,
    jelas, dan tidak bergaris. Dither dijalankan pada kisi internal 2x
    (langkah PORTRAIT_STEP) supaya detailnya halus. Dua tema memakai hasil
    identik.
    """
    source = Image.open(SOURCE).convert("RGBA")
    # Full photo, never cropped: scale the whole image to fit inside the
    # 370x380 MYSELF frame (aspect preserved) and centre it. The side
    # bars stay transparent, so the dither only ever draws the photo itself.
    frame_w, frame_h = 370, 380
    grid_w = frame_w * PORTRAIT_GRID
    grid_h = frame_h * PORTRAIT_GRID
    fitted = source.copy()
    fitted.thumbnail((grid_w, grid_h), Image.Resampling.LANCZOS)
    crop = Image.new("RGBA", (grid_w, grid_h), (0, 0, 0, 0))
    crop.alpha_composite(
        fitted, ((grid_w - fitted.width) // 2, (grid_h - fitted.height) // 2)
    )
    white = Image.new("RGBA", crop.size, "white")
    white.alpha_composite(crop)
    base = ImageOps.grayscale(white.convert("RGB"))

    # Mask "orang saja" dihitung di kisi 1x (koordinat zona terverifikasi),
    # lalu di-upscale 2x supaya tiap sel kisi internal mewarisi sel aslinya.
    coarse_fit = source.copy()
    coarse_fit.thumbnail((frame_w, frame_h), Image.Resampling.LANCZOS)
    coarse = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    coarse.alpha_composite(
        coarse_fit,
        ((frame_w - coarse_fit.width) // 2, (frame_h - coarse_fit.height) // 2),
    )
    coarse_white = Image.new("RGBA", coarse.size, "white")
    coarse_white.alpha_composite(coarse)
    coarse_base = ImageOps.grayscale(coarse_white.convert("RGB"))
    mask = np.kron(
        subject_mask(np.asarray(coarse_base).astype(np.float32)),
        np.ones((PORTRAIT_GRID, PORTRAIT_GRID), dtype=bool),
    )

    # Kurva high-key: titik putih lembut di PORTRAIT_WHITE membuat kulit
    # wajah tetap terang, sementara jaket & rambut tetap pekat. Tanpa ini
    # wajah ikut menyatu jadi blob hitam.
    prepared = base.point(
        [
            int(round(min(v / PORTRAIT_WHITE, 1.0) ** 0.9 * 255))
            for v in range(256)
        ]
    )
    prepared = prepared.filter(
        ImageFilter.UnsharpMask(radius=3, percent=200, threshold=1)
    )

    # Dithering ambang dengan mask R2 (blue-noise): tiap sel gelap
    # menyala sesuai rank mask-nya, jadi sebaran titik rata di semua
    # nada — tidak membentuk garis/gelombang seperti error-diffusion.
    gray = np.asarray(prepared).astype(np.float32)
    alpha = np.asarray(crop.getchannel("A"))
    darkness = 1.0 - gray / 255.0
    active = (
        (darkness > blue_noise_ranks(grid_h, grid_w))
        & (alpha > 0)
        & mask
    )

    # Tanpa cap: setiap sel lattice yang gelap ikut digambar supaya
    # gambar sepadat dan sejelas mungkin.
    ys, xs = np.where(active)
    if len(xs) == 0:
        return np.zeros((0, 2), dtype=np.float32)
    return np.column_stack(
        (
            59 + xs * PORTRAIT_STEP,
            139 + MASTHEAD_SHIFT + ys * PORTRAIT_STEP,
        )
    ).astype(np.float32)


def logo_dots(image: Image.Image) -> np.ndarray:
    """Titik padat kisi 2px untuk siluet logo, dalam koordinat banner.

    Sel disaring dengan mask R2 (cap LOGO_DOT_CAP) supaya siluet tampil
    sebagai matriks titik yang rata dan jelas — bukan blok solid.
    Logo menempati bidang 350x350 di tengah frame MYSELF.
    """
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.where(alpha > 127)
    # Kisi 2px: hanya piksel genap agar antar titik ada jarak merata.
    keep = (xs % LOGO_DOT_STEP == 0) & (ys % LOGO_DOT_STEP == 0)
    xs, ys = xs[keep], ys[keep]
    fx = 69.0 + xs * 0.875
    fy = 154.0 + MASTHEAD_SHIFT + ys * 0.875
    ranks = blue_noise_ranks(380, 370)
    cx = np.clip(np.rint(fx - 59.0).astype(int), 0, 369)
    cy = np.clip(np.rint(fy - 279.0).astype(int), 0, 379)
    capped = ranks[cy, cx] < LOGO_DOT_CAP
    return np.column_stack((fx[capped], fy[capped])).astype(np.float32)


def sample_logo_points(
    points: np.ndarray, rng: np.random.Generator, count: int
) -> np.ndarray:
    """Pilih `count` titik terbang dari layer titik padat logo."""
    chosen = rng.choice(len(points), count, replace=len(points) < count)
    return points[chosen]


def transport(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Order target points by minimum-cost assignment from source points."""
    rows, cols = linear_sum_assignment(cdist(source, target, metric="sqeuclidean"))
    ordered = np.empty_like(target)
    ordered[rows] = target[cols]
    return ordered


def num(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def point_path(points: np.ndarray, step: float = 1.0) -> str:
    """Aggregate adjacent horizontal dots into compact SVG path runs.

    Titik boleh berada di kisi halus (step < 1, mis. 0.5px untuk foto
    resolusi ganda); run yang berdekatan tetap digabung jadi satu bar.
    """
    if not len(points):
        return ""
    quantized = np.rint(np.asarray(points) / step).astype(int)
    unique = sorted(
        {(int(x), int(y)) for x, y in quantized}, key=lambda p: (p[1], p[0])
    )
    chunks: list[str] = []
    i = 0
    while i < len(unique):
        x0, y = unique[i]
        x1 = x0
        i += 1
        while i < len(unique) and unique[i][1] == y and unique[i][0] <= x1 + 1:
            x1 = unique[i][0]
            i += 1
        chunks.append(
            f"M{num(x0 * step)} {num(y * step)}h{num((x1 - x0 + 1) * step)}"
        )
    return "".join(chunks)


def bio_plain_lines(p: dict) -> list[str]:
    """Biodata sebagai teks polos gaya koran (bukan syntax code)."""
    stack = " - ".join(p["stack"][:6])
    tools = ", ".join(p["tools"])
    return [
        f"{p['name']}",
        f"{p['role']} - {p['age']} - {p['base']}",
        "",
        f"STACK: {stack}",
        f"TOOLS: {tools}",
        f"FOCUS: {p['focus']}",
        "",
        f"GOAL: {p['goal']}.",
        f"STATUS: {p['status']}.",
        "",
        f"{p['package']} - hello, world.",
    ]


def bio_delays(code: list[str]) -> list[list[float]]:
    """Waktu reveal per karakter dengan ritme ketikan yang natural.

    Jeda antar karakter bervariasi (jitter), ada jeda ekstra setelah spasi
    dan tanda baca, dan jeda lebih panjang saat pindah baris — supaya
    terasa benar-benar seperti sedang diketik: tidak terlalu cepat, tapi
    juga tidak terlalu lama.
    """
    rnd = random.Random(7)
    out: list[list[float]] = []
    t = 0.0
    for line in code:
        row: list[float] = []
        for ch in line:
            t += BIO_STEP * rnd.uniform(1.0 - BIO_JITTER, 1.0 + BIO_JITTER)
            row.append(t)
            if ch == " ":
                t += BIO_SPACE_PAUSE * rnd.uniform(0.6, 1.4)
            elif ch in ".,:;!?":
                t += BIO_PUNCT_PAUSE * rnd.uniform(0.7, 1.5)
            elif ch in "-/&":
                t += BIO_PUNCT_PAUSE * 0.5 * rnd.uniform(0.6, 1.2)
        t += BIO_LINE_PAUSE * rnd.uniform(0.85, 1.25)
        out.append(row)
    return out


def bio_panel(profile: dict, theme: str) -> tuple[str, str]:
    """Return biodata typing rules + markup as plain typewriter text.

    Animasi mengetik dibuat LOOPING: satu siklus = ketik semua baris,
    jeda (BIO_LOOP_PAUSE), lalu ulang dari kosong. Tiap glyph punya
    keyframes sendiri yang menyembunyikannya dari awal siklus sampai
    giliran ketiknya, jadi resetnya serentak dan urutannya tetap. Bar
    ketik (caret) ikut berjalan mengikuti posisi ketikan lewat animasi
    SMIL discrete, lalu berkedip di ujung teks saat menunggu siklus
    berikutnya.
    """
    card = BANNER_CARD_THEMES[theme]
    code = bio_plain_lines(profile)
    delays = bio_delays(code)
    total = max((max(row) for row in delays if row), default=0.0)
    cycle = total + BIO_LOOP_PAUSE  # durasi satu putaran: ketik + jeda

    css = [
        "@keyframes bl{0%,49%{opacity:1}50%,100%{opacity:0}}",
        ".caret{animation:bl 1.1s steps(1,end) infinite}",
        ".bio text{white-space:pre}",
    ]

    tspans: list[str] = []
    # Posisi caret per karakter: maju setiap glyph muncul, turun ke baris
    # berikutnya begitu barisnya berganti.
    caret_times: list[float] = [0.0]
    caret_xs: list[float] = [BIO_X]
    caret_ys: list[float] = [BIO_TOP - BIO_SIZE + 3]
    n = 0
    for index, (line, times) in enumerate(zip(code, delays)):
        if not line:
            continue
        row = []
        # Baris pertama = nama: tebal + sedikit lebih besar.
        bold = index == 0
        size = BIO_SIZE + 2.5 if bold else BIO_SIZE
        # Tanpa pewarnaan sintaks: semua glyph satu tinta koran.
        for k, ch in enumerate(line):
            delay = times[k]
            n += 1
            # Persen reveal di dalam siklus; 0.01% = lompatan satu frame.
            reveal = delay / cycle * 100.0
            css.append(
                f"@keyframes k{n}{{0%,{reveal:.3f}%{{opacity:0}}"
                f"{reveal + 0.01:.3f}%,100%{{opacity:1}}}}"
            )
            css.append(f".d{n}{{animation:k{n} {cycle:.2f}s linear infinite}}")
            row.append(
                f'<tspan class="d{n}" fill="{card["key"]}">'
                f"{esc(ch)}</tspan>"
            )
            caret_times.append(delay)
            caret_xs.append(BIO_X + width_of(line[: k + 1], size))
            caret_ys.append(BIO_TOP + index * BIO_LH - BIO_SIZE + 3)
        y = BIO_TOP + index * BIO_LH
        weight = "700" if bold else "400"
        tspans.append(
            f'<text x="{num(BIO_X)}" y="{num(y)}" '
            f'font-weight="{weight}" '
            f'font-size="{num(size)}" '
            f'textLength="{width_of(line, size):.1f}" lengthAdjust="spacing">'
            f'{"".join(row)}</text>'
        )

    last = len(code) - 1
    while last > 0 and not code[last]:
        last -= 1
    last_size = BIO_SIZE + 2.5 if last == 0 else BIO_SIZE
    cx = BIO_X + width_of(code[last], last_size) + 1
    cy = BIO_TOP + last * BIO_LH - BIO_SIZE + 3
    key_times = ";".join(
        f"{v / cycle:.5f}".rstrip("0").rstrip(".") for v in caret_times
    )
    values_x = ";".join(num(v) for v in caret_xs)
    values_y = ";".join(num(v) for v in caret_ys)
    caret = (
        f'<g class="bio-caret"><rect class="caret" x="{num(cx)}" y="{num(cy)}" '
        f'width="{num(BIO_SIZE * 0.575)}" height="{num(BIO_SIZE + 2)}" rx="2.5" '
        f'fill="{card["caret"]}">'
        f'<animate attributeName="x" begin="0s" dur="{cycle:.2f}s" '
        f'repeatCount="indefinite" calcMode="discrete" keyTimes="{key_times}" '
        f'values="{values_x}"/>'
        f'<animate attributeName="y" begin="0s" dur="{cycle:.2f}s" '
        f'repeatCount="indefinite" calcMode="discrete" keyTimes="{key_times}" '
        f'values="{values_y}"/>'
        f"</rect></g>"
    )
    head = (
        f'<g class="bio" font-family="{TYPEWRITER}" font-size="{num(BIO_SIZE)}" '
        f'xml:space="preserve" fill="{card["key"]}">'
    )
    return "".join(css), head + "".join(tspans) + caret + "</g>"


def animate_values(points: list[np.ndarray], index: int) -> str:
    return ";".join(f"{num(p[index, 0])} {num(p[index, 1])}" for p in points)


def render_svg(
    theme_name: str,
    portrait: np.ndarray,
    logo_points: dict[str, np.ndarray],
    logo_dense: dict[str, np.ndarray],
    rng: np.random.Generator,
) -> str:
    t = THEMES[theme_name]
    n = min(TRAVELLER_COUNT, len(portrait))
    source = portrait[rng.choice(len(portrait), n, replace=False)]
    # Optimal-transport chain mengikuti LOGO_ORDER:
    # portrait -> tb -> rpl -> altair -> code -> stellar -> portrait.
    ordered: list[np.ndarray] = []
    prev = source
    for name in LOGO_ORDER:
        pts = transport(prev, logo_points[name][:n])
        ordered.append(pts)
        prev = pts

    # Slot seragam: foto & tiap logo tampil sama lama (PHASE_SECONDS),
    # dengan morph seragam MORPH_SECONDS di antaranya. Tiap morph dipecah
    # dua babak (ease-in lalu ease-out) dengan titik tengah yang melengkung
    # keluar dari pusat frame, jadi perpindahannya mengembang halus dan
    # kecepatannya tidak patah di tengah.
    half = MORPH_SECONDS / 2.0
    times: list[float] = [0.0, PHASE_SECONDS]
    cursor = PHASE_SECONDS
    for _ in LOGO_ORDER:
        cursor += half
        times.append(cursor)  # bloom midpoint
        cursor += half
        times.append(cursor)  # tiba di bentuk berikutnya
        cursor += PHASE_SECONDS - MORPH_SECONDS
        times.append(cursor)  # selesai hold
    cursor += half
    times.append(cursor)  # bloom midpoint pulang
    cursor += half
    times.append(cursor)  # kembali ke foto (batas loop)
    loop_seconds = times[-1]
    # Presisi 4 desimal khusus keyTimes: format 1 desimal membuat beberapa
    # key bertabrakan (durasi tiap bentuk jadi tidak seragam).
    key_times = ";".join(
        f"{v / loop_seconds:.4f}".rstrip("0").rstrip(".") for v in times
    )
    # Spline: hold bebas; dua babak morph memakai ease-in yang berakhir
    # dengan kecepatan penuh lalu ease-out yang mulai dengan kecepatan
    # penuh — sambungan keduanya mulus tanpa hentakan.
    hold_spline = "0.42 0 0.58 1"
    in_spline = "0.42 0 1 1"
    out_spline = "0 0 0.58 1"
    splines = [hold_spline]
    for _ in LOGO_ORDER:
        splines += [in_spline, out_spline, hold_spline]
    splines += [in_spline, out_spline]
    key_splines = ";".join(splines)
    # Returning each traveller to its exact starting portrait coordinate keeps
    # the repeat boundary seamless. All logo-to-logo morphs use optimal transport.
    center = np.array([59.0 + 370.0 / 2.0, 139.0 + MASTHEAD_SHIFT + 380.0 / 2.0])

    def bloom(a: np.ndarray, b: np.ndarray, amount: float) -> np.ndarray:
        """Titik tengah perpindahan, didorong keluar dari pusat frame."""
        mid = (a + b) / 2.0
        d = mid - center
        norm = np.hypot(d[:, 0], d[:, 1])
        norm[norm < 1e-6] = 1.0
        return mid + d / norm[:, None] * amount

    frames: list[np.ndarray] = [source, source]
    prev = source
    for slot, pts in enumerate(ordered):
        # Tiap logo punya radius bloom sendiri, jadi lintasan partikel ke
        # bentuk berikutnya berbeda-beda — transisinya tidak terasa seragam.
        frames.extend([bloom(prev, pts, LOGO_BLOOM[slot]), pts, pts])
        prev = pts
    frames.append(bloom(prev, source, MORPH_BLOOM))
    frames.append(source)
    opacity_values = ";".join(["0", "0"] + ["1"] * (len(frames) - 3) + ["0"])
    bio_css, bio_markup = bio_panel(
        json.loads(PROFILE.read_text(encoding="utf-8")), theme_name
    )

    parts: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
        'aria-labelledby="title desc">',
        "<title id=\"title\">Wildan Daffi Altair Darmawan's wanted poster profile</title>",
        '<desc id="desc">Vintage wanted-poster profile with animated dithered '
        "portrait mugshot (MYSELF), TB, RPL, Altair, code, Stellar, and "
        "sheriff badge silhouettes, and a plain-text typing biodata."
        "</desc>",
        "<defs>",
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">'
        f'<feDropShadow dx="0" dy="12" stdDeviation="16" flood-color="{t["shadow"]}" '
        'flood-opacity=".28"/></filter>',
        '<filter id="glow" x="-100%" y="-100%" width="300%" height="300%">'
        f'<feGaussianBlur stdDeviation="3" result="b"/><feFlood flood-color="{t["accent"]}" '
        'flood-opacity=".35"/><feComposite in2="b" operator="in"/>'
        '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        # Tepi tinta kasar ala poster cetakan lama (dipakai headline WANTED).
        '<filter id="rough" x="-6%" y="-14%" width="112%" height="128%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".045 .09" numOctaves="4" seed="11" result="n"/>'
        '<feDisplacementMap in="SourceGraphic" in2="n" scale="6" xChannelSelector="R" yChannelSelector="G"/>'
        '</filter>',
        # --- tekstur kertas tua -------------------------------------------
        # Referensinya selembar stok amber tua: terang di tengah, bercak
        # tidak rata, dan jauh lebih gelap di pinggir. Lapisannya: grain
        # halus, serat panjang, bercak besar (stain), pulp, dan vignette
        # tepi. Semua feTurbulence, jadi tidak menambah byte gambar dan
        # tetap jalan di dalam <img> GitHub tanpa JavaScript.
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
        '<filter id="mottle" x="0" y="0" width="100%" height="100%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".012" numOctaves="5" '
        'seed="7" result="n"/>'
        '<feColorMatrix in="n" type="matrix" '
        'values="0 0 0 0 .42  0 0 0 0 .29  0 0 0 0 .14  0 0 0 .6 -.08"/>'
        '</filter>',
        # Bercak penuaan berskala besar: inilah yang membuat kertas lama
        # terlihat belang, bukan sekadar berbutir. Ambang alfa dibuat tajam
        # (1.7n - .62) supaya terbentuk pulau-pulau, bukan kabut rata.
        '<filter id="stain" x="-5%" y="-5%" width="110%" height="110%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".0045 .006" '
        'numOctaves="4" seed="91" result="n"/>'
        '<feColorMatrix in="n" type="matrix" '
        'values="0 0 0 0 .38  0 0 0 0 .25  0 0 0 0 .11  0 0 0 1.7 -.62"/>'
        '</filter>',
        # Serat kertas koran (pulp) lebih kasar dari grain: frekuensi rendah,
        # kontras tinggi, arah acak — inilah yang membuat kertas cetak murah
        # terlihat "berbintil" saat dilihat dekat.
        '<filter id="pulp" x="0" y="0" width="100%" height="100%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".05" numOctaves="4" '
        'seed="61" result="n"/>'
        '<feColorMatrix in="n" type="saturate" values="0"/>'
        '<feComponentTransfer><feFuncA type="table" '
        'tableValues="0 1 0 1 0 1 0 1 0 1"/></feComponentTransfer>'
        '</filter>',
        # Tepi sobek: kertas lama tidak pernah bertepi lurus. Displacement
        # kecil (3px) pada rect kertas membuat garis luarnya bergelombang.
        '<filter id="deckle" x="-3%" y="-3%" width="106%" height="106%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".018" numOctaves="3" '
        'seed="5" result="n"/>'
        '<feDisplacementMap in="SourceGraphic" in2="n" scale="4" '
        'xChannelSelector="R" yChannelSelector="G"/>'
        '</filter>',
        # Cahaya jatuh di tengah lembar: gradien ini yang menggantikan warna
        # kertas rata.
        '<radialGradient id="paperWash" cx=".5" cy=".42" r=".8">'
        f'<stop offset="0" stop-color="{t["wash"][0]}"/>'
        f'<stop offset=".52" stop-color="{t["wash"][1]}"/>'
        f'<stop offset=".84" stop-color="{t["wash"][2]}"/>'
        f'<stop offset="1" stop-color="{t["wash"][3]}"/>'
        '</radialGradient>',
        f'<clipPath id="paperClip"><rect x="13" y="13" width="1154" '
        f'height="{H - 26}" rx="13"/></clipPath>',
        # Tepi menggelap ke arah amber: di referensi tengahnya krem terang
        # (#FAEFDB) dan pinggirnya oranye-tan (#E3B788), jadi vignette-nya
        # hangat, bukan coklat tua.
        '<radialGradient id="age" cx=".5" cy=".46" r=".8">'
        '<stop offset=".60" stop-color="#C97A1C" stop-opacity="0"/>'
        '<stop offset=".78" stop-color="#CB7C1E" stop-opacity=".07"/>'
        '<stop offset=".91" stop-color="#CD7E20" stop-opacity=".18"/>'
        '<stop offset="1" stop-color="#CF8022" stop-opacity=".34"/>'
        '</radialGradient>',
        f'<clipPath id="visualClip"><rect x="49" y="{num(124 + MASTHEAD_SHIFT)}" width="390" height="414" rx="3"/></clipPath>',
        "</defs>",
        f"<style>{bio_css}</style>",
        # Lembar kertas: tepi luar yang tidak lurus (deckle), lalu gradien
        # amber yang terang di tengah dan pekat di pinggir.
        f'<g filter="url(#deckle)">'
        f'<rect x="13" y="13" width="1154" height="{H - 26}" rx="13" '
        f'fill="url(#paperWash)" stroke="{t["line"]}" stroke-width="3" '
        f'filter="url(#shadow)"/></g>',
        f'<rect x="23" y="23" width="1134" height="{H - 46}" rx="9" fill="none" '
        f'stroke="{t["line"]}" stroke-width="1"/>',
        # Lubang pin di tengah atas: di referensi kertas ini digantung dengan
        # satu paku kecil, dan lubangnya jadi gelap karena bayangan di dalam.
        '<g>'
        '<ellipse cx="590" cy="27" rx="4.6" ry="5.2" fill="#2a1a08" opacity=".55"/>'
        '<ellipse cx="590" cy="26.4" rx="2.5" ry="2.9" fill="#1a1004"/>'
        '<ellipse cx="588.9" cy="25.4" rx="1.1" ry="1.2" fill="#f7dcae" opacity=".7"/>'
        '</g>',
        # Masthead ala "Criminal News": kotak gelap kiri/kanan ala
        # BREAKING NEWS + judul tengah berhias bintang seperti referensi.
        f'<rect x="44" y="33" width="210" height="26" fill="{t["chrome"]}"/>',
        f'<text x="149" y="50" text-anchor="middle" fill="{t["panel"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="14" font-weight="700" '
        'letter-spacing="1.5">\u2605 CODING \u00b7 AI \u00b7 WEB \u2605</text>',
        f'<text x="452" y="49" text-anchor="middle" fill="{t["chrome"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="15">\u2605</text>',
        f'<text x="590" y="50" text-anchor="middle" fill="{t["chrome"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="24" font-weight="900" '
        'letter-spacing="3">PROFILE NEWS</text>',
        f'<text x="728" y="49" text-anchor="middle" fill="{t["chrome"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="15">\u2605</text>',
        f'<rect x="926" y="33" width="210" height="26" fill="{t["chrome"]}"/>',
        f'<text x="1031" y="50" text-anchor="middle" fill="{t["panel"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="14" font-weight="700" '
        'letter-spacing="1.5">\u2605 LIVE EDITION \u2605</text>',
        f'<path d="M23 62H1157" stroke="{t["line"]}" stroke-width="1"/>',
        # Band VOL. 16 / MOST WANTED / handle: latar coklat gelap (chrome)
        # seperti bar "CODING · AI · WEB" paling bawah.
        f'<rect x="23" y="62" width="1134" height="28" fill="{t["chrome"]}"/>',
        f'<text x="44" y="80" fill="{t["panel"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="12" font-weight="700" '
        'letter-spacing="1.2">VOL. 16 \u2014 ID</text>',
        f'<text x="466" y="80" text-anchor="middle" fill="{t["panel"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="11">\u2605</text>',
        f'<text x="590" y="80" text-anchor="middle" fill="{t["panel"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="12" font-weight="700" '
        'letter-spacing="2.5">MOST WANTED EDITION</text>',
        f'<text x="714" y="80" text-anchor="middle" fill="{t["panel"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="11">\u2605</text>',
        f'<text x="1136" y="80" text-anchor="end" fill="{t["panel"]}" '
        f'font-family="{MONO_UI}" font-size="12" font-weight="700" '
        'letter-spacing=".6">@willdannaltairr</text>',
        f'<path d="M23 90H1157" stroke="{t["line"]}" stroke-width="2"/>',
        # Headline besar WANTED, tepinya dikasarkan ala tinta poster.
        # Font sedikit dikecilkan + baseline digeser turun supaya ada gap
        # jelas dari garis band di atasnya (jangan mentok).
        f'<text x="590" y="171" text-anchor="middle" fill="{t["chrome"]}" '
        f'font-family="{WANTED_SLAB}" font-size="88" font-weight="900" '
        'letter-spacing="18" filter="url(#rough)">WANTED</text>',
        f'<rect x="272" y="177" width="636" height="38" rx="4" fill="none" '
        f'stroke="{t["line"]}" stroke-width="2"/>',
        f'<rect x="278" y="183" width="624" height="26" rx="2" fill="none" '
        f'stroke="{t["line"]}" stroke-width="1"/>',
        f'<text x="590" y="202" text-anchor="middle" fill="{t["chrome"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="23" font-weight="900" '
        'letter-spacing="2">\u2605 WILDAN DAFFI ALTAIR DARMAWAN \u2605</text>',
        # Garis pemisah tipis di bawah papan nama (ala poster referensi).
        f'<path d="M35 220H1145" stroke="{t["line"]}" stroke-width="1.5"/>',
        # Left visual frame (mugshot). Geometri digeser +140, animasi ikut.
        # Warnanya sama dengan panel DESCRIPTIVE INFORMATION di kanan —
        # keduanya kotak krem — dan pinggirannya memakai "edge" yang
        # lebih gelap dari bg kertas, jadi kedua kotak terbaca sebagai
        # satu pasangan.
        f'<rect x="35" y="{num(88 + MASTHEAD_SHIFT)}" width="418" height="472" rx="6" fill="{t["panel"]}" '
        f'stroke="{t["edge"]}" stroke-width="2"/>',
        f'<path d="M35 {num(124 + MASTHEAD_SHIFT)}H453" stroke="{t["line"]}" stroke-width="1.5"/>',
        f'<text x="49" y="{num(111 + MASTHEAD_SHIFT)}" fill="{t["chrome"]}" '
        f'font-family="{MASTHEAD_SERIF}" font-size="17" '
        'font-weight="900" letter-spacing="1.2">MYSELF</text>',
        f'<text x="438" y="{num(111 + MASTHEAD_SHIFT)}" text-anchor="end" fill="{t["muted"]}" '
        f'font-family="{MONO_UI}" font-size="12">MUGSHOT \u00b7 370\u00d7380 / 1-BIT</text>',
        # Garis tinggi mugshot di belakang titik-titik, seperti penggaris
        # tinggi badan di poster buronan: garis tipis + angka tinggi di
        # ujung kiri, dari 5 turun ke 0.
        f'<g stroke="{t["line"]}" opacity=".24">'
        + "".join(
            f'<path d="M49 {num(y)}H439" stroke-width="1"/>'
            f'<text x="56" y="{num(y - 5)}" fill="{t["muted"]}" '
            f'font-family="{MONO_UI}" font-size="9" opacity=".9">{5 - i}</text>'
            for i, y in enumerate(
                range(int(300 + MASTHEAD_SHIFT), int(552 + MASTHEAD_SHIFT), 42)
            )
        )
        + "</g>",
        f'<path d="M49 {num(141 + MASTHEAD_SHIFT)}h12M49 {num(141 + MASTHEAD_SHIFT)}v12'
        f'M439 {num(141 + MASTHEAD_SHIFT)}h-12M439 {num(141 + MASTHEAD_SHIFT)}v12'
        f'M49 {num(539 + MASTHEAD_SHIFT)}h12M49 {num(539 + MASTHEAD_SHIFT)}v-12'
        f'M439 {num(539 + MASTHEAD_SHIFT)}h-12M439 {num(539 + MASTHEAD_SHIFT)}v-12" '
        f'fill="none" stroke="{t["chrome"]}" opacity=".55"/>',
        '<g clip-path="url(#visualClip)" shape-rendering="crispEdges">',
        # Loop layer stays visible at t=0 so camo/static first frames still show the face.
        # Intro duplicate below shimmers on top, then hands off at 3.2s.
        '<g opacity="1">',
    ]

    # Dense portrait drift: 120 fine bands gliding subtly toward first logo.
    # Noise kept small so the face stays tidy and readable during the hold.
    # Kisi titik ditulis dalam koordinat kisi internal (2x) di dalam group
    # scale(.5) supaya path datanya pendek — hasil visual sama persis.
    first_centroid = ordered[0].mean(axis=0)
    band_ids = rng.integers(0, 120, size=len(portrait))
    noise = rng.normal(0, 2.5, size=(120, 2))
    total_frames = len(frames)
    drift_opacity = ";".join(
        [".94", ".94", ".47", "0"] + ["0"] * (total_frames - 5) + [".94"]
    )
    grid_origin = np.array([59.0, 139.0 + MASTHEAD_SHIFT])
    parts.append(
        f'<g transform="translate({num(grid_origin[0])} '
        f'{num(grid_origin[1])}) scale(.5)">'
    )
    for band in range(120):
        pts = portrait[band_ids == band]
        if not len(pts):
            continue
        centroid = pts.mean(axis=0)
        delta = ((first_centroid - centroid) * 0.12 + noise[band]) * PORTRAIT_GRID
        dx, dy = num(delta[0]), num(delta[1])
        d = point_path((pts - grid_origin) * PORTRAIT_GRID)
        drift_values = ";".join(
            [
                "0 0",
                "0 0",
                f"{num(delta[0] * 0.5)} {num(delta[1] * 0.5)}",
                f"{dx} {dy}",
                f"{dx} {dy}",
            ]
            + ["0 0"] * (total_frames - 5)
        )
        parts.append(
            f'<path d="{d}" fill="none" '
            f'stroke="{t["portrait"]}" '
            f'stroke-width="{num(PORTRAIT_STROKE * PORTRAIT_GRID)}" opacity=".94">'
            f'<animateTransform attributeName="transform" type="translate" '
            f'begin="{INTRO_SECONDS}s" '
            f'dur="{num(loop_seconds)}s" repeatCount="indefinite" calcMode="spline" '
            f'keyTimes="{key_times}" keySplines="{key_splines}" values="{drift_values}"/>'
            f'<animate attributeName="opacity" begin="{INTRO_SECONDS}s" dur="{num(loop_seconds)}s" '
            f'repeatCount="indefinite" keyTimes="{key_times}" '
            f'values="{drift_opacity}"/></path>'
        )
    parts.append("</g>")

    # Layer titik padat per logo: menyala saat partikel tiba sehingga
    # tiap ikon tampil penuh titik yang rata & jelas, lalu memudar saat
    # partikel terbang ke bentuk berikutnya. Opacity 0.5 di titik tengah
    # tiap morph membuat bentuk lama & baru saling silang dengan rata
    # (total tetap penuh), jadi transisinya lembut.
    #
    # Tiap logo juga punya dua gerak arrival sendiri: layer-nya mendarat
    # sedikit lebih kecil lalu mengunci ke ukuran penuh (skala), dan tinta
    # di titik-titiknya mekar sesaat sebelum kembali normal (lebar garis).
    # Dua-duanya dibungkus <g> yang diskalakan terhadap pusat logo, bukan
    # titik asal, jadi bentuknya melayang masuk ke tempatnya.
    soft_splines = ";".join(["0.42 0 0.58 1"] * (len(times) - 1))
    for slot, name in enumerate(LOGO_ORDER):
        fade = ["0"] * len(times)
        fade[2 + 3 * slot] = "0.5"
        fade[3 + 3 * slot] = "1"
        fade[4 + 3 * slot] = "1"
        fade[5 + 3 * slot] = "0.5"
        fade[6 + 3 * slot] = "0"
        scale = ["1"] * len(times)
        scale[2 + 3 * slot] = f"{LOGO_ARRIVE[slot]:.2f}"
        ink = ["1.5"] * len(times)
        ink[2 + 3 * slot] = "1"
        ink[3 + 3 * slot] = f"{LOGO_SETTLE[slot]:.2f}"
        ink[4 + 3 * slot] = "1.5"
        parts.append(
            f'<g transform="translate({num(LOGO_CX)},{num(LOGO_CY)})"><g>'
            f'<animateTransform attributeName="transform" type="scale" '
            f'begin="{INTRO_SECONDS}s" dur="{num(loop_seconds)}s" '
            f'repeatCount="indefinite" calcMode="spline" '
            f'keyTimes="{key_times}" keySplines="{soft_splines}" '
            f'values="{";".join(scale)}"/>'
            f'<path transform="translate({num(-LOGO_CX)},{num(-LOGO_CY)})" '
            f'd="{point_path(logo_dense[name])}" fill="none" '
            f'stroke="{t["portrait"]}" stroke-width="1.5" opacity="0">'
            f'<animate attributeName="opacity" begin="{INTRO_SECONDS}s" '
            f'dur="{num(loop_seconds)}s" repeatCount="indefinite" '
            f'keyTimes="{key_times}" values="{";".join(fade)}"/>'
            f'<animate attributeName="stroke-width" begin="{INTRO_SECONDS}s" '
            f'dur="{num(loop_seconds)}s" repeatCount="indefinite" '
            f'calcMode="spline" keyTimes="{key_times}" '
            f'keySplines="{soft_splines}" values="{";".join(ink)}"/>'
            f'</path></g></g>'
        )

    # Optimal-transport travellers, represented as tiny path squares (never glyphs).
    for i in range(n):
        positions = animate_values(frames, i)
        parts.append(
            f'<path d="M-.9-.9h1.8v1.8h-1.8z" fill="{t["portrait"]}">'
            f'<animateTransform attributeName="transform" type="translate" begin="{INTRO_SECONDS}s" '
            f'dur="{num(loop_seconds)}s" repeatCount="indefinite" calcMode="spline" '
            f'keyTimes="{key_times}" keySplines="{key_splines}" values="{positions}"/>'
            f'<animate attributeName="opacity" begin="{INTRO_SECONDS}s" dur="{num(loop_seconds)}s" '
            f'repeatCount="indefinite" calcMode="linear" keyTimes="{key_times}" '
            f'values="{opacity_values}"/></path>'
        )
    parts.append("</g>")

    parts.extend(
        [
            "</g>",
            # Small frame telemetry + nomor tahanan ala poster.
            f'<text x="58" y="{num(551 + MASTHEAD_SHIFT)}" fill="{t["muted"]}" '
            f'font-family="{MONO_UI}" font-size="13">'
            f'PTS {len(portrait):05d} \u00b7 BLUE-NOISE</text>',
            f'<text x="438" y="{num(551 + MASTHEAD_SHIFT)}" text-anchor="end" fill="{t["muted"]}" '
            f'font-family="{MONO_UI}" font-size="11">NO. 081541015</text>',
            # Right information panel -> kotak DESCRIPTIVE INFORMATION.
            # Warnanya sama dengan kotak VISUAL MAP dan pinggirannya
            # memakai "edge" yang sama, jadi dua kotak itu berpasangan.
            f'<rect x="474" y="{num(88 + MASTHEAD_SHIFT)}" width="672" height="472" rx="6" fill="{t["panel"]}" '
            f'stroke="{t["edge"]}" stroke-width="2"/>',
            f'<rect x="480" y="{num(94 + MASTHEAD_SHIFT)}" width="660" height="460" rx="4" fill="none" '
            f'stroke="{t["line"]}" stroke-width="1" opacity=".45"/>',
            f'<path d="M474 {num(124 + MASTHEAD_SHIFT)}H1146" stroke="{t["line"]}" stroke-width="1.5"/>',
            f'<text x="810" y="{num(111 + MASTHEAD_SHIFT)}" text-anchor="middle" fill="{t["chrome"]}" '
            f'font-family="{MASTHEAD_SERIF}" font-size="16" '
            'font-weight="900" letter-spacing="1.2">\u2605 DESCRIPTIVE INFORMATION \u2605</text>',
            # Baris penutup ala "WANTED FOR" poster: LIVE + handle,
            # diletakkan tengah bawah di dalam panel informasi.
            '<g filter="url(#glow)"><circle '
            f'cx="697" cy="{num(412 + MASTHEAD_SHIFT)}" r="4" fill="#B3272E">'
            '<animate attributeName="opacity" values="1;.3;1" dur="1.6s" repeatCount="indefinite"/>'
            '</circle></g>',
            f'<text x="709" y="{num(417 + MASTHEAD_SHIFT)}" fill="#B3272E" '
            f'font-family="{MONO_UI}" font-size="14" '
            'font-weight="700">LIVE</text>',
            f'<rect x="766" y="{num(400 + MASTHEAD_SHIFT)}" width="162" height="24" rx="12" fill="{t["chrome"]}" opacity=".16" '
            f'stroke="{t["chrome"]}"/>',
            f'<text x="847" y="{num(417 + MASTHEAD_SHIFT)}" text-anchor="middle" fill="{t["chrome"]}" '
            f'font-family="{MONO_UI}" font-size="14" '
            'font-weight="700">@willdannaltairr</text>',
            bio_css,
            bio_markup,
            # Pita bawah ala "IF SEEN ..." + bar "CODING · AI · WEB".
            # Kartu ditinggikan 8px (H 778→786) dan pita digeser 8px naik,
            # jadi posisi absolutnya tetap sama tapi kini ada jarak 15px
            # antara bar coklat dan tepi bawah kertas — sebelumnya cuma 7px
            # dan terlihat menempel.
            f'<rect x="35" y="{H - 78}" width="1111" height="24" rx="3" fill="none" '
            f'stroke="{t["line"]}" stroke-width="1.5"/>',
            f'<text x="60" y="{H - 61}" text-anchor="middle" fill="{t["chrome"]}" '
            f'font-family="{MASTHEAD_SERIF}" font-size="13">\u2605</text>',
            f'<text x="590" y="{H - 61}" text-anchor="middle" fill="{t["chrome"]}" '
            f'font-family="{MASTHEAD_SERIF}" font-size="13" font-weight="700" '
            'letter-spacing="1.5">IF SEEN \u2014 VISIT GITHUB @willdannaltairr \u00b7 ALWAYS SHIPS</text>',
            f'<text x="1120" y="{H - 61}" text-anchor="middle" fill="{t["chrome"]}" '
            f'font-family="{MASTHEAD_SERIF}" font-size="13">\u2605</text>',
            f'<rect x="35" y="{H - 52}" width="1111" height="24" rx="3" fill="{t["chrome"]}" />',
            f'<text x="590" y="{H - 35}" text-anchor="middle" fill="{t["panel"]}" '
            f'font-family="{MASTHEAD_SERIF}" font-size="15" font-weight="900" '
            'letter-spacing="4">\u2605 CODING \u00b7 AI \u00b7 WEB \u2605</text>',
            # Lapisan kertas terakhir: butiran halus, serat, bercak penuaan,
            # pulp, vignette tepi, dan dua lipatan. Ditaruh paling atas supaya
            # menimpa tinta juga — begitulah kertas tua terlihat: teksturnya
            # ada di seluruh permukaan, bukan cuma di atas dasar kertas.
            '<g clip-path="url(#paperClip)">'
            '<rect x="13" y="13" width="1154" height="'
            f'{H - 26}" filter="url(#grain)" opacity=".10"/>'
            '<rect x="13" y="13" width="1154" height="'
            f'{H - 26}" filter="url(#fiber)" opacity=".05"/>'
            '<rect x="13" y="13" width="1154" height="'
            f'{H - 26}" filter="url(#stain)" opacity=".07"/>'
            f'<rect x="13" y="13" width="1154" height="{H - 26}" fill="url(#age)"/>'
            '</g>',
            "</svg>",
        ]
    )
    return "".join(parts)


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source portrait: {SOURCE}")
    if not PROFILE.exists():
        raise SystemExit(f"Missing profile data: {PROFILE}")
    ASSETS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    logos = make_logos()

    # Titik foto: satu set identik untuk kedua tema (pipeline blue-noise).
    points = portrait_points()
    for theme in THEMES:
        np.save(DATA / f"portrait-{theme}.npy", points)

    # Layer titik padat per logo (dipakai layer statis + sumber partikel).
    dense = {name: logo_dots(image) for name, image in logos.items()}

    # Seed yang sama untuk kedua tema. Dulu tiap tema memakai seed
    # berbeda, jadi dua file Although Sama paletnya tetap bergambar dengan
    # susunan titik acak yang berbeda — itulah sisa 150 byte dan sisa
    # selisih piksel. Satu seed membuat keduanya benar-benar sama.
    for theme in THEMES:
        rng = np.random.default_rng(SEED + 100)
        sampled = {
            name: sample_logo_points(dots, rng, TRAVELLER_COUNT)
            for name, dots in dense.items()
        }
        for name, pts in sampled.items():
            np.save(DATA / f"{name}-{theme}.npy", pts)
        svg = render_svg(theme, points, sampled, dense, rng)
        output = ASSETS / f"banner-{theme}.v9.svg"
        output.write_text(svg, encoding="utf-8")
        byte_size = output.stat().st_size
        print(
            f"{output.relative_to(ROOT)}: {byte_size:,} bytes "
            f"({byte_size / 1024:.1f} KiB), {len(points):,} portrait dots, "
            f"{TRAVELLER_COUNT} travellers"
        )

    for name in logos:
        output = LOGOS / f"{name}.png"
        print(f"{output.relative_to(ROOT)}: {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
