"""
WORLD GATE
World-registered silhouette gate for orthographic reference views. The camera covers exactly the
world window of the reference matte (from the brief's scale), so render and reference compare pixel
for pixel: nothing is re-centred or re-scaled, and a model that is 3 cm too short or sits 2 cm off
the axis loses IoU instead of being normalised away (compose_review.py aligns bounding boxes and
cannot see that).

blender --background --python-exit-code 1 --python scripts/world_gate.py -- \
  --blend CH_Knight/CH_Knight_master.blend --out CH_Knight/review/gate_forms \
  --matte ref/front_clean.png --az 0 --m-per-px 0.0029167 --axis-col 109 --ground-row 610 \
  --collections LOW --name front

--matte       reference crop of ONE orthographic view (PNG). Alpha is the mask when the image has
              transparency, otherwise pixels that differ from the corner colour. Clean it first:
              erase labels, rulers, floor lines and anything the model does not include.
--az          view azimuth, same convention as review_render.py (0 front, 90 camera at +X,
              180 back, 270 camera at -X). Image right is world (cos az, sin az, 0).
--m-per-px    metres per matte pixel (the brief's SCALE).
--axis-col    matte column of the vertical world axis (x = 0 front/back, y = 0 side views).
--ground-row  matte row of z = 0 (the sole / ground line).
Objects render if they are in --collections (recursively), of a renderable type (strand hair
included) and not hidden for render; hide mannequins and helpers before gating.

Writes <out>/gate_<name>.json (iou, ref_only_pct, render_only_pct, 10 band width differences as a
fraction of the subject height, top to bottom), <out>/sil_<name>.png and <out>/diff_<name>.png
(grey = both, red = reference only, blue = model only). Exit code 0 always; read the numbers.
"""
import bpy, sys, os, math, json, argparse
import numpy as np
from mathutils import Vector

RENDERABLE = {"MESH", "CURVE", "CURVES", "FONT", "SURFACE", "META"}


def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--matte", required=True)
    p.add_argument("--az", type=float, required=True)
    p.add_argument("--m-per-px", type=float, required=True)
    p.add_argument("--axis-col", type=float, required=True)
    p.add_argument("--ground-row", type=float, required=True)
    p.add_argument("--collections", default="LOW")
    p.add_argument("--name", default="")
    p.add_argument("--scale", type=int, default=4, help="supersampling of the render per matte pixel")
    p.add_argument("--tol", type=int, default=40, help="background tolerance (0-255) for mattes without alpha")
    return p.parse_args(argv)


def collection_objects(names):
    out = set()
    for n in names:
        c = bpy.data.collections.get(n)
        if c is None:
            print("collection not found:", n)
            continue
        out |= set(c.all_objects)
    return out


def load_rgba(path):
    """(H, W, 4) float array, row 0 = top of the image."""
    img = bpy.data.images.load(os.path.abspath(path), check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[::-1]


def save_rgba(path, arr):
    h, w = arr.shape[:2]
    img = bpy.data.images.new("_gate_out", w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(arr[::-1], dtype=np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def matte_mask(rgba, tol):
    a = rgba[..., 3]
    if a.min() < 0.98:
        return a > 0.5
    rgb = rgba[..., :3]
    corners = np.concatenate([rgb[:8, :8].reshape(-1, 3), rgb[:8, -8:].reshape(-1, 3),
                              rgb[-8:, :8].reshape(-1, 3), rgb[-8:, -8:].reshape(-1, 3)])
    bg = corners.mean(0)
    return np.abs(rgb - bg).sum(-1) * 255.0 > tol


def band_widths(ref, mod, bands=10):
    ys = np.nonzero(ref.any(1))[0]
    if not len(ys):
        raise ValueError("empty reference mask")
    y0, y1 = ys.min(), ys.max()
    height = y1 - y0 + 1
    out = []
    for k in range(bands):
        yy = int(y0 + (k + 0.5) * height / bands)

        def w(m):
            xs = np.nonzero(m[yy])[0]
            return (xs.max() - xs.min() + 1) if len(xs) else 0
        out.append(round(float(w(mod) - w(ref)) / float(height), 4))
    return out


def main():
    a = parse(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    os.makedirs(a.out, exist_ok=True)
    name = a.name or "az%03d" % int(round(a.az))
    ref_rgba = load_rgba(a.matte)
    H, W = ref_rgba.shape[:2]
    ref = matte_mask(ref_rgba, a.tol)
    if not ref.any() or a.m_per_px <= 0 or a.scale <= 0:
        raise ValueError("nonempty matte, positive m-per-px and positive scale required")
    S = a.m_per_px

    scene = bpy.context.scene
    keep = collection_objects([c for c in a.collections.split(",") if c])
    saved = {}
    for o in scene.objects:
        if o.type in RENDERABLE:
            saved[o.name] = o.hide_render
            o.hide_render = o.hide_render or o not in keep
    scene.render.engine = "BLENDER_WORKBENCH"
    sh = scene.display.shading
    sh.light, sh.color_type, sh.single_color = "FLAT", "SINGLE", (0.0, 0.0, 0.0)
    sh.show_shadows = sh.show_cavity = False
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = W * a.scale, H * a.scale
    scene.render.resolution_percentage = 100

    # camera: orthographic window = the matte's world window
    az = math.radians(a.az)
    right = Vector((math.cos(az), math.sin(az), 0.0))
    back = Vector((math.sin(az), -math.cos(az), 0.0))       # from the subject toward the camera
    h_c = (W / 2.0 - a.axis_col) * S
    z_c = (a.ground_row - H / 2.0) * S
    cam = bpy.data.objects.new("_gate_cam", bpy.data.cameras.new("_gate_cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.ortho_scale = W * S
    cam.data.clip_start, cam.data.clip_end = 0.01, 100.0
    cam.location = right * h_c + Vector((0.0, 0.0, z_c)) + back * 20.0
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (-back).to_track_quat("-Z", "Y")
    sil_path = os.path.join(os.path.abspath(a.out), "sil_%s.png" % name)
    scene.render.filepath = sil_path
    bpy.ops.render.render(write_still=True)
    for n, v in saved.items():
        bpy.data.objects[n].hide_render = v

    ren = load_rgba(sil_path)[..., 3]
    mod = ren.reshape(H, a.scale, W, a.scale).mean(axis=(1, 3)) > 0.5
    inter, union = float((ref & mod).sum()), float((ref | mod).sum())
    res = {"view": name, "az": a.az, "matte": os.path.abspath(a.matte), "m_per_px": S,
           "iou": round(inter / max(1.0, union), 4),
           "ref_only_pct": round(100.0 * float((ref & ~mod).sum()) / max(1.0, float(ref.sum())), 2),
           "render_only_pct": round(100.0 * float((~ref & mod).sum()) / max(1.0, float(ref.sum())), 2),
           "band_width_diff": band_widths(ref, mod)}
    diff = np.zeros((H, W, 4), np.float32)
    diff[..., 3] = 1.0
    diff[..., :3] = 0.16
    diff[ref & mod, :3] = 0.59
    diff[ref & ~mod, :3] = (0.90, 0.24, 0.24)
    diff[~ref & mod, :3] = (0.24, 0.43, 0.94)
    save_rgba(os.path.join(os.path.abspath(a.out), "diff_%s.png" % name), diff)
    with open(os.path.join(a.out, "gate_%s.json" % name), "w") as f:
        json.dump(res, f, indent=2)
    print("%s IoU %.3f  reference-only %.1f%%  model-only %.1f%%  bands %s" % (
        name, res["iou"], res["ref_only_pct"], res["render_only_pct"], res["band_width_diff"]))


if __name__ == "__main__":
    main()
