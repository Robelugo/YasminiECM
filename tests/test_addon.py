"""Headless tests. Run with:

    blender -b --factory-startup --python tests/test_addon.py

Exits with code 1 when something fails.
"""

import os
import struct
import sys
import tempfile
import traceback
import zlib

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import yasmini_ecm as ecm  # noqa: E402
from yasmini_ecm import health, icc, live, profiles, textures  # noqa: E402

V5 = bpy.app.version >= (5, 0, 0)
OUT = os.path.join(tempfile.gettempdir(), "ecm_tests")
os.makedirs(OUT, exist_ok=True)
failures = []
checks = 0


def expect(condition, message):
    global checks
    checks += 1
    if not condition:
        failures.append(message)
        print("  FAIL:", message)


def section(name):
    print(f"--- {name}")


class Ctx:
    """Minimal stand-in for bpy.context in health checks."""
    def __init__(self, scene):
        self.scene = scene


# --- Setup -----------------------------------------------------------------------------

ecm.register()
scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.render.resolution_x = scene.render.resolution_y = 16
scene.render.resolution_percentage = 100

section("registration")
icons = set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
for p in profiles.PROFILES:
    expect(p.icon in icons, f"profile {p.id} icon {p.icon} exists")
for idname in textures.PASS_THROUGH | textures.IMAGE_NODES | {'ShaderNodeNormalMap', 'ShaderNodeVectorDisplacement'}:
    if idname not in ('ShaderNodeSeparateRGB', 'ShaderNodeCombineRGB', 'ShaderNodeSeparateHSV', 'ShaderNodeCombineHSV'):
        expect(hasattr(bpy.types, idname), f"node type {idname} exists")
expect(hasattr(bpy.types, "ECM_PT_render"), "render panel registered")
expect(hasattr(scene, "ecm_settings"), "scene settings registered")


# --- Profiles --------------------------------------------------------------------------

section("profiles")
expected = {
    'WEB': ("sRGB", "AgX", "None", 'PNG', '8'),
    'PRODUCT': ("sRGB", "Khronos PBR Neutral", "None", 'PNG', '8'),
    'GRAPHICS': ("sRGB", "Standard", "None", 'PNG', '8'),
    'PRINT': ("sRGB", "AgX", "None", 'TIFF', '16'),
    'WIDE': ("Display P3", "AgX", "None", 'PNG', '16'),
    'CINEMATIC': ("sRGB", "AgX", "AgX - Medium High Contrast", 'PNG', '16'),
    'VIDEO': ("Rec.1886", "AgX", "None", 'FFMPEG', None),
    'HDR': ("Rec.2100-PQ", "AgX - HDR 1000 nits", "None", 'PNG', '16'),
    'VFX': ("sRGB", "ACES 2.0" if V5 else "AgX", "None", 'OPEN_EXR_MULTILAYER', '16'),
    'TEXTURE': ("sRGB", "Standard", "None", 'PNG', '8'),
    'DATA': ("sRGB", "Raw", "None", 'OPEN_EXR', '32'),
}
for profile in profiles.PROFILES:
    if not profile.available:
        expect(profile.id == 'HDR' and not V5, f"{profile.id} only unavailable when expected")
        continue
    scene.view_settings.exposure = 1.5
    result = profiles.apply(scene, profile)
    display, view, look, fmt, depth = expected[profile.id]
    ims = scene.render.image_settings
    got = (scene.display_settings.display_device, scene.view_settings.view_transform, scene.view_settings.look, ims.file_format)
    expect(got == (display, view, look, fmt), f"{profile.id}: got {got}, expected {(display, view, look, fmt)}")
    if depth:
        expect(ims.color_depth == depth, f"{profile.id}: depth {ims.color_depth} != {depth}")
    expect(ims.color_management == 'FOLLOW_SCENE', f"{profile.id}: no output override without a monitor")
    expect(scene.ecm_settings.applied_profile == profile.id, f"{profile.id}: recorded as applied")
    expect(profiles.drift(scene) == [], f"{profile.id}: no drift right after applying")
    if profile.neutral:
        expect(scene.view_settings.exposure == 0.0, f"{profile.id}: neutral profile resets exposure")
    else:
        expect(abs(scene.view_settings.exposure - 1.5) < 1e-6, f"{profile.id}: keeps the user's exposure")
    if profile.id == 'VIDEO':
        expect(scene.render.ffmpeg.codec == 'H264', "VIDEO: H.264 codec")
        expect(scene.render.ffmpeg.constant_rate_factor == 'HIGH', "VIDEO: high quality")
    if profile.id == 'VFX' and V5:
        expect(bpy.data.colorspace.working_space == "ACEScg", "VFX: ACEScg working space")
    expect(result.notes == [] or profile.id == 'VFX', f"{profile.id}: unexpected notes {result.notes}")
    print(f"  {profile.id:10s} -> {profiles.describe_current(scene)}  notes={result.notes}")

if V5:
    expect(bpy.data.colorspace.working_space == "ACEScg", "working space stays ACEScg for other profiles")

section("plan rows")
for profile in profiles.PROFILES:
    rows = profiles.plan_rows(profile, with_output=True, monitor="Display P3")
    expect(rows and rows[0][0] == "Delivers in", f"{profile.id}: plan rows {rows}")

section("drift")
profiles.apply(scene, profiles.get('WEB'))
scene.view_settings.view_transform = "Standard"
scene.view_settings.exposure = 0.7
changes = profiles.drift(scene)
expect([c.key for c in changes if not c.creative] == ["view"], f"technical drift: {[str(c) for c in changes]}")
expect([c.key for c in changes if c.creative] == ["exposure"], f"creative drift: {[str(c) for c in changes]}")
issues = health.check(Ctx(scene))
expect(any("changed since" in i.title for i in issues), "health reports drift")

section("monitor override")
profiles.apply(scene, profiles.get('WEB'), monitor="Display P3")
ims = scene.render.image_settings
expect(scene.display_settings.display_device == "Display P3", "viewport shows on the P3 monitor")
expect(ims.color_management == 'OVERRIDE', "output gets an override")
expect(ims.display_settings.display_device == "sRGB", "files are still sRGB")
expect(ims.view_settings.view_transform == scene.view_settings.view_transform == "AgX", "same view on both")
scene.view_settings.exposure = 2.0
expect(profiles.output_override_differs(scene), "override difference detected")
expect(any("won't match" in i.title for i in health.check(Ctx(scene))), "health reports override mismatch")
profiles.sync_output_override(scene)
expect(ims.view_settings.exposure == 2.0, "sync copies exposure")
profiles.apply(scene, profiles.get('PRODUCT'), monitor="Display P3")
expect(scene.view_settings.view_transform == ims.view_settings.view_transform, "PRODUCT on P3 monitor uses one view for both")
print("  PRODUCT on P3 monitor:", scene.view_settings.view_transform, profiles.describe_current(scene))
profiles.apply(scene, profiles.get('VFX'), monitor="Display P3")
expect(ims.color_management == 'FOLLOW_SCENE' and scene.display_settings.display_device == "Display P3",
       "linear EXR output: no override, viewport on monitor")

section("reset")
profiles.reset_to_defaults(scene)
expect((scene.display_settings.display_device, scene.view_settings.view_transform, scene.view_settings.look)
       == ("sRGB", "AgX", "None"), "reset to factory view")
expect(scene.ecm_settings.applied_profile == "", "reset clears applied profile")
if V5:
    expect(bpy.data.colorspace.working_space == "Linear Rec.709", "reset restores working space")


# --- Textures --------------------------------------------------------------------------

section("textures")


def file_image(name, float_buffer=False):
    path = os.path.join(OUT, name)
    img = bpy.data.images.new("tmp", 4, 4, float_buffer=float_buffer)
    img.filepath_raw = path
    img.file_format = 'OPEN_EXR' if name.endswith(".exr") else 'PNG'
    img.save()
    bpy.data.images.remove(img)
    return bpy.data.images.load(path)


mat = bpy.data.materials.new("Test")
if not mat.node_tree:
    mat.use_nodes = True
nt = mat.node_tree
nodes, links = nt.nodes, nt.links
bsdf = next(n for n in nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
out = next(n for n in nodes if n.bl_idname == 'ShaderNodeOutputMaterial')


def tex(image):
    node = nodes.new('ShaderNodeTexImage')
    node.image = image
    return node


albedo = file_image("wood_albedo.png")
normal = file_image("wood_normal.png")          # sRGB by default: wrong
rough = file_image("packed_orm.png")
mask = file_image("grunge.png")
height = file_image("pebbles.png")
hdri = file_image("sky.exr", float_buffer=True)
named_only = file_image("brick_Roughness_4k.png")
both = file_image("leaf.png")
grouped = file_image("group_normal.png")
mixed_alpha = file_image("decal.png")

links.new(tex(albedo).outputs["Color"], bsdf.inputs["Base Color"])
nmap = nodes.new('ShaderNodeNormalMap')
reroute = nodes.new('NodeReroute')
links.new(tex(normal).outputs["Color"], reroute.inputs[0])
links.new(reroute.outputs[0], nmap.inputs["Color"])
links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
sep = nodes.new('ShaderNodeSeparateColor')
links.new(tex(rough).outputs["Color"], sep.inputs["Color"])
links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
ramp = nodes.new('ShaderNodeValToRGB')
links.new(tex(mask).outputs["Color"], ramp.inputs["Fac"])
links.new(ramp.outputs["Color"], bsdf.inputs["Specular Tint"])
disp = nodes.new('ShaderNodeDisplacement')
links.new(tex(height).outputs["Color"], disp.inputs["Height"])
links.new(disp.outputs["Displacement"], out.inputs["Displacement"])
tex(named_only)
leaf = tex(both)
links.new(leaf.outputs["Color"], bsdf.inputs["Emission Color"])
bump = nodes.new('ShaderNodeBump')
links.new(leaf.outputs["Color"], bump.inputs["Height"])
decal = tex(mixed_alpha)
links.new(decal.outputs["Alpha"], bsdf.inputs["Alpha"])   # Alpha output says nothing about color

# An image inside a node group whose output feeds a Normal Map outside.
group = bpy.data.node_groups.new("NormalGroup", 'ShaderNodeTree')
if hasattr(group, "interface"):
    group.interface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
else:
    group.outputs.new('NodeSocketColor', "Color")
g_out = group.nodes.new('NodeGroupOutput')
g_tex = group.nodes.new('ShaderNodeTexImage')
g_tex.image = grouped
group.links.new(g_tex.outputs["Color"], g_out.inputs[0])
g_node = nodes.new('ShaderNodeGroup')
g_node.node_tree = group
nmap2 = nodes.new('ShaderNodeNormalMap')
links.new(g_node.outputs[0], nmap2.inputs["Color"])
links.new(nmap2.outputs["Normal"], bsdf.inputs["Coat Normal"])

world = bpy.data.worlds.new("W")
if not world.node_tree:
    world.use_nodes = True
background = next(n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeBackground')
env = world.node_tree.nodes.new('ShaderNodeTexEnvironment')
env.image = hdri
world.node_tree.links.new(env.outputs["Color"], background.inputs["Color"])
hdri.colorspace_settings.name = "sRGB"   # a classic mistake

verdicts = {v.image.name: v for v in textures.analyze()}
for name, v in verdicts.items():
    print(f"  {name:22s} kind={v.kind} sure={v.sure} target={v.target} :: {v.reason}")


def verdict(image, kind, target, sure=True):
    v = verdicts.get(image.name)
    expect(v is not None, f"{image.name} analyzed")
    if v:
        expect((v.kind, v.target, v.sure) == (kind, target, sure),
               f"{image.name}: got {(v.kind, v.target, v.sure)}, expected {(kind, target, sure)} ({v.reason})")


verdict(albedo, 'COLOR', None)
verdict(normal, 'DATA', "Non-Color")
verdict(rough, 'DATA', "Non-Color")
verdict(mask, 'DATA', "Non-Color", sure=False)
verdict(height, 'DATA', "Non-Color")
verdict(hdri, 'COLOR', "Linear Rec.709")
verdict(named_only, 'DATA', "Non-Color")
verdict(both, None, None, sure=False)
verdict(grouped, 'DATA', "Non-Color")
verdict(mixed_alpha, None, None, sure=False)

textures.invalidate()             # in Blender this happens on the depsgraph update
textures._cache["time"] = 0.0
issues = health.check(Ctx(scene))
expect(any("wrong color space" in i.title for i in issues), "health reports wrong textures")
fixes = textures.fix(textures.analyze())
fixed = {f.image_name: f.after for f in fixes}
expect(fixed == {"wood_normal.png": "Non-Color", "packed_orm.png": "Non-Color", "pebbles.png": "Non-Color",
                 "sky.exr": "Linear Rec.709", "brick_Roughness_4k.png": "Non-Color", "group_normal.png": "Non-Color"},
       f"fixed set: {fixed}")
expect(mask.colorspace_settings.name == "sRGB", "unsure mask left alone")
expect(normal.get(textures.AUTO_KEY) == "Non-Color", "fixed image is tracked")
expect(not any(v.target and v.sure for v in textures.analyze()), "nothing sure left to fix")

section("live mode")
live.remember_existing_images()
fresh = file_image("new_bark_normal.png")
fresh_tex = tex(fresh)
fixes = textures.live_fix(live._known)
expect([f.image_name for f in fixes] == ["new_bark_normal.png"], f"filename-based live fix: {[f.image_name for f in fixes]}")
fresh.colorspace_settings.name = "sRGB"          # the user overrides it by hand
textures.live_fix(live._known)
expect(fresh.colorspace_settings.name == "sRGB" and fresh.get(textures.LOCK_KEY), "manual choice is respected and locked")
plain = file_image("photo.png")
plain_tex = tex(plain)
textures.live_fix(live._known)
expect(plain.get(textures.AUTO_KEY) == "sRGB", "unconnected new image is tracked")
links.new(plain_tex.outputs["Color"], nmap.inputs["Strength"])
textures.live_fix(live._known)
expect(plain.colorspace_settings.name == "Non-Color", "tracked image follows its new connection")
old_mask = mask.colorspace_settings.name
textures.live_fix(live._known)
expect(mask.colorspace_settings.name == old_mask, "images from the opened file are left alone")

live._image_count = len(bpy.data.images)
bpy.context.view_layer.update()
live._on_depsgraph_update(scene, bpy.context.evaluated_depsgraph_get())
expect(isinstance(live._busy(), bool), "job check works")

section("operators")
bpy.ops.ecm.select_profile(profile_id='CINEMATIC')
expect(scene.ecm_settings.selected == 'CINEMATIC', "select operator")
normal.colorspace_settings.name = "sRGB"
if textures.AUTO_KEY in normal:
    del normal[textures.AUTO_KEY]
expect(bpy.ops.ecm.apply_profile() == {'FINISHED'}, "apply operator")
expect(scene.ecm_settings.applied_profile == 'CINEMATIC', "apply operator uses the selection")
expect(normal.colorspace_settings.name == "Non-Color", "apply operator fixes textures too")
bpy.ops.ecm.scan_textures()
wm = bpy.context.window_manager
expect(wm.ecm_scan_done and any(item.name == "grunge.png" for item in wm.ecm_scan), "scan lists the unsure mask")
for item in wm.ecm_scan:
    item.include = item.name == "grunge.png"
bpy.ops.ecm.fix_textures(mode='LISTED')
expect(mask.colorspace_settings.name == "Non-Color", "fix checked items")
bpy.ops.ecm.image_profile(image_name=albedo.name, kind='P3')
expect(albedo.colorspace_settings.name == "Display P3" and albedo.get(textures.LOCK_KEY), "assign image profile locks it")
bpy.ops.ecm.image_auto(image_name=albedo.name)
expect(albedo.colorspace_settings.name == "Display P3" and not albedo.get(textures.LOCK_KEY)
       and albedo.get(textures.AUTO_KEY) == "Display P3", "auto-detect keeps a valid color profile, unlocks and tracks it")
bpy.ops.ecm.image_profile(image_name=albedo.name, kind='DATA')
bpy.ops.ecm.image_auto(image_name=albedo.name)
expect(albedo.colorspace_settings.name == "sRGB", "auto-detect fixes a color texture set to Non-Color")
bpy.ops.ecm.set_output(file_format='PNG', color_depth='16')
expect(scene.render.image_settings.color_depth == '16', "set output")
kinds = [k[0] for k in ecm.operators.image_kinds()]
expect(('ADOBE' in kinds) == (bpy.app.version >= (5, 2, 0)), "Adobe RGB offered only where it exists")
if 'ADOBE' in kinds:
    expect(bpy.ops.ecm.image_profile(image_name=albedo.name, kind='ADOBE') == {'FINISHED'}, "Adobe RGB assignable")

section("health")
profiles.apply(scene, profiles.get('WIDE'))
scene.render.image_settings.color_depth = '8'
titles = [i.title for i in health.check(Ctx(scene))]
print("  ", titles)
expect(any("banding" in t for t in titles), "8-bit wide gamut warning")
expect(any(("tagged as" in t) for t in titles) != V5, "legacy tagging note only on Blender 4")
scene.view_settings.view_transform = "False Color"
expect(any("False Color" in i.title for i in health.check(Ctx(scene))), "false color warning")


# --- ICC embedding ---------------------------------------------------------------------

section("icc")


def png_icc(path):
    data = open(path, "rb").read()
    pos = 8
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"iCCP":
            body = data[pos + 8:pos + 8 + length]
            return zlib.decompress(body.split(b"\0", 1)[1][1:])
        pos += 12 + length
    return None


def has_icc(path):
    data = open(path, "rb").read()
    if data.startswith(b"\x89PNG"):
        return png_icc(path) is not None
    return b"ICC_PROFILE" in data or b"desc" in data[-2000:]


profiles.apply(scene, profiles.get('WIDE'))
for fmt, ext in (('PNG', ".png"), ('JPEG', ".jpg"), ('TIFF', ".tif")):
    scene.render.image_settings.file_format = fmt
    path = os.path.join(OUT, "render_p3" + ext)
    if os.path.exists(path):
        os.remove(path)
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    expect(os.path.exists(path), f"{fmt} render written")
    if not V5:
        expect(has_icc(path), f"{fmt} render tagged with a profile on Blender 4")
    else:
        icc.tag_file(path, "Display P3")   # our tagging must also cope with Blender 5's files
        expect(has_icc(path), f"{fmt} retagged on Blender 5")
    # The tagged file must still load.
    loaded = bpy.data.images.load(path, check_existing=False)
    expect(tuple(loaded.size) == (16, 16), f"{fmt} still loads after tagging: {tuple(loaded.size)}")
    bpy.data.images.remove(loaded)
p3 = png_icc(os.path.join(OUT, "render_p3.png"))
expect(p3 == icc.build_profile("Display P3"), "PNG carries our Display P3 profile")

scene.render.image_settings.file_format = 'PNG'
scene.frame_start, scene.frame_end = 1, 2
scene.render.filepath = os.path.join(OUT, "anim_")
for f in ("anim_0001.png", "anim_0002.png"):
    if os.path.exists(os.path.join(OUT, f)):
        os.remove(os.path.join(OUT, f))
bpy.ops.render.render(animation=True)
for f in ("anim_0001.png", "anim_0002.png"):
    if not V5:
        expect(png_icc(os.path.join(OUT, f)) is not None, f"animation frame {f} tagged")


# --- Wrap up ---------------------------------------------------------------------------

section("unregister")
try:
    ecm.unregister()
    expect(not hasattr(bpy.types.Scene, "ecm_settings"), "scene settings removed")
except Exception:
    traceback.print_exc()
    expect(False, "unregister raised")

print(f"\nBlender {bpy.app.version_string}: {checks - len(failures)}/{checks} checks passed")
if failures:
    print("RESULT: FAIL")
    sys.exit(1)
print("RESULT: PASS")
