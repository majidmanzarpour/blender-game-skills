"""
REVIEW RENDER
Render the asset from fixed review views plus a camera matched to the reference image.
PNGs have a transparent background so compose_review.py can measure the silhouette.

blender --background --python-exit-code 1 --python scripts/review_render.py -- \
  --blend CH_Knight/CH_Knight_master.blend --out CH_Knight/review/02_blockout \
  --collections LOW --views front,side,threequarter --ref-cam 35 12 50 \
  --mode clay --res 1024 --gameplay-px 128

Modes: clay (dark solid, studio light), silhouette (black flat), wire (LOW only),
checker (UV checker, Cycles), material (scene materials, Cycles, adds a light rig if none).
--ref-cam AZ EL LENS: azimuth from the front (-Y) toward +X, elevation, lens in mm (0 = ortho).
--action NAME --frame N poses the armature before rendering (extreme pose sheets).
--turntable N renders N frames around the asset.
"""
import bpy, sys, os, math, json, argparse
from mathutils import Vector

FIXED_VIEWS = {
    "front": (0, 0, 0), "back": (180, 0, 0), "side": (90, 0, 0), "right": (90, 0, 0),
    "left": (270, 0, 0), "threequarter": (35, 15, 50), "threequarter_back": (215, 15, 50),
}
RENDERABLE = {"MESH", "CURVE", "CURVES", "FONT", "SURFACE", "META"}   # CURVES: strand hair, brows, lashes


# ARGS

def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--collections", default="LOW", help="comma list of existing collections")
    p.add_argument("--views", default="front,side,threequarter")
    p.add_argument("--ref-cam", type=float, nargs=3, default=None, metavar=("AZ", "EL", "LENS"))
    p.add_argument("--mode", default="clay", choices=["clay", "silhouette", "wire", "checker", "material"])
    p.add_argument("--res", type=int, nargs="+", default=[1024])
    p.add_argument("--gameplay-px", type=int, default=0, help="also render the ref (or threequarter) view this many px tall")
    p.add_argument("--engine", default="auto", choices=["auto", "workbench", "cycles"])
    p.add_argument("--samples", type=int, default=32)
    p.add_argument("--turntable", type=int, default=0)
    p.add_argument("--action", default=None)
    p.add_argument("--frame", type=int, default=None)
    p.add_argument("--margin", type=float, default=1.15)
    p.add_argument("--show-ref", action="store_true", help="include REF collection meshes (ruler, cell) for scale checks")
    p.add_argument("--only", default="", help="comma list of object names to render instead of whole collections")
    p.add_argument("--fit", default="", help="comma list of object names to fit the camera to (default: everything rendered)")
    return p.parse_args(argv)


# SCOPE AND VISIBILITY

def scope_objects(a):
    names = [n for n in a.collections.split(",") if n]
    objs = []
    if a.only:
        missing = [n for n in a.only.split(",") if n not in bpy.data.objects]
        if missing:
            raise ValueError("objects not found: " + ", ".join(missing))
        objs = [bpy.data.objects[n] for n in a.only.split(",") if n in bpy.data.objects]
    else:
        for n in names:
            c = bpy.data.collections.get(n)
            if c is None:
                raise ValueError("collection not found: " + n)
            if c:
                objs += [o for o in c.all_objects if o.type in RENDERABLE and not o.hide_render]
        if a.show_ref and bpy.data.collections.get("REF"):
            objs += [o for o in bpy.data.collections["REF"].all_objects if o.type == "MESH"]
    if not objs:
        raise ValueError("nothing to render in requested scope")
    seen, out = set(), []
    for o in objs:
        if o.name not in seen:
            seen.add(o.name)
            out.append(o)
    return out


def apply_visibility(scene, scope):
    keep = {o.name for o in scope}

    def walk(lc):
        lc.exclude = False
        lc.collection.hide_render = False
        for ch in lc.children:
            walk(ch)
    walk(bpy.context.view_layer.layer_collection)
    for o in scene.objects:
        if o.type in RENDERABLE:
            o.hide_render = o.name not in keep
        elif o.type in ("ARMATURE", "EMPTY"):
            o.hide_render = True


# GEOMETRY

def world_corners(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        eo = o.evaluated_get(dg)
        for c in eo.bound_box:
            pts.append(eo.matrix_world @ Vector(c))
    if not pts:
        raise RuntimeError("nothing to render in scope")
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    return corners, lo, hi


def view_direction(az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def fit_camera(cam, corners, center, dirv, W, H, lens, margin):
    look = -dirv
    q = look.to_track_quat("-Z", "Y")
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = q
    R = q.to_matrix()
    xa, ya = R @ Vector((1, 0, 0)), R @ Vector((0, 1, 0))
    lat = [((p - center).dot(xa), (p - center).dot(ya), (p - center).dot(dirv)) for p in corners]
    maxx = max(abs(v[0]) for v in lat)
    maxy = max(abs(v[1]) for v in lat)
    maxa = max(v[2] for v in lat)
    aspect = W / H
    cd = cam.data
    cd.sensor_fit = "HORIZONTAL"
    cd.sensor_width = 36.0
    if not lens:
        cd.type = "ORTHO"
        cd.ortho_scale = max(maxx * 2, maxy * 2 * aspect) * margin
        d = maxa + max(maxx, maxy, 1.0) * 4
    else:
        cd.type = "PERSP"
        cd.lens = lens
        th = math.atan(36.0 / (2 * lens))
        tv = math.atan((36.0 / aspect) / (2 * lens))
        d = 0.0
        for cx, cy, az in lat:
            d = max(d, az + abs(cx) * margin / math.tan(th), az + abs(cy) * margin / math.tan(tv))
    cam.location = center + dirv * d
    cd.clip_start = 0.01
    cd.clip_end = max(1000.0, d * 4)
    return d


# RENDER SETUP

def clay_material(color=(0.18, 0.18, 0.18, 1), emission=False):
    m = bpy.data.materials.new("_review_override")
    if bpy.app.version < (5, 0, 0) and not m.use_nodes:   # 5.0+ materials always use nodes
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    if emission:
        sh = nt.nodes.new("ShaderNodeEmission")
        sh.inputs["Color"].default_value = color
    else:
        sh = nt.nodes.new("ShaderNodeBsdfDiffuse")
        sh.inputs["Color"].default_value = color
    nt.links.new(sh.outputs[0], out.inputs["Surface"])
    return m


def checker_material():
    m = bpy.data.materials.new("_review_checker")
    if bpy.app.version < (5, 0, 0) and not m.use_nodes:   # 5.0+ materials always use nodes
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    ch = nt.nodes.new("ShaderNodeTexChecker")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    ch.inputs["Scale"].default_value = 16.0
    ch.inputs["Color1"].default_value = (0.9, 0.9, 0.9, 1)
    ch.inputs["Color2"].default_value = (0.15, 0.35, 0.8, 1)
    nt.links.new(tc.outputs["UV"], ch.inputs["Vector"])
    nt.links.new(ch.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m


def ensure_world(scene, strength=1.0, color=(0.5, 0.5, 0.5)):
    w = scene.world or bpy.data.worlds.new("_review_world")
    scene.world = w
    if bpy.app.version < (5, 0, 0) and not w.use_nodes:   # 5.0+ materials always use nodes
        w.use_nodes = True
    bg = next((n for n in w.node_tree.nodes if n.type == "BACKGROUND"), None)   # by type, not UI name
    if bg:
        bg.inputs[0].default_value = (color[0], color[1], color[2], 1)
        bg.inputs[1].default_value = strength


def add_light_rig(scene, center, extent):
    for name, az, el, power in (("key", -35, 45, 4.0), ("fill", 60, 20, 1.2), ("rim", 200, 40, 2.0)):
        ld = bpy.data.lights.new("_review_" + name, "SUN")
        ld.energy = power
        ld.angle = math.radians(5)
        lo = bpy.data.objects.new("_review_" + name, ld)
        d = view_direction(az, el)
        lo.location = center + d * extent * 4
        lo.rotation_mode = "QUATERNION"
        lo.rotation_quaternion = (-d).to_track_quat("-Z", "Y")
        scene.collection.objects.link(lo)


def setup_engine(scene, a, engine, scope, center, extent):
    vl = bpy.context.view_layer
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "AgX" if a.mode == "material" else "Standard"
    if a.mode == "wire":
        for o in scope:
            if o.type == "MESH" and not any(m.type == "WIREFRAME" and m.name == "_review_wire" for m in o.modifiers):
                w = o.modifiers.new("_review_wire", "WIREFRAME")
                w.thickness = max(0.0005, extent * 0.0025)
                w.use_replace = True
    if engine == "workbench":
        scene.render.engine = "BLENDER_WORKBENCH"
        sh = scene.display.shading
        sh.color_type = "SINGLE"
        sh.show_cavity = False
        sh.show_object_outline = False
        if a.mode in ("silhouette", "wire"):
            sh.light = "FLAT"
            sh.single_color = (0.0, 0.0, 0.0)
            sh.show_shadows = False
        else:
            sh.light = "STUDIO"
            sh.single_color = (0.18, 0.18, 0.18)
            sh.show_shadows = True
        try:
            scene.display.render_aa = "8"
        except Exception:
            pass
    else:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = a.samples
        scene.cycles.use_denoising = False
        scene.cycles.device = "CPU"
        if a.mode == "checker":
            vl.material_override = checker_material()
            scene.cycles.samples = min(a.samples, 8)
        elif a.mode in ("clay", "silhouette", "wire"):
            col = (0, 0, 0, 1) if a.mode != "clay" else (0.18, 0.18, 0.18, 1)
            vl.material_override = clay_material(col, emission=(a.mode != "clay"))
            if a.mode == "clay":
                ensure_world(scene, 0.6, (0.7, 0.7, 0.7))
                add_light_rig(scene, center, extent)
            else:
                scene.cycles.samples = min(a.samples, 8)
        else:
            ensure_world(scene, 0.4, (0.55, 0.55, 0.6))
            if not any(o.type == "LIGHT" and not o.hide_render for o in scene.objects):
                add_light_rig(scene, center, extent)


def render_to(scene, path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return path


# MAIN

def main(a):
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    scene = bpy.context.scene
    os.makedirs(a.out, exist_ok=True)
    scope = scope_objects(a)
    apply_visibility(scene, scope)

    if a.action:
        arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
        act = bpy.data.actions.get(a.action)
        if arm and act:
            ad = arm.animation_data or arm.animation_data_create()
            ad.action = act
            if hasattr(ad, "action_slot") and getattr(act, "slots", None) and len(act.slots):
                ad.action_slot = act.slots[0]
        else:
            raise ValueError("action or armature not found: " + a.action)
    if a.frame is not None:
        scene.frame_set(a.frame)
    bpy.context.view_layer.update()

    fit_objs = [bpy.data.objects[n] for n in a.fit.split(",") if n and n in bpy.data.objects] or scope
    corners, lo, hi = world_corners(fit_objs)
    size = hi - lo
    center = (lo + hi) / 2
    extent = max(size.x, size.y, size.z)
    W = a.res[0]
    H = a.res[1] if len(a.res) > 1 else a.res[0]

    engine = a.engine
    if engine == "auto":
        engine = "cycles" if a.mode in ("checker", "material") else "workbench"
    setup_engine(scene, a, engine, scope, center, extent)

    cam = bpy.data.objects.new("_review_cam", bpy.data.cameras.new("_review_cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam

    views = {}
    for v in [x for x in a.views.split(",") if x]:
        if v == "top":
            views[v] = ("top", 0, 0)
        elif v in FIXED_VIEWS:
            views[v] = FIXED_VIEWS[v]
        else:
            raise ValueError("unknown view: " + v)
    if a.ref_cam:
        views["ref"] = tuple(a.ref_cam)

    def camera_for(spec, w, h):
        if spec[0] == "top":
            dirv, lens = Vector((0, 0, 1)), 0
        else:
            dirv, lens = view_direction(spec[0], spec[1]), spec[2]
        return fit_camera(cam, corners, center, dirv, w, h, lens, a.margin)

    info = {"blend": os.path.abspath(a.blend), "mode": a.mode, "engine": engine,
            "bbox_min": list(lo), "bbox_max": list(hi), "size_xyz": list(size),
            "objects": [o.name for o in scope], "renders": {}}
    fallback_done = False
    for name, spec in views.items():
        scene.render.resolution_x, scene.render.resolution_y = W, H
        d = camera_for(spec, W, H)
        path = os.path.join(a.out, "%s_%s.png" % (a.mode, name))
        try:
            render_to(scene, path)
        except Exception as e:
            if engine == "workbench" and not fallback_done:
                print("workbench failed (%s), falling back to cycles" % e)
                engine = "cycles"
                fallback_done = True
                setup_engine(scene, a, engine, scope, center, extent)
                render_to(scene, path)
            else:
                raise
        info["renders"][name] = {"file": path, "az_el_lens": list(spec) if spec[0] != "top" else "top", "distance": d}
        print("rendered", path)

    if a.gameplay_px:
        spec = views.get("ref") or FIXED_VIEWS["threequarter"]
        px = int(a.gameplay_px * a.margin)
        scene.render.resolution_x, scene.render.resolution_y = px, px
        camera_for(spec, px, px)
        path = os.path.join(a.out, "%s_gameplay_%dpx.png" % (a.mode, a.gameplay_px))
        render_to(scene, path)
        info["renders"]["gameplay"] = {"file": path, "subject_px": a.gameplay_px}
        print("rendered", path)

    if a.turntable:
        scene.render.resolution_x, scene.render.resolution_y = W, H
        for i in range(a.turntable):
            az = i * 360.0 / a.turntable
            camera_for((az, 15, 50), W, H)
            path = os.path.join(a.out, "%s_turntable_%02d.png" % (a.mode, i))
            render_to(scene, path)
        info["renders"]["turntable"] = {"frames": a.turntable, "pattern": "%s_turntable_NN.png" % a.mode}

    with open(os.path.join(a.out, "render_info.json"), "w") as f:
        json.dump(info, f, indent=2)
    print("size x %.3f y %.3f z %.3f m" % (size.x, size.y, size.z))


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    main(parse(argv))
