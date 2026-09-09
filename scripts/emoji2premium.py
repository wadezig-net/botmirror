import io
import json
import math
import os
import subprocess
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
SIZE = 100
FPS = 30
DURATION = 2.0
N_FRAMES = int(FPS * DURATION)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emotes-premium")

SPIN = {"🔄", "⚙", "🔁", "🔃", "💫", "🌀"}
POP = {"🎁", "⭐", "💎", "✨", "🆕", "🥳"}
WIGGLE = {"📍", "📌", "⬆", "⬇", "↗", "↘", "🖐", "👆"}


def slug(emo):
    src = emo[0] if emo else emo
    name = unicodedata.name(src, "emoji").lower().replace(" ", "_")
    name = "".join(c for c in name if c.isalnum() or c == "_")
    return name or f"u{ord(src):x}"


def load_base(emo):
    font = ImageFont.truetype(FONT_PATH, 109)
    probe = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    d.text((60, 60), emo, font=font, embedded_color=True)
    bbox = probe.split()[3].getbbox()
    if bbox is None:
        return None
    glyph = probe.crop(bbox)
    glyph = ImageOps.contain(glyph, (86, 86), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.alpha_composite(glyph, ((SIZE - glyph.width) // 2, (SIZE - glyph.height) // 2))
    return canvas


def ease_in_out(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def scale_img(img, sx, sy):
    w, h = img.size
    nw, nh = max(1, int(round(w * sx))), max(1, int(round(h * sy)))
    return img.resize((nw, nh), Image.LANCZOS)


def rotate_img(img, deg):
    return img.rotate(deg, Image.LANCZOS, expand=False)


def animate(base, style, n=N_FRAMES):
    frames = []
    for k in range(n):
        t = k / n
        canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        if style == "spin":
            deg = 40 * math.sin(2 * math.pi * t)
            s = 1.0 + 0.05 * math.sin(2 * math.pi * t + 1.0)
            layer = scale_img(base, s, s)
            layer = rotate_img(layer, deg)
        elif style == "wiggle":
            deg = 5 * math.sin(2 * math.pi * t * 2)
            s = 1.0 + 0.03 * math.sin(2 * math.pi * t)
            layer = scale_img(base, s, s)
            layer = rotate_img(layer, deg)
        elif style == "pop":
            if t < 0.15:
                p = ease_in_out(t / 0.15)
                s = 0.5 + 0.56 * p
            elif t < 0.35:
                p = (t - 0.15) / 0.2
                s = 1.06 - 0.06 * p
            else:
                s = 1.0
            layer = scale_img(base, s, s)
        else:
            s = 1.0 + 0.045 * math.sin(2 * math.pi * t)
            layer = scale_img(base, s, s)
        canvas.alpha_composite(layer, ((SIZE - layer.width) // 2, (SIZE - layer.height) // 2))
        frames.append(canvas.tobytes())
    return frames


def encode_webm(frames, out_path):
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{SIZE}x{SIZE}",
        "-r", str(FPS), "-i", "pipe:0",
        "-pix_fmt", "yuva420p",
        "-c:v", "libvpx-vp9", "-crf", "40", "-b:v", "0",
        "-deadline", "realtime", "-cpu-used", "8",
        "-row-mt", "1", "-tile-columns", "2",
        "-auto-alt-ref", "0", "-lag-in-frames", "0",
        "-an", out_path,
    ]
    proc = subprocess.run(cmd, input=b"".join(frames), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace"))


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,r_frame_rate",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    return json.loads(out.stdout or "{}")


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
        base = load_base(emo)
        if base is None:
            print(f"skip: {emo} (no glyph)")
            continue
        style = "spin" if emo in SPIN else ("pop" if emo in POP else
                 ("wiggle" if emo in WIGGLE else "bounce"))
        stem = slug(emo)
        webm = os.path.join(OUT, f"{stem}.webm")
        png = os.path.join(OUT, f"{stem}.png")
        encode_webm(animate(base, style), webm)
        base.save(png)
        size = os.path.getsize(webm)
        info = probe(webm)
        manifest.append({
            "emoji": emo, "style": style, "file": f"{stem}.webm",
            "png": f"{stem}.png", "bytes": size,
            "codec": info.get("streams", [{}])[0].get("codec_name"),
            "w": info.get("streams", [{}])[0].get("width"),
            "h": info.get("streams", [{}])[0].get("height"),
        })
        flag = "OK" if size <= 262144 else "TOO-BIG"
        print(f"{flag} {emo} {style:6s} {stem:30s} {size:>7} B")
    with open(os.path.join(OUT, "index.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()