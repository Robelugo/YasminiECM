"""Opens Blender with its interface, shows every panel of the add-on and saves screenshots.

    blender --factory-startup --python tests/ui_smoke.py -- <screenshot folder>

Drawing errors show up as Python tracebacks in the console. Blender closes by itself.
"""

import os
import sys
import tempfile

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import yasmini_ecm as ecm  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SHOTS = argv[0] if argv else os.path.join(tempfile.gettempdir(), "ecm_ui")
os.makedirs(SHOTS, exist_ok=True)

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.ui_scale = 0.8
# Sidebar tabs can't be switched from Python, so show our panels in the tabs that are open by default.
for cls in (ecm.ui.ECM_PT_view3d, ecm.ui.ECM_PT_view3d_textures, ecm.ui.ECM_PT_view3d_health, ecm.ui.ECM_PT_view3d_advanced):
    cls.bl_category = "Item"
ecm.ui.ECM_PT_image_editor.bl_category = "Tool"
ecm.register()

# A material with a classic mistake: a normal map left on sRGB.
path = os.path.join(SHOTS, "rock_normal.png")
img = bpy.data.images.new("tmp", 8, 8)
img.filepath_raw = path
img.file_format = 'PNG'
img.save()
bpy.data.images.remove(img)
normal = bpy.data.images.load(path)
obj = bpy.data.objects.get("Cube")
mat = bpy.data.materials.new("Rock")
if not mat.node_tree:
    mat.use_nodes = True
obj.data.materials[0] = mat
nodes, links = mat.node_tree.nodes, mat.node_tree.links
bsdf = next(n for n in nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
tex = nodes.new('ShaderNodeTexImage')
tex.image = normal
tex.location = (-600, -300)
nmap = nodes.new('ShaderNodeNormalMap')
nmap.location = (-300, -300)
links.new(tex.outputs["Color"], nmap.inputs["Color"])
links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
nodes.active = tex
bpy.context.scene.ecm_settings.selected = 'WIDE'


def log(*args):
    print(*args)
    sys.stdout.flush()


def main_area():
    window = bpy.context.window_manager.windows[0]
    return window, max(window.screen.areas, key=lambda a: a.width * a.height)


def find_area(kind):
    window = bpy.context.window_manager.windows[0]
    return window, next(a for a in window.screen.areas if a.type == kind)


def show_sidebar(area, category):
    area.spaces.active.show_region_ui = True
    for region in area.regions:
        if region.type == 'UI':
            try:
                region.active_panel_category = category
            except (AttributeError, TypeError) as error:
                log("category:", error)


def shoot(name):
    window, _ = main_area()
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=os.path.join(SHOTS, name))
    log("screenshot:", name)


def then(fn, delay=0.8):
    bpy.app.timers.register(fn, first_interval=delay)


def step_sidebar():
    _, props = find_area('PROPERTIES')
    props.spaces.active.context = 'RENDER'
    _, view = main_area()
    show_sidebar(view, "Color")
    then(step_render)


def step_render():
    shoot("1_sidebar.png")
    window, props = find_area('PROPERTIES')
    region = next(r for r in props.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=props, region=region):
        for _ in range(14):
            bpy.ops.view2d.scroll_down(deltax=0, deltay=60)
    then(step_apply)


def step_apply():
    shoot("2_render_tab.png")
    bpy.ops.ecm.apply_profile(profile_id='WIDE')
    then(step_nodes)


def step_nodes():
    shoot("3_after_apply.png")
    _, area = main_area()
    area.type = 'NODE_EDITOR'
    area.spaces.active.tree_type = 'ShaderNodeTree'
    normal.colorspace_settings.name = "sRGB"   # break it again so the panel has something to say
    bpy.data.materials["Rock"].node_tree.nodes.active = tex
    show_sidebar(area, "Node")
    then(step_image)


def step_image():
    shoot("4_node_editor.png")
    _, area = main_area()
    area.type = 'IMAGE_EDITOR'
    area.spaces.active.image = normal
    show_sidebar(area, "Image")
    then(step_popover)


def step_popover():
    shoot("5_image_editor.png")
    window, area = main_area()
    area.type = 'VIEW_3D'
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.wm.call_panel(name="ECM_PT_popover", keep_open=True)
    then(step_done)


def step_done():
    shoot("6_popover.png")
    log("UI SMOKE DONE")
    sys.stderr.flush()
    os._exit(0)


then(step_sidebar, 1.5)
