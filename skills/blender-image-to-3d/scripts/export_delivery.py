"""
EXPORT DELIVERY
Export the delivery selection (LOW meshes, deformation skeleton, sockets, colliders) to GLB or FBX,
and write asset-manifest.json and animation-contract.json next to it. REF, HIGH and RIG_CTRL never export.

blender --background --python-exit-code 1 --python scripts/export_delivery.py -- \
  --blend CH_Knight/CH_Knight_baked.blend --out CH_Knight/exports --name CH_Knight \
  --format GLB --collections LOW,RIG_DEF,SOCKETS,COLLISION --split-lods --inferred "back of cloak,sole tread"

--no-apply keeps modifiers unapplied at export (required when meshes carry shape keys: apply
the modifiers on the delivery copy first, then export with --no-apply).
glTF is exported Y up; the manifest records the authoring axes so the importer can check.
"""
import bpy, sys, os, re, json, math, argparse, datetime
from mathutils import Vector

LOD_RE = re.compile(r"_LOD(\d+)$")


# ARGS

def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--format", default="GLB", choices=["GLB", "GLTF", "FBX"])
    p.add_argument("--collections", default="LOW,RIG_DEF,SOCKETS,COLLISION")
    p.add_argument("--split-lods", action="store_true", help="one file per LOD level in addition to the combined file")
    p.add_argument("--no-apply", action="store_true")
    p.add_argument("--no-animation", action="store_true")
    p.add_argument("--inferred", default="", help="comma list of parts modelled without reference coverage")
    p.add_argument("--engine", default="", help="target engine name for the manifest")
    return p.parse_args(argv)


# HELPERS

def op_kwargs(op, kwargs):
    props = op.get_rna_type().properties.keys()
    return {k: v for k, v in kwargs.items() if k in props}


def select_only(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.hide_set(False)
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def bounds(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        if o.type != "MESH":
            continue
        eo = o.evaluated_get(dg)
        pts += [eo.matrix_world @ Vector(c) for c in eo.bound_box]
    if not pts:
        return None
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return {"min": [round(v, 4) for v in lo], "max": [round(v, 4) for v in hi],
            "size": [round(hi[i] - lo[i], 4) for i in range(3)]}


def tri_count(o):
    dg = bpy.context.evaluated_depsgraph_get()
    eo = o.evaluated_get(dg)
    me = eo.to_mesh()
    n = sum(len(p.vertices) - 2 for p in me.polygons)
    eo.to_mesh_clear()
    return n


def export(path, objs, a):
    select_only(objs)
    if a.format in ("GLB", "GLTF"):
        kw = dict(filepath=path, export_format="GLB" if a.format == "GLB" else "GLTF_SEPARATE", use_selection=True, export_apply=not a.no_apply,
                  export_yup=True, export_animations=not a.no_animation, export_animation_mode="ACTIONS",
                  export_nla_strips=True, export_def_bones=True, export_skins=True, export_morph=True,
                  export_texcoords=True, export_normals=True, export_materials="EXPORT",
                  export_image_format="AUTO", export_extras=True, export_cameras=False, export_lights=False,
                  export_rest_position_armature=True, export_optimize_animation_size=False)
        bpy.ops.export_scene.gltf(**op_kwargs(bpy.ops.export_scene.gltf, kw))
    else:
        kw = dict(filepath=path, use_selection=True, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
                  bake_space_transform=False, object_types={"ARMATURE", "MESH", "EMPTY"},
                  use_mesh_modifiers=not a.no_apply, add_leaf_bones=False, bake_anim=not a.no_animation,
                  bake_anim_use_all_actions=True, bake_anim_use_nla_strips=True, use_armature_deform_only=True,
                  mesh_smooth_type="FACE", path_mode="COPY", embed_textures=True, axis_forward="-Z", axis_up="Y",
                  use_custom_props=True)
        bpy.ops.export_scene.fbx(**op_kwargs(bpy.ops.export_scene.fbx, kw))
    print("exported", path, "objects", len(objs))


def action_contract(act, fps):
    fs, fe = act.frame_range
    loop = bool(act.get("loop", act.name.lower().endswith("_loop") or act.name.lower() in ("idle", "walk", "run")))
    events = {}
    for m in act.pose_markers:
        events[m.name] = round((m.frame - fs) / fps, 4)
    rec = {"name": act.name, "fps": fps, "frame_start": int(fs), "frame_end": int(fe),
           "length_s": round((fe - fs) / fps, 4), "loop": loop, "events_s": events}
    for k in ("speed_mps", "blend_direction", "root_motion", "interruptible", "locomotion_allowed"):
        if k in act:
            rec[k] = act[k] if isinstance(act[k], (int, float, str, bool)) else str(act[k])
    return rec


# MAIN

def main(a):
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    scene = bpy.context.scene
    os.makedirs(a.out, exist_ok=True)

    def walk(lc):
        lc.exclude = False
        for ch in lc.children:
            walk(ch)
    walk(bpy.context.view_layer.layer_collection)

    objs, seen = [], set()
    for n in [x for x in a.collections.split(",") if x and x not in ("REF", "HIGH", "RIG_CTRL")]:
        c = bpy.data.collections.get(n)
        if not c:
            continue
        for o in c.all_objects:
            if o.name not in seen and not o.hide_render and not o.name.startswith(("CUT_", "REF_")):
                seen.add(o.name)
                objs.append(o)
    meshes = [o for o in objs if o.type == "MESH"]
    arms = [o for o in objs if o.type == "ARMATURE"]
    for m in meshes:
        for mod in m.modifiers:
            if mod.type == "ARMATURE" and mod.object and mod.object.name not in seen:
                seen.add(mod.object.name)
                objs.append(mod.object)
                arms.append(mod.object)
    if not meshes:
        raise RuntimeError("no meshes in export collections " + a.collections)

    ext = {"GLB": ".glb", "GLTF": ".gltf", "FBX": ".fbx"}[a.format]
    files = {}
    main_path = os.path.join(a.out, a.name + ext)
    export(main_path, objs, a)
    files["all"] = main_path

    lods = {}
    for m in meshes:
        if m.name.startswith("COL_"):
            continue
        mm = LOD_RE.search(m.name)
        lods.setdefault(int(mm.group(1)) if mm else 0, []).append(m)
    if a.split_lods and len(lods) > 1:
        for level, ms in sorted(lods.items()):
            extra = arms + [o for o in objs if o.type == "EMPTY"] if level == 0 else arms
            extra += [m for m in meshes if m.name.startswith("COL_")] if level == 0 else []
            p = os.path.join(a.out, "%s_LOD%d%s" % (a.name, level, ext))
            export(p, ms + extra, a)
            files["LOD%d" % level] = p

    fps = scene.render.fps / scene.render.fps_base
    actions = [] if a.no_animation else [act for act in bpy.data.actions if act.users and not act.name.startswith("_")]
    contract = {"asset": a.name, "fps": fps, "time_unit": "seconds from clip start",
                "clips": [action_contract(act, fps) for act in actions],
                "note": "events come from pose markers on each action; loop/speed/root_motion from action custom properties"}
    with open(os.path.join(a.out, "animation-contract.json"), "w") as f:
        json.dump(contract, f, indent=2)

    # TEXTURES: only images referenced by the materials of exported meshes (reference planes stay out)
    used = {}
    for m in meshes:
        for mat in m.data.materials:
            if mat and mat.node_tree:
                for n in mat.node_tree.nodes:
                    if n.type == "TEX_IMAGE" and n.image is not None:
                        used[n.image.name] = n.image
    images = []
    for img in used.values():
        images.append({"name": img.name, "size": list(img.size), "colorspace": img.colorspace_settings.name,
                       "packed": bool(img.packed_file), "filepath": bpy.path.abspath(img.filepath) if img.filepath else ""})
    manifest = {
        "asset": a.name,
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "blender": bpy.app.version_string,
        "source": os.path.abspath(a.blend),
        "target_engine": a.engine,
        "units": {"system": scene.unit_settings.system, "scale_length": scene.unit_settings.scale_length,
                  "authoring_up": scene.get("authoring_up", "Z"), "authoring_forward": scene.get("authoring_forward", "-Y"),
                  "export_up": "Y", "export_forward": "+Z" if a.format != "FBX" else "-Z",
                  "note": "glTF converts Z up to Y up; a subject facing -Y in Blender faces +Z in glTF"},
        "target_height_m": scene.get("target_height_m"),
        "bounds_m": bounds(lods.get(0, meshes)),
        "files": files,
        "lods": [{"level": lvl, "objects": [m.name for m in ms], "tris": sum(tri_count(m) for m in ms)}
                 for lvl, ms in sorted(lods.items())],
        "materials": sorted({mat.name for m in meshes for mat in m.data.materials if mat}),
        "textures": images,
        "texture_contract": {"basecolor": "sRGB", "normal": "Non-Color, tangent space, OpenGL +Y",
                             "roughness": "Non-Color", "metallic": "Non-Color", "ao": "Non-Color",
                             "packed_channels": "none by default; document any ORM packing here"},
        "skeleton": [{"armature": arm.name, "bones": len(arm.data.bones),
                      "deform_bones": [b.name for b in arm.data.bones if b.use_deform],
                      "roots": [b.name for b in arm.data.bones if b.parent is None],
                      "rest_pose": "as exported"} for arm in arms],
        "sockets": [{"name": o.name, "parent": o.parent.name if o.parent else None,
                     "parent_bone": o.parent_bone if o.parent_type == "BONE" else None,
                     "world_location": [round(v, 4) for v in o.matrix_world.translation],
                     "world_rotation_deg": [round(math.degrees(v), 2) for v in o.matrix_world.to_euler()],
                     "axes": "local +Y forward of the attachment, +Z up",
                     "props": {k: (v if isinstance(v, (int, float, str)) else str(v)) for k, v in o.items()}}
                    for o in objs if o.type == "EMPTY" and o.name.startswith("SOCKET_")],
        "colliders": [{"name": m.name, "shape": m.get("shape", "mesh"), "tris": tri_count(m), "bounds_m": bounds([m])}
                      for m in meshes if m.name.startswith("COL_")],
        "animation_contract": "animation-contract.json",
        "inferred_from_no_reference": [s.strip() for s in a.inferred.split(",") if s.strip()],
        "dependencies": [i["filepath"] for i in images if i["filepath"] and not i["packed"]],
    }
    mpath = os.path.join(a.out, "asset-manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)
    print("manifest", mpath)
    print("LODs", {("LOD%d" % l): sum(tri_count(m) for m in ms) for l, ms in lods.items()})
    print("clips", [c["name"] for c in contract["clips"]])


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    main(parse(argv))
