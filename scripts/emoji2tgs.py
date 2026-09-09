import gzip
import json
import os
import re
import subprocess
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
TGS_W = TGS_H = 512
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emotes-premium/tgs")
FPS = 30
DURATION = 2.0
N = int(FPS * DURATION)
BIG = 340

SPIN = {"🔄", "⚙", "🔁", "🔃", "💫", "🌀"}
WIGGLE = {"📍", "📌", "⬆", "⬇", "↗", "↘", "🖐", "👆"}


def slug(emo):
    name = unicodedata.name(emo[0], "emoji").lower().replace(" ", "_")
    name = "".join(c for c in name if c.isalnum() or c == "_")
    return name or f"u{ord(emo[0]):x}"


def render_rgba(emo):
    font = ImageFont.truetype(FONT_PATH, 109)
    probe = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    d.text((60, 60), emo, font=font, embedded_color=True)
    bbox = probe.split()[3].getbbox()
    if bbox is None:
        return None
    glyph = probe.crop(bbox)
    glyph = ImageOps.contain(glyph, (BIG, BIG), Image.LANCZOS)
    canvas = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    canvas.alpha_composite(glyph, ((BIG - glyph.width) // 2, (BIG - glyph.height) // 2))
    return canvas


def quantize_colors(im, maxc=8):
    rgba = np.asarray(im)
    a = rgba[..., 3]
    mask = a > 40
    if not mask.any():
        return []
    rgb = rgba[..., :3][mask]
    q = (rgb // 16).astype(np.int32)  # bit-lossy key
    keys, counts = np.unique(q, axis=0, return_counts=True)
    total = len(q)
    order = np.argsort(-counts)
    chosen = []
    for idx in order:
        if counts[idx] < max(500, 0.004 * total):
            break
        key = keys[idx]
        col = (key * 16 + 8).astype(int).clip(0, 255)
        if all(np.all(np.abs(col.astype(int) - c) > 24) for c in chosen):
            chosen.append(tuple(col))
        if len(chosen) >= maxc:
            break
    return chosen


def mask_for(im, color):
    raise NotImplementedError


def to_bmp(path, mask):
    h, w = mask.shape
    row_bytes = ((w + 31) // 32) * 4
    pixel = row_bytes * h
    header = bytearray()
    header += b"BM" + int(54 + 8 + pixel).to_bytes(4, "little") + b"\x00\x00\x00\x00"
    header += (54 + 8).to_bytes(4, "little")
    header += (40).to_bytes(4, "little") + int(w).to_bytes(4, "little") + int(h).to_bytes(4, "little")
    header += (1).to_bytes(2, "little") + (1).to_bytes(2, "little")   # biBitCount=1
    header += (0).to_bytes(4, "little")                                # BI_RGB
    header += int(pixel).to_bytes(4, "little")
    header += (2835).to_bytes(4, "little") + (2835).to_bytes(4, "little")
    header += (0).to_bytes(4, "little") + (0).to_bytes(4, "little")
    header += (2).to_bytes(4, "little")                                # biClrUsed=2
    palette = bytes([0, 0, 0, 0, 255, 255, 255, 0])                   # idx0=black, idx1=white
    body = bytearray()
    for y in range(h - 1, -1, -1):
        row = bytearray(row_bytes)
        for x in range(w):
            if mask[y, x]:
                row[x >> 3] |= 0x80 >> (x & 7)
        body += row
    with open(path, "wb") as f:
        f.write(bytes(header) + palette + bytes(body))


def potrace_svg(bmp_path):
    svg_path = bmp_path + ".svg"
    subprocess.run([
        "potrace", "-b", "svg", "-t", "0.4", "-a", "1.0",
        "-o", svg_path, bmp_path,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    svg = open(svg_path, encoding="utf-8").read()
    return svg


def parse_svg_paths(svg):
    dstrs = re.findall(r'd="([^"]*)"', svg)
    loops = []
    for d in dstrs:
        toks = re.findall(r'[MmCcLlZz]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', re.sub(r"\s+", " ", d).strip())
        loop = []
        curx = cury = 0.0
        startx = starty = 0.0
        i = 0
        cmd = None
        while i < len(toks):
            t = toks[i]
            if t in "MmCcLlZz":
                cmd = t
                i += 1
                continue
            if cmd is None:
                break
            if cmd in "Mm":
                if loop:
                    loops.append(loop)
                    loop = []
                dx, dy = float(toks[i]), float(toks[i + 1])
                startx = curx + dx if cmd == "m" else dx
                starty = cury + dy if cmd == "m" else dy
                curx, cury = startx, starty
                loop.append(("M", startx, starty))
                i += 2
                cmd = "l" if cmd == "m" else "L"
                continue
            if cmd in "Ll":
                dx, dy = float(toks[i]), float(toks[i + 1])
                ex = curx + dx if cmd == "l" else dx
                ey = cury + dy if cmd == "l" else dy
                loop.append(("L", ex, ey))
                curx, cury = ex, ey
                i += 2
                continue
            if cmd in "Cc":
                c1x, c1y = float(toks[i]), float(toks[i + 1])
                c2x, c2y = float(toks[i + 2]), float(toks[i + 3])
                dx, dy = float(toks[i + 4]), float(toks[i + 5])
                if cmd == "c":
                    c1x += curx; c1y += cury
                    c2x += curx; c2y += cury
                    dx += curx; dy += cury
                loop.append(("C", c1x, c1y, c2x, c2y, dx, dy))
                curx, cury = dx, dy
                i += 6
                continue
            if cmd in "Zz":
                if loop:
                    loops.append(loop)
                    loop = []
                curx, cury = startx, starty
                i += 1
                cmd = None
                continue
            break
        if loop:
            loops.append(loop)
    return loops


def loops_to_lottie(loops, cx, cy):
    v, i, o = [], [], []
    for loop in loops:
        vals = []
        for seg in loop:
            if seg[0] == "M":
                pass
            elif seg[0] == "L":
                vals.append(("L", (seg[1] - cx, seg[2] - cy)))
            else:
                vals.append(("C", (seg[1] - cx, seg[2] - cy),
                             (seg[3] - cx, seg[4] - cy), (seg[5] - cx, seg[6] - cy)))
        if not vals:
            continue
        ls_v, ls_i, ls_o = [], [], []
        first_x, first_y = loop[0][1] - cx, loop[0][2] - cy
        ls_v.append([round(first_x, 2), round(first_y, 2)])
        ls_i.append([0.0, 0.0])
        ls_o.append([0.0, 0.0])
        px, py = first_x, first_y
        for seg in vals:
            if seg[0] == "L":
                ex, ey = seg[1]
                ls_o[-1] = [0.0, 0.0]
                ls_v.append([round(ex, 2), round(ey, 2)])
                ls_i.append([0.0, 0.0])
                ls_o.append([0.0, 0.0])
                px, py = ex, ey
            else:
                c1x, c1y = seg[1]
                c2x, c2y = seg[2]
                ex, ey = seg[3]
                ls_o[-1] = [round(c1x - px, 2), round(c1y - py, 2)]
                ls_v.append([round(ex, 2), round(ey, 2)])
                ls_i.append([round(c2x - ex, 2), round(c2y - ey, 2)])
                ls_o.append([0.0, 0.0])
                px, py = ex, ey
        if len(ls_v) and abs(ls_v[0][0] - ls_v[-1][0]) < 0.5 and abs(ls_v[0][1] - ls_v[-1][1]) < 0.5:
            ls_v.pop()
            ls_i.pop()
            ls_o.pop()
        v.extend(ls_v)
        i.extend(ls_i)
        o.extend(ls_o)
    return v, i, o


def build_lottie(colored_paths):
    layers = []
    for idx, (color, loops) in enumerate(colored_paths):
        shp = []
        for loop in loops:
            v, i, o = loops_to_lottie([loop], TGS_W / 2, TGS_H / 2)
            if not v:
                continue
            shp.append({
                "ty": "sh",
                "ks": {"a": 0, "k": {"i": i, "o": o, "v": v, "c": True}},
            })
        if not shp:
            continue
        r, g, b = color
        shp.append({
            "ty": "fl",
            "c": {"a": 0, "k": [r / 255, g / 255, b / 255, 1]},
            "o": {"a": 0, "k": 100}, "r": 1, "bm": 0,
        })
        layers.append({
            "ddd": 0, "ind": idx + 1, "ty": 4, "nm": f"c{idx}", "sr": 1,
            "ks": {
                "o": {"a": 0, "k": 100},
                "r": {"a": 0, "k": 0},
                "p": {"a": 0, "k": [TGS_W / 2, TGS_H / 2, 0]},
                "a": {"a": 0, "k": [0, 0, 0]},
                "s": {"a": 1, "k": [
                    {"t": 0, "s": [100, 100, 100]},
                    {"t": N // 2, "s": [107, 107, 100]},
                    {"t": N, "s": [100, 100, 100]},
                ]},
            }, "ao": 0, "shapes": [
                {"ty": "gr", "it": shp, "nm": "p", "np": len(shp),
                 "cix": 2, "bm": 0, "ix": 1, "cl": "", "ln": ""}
            ], "ip": 0, "op": N, "st": 0, "bm": 0,
        })
    layers.reverse()
    return {
        "v": "5.5.7", "fr": FPS, "ip": 0, "op": N,
        "w": TGS_W, "h": TGS_H, "nm": "emoji", "ddd": 0,
        "assets": [], "layers": layers,
    }


def normalize_lottie(L):
    pts = []
    for layer in L["layers"]:
        for grp in layer.get("shapes", []):
            for it in grp.get("it", []):
                if it.get("ty") == "sh":
                    k = it["ks"].get("k")
                    if isinstance(k, dict):
                        pts += list(k.get("v", [])) + list(k.get("i", [])) + list(k.get("o", []))
    if not pts:
        return L
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = L["w"], L["h"]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    span = max(xmax - xmin, ymax - ymin, 1e-6)
    scale = min(20.0, (w - 24) / span)
    ox = (w - (xmax - xmin) * scale) / 2 - xmin * scale
    oy = (h - (ymax - ymin) * scale) / 2 - ymin * scale
    for layer in L["layers"]:
        for grp in layer.get("shapes", []):
            for it in grp.get("it", []):
                if it.get("ty") == "sh":
                    k = it["ks"].get("k")
                    if not isinstance(k, dict):
                        continue
                    k["v"] = [[round(x * scale + ox, 2), round((ymin + ymax - y) * scale + oy, 2)] for x, y in k["v"]]
                    k["i"] = [[round(x * scale, 2), round(y * scale, 2)] for x, y in k["i"]]
                    k["o"] = [[round(x * scale, 2), round(y * scale, 2)] for x, y in k["o"]]
    return L


def write_tgs(lottie, path):
    raw = json.dumps(lottie, separators=(",", ":")).encode()
    import gzip as _gz
    buf = _gz.compress(raw, mtime=0)
    with open(path, "wb") as f:
        f.write(buf)


def convert(emo):
    im = render_rgba(emo)
    if im is None:
        return None
    rgba = np.asarray(im)
    a = rgba[..., 3] > 40
    colors = quantize_colors(im)
    if not colors:
        return None
    rgb = rgba[..., :3]
    cents = np.array(colors, dtype=int)
    d = (rgb.astype(int)[:, :, None, :] - cents[None, None, :, :]) ** 2
    lab = d.sum(axis=-1).argmin(axis=-1)
    colored = []
    for j, color in enumerate(colors):
        mask = a & (lab == j)
        if not mask.sum():
            continue
        bmp = os.path.join("/tmp", f"m{len(colored)}.bmp")
        to_bmp(bmp, mask)
        svg = potrace_svg(bmp)
        loops = parse_svg_paths(svg)
        if loops:
            colored.append((color, loops))
    return build_lottie(colored)


def main():
    emojis = [c for c in os.getenv("EMOJIS", "").split(",") if c] or [
        "❌", "⚠️", "✅", "💎", "📁", "📦", "🚫", "🔒", "📤", "❤️", "📥",
        "👤", "⏱", "⏳", "🟢", "🌐", "☕", "🔗", "🔄", "📡", "⬇", "⬆",
        "🙏", "🗑", "📊", "✏", "☁", "📨", "📄", "💡", "⚙", "🚀", "🔧",
        "🏷", "🔹", "🗂", "❔", "🎁", "🧾", "⚡", "📖", "⏩", "📂", "⭐",
        "👑", "🛡", "→", "🧲", "📭", "🔍",
    ]
    os.makedirs(OUT, exist_ok=True)
    manifest = []
    for emo in emojis:
        lottie = convert(emo)
        if not lottie or not lottie["layers"]:
            print(f"skip: {emo}")
            continue
        lottie = normalize_lottie(lottie)
        stem = slug(emo)
        path = os.path.join(OUT, f"{stem}.tgs")
        write_tgs(lottie, path)
        size = os.path.getsize(path)
        flag = "OK" if size <= 65536 else "TOO-BIG"
        print(f"{flag} {emo} layers={len(lottie['layers'])} {stem:28s} {size:>6} B")
        manifest.append({"emoji": emo, "style": "bounce", "file": f"{stem}.tgs", "bytes": size})
    with open(os.path.join(OUT, "index.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()