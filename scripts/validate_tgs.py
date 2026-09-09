import gzip
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

from emoji2tgs import render_rgba, TGS_W, TGS_H, BIG


def cubic_point(p0, c1, c2, p1, s):
    m0 = (1 - s) * p0 + s * c1
    m1 = (1 - s) * c1 + s * c2
    m2 = (1 - s) * c2 + s * p1
    return (1 - s) * m1 + s * m2


def path_vertices(v, i, o):
    pts = []
    n = len(v) - 1
    for k in range(n):
        p0 = np.array(v[k], float)
        p1 = np.array(v[k + 1], float)
        c1 = p0 + np.array(o[k], float)
        c2 = p1 + np.array(i[k + 1], float)
        for s in np.linspace(0, 1, 8):
            pts.append(cubic_point(p0, c1, c2, p1, s))
    return pts


def lottie_to_img(tgs_path):
    with gzip.open(tgs_path, "rb") as f:
        L = json.loads(f.read())
    img = Image.new("RGBA", (L["w"], L["h"]), (0, 0, 0, 0))
    for layer in L["layers"]:
        color = (1, 1, 1, 1)
        polys = []
        for grp in layer.get("shapes", []):
            for it in grp.get("it", []):
                if it.get("ty") == "fl":
                    color = it["c"]["k"]
                elif it.get("ty") == "sh":
                    k = it["ks"]
                    if k.get("a") == 0:
                        kd = k["k"]
                        polys.append(path_vertices(kd["v"], kd["i"], kd["o"]))
        d = ImageDraw.Draw(img)
        for poly in polys:
            d.polygon([(p[0], p[1]) for p in poly],
                      fill=(int(color[0] * 255), int(color[1] * 255),
                            int(color[2] * 255), int(color[3] * 255)))
    return img, L


def main():
    for tgs in sys.argv[1:]:
        img, L = lottie_to_img(tgs)
        png = tgs.replace(".tgs", ".preview.png")
        img.save(png)
        print(f"{tgs} {img.size} -> {png}")
        a = np.asarray(img)[..., 3] > 40
        canv = a.mean().item() * 100
        emo = BASE_EMOJI.get(os.path.basename(tgs))
        src = None
        if emo:
            src_im = render_rgba(emo)
            if src_im is not None:
                src = np.asarray(src_im.resize((img.width, img.height), Image.LANCZOS))[..., 3] > 40
                src_cov = src.mean().item() * 100
                inter = (a & src).sum()
                union = (a | src).sum()
                iou = (inter / union * 100) if union else 0
                print(f"  lottie cov {canv:.1f}% | source cov {src_cov:.1f}% | IoU {iou:.1f}%")
        else:
            print(f"  lottie cov {canv:.1f}%")


BASE_EMOJI = {}
try:
    idx = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "emotes-premium/tgs/index.json")))
    for e in idx:
        BASE_EMOJI[e["file"]] = e["emoji"]
except FileNotFoundError:
    pass

if __name__ == "__main__":
    main()