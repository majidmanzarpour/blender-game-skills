"""
INIT MASTER
Create the master .blend for one asset: units, collections, calibration objects,
reference images placed at real scale, and the intended game camera.

blender --background --python-exit-code 1 --python scripts/init_master.py -- \
  --out CH_Knight/CH_Knight_master.blend --name CH_Knight --height 1.85 \
  --ref-front ref/front.png --ref-right ref/side.png --ref-back ref/back.png \
  --ref-extra ref/concept.png --subject-frac 0.9 --ground-frac 0.05 \
  --cell 2.0 --cam-elev 50 --cam-az 30 --cam-dist 12 --cam-lens 50

Convention: metres, Z up, subject faces -Y, origin at ground contact.
--height is the subject's real vertical extent. --subject-frac is how much of the
image height the subject occupies in the front/side/back views (measure it).
"""
import bpy, bmesh, sys, os, math, argparse
from mathutils import Vector

COLLECTIONS = ["REF", "HIGH", "LOW", "RIG_CTRL", "RIG_DEF", "COLLISION", "SOCKETS", "EXPORT"]


# ARGS

def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--name", required=True, help="asset name with prefix, e.g. CH_Knight, VH_Buggy, AR_Chapel")
    p.add_argument("--height", type=float, default=1.8, help="subject vertical extent in metres")
    p.add_argument("--length", type=float, default=None, help="subject extent along Y in metres, used to scale a top view")
    p.add_argument("--ref-front")
    p.add_argument("--ref-back")
    p.add_argument("--ref-left")
    p.add_argument("--ref-right")
    p.add_argument("--ref-top")
    p.add_argument("--ref-extra", nargs="*", default=[])
    p.add_argument("--subject-frac", type=float, default=0.9)
    p.add_argument("--ground-frac", type=float, default=0.05)
    p.add_argument("--ref-alpha", type=float, default=0.6)
    p.add_argument("--cell", type=float, default=2.0, help="gameplay grid cell in metres")
    p.add_argument("--door", type=float, nargs=2, default=[1.0, 2.2], help="door clearance width height")
    p.add_argument("--capsule", type=float, nargs=2, default=[0.35, 1.8], help="collision capsule radius height")
    p.add_argument("--cam-elev", type=float, default=50)
    p.add_argument("--cam-az", type=float, default=30)
    p.add_argument("--cam-dist", type=float, default=12)
    p.add_argument("--cam-lens", type=float, default=50)
    p.add_argument("--fps", type=int, default=30)
    return p.parse_args(argv)


# HELPERS

def collection(name):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
    return c


def mesh_ob(name, bm, col, wire=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    col.objects.link(ob)
    if wire:
        ob.display_type = "WIRE"
    ob.hide_render = True
    return ob


def box_bm(size, base_at_origin=True):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    if base_at_origin:
        bmesh.ops.translate(bm, vec=(0, 0, size[2] / 2), verts=bm.verts)
    return bm


def label(text, location, col, size=0.15):
    cu = bpy.data.curves.new("REF_label_" + text.split()[0].lower(), "FONT")
    cu.body = text
    cu.size = size
    ob = bpy.data.objects.new(cu.name, cu)
    ob.location = location
    col.objects.link(ob)
    ob.hide_render = True
    return ob


def view_direction(az_deg, el_deg):
    """Direction from the subject centre to the camera. az 0 = front (-Y), 90 = right (+X)."""
    az, el = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def image_empty(name, path, col, height_m, rotation, location, offset, alpha):
    img = bpy.data.images.load(os.path.abspath(path), check_existing=True)
    w, h = img.size
    if w == 0 or h == 0:
        raise RuntimeError("could not read image size: " + path)
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = "IMAGE"
    ob.data = img
    # displayed size of the larger image dimension equals empty_display_size
    ob.empty_display_size = height_m * max(w, h) / h
    ob.empty_image_offset = offset
    ob.empty_image_depth = "BACK"
    ob.empty_image_side = "DOUBLE_SIDED"
    ob.use_empty_image_alpha = True
    ob.color = (1, 1, 1, alpha)
    ob.rotation_euler = rotation
    ob.location = location
    col.objects.link(ob)
    return ob


# MAIN

def main(a):
    if a.height <= 0 or not 0 < a.subject_frac <= 1 or not 0 <= a.ground_frac < 1:
        raise ValueError("height must be positive; subject-frac in (0, 1], ground-frac in [0, 1)")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = a.name
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"
    scene.render.fps = a.fps
    scene.frame_start, scene.frame_end = 1, 60
    scene["asset_name"] = a.name
    scene["target_height_m"] = a.height
    scene["authoring_up"] = "Z"
    scene["authoring_forward"] = "-Y"
    scene["grid_cell_m"] = a.cell

    for c in COLLECTIONS:
        collection(c)
    ref = collection("REF")
    H = a.height
    off = max(H, a.length or 0.0) * 0.75 + 0.5   # image planes sit outside the working volume

    # AXIS MARKER AND LABELS
    axis = bpy.data.objects.new("REF_axis", None)
    axis.empty_display_type = "ARROWS"
    axis.empty_display_size = max(0.25, H * 0.2)
    ref.objects.link(axis)
    label("FRONT -Y", (-0.3, -H * 0.4 - 0.2, 0), ref, size=max(0.08, H * 0.06))
    label("RIGHT +X", (H * 0.4 + 0.1, -0.05, 0), ref, size=max(0.08, H * 0.06))

    # CALIBRATION OBJECTS
    ruler = mesh_ob("REF_ruler_%.2fm" % H, box_bm((0.04, 0.04, H)), ref)
    ruler.location = (-H * 0.45, 0, 0)
    for i in range(1, int(H) + 1):
        t = mesh_ob("REF_tick_%dm" % i, box_bm((0.12, 0.04, 0.005)), ref)
        t.location = (-H * 0.45, 0, i)
    cell = mesh_ob("REF_cell_%.1fm" % a.cell, box_bm((a.cell, a.cell, 0.01)), ref)
    door = mesh_ob("REF_door_%.1fx%.1f" % tuple(a.door), box_bm((a.door[0], 0.05, a.door[1])), ref)
    door.location = (a.cell, 0, 0)
    r, ch = a.capsule
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=16, radius1=r, radius2=r, depth=max(0.01, ch - 2 * r))
    bmesh.ops.translate(bm, vec=(0, 0, ch / 2), verts=bm.verts)
    for z in (r, ch - r):
        s = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=r)
        bmesh.ops.translate(bm, vec=(0, 0, z), verts=s["verts"])
    cap = mesh_ob("REF_capsule_r%.2f_h%.2f" % (r, ch), bm, ref)
    cap.location = (-a.cell, 0, 0)

    # GAME CAMERA
    cd = bpy.data.cameras.new("REF_game_camera")
    cd.lens = a.cam_lens
    cam = bpy.data.objects.new("REF_game_camera", cd)
    d = view_direction(a.cam_az, a.cam_elev)
    target = Vector((0, 0, H * 0.5))
    cam.location = target + d * a.cam_dist
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (-d).to_track_quat("-Z", "Y")
    ref.objects.link(cam)
    scene.camera = cam

    # REFERENCE IMAGES (standing planes behind the subject, ground line on z = 0)
    hd = H / a.subject_frac   # occupied image fraction; ground offset does not change scale
    offset = (-0.5, -a.ground_frac)
    p2 = math.pi / 2
    if a.ref_front:
        image_empty("REF_img_front", a.ref_front, ref, hd, (p2, 0, 0), (0, off, 0), offset, a.ref_alpha)
    if a.ref_back:
        image_empty("REF_img_back", a.ref_back, ref, hd, (p2, 0, math.pi), (0, -off, 0), offset, a.ref_alpha)
    if a.ref_right:
        image_empty("REF_img_right", a.ref_right, ref, hd, (p2, 0, p2), (-off, 0, 0), offset, a.ref_alpha)
    if a.ref_left:
        image_empty("REF_img_left", a.ref_left, ref, hd, (p2, 0, -p2), (off, 0, 0), offset, a.ref_alpha)
    if a.ref_top:
        L = (a.length or H) / a.subject_frac
        image_empty("REF_img_top", a.ref_top, ref, L, (0, 0, 0), (0, 0, -0.01), (-0.5, -0.5), a.ref_alpha)
    for i, path in enumerate(a.ref_extra):
        image_empty("REF_img_extra_%d" % i, path, ref, hd, (p2, 0, 0), (off * (1.5 + i * 1.2), off, 0),
                    (-0.5, 0.0), 1.0)

    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out)
    try:
        bpy.ops.file.make_paths_relative()
        bpy.ops.wm.save_as_mainfile(filepath=out)
    except Exception as e:
        print("relative paths skipped:", e)
    print("MASTER SAVED", out)
    print("collections:", ", ".join(COLLECTIONS))
    print("subject height %.3f m, image plane height %.3f m, game camera az %.0f el %.0f lens %.0f"
          % (H, hd, a.cam_az, a.cam_elev, a.cam_lens))


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    main(parse(argv))
