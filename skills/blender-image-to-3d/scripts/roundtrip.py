"""
ROUNDTRIP
Import the exported file into a blank Blender, report what actually arrived (meshes, tris,
materials, images, bones, clips, bounds) and render a clay frame. The export is only trusted
after this passes; the modelling viewport is not evidence of what an engine will receive.

blender --background --python-exit-code 1 --python scripts/roundtrip.py -- \
  --file CH_Knight/exports/CH_Knight.glb --out CH_Knight/review/09_roundtrip --expect-height 1.85

Checks object names, triangle totals per LOD and required deformation bones against
asset-manifest.json beside the export (override with --manifest). Clip timing, texture
roles and visual appearance still require inspection of the report and rendered evidence.
"""
import bpy, sys, os, math, json, argparse
from mathutils import Vector


def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--expect-height", type=float, default=0.0)
    p.add_argument("--manifest", help="defaults to asset-manifest.json beside the export")
    p.add_argument("--res", type=int, default=768)
    return p.parse_args(argv)


def view_direction(az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def check_manifest(report, manifest, path):
    """Compare the combined export or the matching split LOD with its source contract."""
    keys = [key for key, value in manifest["files"].items()
            if os.path.basename(value) == os.path.basename(path)]
    if len(keys) != 1:
        report["errors"].append("export is not uniquely identified in manifest files")
        return
    key = keys[0]
    lods = [lod for lod in manifest["lods"] if key == "all" or key == "LOD%d" % lod["level"]]
    if not lods:
        report["errors"].append("manifest contains no matching delivery LOD")
        return
    meshes = {mesh["name"]: mesh for mesh in report["meshes"]}
    expected = set()
    for lod in lods:
        expected.update(lod["objects"])
        missing = set(lod["objects"]) - meshes.keys()
        if missing:
            report["errors"].append("missing LOD objects: " + ", ".join(sorted(missing)))
        tris = sum(meshes[name]["tris"] for name in lod["objects"] if name in meshes)
        if tris != lod["tris"]:
            report["errors"].append("LOD%d triangles: expected %d, imported %d" % (lod["level"], lod["tris"], tris))
    if key in ("all", "LOD0"):
        expected.update(collider["name"] for collider in manifest.get("colliders", []))
    if meshes.keys() != expected:
        report["errors"].append("imported mesh names differ from manifest")
    bones = {bone for arm in report["armatures"] for bone in arm["bones"]}
    required = {bone for arm in manifest.get("skeleton", []) for bone in arm["deform_bones"]}
    if not required <= bones:
        report["errors"].append("missing deformation bones: " + ", ".join(sorted(required - bones)))
    report["manifest_checks"] = ["mesh names", "triangles per LOD", "required deformation bones"]
    report["manual_checks"] = ["clip names and timing", "texture roles and packing", "visual match"]


def main(a):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    os.makedirs(a.out, exist_ok=True)
    path = os.path.abspath(a.file)
    manifest_path = a.manifest or os.path.join(os.path.dirname(path), "asset-manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        kw = {"filepath": path}
        if "disable_bone_shape" in bpy.ops.import_scene.gltf.get_rna_type().properties:
            kw["disable_bone_shape"] = True      # otherwise the importer adds icosphere bone shapes to the scene
        bpy.ops.import_scene.gltf(**kw)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise RuntimeError("unsupported file " + ext)
    shapes = set()
    for o in scene.objects:
        if o.type == "ARMATURE":
            for pb in o.pose.bones:
                if pb.custom_shape:
                    shapes.add(pb.custom_shape.name)

    dg = bpy.context.evaluated_depsgraph_get()
    report = {"file": path, "errors": [], "meshes": [], "armatures": [], "clips": [], "materials": [], "images": []}
    pts = []
    for o in scene.objects:
        if o.name in shapes:
            continue
        if o.type == "MESH":
            eo = o.evaluated_get(dg)
            me = eo.to_mesh()
            tris = sum(len(p.vertices) - 2 for p in me.polygons)
            eo.to_mesh_clear()
            pts += [eo.matrix_world @ Vector(c) for c in eo.bound_box]
            report["meshes"].append({"name": o.name, "tris": tris, "verts": len(o.data.vertices),
                                     "uv": bool(o.data.uv_layers), "materials": [m.name for m in o.data.materials if m],
                                     "skinned": any(m.type == "ARMATURE" for m in o.modifiers),
                                     "shape_keys": len(o.data.shape_keys.key_blocks) if o.data.shape_keys else 0})
        elif o.type == "ARMATURE":
            report["armatures"].append({"name": o.name, "bones": [b.name for b in o.data.bones],
                                        "roots": [b.name for b in o.data.bones if b.parent is None]})
        elif o.type == "EMPTY":
            report.setdefault("empties", []).append({"name": o.name, "parent": o.parent.name if o.parent else None,
                                                     "parent_bone": o.parent_bone or None})
    for act in bpy.data.actions:
        fs, fe = act.frame_range
        report["clips"].append({"name": act.name, "frames": [int(fs), int(fe)],
                                "length_s": round((fe - fs) / (scene.render.fps / scene.render.fps_base), 4)})
    for m in bpy.data.materials:
        if m.users:
            report["materials"].append(m.name)
    for img in bpy.data.images:
        if img.users and img.type == "IMAGE":
            ok = img.has_data or bool(img.packed_file) or (img.filepath and os.path.exists(bpy.path.abspath(img.filepath)))
            report["images"].append({"name": img.name, "size": list(img.size), "ok": bool(ok)})
            if not ok:
                report["errors"].append("image missing: " + img.name)
    if not report["meshes"]:
        report["errors"].append("no meshes imported")
    check_manifest(report, manifest, path)
    if pts:
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        report["bounds_m"] = {"min": [round(v, 4) for v in lo], "max": [round(v, 4) for v in hi],
                              "size": [round(hi[i] - lo[i], 4) for i in range(3)]}
        height = hi[2] - lo[2]
        report["height_m"] = round(height, 4)
        if a.expect_height and abs(height - a.expect_height) / a.expect_height > 0.01:
            report["errors"].append("height %.3f differs from expected %.3f" % (height, a.expect_height))
        if lo[2] < -0.02:
            report["errors"].append("geometry sits %.3f m below the ground plane" % -lo[2])

        # CLAY RENDER, WORKBENCH
        center = Vector([(lo[i] + hi[i]) / 2 for i in range(3)])
        corners = [Vector((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        cam = bpy.data.objects.new("_rt_cam", bpy.data.cameras.new("_rt_cam"))
        scene.collection.objects.link(cam)
        scene.camera = cam
        d = view_direction(35, 15)
        q = (-d).to_track_quat("-Z", "Y")
        cam.rotation_mode = "QUATERNION"
        cam.rotation_quaternion = q
        R = q.to_matrix()
        xa, ya = R @ Vector((1, 0, 0)), R @ Vector((0, 1, 0))
        lens = 50.0
        t = math.atan(36.0 / (2 * lens))
        dist = 0.0
        for p in corners:
            r = p - center
            dist = max(dist, r.dot(d) + abs(r.dot(xa)) * 1.15 / math.tan(t), r.dot(d) + abs(r.dot(ya)) * 1.15 / math.tan(t))
        cam.data.lens = lens
        cam.data.sensor_fit = "HORIZONTAL"
        cam.location = center + d * dist
        cam.data.clip_end = max(1000.0, dist * 4)
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "SINGLE"
        scene.display.shading.single_color = (0.18, 0.18, 0.18)
        scene.display.shading.show_shadows = True
        scene.render.film_transparent = True
        scene.render.resolution_x = scene.render.resolution_y = a.res
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.filepath = os.path.join(a.out, "roundtrip_clay_threequarter.png")
        try:
            bpy.ops.render.render(write_still=True)
            report["render"] = scene.render.filepath
        except Exception as e:
            report["errors"].append("render failed: %s" % e)

    with open(os.path.join(a.out, "roundtrip.json"), "w") as f:
        json.dump(report, f, indent=2)
    print("MESHES", [(m["name"], m["tris"]) for m in report["meshes"]])
    print("ARMATURES", [(x["name"], len(x["bones"])) for x in report["armatures"]])
    print("CLIPS", [(c["name"], c["length_s"]) for c in report["clips"]])
    print("IMAGES", [(i["name"], i["ok"]) for i in report["images"]])
    print("HEIGHT", report.get("height_m"))
    print("ERRORS", report["errors"])
    sys.exit(1 if report["errors"] else 0)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    main(parse(argv))
