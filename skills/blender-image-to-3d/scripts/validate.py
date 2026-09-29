"""
VALIDATE
Report everything that will break deformation, baking or export before it does.
Writes JSON and prints a summary. Exit code 1 when any FAIL is present.

blender --background --python-exit-code 1 --python scripts/validate.py -- \
  --blend CH_Knight/CH_Knight_master.blend --collections LOW,COLLISION,SOCKETS \
  --out CH_Knight/review/validate.json --budget-tris 60000 --max-influences 4 --require-uv

FAIL: negative scale, unweighted or over-influenced vertices on a bound mesh, open collision
proxies (COL_*), tri budget exceeded, missing UVs with --require-uv.
WARN: unapplied transforms, doubles, loose geometry, ngons, zero-area faces, .001 names,
non-normalised weights, tiny weights, vertex groups with no bone, unparented sockets.
Open boundaries are reported, not failed: cloth sheets, hair cards and decals are open by design.
"""
import bpy, bmesh, sys, os, re, json, argparse

NUMERIC_SUFFIX = re.compile(r"\.\d{3}$")


# ARGS

def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--collections", default="LOW,COLLISION,SOCKETS")
    p.add_argument("--out", default=None)
    p.add_argument("--budget-tris", type=int, default=0, help="LOD0 triangle budget for the LOW meshes (0 = no check)")
    p.add_argument("--max-influences", type=int, default=4)
    p.add_argument("--require-uv", action="store_true")
    p.add_argument("--doubles-dist", type=float, default=1e-4)
    p.add_argument("--max-verts-islands", type=int, default=400000, help="skip island counting above this")
    return p.parse_args(argv)


# CHECKS

def mesh_checks(ob, a, dg, report):
    me = ob.data
    rec = {"name": ob.name, "type": "MESH", "warn": [], "fail": [], "info": {}}
    W, F = rec["warn"].append, rec["fail"].append
    if NUMERIC_SUFFIX.search(ob.name) or NUMERIC_SUFFIX.search(me.name):
        W("numeric suffix in object or mesh name")
    if any(s < 0 for s in ob.scale):
        F("negative scale")
    if any(abs(s - 1) > 1e-4 for s in ob.scale):
        W("unapplied scale %s" % [round(s, 3) for s in ob.scale])
    if any(abs(r) > 1e-4 for r in ob.rotation_euler) and ob.parent is None:
        W("unapplied rotation")

    eo = ob.evaluated_get(dg)
    eme = eo.to_mesh()
    tris = sum(len(p.vertices) - 2 for p in eme.polygons)
    rec["info"]["tris_evaluated"] = tris
    rec["info"]["verts_evaluated"] = len(eme.vertices)
    eo.to_mesh_clear()

    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.normal_update()
    rec["info"]["verts"] = len(bm.verts)
    rec["info"]["faces"] = len(bm.faces)
    ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
    if ngons:
        W("%d ngons" % ngons)
    zero = sum(1 for f in bm.faces if f.calc_area() < 1e-9)
    if zero:
        W("%d zero-area faces" % zero)
    loose_v = sum(1 for v in bm.verts if not v.link_edges)
    wire = sum(1 for e in bm.edges if not e.link_faces)
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    nonmanifold = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    rec["info"].update({"boundary_edges": boundary, "nonmanifold_edges": nonmanifold})
    if loose_v:
        W("%d loose vertices" % loose_v)
    if wire:
        W("%d wire edges" % wire)
    if nonmanifold:
        W("%d edges shared by more than two faces" % nonmanifold)
    if len(bm.verts) <= a.max_verts_islands:
        d = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=a.doubles_dist)
        if d["targetmap"]:
            W("%d doubled vertices within %g" % (len(d["targetmap"]), a.doubles_dist))
        seen, islands = set(), 0
        for v in bm.verts:
            if v.index in seen:
                continue
            islands += 1
            stack = [v]
            while stack:
                cur = stack.pop()
                if cur.index in seen:
                    continue
                seen.add(cur.index)
                stack.extend(e.other_vert(cur) for e in cur.link_edges)
        rec["info"]["islands"] = islands
        bm2 = bm.copy()
        bmesh.ops.recalc_face_normals(bm2, faces=bm2.faces)
        bm2.faces.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        flipped = sum(1 for i, f in enumerate(bm.faces) if f.normal.dot(bm2.faces[i].normal) < 0)
        bm2.free()
        if flipped and boundary == 0:
            W("%d faces flipped relative to a consistent recalculation" % flipped)
        elif flipped:
            rec["info"]["flipped_vs_recalc"] = flipped
    if ob.name.startswith("COL_"):
        if boundary or nonmanifold or wire:
            F("collision proxy is not closed")
        if tris > 400:
            W("collision proxy has %d tris, expected a simple shape" % tris)
    bm.free()

    if not me.uv_layers:
        (F if a.require_uv else W)("no UV layer")
    else:
        uv = me.uv_layers.active.data
        outside = sum(1 for l in uv if not (0 <= l.uv.x <= 1 and 0 <= l.uv.y <= 1))
        rec["info"]["uv_loops_outside_0_1"] = outside
    if not me.materials or all(m is None for m in me.materials):
        W("no material")
    rec["info"]["materials"] = [m.name for m in me.materials if m]
    rec["info"]["modifiers"] = ["%s:%s" % (m.type, m.name) for m in ob.modifiers]
    if me.shape_keys:
        rec["info"]["shape_keys"] = len(me.shape_keys.key_blocks)
        if any(m.type not in ("ARMATURE",) for m in ob.modifiers):
            W("shape keys with other modifiers: glTF export cannot apply modifiers and keep morphs")

    arm_mods = [m for m in ob.modifiers if m.type == "ARMATURE" and m.object]
    if arm_mods:
        arm = arm_mods[0].object
        deform = {b.name for b in arm.data.bones if b.use_deform}
        vg_names = {vg.index: vg.name for vg in ob.vertex_groups}
        orphan = sorted(n for n in vg_names.values() if n not in deform and n not in arm.data.bones)
        unweighted = over = unnorm = tiny = 0
        for v in me.vertices:
            ws = [(vg_names.get(g.group, ""), g.weight) for g in v.groups]
            ws = [(n, w) for n, w in ws if n in deform and w > 0.0]
            total = sum(w for _, w in ws)
            if total < 1e-6:
                unweighted += 1
            if len(ws) > a.max_influences:
                over += 1
            if total > 1e-6 and abs(total - 1.0) > 0.01:
                unnorm += 1
            tiny += sum(1 for _, w in ws if w < 0.01)
        rec["info"]["skin"] = {"armature": arm.name, "unweighted": unweighted, "over_influences": over,
                               "not_normalised": unnorm, "tiny_weights": tiny, "orphan_groups": orphan}
        if unweighted:
            F("%d unweighted vertices" % unweighted)
        if over:
            F("%d vertices exceed %d influences" % (over, a.max_influences))
        if unnorm:
            W("%d vertices with weights not summing to 1" % unnorm)
        if tiny:
            W("%d tiny influences below 0.01" % tiny)
        if orphan:
            W("vertex groups with no bone: %s" % ", ".join(orphan[:8]))
    report.append(rec)
    return tris


def armature_checks(ob, report):
    rec = {"name": ob.name, "type": "ARMATURE", "warn": [], "fail": [], "info": {}}
    W, F = rec["warn"].append, rec["fail"].append
    bones = ob.data.bones
    rec["info"]["bones"] = len(bones)
    rec["info"]["deform_bones"] = sum(1 for b in bones if b.use_deform)
    roots = [b.name for b in bones if b.parent is None]
    rec["info"]["roots"] = roots
    if len(roots) > 1:
        W("multiple root bones: %s" % ", ".join(roots[:6]))
    if any(s < 0 for s in ob.scale):
        F("negative scale on armature")
    if any(abs(s - 1) > 1e-4 for s in ob.scale):
        W("unapplied armature scale")
    bad = [b.name for b in bones if NUMERIC_SUFFIX.search(b.name)]
    if bad:
        W("numeric suffix bones: %s" % ", ".join(bad[:6]))
    short = [b.name for b in bones if (b.head_local - b.tail_local).length < 1e-5]
    if short:
        F("zero-length bones: %s" % ", ".join(short[:6]))
    report.append(rec)


def socket_checks(ob, report):
    rec = {"name": ob.name, "type": "SOCKET", "warn": [], "fail": [], "info": {}}
    rec["info"]["parent"] = ob.parent.name if ob.parent else None
    rec["info"]["parent_bone"] = ob.parent_bone if ob.parent_type == "BONE" else None
    rec["info"]["props"] = {k: (v if isinstance(v, (int, float, str)) else str(v)) for k, v in ob.items()
                            if not k.startswith("_")}
    if ob.parent is None:
        rec["warn"].append("socket has no parent; attach it to the bone or part it follows")
    report.append(rec)


# MAIN

def main(a):
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    dg = bpy.context.evaluated_depsgraph_get()
    objs, missing = [], []
    for n in [x for x in a.collections.split(",") if x]:
        c = bpy.data.collections.get(n)
        if c:
            objs += list(c.all_objects)
        else:
            missing.append(n)
    seen = set()
    report, total_tris, lod_tris = [], 0, {}
    for ob in objs:
        if ob.name in seen:
            continue
        seen.add(ob.name)
        if ob.type == "MESH":
            t = mesh_checks(ob, a, dg, report)
            m = re.search(r"_LOD(\d+)$", ob.name)
            lod = int(m.group(1)) if m else 0
            if not ob.name.startswith("COL_"):
                lod_tris[lod] = lod_tris.get(lod, 0) + t
        elif ob.type == "ARMATURE":
            armature_checks(ob, report)
        elif ob.type == "EMPTY" and ob.name.startswith("SOCKET_"):
            socket_checks(ob, report)
    for ob in bpy.data.objects:
        if ob.type == "ARMATURE" and ob.name not in seen:
            seen.add(ob.name)
            armature_checks(ob, report)

    summary = {"tris_by_lod": lod_tris, "fail": [], "warn": []}
    summary["fail"] += ["collection not found: " + n for n in missing]
    if not any(lod_tris.values()):
        summary["fail"].append("no delivery mesh triangles in selected collections")
    lod0 = lod_tris.get(0, 0)
    if a.budget_tris and lod0 > a.budget_tris:
        summary["fail"].append("LOD0 tris %d exceed budget %d" % (lod0, a.budget_tris))
    for r in report:
        summary["fail"] += ["%s: %s" % (r["name"], f) for f in r["fail"]]
        summary["warn"] += ["%s: %s" % (r["name"], w) for w in r["warn"]]
    out = {"blend": os.path.abspath(a.blend), "summary": summary, "objects": report}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(out, f, indent=2)
    print("TRIS BY LOD", lod_tris)
    for r in report:
        print("%-28s %-8s tris=%s %s" % (r["name"], r["type"], r["info"].get("tris_evaluated", "-"),
                                         ("FAIL " + "; ".join(r["fail"])) if r["fail"] else ""))
    print("WARN", len(summary["warn"]))
    for w in summary["warn"]:
        print("  ", w)
    print("FAIL", len(summary["fail"]))
    for f in summary["fail"]:
        print("  ", f)
    if a.out:
        print("report", a.out)
    sys.exit(1 if summary["fail"] else 0)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    main(parse(argv))
