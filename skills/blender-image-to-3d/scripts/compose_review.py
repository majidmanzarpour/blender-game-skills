"""
COMPOSE REVIEW
Build one compare sheet from a reference image and a review render, and measure the
silhouette match numerically so iteration is driven by numbers, not impressions.

python scripts/compose_review.py --ref ref/front.png --render review/02_blockout/clay_ref.png \
  --out review/02_blockout/compare_ref.png --overlay 0.5 --gameplay-px 128 --measure

Needs Pillow (pip install pillow). Renders from review_render.py have transparent backgrounds,
which gives an exact render mask. The reference mask uses its alpha channel when present,
otherwise the corner colour is treated as background (works for plain-background concepts,
approximate for photos: crop or matte those first).

--measure prints and saves JSON: silhouette aspect ratios, width profile at 10 height bands,
IoU after aligning both silhouettes by their bounding boxes. Use it as the gate metric:
blockout IoU above ~0.85 and band widths within ~5 percent are the working targets.
"""
import argparse, json, os, sys
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

GREY = (110, 110, 110, 255)


# MASKS

def load(path):
    return Image.open(path).convert("RGBA")


def render_mask(img, thresh=16):
    return img.getchannel("A").point(lambda v: 255 if v > thresh else 0)


def ref_mask(img, tol=40):
    a = img.getchannel("A")
    lo, hi = a.getextrema()
    if lo < 250:
        return a.point(lambda v: 255 if v > 16 else 0)
    rgb = img.convert("RGB")
    w, h = rgb.size
    pads = [rgb.crop(b) for b in ((0, 0, 8, 8), (w - 8, 0, w, 8), (0, h - 8, 8, h), (w - 8, h - 8, w, h))]
    px = [p.getpixel((x, y)) for p in pads for x in range(p.width) for y in range(p.height)]
    bg = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
    diff = ImageChops.difference(rgb, Image.new("RGB", (w, h), bg)).convert("L")
    return diff.point(lambda v: 255 if v > tol else 0)


def bbox_of(mask):
    return mask.getbbox()


def crop_pad(img, mask, pad_frac=0.03):
    b = bbox_of(mask)
    if not b:
        return img, mask
    w, h = b[2] - b[0], b[3] - b[1]
    p = int(max(w, h) * pad_frac)
    box = (max(0, b[0] - p), max(0, b[1] - p), min(img.width, b[2] + p), min(img.height, b[3] + p))
    return img.crop(box), mask.crop(box)


def fit_height(img, height):
    s = height / img.height
    return img.resize((max(1, int(img.width * s)), height), Image.LANCZOS)


def over_grey(img):
    bg = Image.new("RGBA", img.size, GREY)
    return Image.alpha_composite(bg, img)


# MEASURE

def profile(mask, bands=10, height=256):
    """Widths (as a fraction of silhouette height) at band centres from top to bottom, plus aspect."""
    b = bbox_of(mask)
    if not b:
        return None
    m = mask.crop(b)
    s = height / m.height
    m = m.resize((max(1, int(m.width * s)), height), Image.NEAREST)
    px = m.load()
    widths = []
    for i in range(bands):
        y = int((i + 0.5) * height / bands)
        xs = [x for x in range(m.width) if px[x, y] > 127]
        widths.append(round((max(xs) - min(xs) + 1) / height, 4) if xs else 0.0)
    return {"aspect_w_over_h": round(m.width / height, 4), "band_widths": widths, "_img": m}


def iou(m1, m2):
    w = max(m1.width, m2.width)
    h = max(m1.height, m2.height)

    def centre(m):
        c = Image.new("L", (w, h), 0)
        c.paste(m, ((w - m.width) // 2, (h - m.height) // 2))
        return c
    a, b = centre(m1).load(), centre(m2).load()
    inter = union = 0
    for y in range(h):
        for x in range(w):
            p, q = a[x, y] > 127, b[x, y] > 127
            inter += p and q
            union += p or q
    return round(inter / union, 4) if union else 0.0


# MAIN

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ref", required=True)
    p.add_argument("--render", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--height", type=int, default=768)
    p.add_argument("--overlay", type=float, default=0.5, help="reference opacity in the overlay panel, 0 disables")
    p.add_argument("--gameplay-px", type=int, default=0)
    p.add_argument("--grey", action="store_true")
    p.add_argument("--measure", action="store_true")
    p.add_argument("--ref-tol", type=int, default=40)
    p.add_argument("--no-crop", action="store_true", help="keep full frames instead of cropping to the silhouettes")
    a = p.parse_args()

    ref, ren = load(a.ref), load(a.render)
    rm, nm = ref_mask(ref, a.ref_tol), render_mask(ren)
    if a.measure and (not rm.getbbox() or not nm.getbbox()):
        raise SystemExit("MEASURE failed: empty reference or render mask")
    if not a.no_crop:
        ref, rm = crop_pad(ref, rm)
        ren, nm = crop_pad(ren, nm)
    if a.grey:
        ref = ImageOps.grayscale(ref).convert("RGBA")
        ren_rgb = ImageOps.grayscale(ren).convert("RGBA")
        ren_rgb.putalpha(ren.getchannel("A"))
        ren = ren_rgb

    H = a.height
    panels = [("REF", fit_height(ref, H)), ("RENDER", fit_height(over_grey(ren), H))]
    if a.overlay > 0:
        rr = fit_height(ref, H).convert("RGBA")
        rn = fit_height(over_grey(ren), H)
        w = max(rr.width, rn.width)
        base = Image.new("RGBA", (w, H), GREY)
        base.paste(rn, ((w - rn.width) // 2, 0), rn)
        top = Image.new("RGBA", (w, H), (0, 0, 0, 0))
        top.paste(rr, ((w - rr.width) // 2, 0))
        top.putalpha(top.getchannel("A").point(lambda v: int(v * a.overlay)))
        panels.append(("OVERLAY %.0f%%" % (a.overlay * 100), Image.alpha_composite(base, top)))
    if a.gameplay_px:
        gp = a.gameplay_px
        strip = Image.new("RGBA", (fit_height(ref, gp).width + fit_height(ren, gp).width + 12, H), GREY)
        strip.paste(fit_height(ref, gp), (0, 0))
        strip.paste(fit_height(over_grey(ren), gp), (fit_height(ref, gp).width + 12, 0))
        panels.append(("GAMEPLAY %dpx" % gp, strip))

    gap, label_h = 16, 28
    W = sum(im.width for _, im in panels) + gap * (len(panels) + 1)
    sheet = Image.new("RGBA", (W, H + label_h + gap * 2), (40, 40, 40, 255))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    x = gap
    for name, im in panels:
        draw.text((x, gap // 2), name, fill=(230, 230, 230, 255), font=font)
        sheet.paste(im, (x, label_h + gap), im if im.mode == "RGBA" else None)
        x += im.width + gap
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    sheet.convert("RGB").save(a.out)
    print("sheet", a.out)

    if a.measure:
        pr, pn = profile(rm), profile(nm)
        res = {
            "ref": {k: v for k, v in pr.items() if k != "_img"},
            "render": {k: v for k, v in pn.items() if k != "_img"},
            "iou": iou(pr["_img"], pn["_img"]),
            "band_width_diff": [round(n - r, 4) for r, n in zip(pr["band_widths"], pn["band_widths"])],
            "aspect_diff": round(pn["aspect_w_over_h"] - pr["aspect_w_over_h"], 4),
        }
        jpath = os.path.splitext(a.out)[0] + ".json"
        with open(jpath, "w") as f:
            json.dump(res, f, indent=2)
        print("IoU %.3f  aspect diff %+.3f" % (res["iou"], res["aspect_diff"]))
        print("band width diff (render minus ref, top to bottom):", res["band_width_diff"])
        print("measure", jpath)


if __name__ == "__main__":
    main()
