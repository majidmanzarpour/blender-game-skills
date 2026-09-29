# Blender Game Skills

[Agent Skills](https://agentskills.io) for [Claude Code](https://code.claude.com/docs/en/skills) that
build game assets in Blender. Each skill is a self-contained folder of instructions, reference
notes and scripts that the agent follows and runs for you.

By [Majid Manzarpour](https://x.com/majidmanzarpour).

## Skills

| Skill | What it does |
| --- | --- |
| [`blender-image-to-3d`](skills/blender-image-to-3d/SKILL.md) | Turns reference images (concept art, model sheets, photos, turnarounds, sketches) into game-ready 3D assets: characters, creatures, architecture, vehicles, props, weapons and environment pieces. Gated phases from brief to rigged, exported GLB or FBX, each passed on rendered and measured evidence. |

The repository is set up to hold several skills. Each one lives in its own folder under
[`skills/`](skills/) and installs on its own, so more Blender skills can be added here over time.

## Installation

### With `npx skills` (recommended)

The [`skills` CLI](https://github.com/vercel-labs/skills) from Vercel Labs installs skills from a
GitHub repository into the skill folders of Claude Code and other agents. `npx` runs it without a
global install; it needs Node.js 22.20 or newer.

```bash
# One skill, for all your projects (~/.claude/skills/blender-image-to-3d):
npx skills add majidmanzarpour/blender-game-skills --skill blender-image-to-3d -g -a claude-code

# One skill, for the current project only (./.claude/skills/blender-image-to-3d):
npx skills add majidmanzarpour/blender-game-skills --skill blender-image-to-3d -a claude-code

# Every skill in this repository:
npx skills add majidmanzarpour/blender-game-skills --skill '*' -g -a claude-code

# See what the repository contains without installing anything:
npx skills add majidmanzarpour/blender-game-skills --list
```

Without `--skill` and `-a`, the CLI asks which skills and agents you want. It copies only each
skill's folder (`skills/<name>/`) into the agent's skills directory; the rest of the repository
stays out. The full URL, `https://github.com/majidmanzarpour/blender-game-skills`, works in place
of `majidmanzarpour/blender-game-skills`, and `-y` skips confirmation prompts.

Update or uninstall (add `-g` for a global install):

```bash
npx skills update blender-image-to-3d
npx skills remove blender-image-to-3d
```

The CLI sends anonymous usage telemetry. Set `DISABLE_TELEMETRY=1` to turn it off.

### Manual install

Copy a skill's folder into a Claude Code skills directory and keep its folder name.

```bash
git clone https://github.com/majidmanzarpour/blender-game-skills.git
cd blender-game-skills

# For all your projects:
mkdir -p ~/.claude/skills
cp -R skills/blender-image-to-3d ~/.claude/skills/

# Or for one project:
mkdir -p /path/to/project/.claude/skills
cp -R skills/blender-image-to-3d /path/to/project/.claude/skills/
```

Claude Code picks up a new skill in an existing skills directory during the session. If
`~/.claude/skills` or the project's `.claude/skills` did not exist when the session started,
restart Claude Code.

## blender-image-to-3d

The agent works through gated phases: brief, calibration, blockout, forms, topology, UVs and
baking, rig, secondary motion, animation, export, and acceptance. Each gate is passed on evidence:
renders compared against the reference from the same camera, and measured numbers. Looking at the
viewport and deciding it seems fine is not accepted.

### Example prompts

Attach the images to the message (drag them into the terminal, or paste with Ctrl+V, Alt+V on
Windows) or give their paths. Any number of images works in one prompt: separate views, a single
model sheet that holds several views, extra concepts and detail callouts.

```text
Here are front and side views of a knight (ref/knight_front.png, ref/knight_side.png). Build him
in Blender as a game-ready character for a third-person action game in Unity, about 1.85 m tall,
rigged, with LOD0 to LOD2.
```

```text
This model sheet has front, side and back views plus face and belt callouts in one image, and the
second image is a painted three-quarter concept of the same character. Build her in Blender for
Unreal: about 1.70 m, rigged, hair as strands in the master and cards for the game mesh, GLB with
LOD0 and LOD1. Measure proportions from the sheet only.
```

```text
Recreate this dune buggy from the concept art as a drivable vehicle asset for Godot: separate
wheels with pivots at the axles, doors that open, a collision hull, GLB export.
```

```text
Here's a photo of a stone chapel. Make a modular kit that snaps together on a 2 m grid in
three.js: wall, window wall, doorway, corner, pillar, arch, roof segment, and the door as a
separate hinged object. Tileable materials with a trim sheet.
```

```text
Turn these three sketches of a sword (side, edge-on, pommel detail) into a game-ready prop:
about 1.1 m long, a socket at the grip, under 3k triangles, textured, FBX for Unity.
```

```text
Use these front, side and back renders of a stylised fox creature and build it as a quadruped
for a mobile game: rigged for walk, run and idle, one 1024 texture set, glTF. Don't ask me
questions unless you really have to.
```

Claude picks the skill up on its own when you supply a reference image and ask for a 3D model or
game asset, even without mentioning Blender. To make sure it is used, type
`/blender-image-to-3d` followed by your request.

### Why gates and measurements

Most wasted work in reference-driven modelling is detail added on top of wrong proportions, and
the eye forgives drift that numbers catch. The skill makes the agent measure instead of judging
by eye:

- Every constant in the build scripts is a measured value with a comment naming the reference
  view it came from, or it is marked `# inferred`.
- Each phase ends with renders from a camera matched to the reference and at gameplay pixel size,
  and a compare sheet with numbers: silhouette IoU and a ten-band width profile. For orthographic
  sheets, a world-registered gate also catches a model that is the wrong height or off its axis,
  which bounding-box alignment would hide.
- Mismatches are written as measurements ("head 12 percent too tall", "wheelbase 0.3 m short"),
  fixed in the build script, and re-rendered until they are inside the phase tolerance. Nothing
  gets detail until its silhouette passes.
- Exports are re-imported into an empty Blender scene and checked against the manifest, because
  the modelling viewport is not what the engine receives.
- Anything the references do not show is listed as inferred in the brief, the final report and
  the export manifest.

### Phases

| Phase | What happens | Evidence at the gate |
| --- | --- | --- |
| 0. Brief | Reads every image and writes `asset-brief.md`: views and camera estimates, real scale and its evidence, distinguishing proportions, parts, materials, what must be inferred, target engine and budget | The brief; consequential missing choices clarified |
| 1. Calibration | Master `.blend` with reference images as planes at real scale, a ruler, grid cell, door clearance, collision capsule and the game camera | Everything after this is measured in the same file and scale |
| 2. Blockout | Primary masses, separate appendages, joint centres and pivots | Clay and silhouette compare sheets: IoU 0.85 or more, band widths within 0.05, proportions within 5 percent |
| 3. Forms | Secondary and tertiary forms, layered parts in their real overlap order | IoU 0.90 or more, band widths within 0.03, proportions within 2 percent, clay turntable |
| 4. Topology | LOW delivery meshes over the approved forms, additional LODs when required | `validate.py` within the triangle budget, wire render, no silhouette loss |
| 5. UVs, baking, materials | UVs, original materials, baked portable maps | Material turntable under a moving light, greyscale at gameplay size, clean checker render |
| 6. Rig | Deformation skeleton, controls, pivots, sockets and weights (skipped for static props) | Extreme-pose sheet; no unweighted or over-influenced vertices |
| 7. Secondary motion | Cloth, chains, tails, wings or cables, only where the brief asks for them | Posed renders without penetration |
| 8. Animation | Named clips with loop flags and event markers, only when requested | Clips rendered at the game camera, loop seams and event timings checked |
| 9. Export and round trip | GLB or FBX with `asset-manifest.json` and `animation-contract.json`, then a reimport into an empty scene | Mesh names, triangle totals and required deformation bones checked against the manifest; height within 1 percent. Clip timing and texture roles inspected separately |
| 10. Acceptance | Acceptance checklist and a `review/final/` handover folder | Report of what matches, the deviations that remain, what was inferred and the budgets used |

### What to expect

- The agent saves the references into `<asset>/ref/`, studies every image and writes
  `<asset>/asset-brief.md` first. It asks only for what the images cannot tell: target engine and
  format, real size when nothing in the image gives scale, budget tier, whether to rig and
  animate, and the game camera. It offers defaults (1 unit = 1 m, Z up, GLB, the standard budget
  tier, a three-quarter camera at 50 mm, 128 px subject height) and uses them if you tell it to
  proceed. Only the phases and deliverables needed for your request are required.
- At the end of each phase it shows a compare sheet (reference, render, overlay, gameplay-size
  strip), the measured deviations and what was inferred. It continues within the authorized
  workflow unless you requested phase approval or a consequential choice is unresolved, keeping sheets in
  `<asset>/review/`.
- Claude Code asks for permission before it runs Blender or Python commands, unless you have
  allowed them.
- A Blender session connected through an MCP server is optional. The agent can use it to inspect
  the scene and reload the master file in your open Blender after each rebuild; the build itself
  still runs through the scripts.

A typical asset folder:

```text
CH_Knight/
|-- asset-brief.md
|-- ref/                      # the references, including crops of multi-view sheets
|-- CH_Knight_master.blend    # source of truth: REF, HIGH, LOW, RIG_DEF, COLLISION, SOCKETS ...
|-- CH_Knight_baked.blend     # regenerated from the finished master before export
|-- build/                    # 02_blockout.py, 03_forms.py, ...: re-runnable phase scripts
|-- review/                   # compare sheets, gate JSON, turntables, final/
|-- textures/                 # baked maps
`-- exports/                  # GLB or FBX, asset-manifest.json, animation-contract.json
```

### Requirements

- **Blender 4.2 or newer.** Tested on Blender 5.2. Blender 5.x changed several APIs;
  [`references/blender-5-notes.md`](skills/blender-image-to-3d/references/blender-5-notes.md)
  lists the ones these scripts deal with. The skill looks for Blender in `$BLENDER_BIN`, then
  `blender` on `PATH`, then `/Applications/Blender.app/Contents/MacOS/Blender`; set
  `BLENDER_BIN` if yours is somewhere else.
- **Python 3 with Pillow** for `compose_review.py`, the one script that runs outside Blender:
  `python3 -m pip install pillow`. The other scripts run in Blender's bundled Python and need
  nothing else.
- **Claude Code.** The skill uses the open Agent Skills format, so other agents that support
  skills and can run shell commands may work, but only Claude Code has been tested.

GPU notes:

- No GPU is required. Clay, silhouette and wire review renders, `world_gate.py` and
  `roundtrip.py` use the Workbench engine, which needs no GPU on macOS or Windows.
- On a headless Linux machine without a GPU, pass `--engine cycles` to `review_render.py` (it
  also falls back to Cycles by itself when Workbench fails).
- Checker and material review renders use Cycles on the CPU. Baking uses Cycles on the CPU by
  default; `bake_maps.py --device GPU` uses the device enabled in Blender's Preferences > System >
  Cycles Render Devices. On Apple Silicon, enable only the Metal device there.

### Scripts

The scripts live in `skills/blender-image-to-3d/scripts/`. The agent runs them for you; you only
need them to rerun a gate yourself or to work without an agent.

| Script | Runs in | Purpose |
| --- | --- | --- |
| `init_master.py` | Blender | Creates the master `.blend`: units, collections, reference planes at real scale (one per view plus any number of extra images), calibration objects and the game camera. |
| `review_render.py` | Blender | Clay, silhouette, wire, checker or material renders from fixed views and a reference-matched camera, at gameplay size, as turntables or in posed frames. |
| `compose_review.py` | Python 3 + Pillow | Compare sheet (reference, render, overlay, gameplay strip) with silhouette IoU and a ten-band width profile. |
| `world_gate.py` | Blender | World-registered silhouette IoU against a cleaned orthographic reference matte, so scale and placement errors count. |
| `validate.py` | Blender | Checks topology, transforms, UVs, weights, armature, sockets, colliders, naming and the triangle budget; exits 1 on any FAIL. |
| `bake_maps.py` | Blender (Cycles) | Bakes normal and AO from HIGH to LOW, plus base colour, roughness and metallic; can rebuild export-ready materials. |
| `export_delivery.py` | Blender | Exports GLB, glTF or FBX with `asset-manifest.json` and `animation-contract.json`. |
| `roundtrip.py` | Blender | Imports the export into an empty scene, reports what arrived and renders a check frame. |

`assets/build_template.py` is the template for the per-phase build scripts. Each copy opens the
master file, deletes the objects it owns, rebuilds them and saves, so any phase can be rerun.

Every script prints its options with `--help`. For the Blender scripts, put it after the `--`
separator: `blender --background --python-exit-code 1 --python scripts/validate.py -- --help`.

Use `--python-exit-code 1` before `--python` when running Blender helpers so exceptions produce
a failing process exit code. An empty delivery, missing render scope, missing UVs or unavailable
requested bake map is an error. Do not infer gate success from a file left by an earlier run.

Example: the review and gate loop. The values come from the skill's example, a 1.85 m knight; in
a real build they come from the brief.

```bash
SKILL=~/.claude/skills/blender-image-to-3d   # where the skill is installed
BLENDER=${BLENDER_BIN:-blender}

# 1. Clay renders of the LOW collection from the fixed views and the reference-matched camera
"$BLENDER" --background --python-exit-code 1 --python "$SKILL/scripts/review_render.py" -- \
  --blend CH_Knight/CH_Knight_master.blend --out CH_Knight/review/02_blockout \
  --collections LOW --views front,side,threequarter --ref-cam 35 12 50 --mode clay --gameplay-px 128

# 2. Compare sheet and numbers against the front reference (--measure also writes compare_front.json)
python3 "$SKILL/scripts/compose_review.py" --ref ref/front.png \
  --render CH_Knight/review/02_blockout/clay_front.png \
  --out CH_Knight/review/02_blockout/compare_front.png --measure --gameplay-px 128

# 3. Orthographic sheets only: world-registered IoU against a cleaned matte of the front view
"$BLENDER" --background --python-exit-code 1 --python "$SKILL/scripts/world_gate.py" -- \
  --blend CH_Knight/CH_Knight_master.blend --out CH_Knight/review/02_blockout \
  --matte ref/front_clean.png --az 0 --m-per-px 0.0029167 --axis-col 109 --ground-row 610 \
  --collections LOW --name front
```

Read the sheet and the numbers, fix the constants in the phase script, rebuild, and repeat until
every mismatch is inside the phase tolerance.

### Limits

- The skill is instructions plus scripts. The agent still does the modelling, as bpy code, so
  results depend on the model running the skill, the quality of the references and your review
  at each gate.
- Dimensions are measured from orthographic views. Perspective concept art and photos are used
  for silhouette and surface detail, not proportions.
- A single image cannot show the back or the sides. Those parts are inferred and labelled as such.

## Repository layout

```text
.
|-- README.md
|-- LICENSE
|-- .gitignore
`-- skills/
    `-- blender-image-to-3d/         # one folder per skill; this folder is what gets installed
        |-- SKILL.md                 # instructions, phases and gates
        |-- scripts/                 # the scripts above
        |-- references/              # categories, delivery and acceptance, rigging and
        |                            # animation, Blender 5 notes
        |-- assets/build_template.py # template for the per-phase build scripts
        `-- evals/evals.json         # test prompts for evaluating the skill
```

### Adding a skill

- Create `skills/<skill-name>/SKILL.md` with `name` and `description` frontmatter. The folder name
  must match `name`: lowercase letters, digits and hyphens, at most 64 characters. Keep
  `description` within 1024 characters.
- Keep the skill self-contained: paths in `SKILL.md` are relative to its folder, and everything it
  needs (scripts, references, assets) lives inside it.
- Add a row to the Skills table above, and check that `npx skills add . --list` finds every skill.

## Contributing

Issues and pull requests are welcome. For larger changes, open an issue first.

- Scripts run headless (`blender --background --python-exit-code 1 --python <script> -- <args>`), accept `--help`, and
  look up nodes by type, not by display name, because display names are translated in
  non-English Blender UIs.
- Test on Blender 4.2 and a current 5.x release where you can, and record API differences in the
  skill's `references/blender-5-notes.md`.
- Run `python3 -m py_compile` on every script. `compose_review.py` must keep working with only
  Pillow installed.
- Keep each `SKILL.md` short (the Agent Skills specification recommends under 500 lines) and move
  detail into `references/`.
- For changes to a skill's behaviour, run the prompts in its `evals/evals.json` before and after,
  for example with Anthropic's skill-creator skill.

For reproducible script checks, run the smoke test with Python + Pillow and an installed Blender:

```bash
python3 skills/blender-image-to-3d/scripts/test_pipeline.py --blender /path/to/blender
```

It creates temporary fixtures for calibration, validation, baking, review rendering and GLB
export/reimport, including negative checks that must fail. Pass `--out /new/directory` to retain
artifacts and logs. The eval prompts require reference images not bundled in this repository;
the smoke test does not replace artistic review or testing in the target engine.

To try a working copy in Claude Code, link it into your skills folder after removing any installed
copy: `ln -s "$PWD/skills/blender-image-to-3d" ~/.claude/skills/blender-image-to-3d`.

## License

[MIT](LICENSE) © 2026 Majid Manzarpour.
