"""Regression smoke test. Run with plain Python + Pillow, --blender /path/to/blender.

Creates only temporary assets; never opens or modifies an interactive Blender scene.
"""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile

from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", required=True)
    parser.add_argument("--out", type=Path, help="retain fixtures and reports in a new directory")
    args = parser.parse_args()
    scripts = Path(__file__).resolve().parent
    failures = []

    def check(condition, message):
        print(("PASS " if condition else "FAIL ") + message)
        if not condition:
            failures.append(message)

    if args.out:
        args.out.mkdir(parents=True, exist_ok=False)
    with (nullcontext(args.out) if args.out else tempfile.TemporaryDirectory(prefix="blender-skill-test-")) as tmp:
        root = Path(tmp)

        def blender(script, *argv):
            result = subprocess.run([args.blender, "--background", "--factory-startup",
                                     "--python-exit-code", "1", "--python", str(script),
                                     "--", *map(str, argv)], capture_output=True, text=True)
            if result.returncode not in (0, 1):
                raise RuntimeError(result.stdout + result.stderr)
            with (root / "commands.log").open("a") as log:
                log.write(result.stdout + result.stderr)
            return result

        ref = Image.new("RGBA", (100, 100))
        ImageDraw.Draw(ref).rectangle((30, 10, 69, 89), fill="white")
        ref.save(root / "ref.png")
        Image.new("RGBA", (100, 100)).save(root / "empty.png")
        result = subprocess.run([sys.executable, str(scripts / "compose_review.py"),
                                 "--ref", str(root / "ref.png"), "--render", str(root / "empty.png"),
                                 "--out", str(root / "compare.png"), "--measure"], capture_output=True)
        check(result.returncode != 0, "empty silhouette cannot pass measurement")

        result = blender(scripts / "init_master.py", "--out", root / "master.blend",
                         "--name", "PR_Test", "--height", "2", "--subject-frac", "0.8",
                         "--ground-frac", "0.1", "--ref-front", root / "ref.png")
        check(result.returncode == 0, "master initializes")
        result = blender(scripts / "validate.py", "--blend", root / "master.blend")
        check(result.returncode != 0, "empty delivery cannot pass validation")

        setup = root / "setup.py"
        setup.write_text(f'''
import bpy, json
bpy.ops.wm.open_mainfile(filepath={str(root / 'master.blend')!r})
ref = bpy.data.objects['REF_img_front']
with open({str(root / 'calibration.json')!r}, 'w') as f:
    json.dump({{'subject_height': ref.empty_display_size * 0.8}}, f)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
obj = bpy.context.object
obj.name = obj.data.name = 'PR_Test_Body_LOD0'
for c in list(obj.users_collection): c.objects.unlink(obj)
bpy.data.collections['LOW'].objects.link(obj)
mat = bpy.data.materials.new('TestMaterial')
obj.data.materials.append(mat)
obj.keyframe_insert(data_path='location', frame=1)
obj.keyframe_insert(data_path='location', frame=31)
obj.animation_data.action.name = 'Idle'
bpy.context.scene.render.fps = 30
bpy.context.scene.render.fps_base = 1.001
lod = obj.copy()
lod.data = obj.data.copy()
lod.animation_data_clear()
lod.name = lod.data.name = 'PR_Test_Body_LOD1'
bpy.data.collections['LOW'].objects.link(lod)
bpy.ops.wm.save_as_mainfile(filepath={str(root / 'master.blend')!r})
''')
        result = blender(setup)
        check(result.returncode == 0, "fixture builds")
        check(abs(json.loads((root / "calibration.json").read_text())["subject_height"] - 2) < 1e-5,
              "reference subject retains requested physical height")
        result = blender(scripts / "validate.py", "--blend", root / "master.blend",
                         "--budget-tris", "12", "--require-uv")
        check(result.returncode == 0, "valid cube passes mesh validation")
        result = blender(scripts / "review_render.py", "--blend", root / "master.blend",
                         "--out", root / "review", "--res", "64", "--views", "front", "--mode", "silhouette")
        check(result.returncode == 0 and (root / "review/silhouette_front.png").exists(), "review render succeeds")
        result = blender(scripts / "review_render.py", "--blend", root / "master.blend",
                         "--out", root / "bad-review", "--res", "64", "--collections", "MISSING")
        check(result.returncode != 0, "missing render collection cannot expand scope")
        for maps in ("typo", "normal"):
            result = blender(scripts / "bake_maps.py", "--blend", root / "master.blend",
                             "--out", root / "bad-bake", "--maps", maps, "--res", "16", "--samples", "1")
            check(result.returncode != 0, "invalid or unavailable bake map fails: " + maps)
        result = blender(scripts / "bake_maps.py", "--blend", root / "master.blend",
                         "--out", root / "textures", "--maps", "ao,basecolor,roughness,metallic",
                         "--res", "16", "--samples", "1", "--rebuild-material", "--save", root / "baked.blend")
        check(result.returncode == 0 and len(list((root / "textures").glob("*.png"))) == 4,
              "requested bake maps are produced")
        result = blender(scripts / "export_delivery.py", "--blend", root / "master.blend",
                         "--out", root / "exports", "--name", "PR_Test", "--no-animation")
        check(result.returncode == 0, "GLB export succeeds")
        contract = json.loads((root / "exports/animation-contract.json").read_text())
        check(contract["clips"] == [], "no-animation export declares no clips")
        result = blender(scripts / "roundtrip.py", "--file", root / "exports/PR_Test.glb",
                         "--out", root / "roundtrip", "--res", "64", "--expect-height", "2")
        check(result.returncode == 0, "valid GLB imports and renders")
        manifest_path = root / "exports/asset-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["lods"][0]["tris"] += 12
        manifest_path.write_text(json.dumps(manifest))
        result = blender(scripts / "roundtrip.py", "--file", root / "exports/PR_Test.glb",
                         "--out", root / "bad-roundtrip", "--res", "64", "--expect-height", "2")
        check(result.returncode != 0, "manifest triangle mismatch cannot pass roundtrip")
        result = blender(scripts / "export_delivery.py", "--blend", root / "baked.blend",
                         "--out", root / "baked-exports", "--name", "PR_Test", "--split-lods")
        check(result.returncode == 0, "baked animated GLB exports")
        contract = json.loads((root / "baked-exports/animation-contract.json").read_text())
        check(abs(contract["clips"][0]["length_s"] - 1.001) < 1e-4, "clip duration uses fps_base")
        glb = (root / "baked-exports/PR_Test.glb").read_bytes()
        json_size = struct.unpack_from("<I", glb, 12)[0]
        gltf = json.loads(glb[20:20 + json_size])
        check(any("occlusionTexture" in mat for mat in gltf["materials"]), "baked AO reaches exported glTF")
        for level in (0, 1):
            result = blender(scripts / "roundtrip.py", "--file", root / f"baked-exports/PR_Test_LOD{level}.glb",
                             "--out", root / f"roundtrip-lod{level}", "--res", "64", "--expect-height", "2")
            check(result.returncode == 0, f"split LOD{level} matches its manifest subset")
        setup.write_text(f'''
import runpy, numpy as np
gate = runpy.run_path({str(scripts / 'world_gate.py')!r})
ref = np.ones((1, 1), dtype=bool)
assert gate['band_widths'](ref, ref) == [0.0] * 10
try:
    gate['band_widths'](np.zeros((1, 1), dtype=bool), ref)
except ValueError:
    pass
else:
    raise AssertionError('empty world matte accepted')
rt = runpy.run_path({str(scripts / 'roundtrip.py')!r})
report = {{'meshes': [{{'name': 'Body', 'tris': 12}}], 'armatures': [], 'errors': []}}
manifest = {{'files': {{'all': 'test.glb'}}, 'lods': [{{'level': 0, 'objects': ['Body'], 'tris': 12}}],
            'skeleton': [{{'deform_bones': ['Root']}}]}}
rt['check_manifest'](report, manifest, 'test.glb')
assert any('missing deformation bones' in error for error in report['errors'])
''')
        check(blender(setup).returncode == 0, "thin/empty world mattes and missing skeleton are handled")
        setup.write_text(f'''
import bpy
bpy.ops.wm.open_mainfile(filepath={str(root / 'master.blend')!r})
mesh = bpy.data.objects['PR_Test_Body_LOD0'].data
while mesh.uv_layers: mesh.uv_layers.remove(mesh.uv_layers[0])
bpy.ops.wm.save_as_mainfile(filepath={str(root / 'no-uv.blend')!r})
''')
        check(blender(setup).returncode == 0, "missing-UV fixture builds")
        result = blender(scripts / "bake_maps.py", "--blend", root / "no-uv.blend",
                         "--out", root / "no-uv-textures", "--maps", "ao", "--res", "16",
                         "--save", root / "no-uv-baked.blend")
        check(result.returncode != 0 and not (root / "no-uv-baked.blend").exists(),
              "missing UVs cannot produce a successful baked file")
    if failures:
        raise SystemExit("Failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
