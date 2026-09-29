"""
BAKE MAPS
Bake the texture contract for every LOW mesh with Cycles:
  normal, ao   : HIGH to LOW (selected to active) with a cage extrusion and ray distance
  basecolor, roughness, metallic : HIGH when present, otherwise LOW (--surface-from overrides)
Then optionally rebuild each LOW material as a plain Principled BSDF wired to the baked images,
which is what glTF/FBX export can carry.

blender --background --python-exit-code 1 --python scripts/bake_maps.py -- \
  --blend CH_Knight/CH_Knight_master.blend --low LOW --high HIGH --res 2048 \
  --maps normal,ao,basecolor,roughness,metallic --out CH_Knight/textures \
  --extrusion 0.02 --ray-dist 0.05 --margin 16 --samples 64 --rebuild-material \
  --save CH_Knight/CH_Knight_baked.blend

Matching: with --match-by-name, a LOW object bakes only from HIGH objects whose names start with
its base name (CH_Knight_Body_LOD0 <- CH_Knight_Body*). Otherwise every HIGH object projects
onto every LOW object, which crosses onto neighbours (teeth, garments): use groups for those.
--exclude-sources REGEX leaves matching HIGH objects out of every bake: transparent shells (corneas,
visors, glass) otherwise catch the rays in front of what they cover.
Metallic has no bake type in Cycles, so it is routed through an Emission shader temporarily, on the
output node Cycles renders (materials with separate EEVEE and Cycles outputs keep the EEVEE one active).
Every bake except AO hides the renderables that take no part in it: Cycles syncs the whole scene for
each bake call, and strand hair or dense props elsewhere can dominate the time. AO keeps the scene so
neighbouring parts still occlude.
Colour spaces: basecolor sRGB, everything else Non-Color. Normal maps are tangent space, OpenGL +Y.
"""
import re
import bpy, sys, os, argparse

DATA_MAPS = {"normal", "ao", "roughness", "metallic"}
RENDERABLE = {"MESH", "CURVE", "CURVES", "FONT", "SURFACE", "META"}


# ARGS

def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--low", default="LOW")
    p.add_argument("--high", default="HIGH")
    p.add_argument("--only", default="", help="comma list of LOW object names")
    p.add_argument("--res", type=int, default=2048)
    p.add_argument("--maps", default="normal,ao,basecolor,roughness,metallic")
    p.add_argument("--out", required=True)
    p.add_argument("--extrusion", type=float, default=0.02)
    p.add_argument("--ray-dist", type=float, default=0.0, help="0 = unlimited")
    p.add_argument("--margin", type=int, default=16)
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--device", default="CPU", choices=["CPU", "GPU"])
    p.add_argument("--match-by-name", action="store_true",
                   help="bake <Name>_<Part>_LOD0 only from HIGH objects named <Name>_<Part>*")
    p.add_argument("--exclude-sources", default="",
                   help="regex: HIGH objects whose names match are never bake sources (corneas, glass)")
    p.add_argument("--surface-from", default="auto", choices=["auto", "high", "low"],
                   help="where base colour, roughness and metallic are sampled (auto = HIGH when present)")
    p.add_argument("--rebuild-material", action="store_true")
    p.add_argument("--all-lods", action="store_true",
                   help="bake every LOD separately with its own maps (default: LOD0 only; LOD1+ reuse the LOD0 material)")
    p.add_argument("--save", default=None, help="save the baked file here (default: do not save)")
    return p.parse_args(argv)


# HELPERS

def objects_in(col_name):
    c = bpy.data.collections.get(col_name)
    return [o for o in c.all_objects if o.type == "MESH"] if c else []


def base_name(name):
    for suf in ("_LOD0", "_LOD1", "_LOD2", "_LOD3"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def new_image(name, res, data):
    img = bpy.data.images.get(name)
    if img:
        bpy.data.images.remove(img)
    img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=False)
    img.colorspace_settings.name = "Non-Color" if data else "sRGB"
    return img


def ensure_material(ob):
    if not ob.data.materials or all(m is None for m in ob.data.materials):
        m = bpy.data.materials.new(ob.name + "_mat")
        if bpy.app.version < (5, 0, 0) and not m.use_nodes:   # 5.0+ materials always use nodes
            m.use_nodes = True
        if ob.data.materials:
            ob.data.materials[0] = m
        else:
            ob.data.materials.append(m)
    for m in ob.data.materials:
        if m and bpy.app.version < (5, 0, 0) and not m.use_nodes:   # 5.0+ materials always use nodes
            m.use_nodes = True


def set_bake_target(ob, img):
    """Every material on the object gets a selected, active Image Texture node pointing at img."""
    nodes_added = []
    for m in ob.data.materials:
        if not m:
            continue
        nt = m.node_tree
        for n in nt.nodes:
            n.select = False
        node = nt.nodes.new("ShaderNodeTexImage")
        node.name = "_bake_target"
        node.image = img
        node.select = True
        nt.nodes.active = node
        nodes_added.append((nt, node))
    return nodes_added


def clear_bake_targets(nodes_added):
    for nt, node in nodes_added:
        nt.nodes.remove(node)


def select(low, highs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for h in highs:
        h.hide_set(False)
        h.hide_render = False
        h.select_set(True)
    low.hide_set(False)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low


def cycles_output(nt):
    """The output node Cycles renders: a CYCLES-target output wins over an ALL-target one, the active
    one over the others (same rule as Blender; a material with separate EEVEE and Cycles outputs
    usually has the EEVEE one active)."""
    outs = [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
    for target in ("CYCLES", "ALL"):
        cand = [n for n in outs if n.target == target]
        if cand:
            return next((n for n in cand if n.is_active_output), cand[0])
    return None


def principled_feeding(out):
    """The Principled BSDF upstream of an output's Surface input (depth-first through mixes and groups
    of nodes), so the metallic comes from the shader that output actually renders."""
    seen, stack = set(), [l.from_node for l in out.inputs["Surface"].links]
    while stack:
        n = stack.pop()
        if n.name in seen:
            continue
        seen.add(n.name)
        if n.type == "BSDF_PRINCIPLED":
            return n
        stack += [l.from_node for i in n.inputs for l in i.links]
    return None


def isolate(scene, keep):
    """Hide every renderable outside `keep` for render. Returns what to unhide afterwards."""
    hidden = []
    for o in scene.objects:
        if o.type in RENDERABLE and o not in keep and not o.hide_render:
            o.hide_render = True
            hidden.append(o)
    return hidden


def metallic_to_emission(ob):
    """Temporarily drive the material output with Emission = metallic. Returns restore data."""
    restore = []
    for m in ob.data.materials:
        if not m:
            continue
        nt = m.node_tree
        out = cycles_output(nt)
        bsdf = principled_feeding(out) if out else None
        if not out or not bsdf:
            continue
        prev = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].links else None
        em = nt.nodes.new("ShaderNodeEmission")
        em.name = "_bake_metal_emit"
        mi = bsdf.inputs["Metallic"]
        if mi.links:
            nt.links.new(mi.links[0].from_socket, em.inputs["Color"])
        else:
            v = mi.default_value
            em.inputs["Color"].default_value = (v, v, v, 1)
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        restore.append((nt, out, prev, em))
    return restore


def restore_emission(restore):
    for nt, out, prev, em in restore:
        nt.nodes.remove(em)
        if prev is not None:
            nt.links.new(prev, out.inputs["Surface"])


def unlink_normal_inputs(ob):
    """Base colour/roughness bakes should not be affected by the material's own normal map."""
    restore = []
    for m in ob.data.materials:
        if not m:
            continue
        for n in m.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED" and n.inputs["Normal"].links:
                link = n.inputs["Normal"].links[0]
                restore.append((m.node_tree, link.from_socket, n.inputs["Normal"]))
                m.node_tree.links.remove(link)
    return restore


def relink(restore):
    for nt, src, dst in restore:
        nt.links.new(src, dst)


def bake(scene, kind, low, highs, img, a):
    select(low, highs if highs is not None else [])
    added = set_bake_target(low, img)
    b = scene.render.bake
    b.use_selected_to_active = highs is not None
    b.cage_extrusion = a.extrusion
    b.max_ray_distance = a.ray_dist
    b.margin = a.margin
    b.use_clear = True
    kwargs = dict(type=kind, margin=a.margin, use_clear=True,
                  use_selected_to_active=highs is not None, cage_extrusion=a.extrusion,
                  max_ray_distance=a.ray_dist)
    if kind == "NORMAL":
        b.normal_space = "TANGENT"
        kwargs["normal_space"] = "TANGENT"
    if kind == "DIFFUSE":
        b.use_pass_direct = b.use_pass_indirect = False
        b.use_pass_color = True
        kwargs["pass_filter"] = {"COLOR"}
    props = bpy.ops.object.bake.get_rna_type().properties.keys()
    kwargs = {k: v for k, v in kwargs.items() if k in props}
    hidden = [] if kind == "AO" else isolate(scene, {low, *(highs or [])})
    try:
        bpy.ops.object.bake(**kwargs)
    finally:
        for o in hidden:
            o.hide_render = False
        clear_bake_targets(added)


def rebuild_material(low, images):
    m = bpy.data.materials.new(base_name(low.name) + "_baked")
    if bpy.app.version < (5, 0, 0) and not m.use_nodes:   # 5.0+ materials always use nodes
        m.use_nodes = True
    nt = m.node_tree
    # by type, not UI name (display names are translated in non-English Blender UIs)
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    y = 300
    for key, sock in (("basecolor", "Base Color"), ("roughness", "Roughness"), ("metallic", "Metallic")):
        if key in images:
            t = nt.nodes.new("ShaderNodeTexImage")
            t.image = images[key]
            t.location = (-500, y)
            y -= 300
            nt.links.new(t.outputs["Color"], bsdf.inputs[sock])
    if "normal" in images:
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = images["normal"]
        t.location = (-700, y)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (-400, y)
        nt.links.new(t.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if "ao" in images:
        # glTF reads this socket; keep AO separate from base colour lighting.
        group = bpy.data.node_groups.get("glTF Material Output")
        if group is None:
            group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
        if not any(s.name == "Occlusion" and s.in_out == "INPUT" for s in group.interface.items_tree if s.item_type == "SOCKET"):
            group.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
        output = nt.nodes.new("ShaderNodeGroup")
        output.node_tree = group
        texture = nt.nodes.new("ShaderNodeTexImage")
        texture.image = images["ao"]
        nt.links.new(texture.outputs["Color"], output.inputs["Occlusion"])
    low.data.materials.clear()
    low.data.materials.append(m)
    return m


# MAIN

def main(a):
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = a.device
    scene.cycles.samples = a.samples
    scene.render.bake.target = "IMAGE_TEXTURES"
    os.makedirs(a.out, exist_ok=True)

    lows = objects_in(a.low)
    higher = []
    if not a.all_lods:
        higher = [o for o in lows if re.search(r"_LOD[1-9]$", o.name)]
        lows = [o for o in lows if o not in higher]
    if a.only:
        lows = [o for o in lows if o.name in a.only.split(",")]
    highs_all = objects_in(a.high)
    maps = [m for m in a.maps.split(",") if m]
    if not maps or set(maps) - (DATA_MAPS | {"basecolor"}):
        raise ValueError("maps must be a nonempty list of normal,ao,basecolor,roughness,metallic")
    if not lows:
        raise RuntimeError("no LOW meshes found in collection " + a.low)
    missing_uv = [o.name for o in lows if not o.data.uv_layers]
    if missing_uv:
        raise RuntimeError("missing UVs: " + ", ".join(missing_uv))

    def walk(lc):
        lc.exclude = False
        for ch in lc.children:
            walk(ch)
    walk(bpy.context.view_layer.layer_collection)

    for low in lows:
        ensure_material(low)
        highs = highs_all
        if a.match_by_name:
            bn = base_name(low.name)
            highs = [h for h in highs_all if h.name.startswith(bn)]
        if a.exclude_sources:
            highs = [h for h in highs if not re.search(a.exclude_sources, h.name)]
        images = {}
        # SURFACE SOURCE: colour, roughness and metallic come from the HIGH materials when they
        # exist (auto), so material breakup authored on HIGH lands in the LOW's single UV set.
        use_high_surface = (a.surface_from == "high") or (a.surface_from == "auto" and bool(highs))
        if a.surface_from == "high" and not highs:
            raise RuntimeError("--surface-from high but no HIGH objects matched " + low.name)
        surface_highs = highs if use_high_surface else None
        surface_objs = highs if use_high_surface else [low]
        for o in surface_objs:
            ensure_material(o)
        print("BAKE", low.name, "geometry from", [h.name for h in highs] if highs else "self",
              "| surface from", "HIGH" if use_high_surface else "LOW")
        for kind in maps:
            img = new_image("%s_%s" % (low.name if a.all_lods else base_name(low.name), kind), a.res, kind in DATA_MAPS)
            if kind == "normal":
                if highs:
                    bake(scene, "NORMAL", low, highs, img, a)
                else:
                    raise RuntimeError("normal bake requires matching HIGH objects: " + low.name)
            elif kind == "ao":
                bake(scene, "AO", low, highs if highs else None, img, a)
            elif kind == "basecolor":
                r = [unlink_normal_inputs(o) for o in surface_objs]
                try:
                    bake(scene, "DIFFUSE", low, surface_highs, img, a)
                finally:
                    for rr in r:
                        relink(rr)
            elif kind == "roughness":
                bake(scene, "ROUGHNESS", low, surface_highs, img, a)
            elif kind == "metallic":
                r = [metallic_to_emission(o) for o in surface_objs]
                try:
                    bake(scene, "EMIT", low, surface_highs, img, a)
                finally:
                    for rr in r:
                        restore_emission(rr)
            path = os.path.join(a.out, img.name + ".png")
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
            images[kind] = img
            print("  saved", path)
        if a.rebuild_material and images:
            rebuild_material(low, images)
            print("  material rebuilt from baked maps")

    # HIGHER LODS SHARE THE LOD0 MATERIAL (same UV layout expected from lod_copy)
    if a.rebuild_material:
        for h in higher:
            lod0 = bpy.data.objects.get(base_name(h.name) + "_LOD0")
            if lod0 and lod0.data.materials and lod0.data.materials[0]:
                h.data.materials.clear()
                h.data.materials.append(lod0.data.materials[0])
                print("  %s reuses %s" % (h.name, lod0.data.materials[0].name))

    if a.save:
        out = os.path.abspath(a.save)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=out)
        print("saved", out)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    main(parse(argv))
