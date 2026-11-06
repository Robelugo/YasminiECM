"""Works out what each image texture is used for, and which color space it needs.

Color images (base color, emission, world backgrounds) need a color space such as sRGB.
Everything else (normal, roughness, metallic, height, masks) is data and must be Non-Color,
or Blender "corrects" the numbers and the material renders wrong.

The usage is read from the node graphs: each image's Color output is followed through
reroutes, node groups and converter nodes to the sockets it finally feeds.
"""

import os
import re
import time
from dataclasses import dataclass

import bpy

COLOR, DATA = 'COLOR', 'DATA'

# Custom properties stored on images.
AUTO_KEY = "ecm_auto"      # color space Yasmini ECM last set; lets live mode keep managing it
LOCK_KEY = "ecm_locked"    # the user chose a color space by hand; never change it automatically

DATA_SPACES = ("Non-Color", "Raw", "Generic Data", "Utility - Raw", "data")
SRGB_SPACES = ("sRGB", "sRGB - Texture", "Utility - sRGB - Texture", "srgb_tx")
LINEAR_SPACES = ("Linear Rec.709", "Linear", "Linear sRGB", "Utility - Linear - sRGB", "scene_linear")
_TARGET_CANDIDATES = {"Non-Color": DATA_SPACES, "sRGB": SRGB_SPACES, "Linear Rec.709": LINEAR_SPACES}

FLOAT_EXTENSIONS = {".exr", ".hdr", ".pfm"}
FLOAT_FORMATS = {'OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'HDR'}

IMAGE_NODES = {'ShaderNodeTexImage', 'ShaderNodeTexEnvironment'}
GROUP_NODES = {'ShaderNodeGroup'}

# Nodes that only reshape a value on its way somewhere else. What an image means is decided
# where its path ends: Separate Color -> Roughness is data, Hue/Saturation -> Base Color is color.
PASS_THROUGH = {
    'ShaderNodeMix', 'ShaderNodeMixRGB', 'ShaderNodeHueSaturation', 'ShaderNodeGamma',
    'ShaderNodeBrightContrast', 'ShaderNodeInvert', 'ShaderNodeRGBCurve', 'ShaderNodeFloatCurve',
    'ShaderNodeVectorCurve', 'ShaderNodeSeparateColor', 'ShaderNodeSeparateRGB',
    'ShaderNodeSeparateHSV', 'ShaderNodeSeparateXYZ', 'ShaderNodeCombineColor',
    'ShaderNodeCombineRGB', 'ShaderNodeCombineHSV', 'ShaderNodeCombineXYZ', 'ShaderNodeRGBToBW',
    'ShaderNodeValToRGB', 'ShaderNodeMath', 'ShaderNodeVectorMath', 'ShaderNodeMapRange',
    'ShaderNodeClamp',
}

# File name words. Strong ones are reliable enough to act on for unconnected images.
_DATA_STRONG = {
    "normal", "normals", "nrm", "nor", "nrml", "nml", "normalgl", "normaldx", "rough",
    "roughness", "rgh", "gloss", "glossiness", "metal", "metallic", "metalness", "ao",
    "occlusion", "ambientocclusion", "height", "heightmap", "disp", "displacement", "bump",
    "bumpmap", "orm", "arm", "rma", "mra", "cavity", "curvature", "vdm",
}
_DATA_WEAK = {"mask", "opacity", "alpha", "thickness", "transmission", "specular", "spec", "depth", "flow"}
_COLOR_STRONG = {"albedo", "diffuse", "diff", "basecolor", "emission", "emissive", "emit"}
_COLOR_WEAK = {"color", "colour", "col", "base", "tint"}


@dataclass
class Use:
    kind: str        # COLOR or DATA
    sure: bool       # False when the image only drives a mask or a factor
    where: str


@dataclass
class Verdict:
    image: object
    kind: str        # COLOR, DATA, or None when unknown or used both ways
    sure: bool
    reason: str
    target: str      # color space to switch to, or None when the current one is fine

    @property
    def current(self):
        return self.image.colorspace_settings.name

    @property
    def locked(self):
        return bool(self.image.get(LOCK_KEY))


# --- Color space helpers ---------------------------------------------------------------

def is_linear_space(name):
    lowered = name.lower()
    return "linear" in lowered or "acescg" in lowered or "aces2065" in lowered or lowered == "scene_linear"


def is_float_image(image):
    ext = os.path.splitext(image.filepath_raw or image.filepath or "")[1].lower()
    if ext in FLOAT_EXTENSIONS or image.file_format in FLOAT_FORMATS:
        return True
    return image.source == 'GENERATED' and image.use_generated_float


def holds_own_pixels(image):
    """Generated, baked or painted images: their pixels were made in Blender, so changing the
    color space reinterprets them instead of just describing a file."""
    return image.source == 'GENERATED' or image.is_dirty


def set_colorspace(image, target):
    """Set one of the canonical targets (or any color space name); returns what was set."""
    settings = image.colorspace_settings
    for name in _TARGET_CANDIDATES.get(target, (target,)):
        try:
            settings.name = name
        except TypeError:
            continue
        return settings.name
    if target == "Non-Color":
        try:
            settings.is_data = True
            return settings.name
        except (AttributeError, TypeError):
            pass
    return None


def target_for(image, kind):
    cs = image.colorspace_settings
    if kind == DATA:
        return None if cs.is_data else "Non-Color"
    if is_float_image(image):
        return None if not cs.is_data and is_linear_space(cs.name) else "Linear Rec.709"
    return None if not cs.is_data and not is_linear_space(cs.name) else "sRGB"


def friendly_target(target):
    return {"Non-Color": "Data (Non-Color)", "sRGB": "Color (sRGB)", "Linear Rec.709": "Linear (HDR)"}.get(target, target)


# --- File names ------------------------------------------------------------------------

def _words(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    words = [w.lower() for w in re.split(r"[^A-Za-z]+", stem) if w]
    return set(words) | {a + b for a, b in zip(words, words[1:])}


def guess_from_name(image):
    """(kind, sure) from texture naming conventions like wood_Normal_4k.png."""
    words = _words(bpy.path.basename(image.filepath) or image.name)
    data_strong, data_weak = words & _DATA_STRONG, words & _DATA_WEAK
    color_strong, color_weak = words & _COLOR_STRONG, words & _COLOR_WEAK
    if color_strong and not (data_strong or data_weak):
        return COLOR, True
    if data_strong and not color_strong:
        return DATA, True
    if data_weak and not (color_strong or color_weak):
        return DATA, False
    if color_weak and not (data_strong or data_weak):
        return COLOR, False
    return None, False


# --- Node graph walk -------------------------------------------------------------------

def _label(node):
    return node.label or node.bl_label


class _Walker:
    def __init__(self):
        self.images = {}   # pointer -> image
        self.uses = {}     # pointer -> [Use]
        self._links = {}   # tree pointer -> {output socket pointer: [links]}

    def _links_from(self, socket):
        tree = socket.id_data
        table = self._links.get(tree.as_pointer())
        if table is None:
            table = {}
            for link in tree.links:
                table.setdefault(link.from_socket.as_pointer(), []).append(link)
            self._links[tree.as_pointer()] = table
        return table.get(socket.as_pointer(), ())

    def _add_image(self, image):
        key = image.as_pointer()
        self.images[key] = image
        return self.uses.setdefault(key, [])

    def scan_tree(self, tree, stack=()):
        if tree is None or len(stack) > 16:
            return
        for node in tree.nodes:
            if node.bl_idname in IMAGE_NODES and node.image:
                uses = self._add_image(node.image)
                if not node.mute and node.outputs:
                    output = node.outputs.get("Color") or node.outputs[0]
                    self._follow(output, None, stack, set(), uses)
            elif node.bl_idname in GROUP_NODES and node.node_tree:
                if all(n.node_tree != node.node_tree for n in stack):
                    self.scan_tree(node.node_tree, stack + (node,))

    def scan_modifiers(self):
        for obj in bpy.data.objects:
            for mod in getattr(obj, "modifiers", ()):
                texture = getattr(mod, "texture", None) if mod.type == 'DISPLACE' else None
                image = getattr(texture, "image", None)
                if image:
                    self._add_image(image).append(Use(DATA, True, f"Displace modifier on {obj.name}"))

    def _follow(self, socket, via, stack, seen, uses, depth=0):
        if depth > 200:
            return
        for link in self._links_from(socket):
            if not link.is_valid or getattr(link, "is_muted", False):
                continue
            self._enter(link.to_node, link.to_socket, via, stack, seen, uses, depth + 1)

    def _enter(self, node, socket, via, stack, seen, uses, depth):
        key = (socket.as_pointer(), via is None, tuple(n.as_pointer() for n in stack))
        if key in seen:
            return
        seen.add(key)
        idname = node.bl_idname

        if node.mute:
            for link in node.internal_links:
                if link.from_socket == socket:
                    self._follow(link.to_socket, via, stack, seen, uses, depth)
        elif idname == 'NodeReroute':
            self._follow(node.outputs[0], via, stack, seen, uses, depth)
        elif idname in GROUP_NODES:
            if node.node_tree is None:
                return
            for inner in node.node_tree.nodes:
                if inner.bl_idname == 'NodeGroupInput':
                    for output in inner.outputs:
                        if output.identifier == socket.identifier:
                            self._follow(output, via, stack + (node,), seen, uses, depth)
        elif idname == 'NodeGroupOutput':
            if stack and node.is_active_output:
                parent = stack[-1]
                for output in parent.outputs:
                    if output.identifier == socket.identifier:
                        self._follow(output, via, stack[:-1], seen, uses, depth)
        elif idname == 'ShaderNodeNormalMap' and socket.identifier == 'Color':
            uses.append(Use(DATA, True, "Normal Map"))
        elif idname == 'ShaderNodeVectorDisplacement' and socket.identifier == 'Vector':
            uses.append(Use(DATA, True, "Vector Displacement"))
        elif idname in PASS_THROUGH:
            if via is None and socket.type != 'RGBA':
                via = _label(node)
            for output in node.outputs:
                if output.enabled:
                    self._follow(output, via, stack, seen, uses, depth)
        else:
            where = f"{_label(node)} › {socket.name}"
            if socket.type in ('RGBA', 'SHADER'):
                uses.append(Use(COLOR, True, where) if via is None else Use(DATA, False, f"mask (through {via})"))
            elif idname == 'ShaderNodeMixShader':
                uses.append(Use(DATA, False, f"mask ({where})"))
            else:
                uses.append(Use(DATA, True, where))


def _editable(image):
    return image.library is None and image.type not in {'RENDER_RESULT', 'COMPOSITING'}


def _judge(image, uses):
    color = [u for u in uses if u.kind == COLOR]
    data_sure = [u for u in uses if u.kind == DATA and u.sure]
    data_mask = [u for u in uses if u.kind == DATA and not u.sure]
    # Reasons complete the sentence "<image name> ...".
    if color and data_sure:
        reason = f"is used as color ({color[0].where}) and as data ({data_sure[0].where})"
        return Verdict(image, None, False, reason, None)
    if color:
        kind, sure, reason = COLOR, True, f"feeds {color[0].where}"
    elif data_sure:
        kind, sure, reason = DATA, True, f"feeds {data_sure[0].where}"
    elif data_mask:
        kind, sure, reason = DATA, False, f"is used as a {data_mask[0].where}"
    else:
        kind, sure = guess_from_name(image)
        if kind is None:
            return Verdict(image, None, False, "isn't connected to anything yet", None)
        reason = "has a file name that looks like " + ("a data map" if kind == DATA else "a color texture")
    if sure and holds_own_pixels(image):
        sure = False
        reason += ", but it holds pixels made in Blender, which a new color space would alter"
    return Verdict(image, kind, sure, reason, target_for(image, kind))


def analyze():
    """A Verdict for every editable image used as a texture in this file."""
    walker = _Walker()
    for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        for datablock in collection:
            walker.scan_tree(datablock.node_tree)
    walker.scan_modifiers()
    return [
        _judge(image, walker.uses[key])
        for key, image in walker.images.items()
        if _editable(image)
    ]


def verdict_for(image):
    for verdict in cached():
        if verdict.image == image:
            return verdict
    return None


# --- Cache for the UI, which redraws often ---------------------------------------------

_cache = {"verdicts": [], "dirty": True, "time": 0.0}


def invalidate():
    _cache["dirty"] = True


def cached():
    """Analysis results, recomputed at most a few times per second while things change."""
    now = time.monotonic()
    if _cache["dirty"] and now - _cache["time"] > 0.25:
        try:
            _cache["verdicts"] = analyze()
        except Exception as error:  # never break drawing the UI
            print(f"Yasmini ECM: texture analysis failed: {error}")
            _cache["verdicts"] = []
        _cache["dirty"] = False
        _cache["time"] = now
    try:
        for verdict in _cache["verdicts"]:
            verdict.image.name  # raises once the image is gone (undo, file load)
    except ReferenceError:
        _cache["verdicts"] = analyze()
    return _cache["verdicts"]


# --- Fixing ----------------------------------------------------------------------------

@dataclass
class Fix:
    image_name: str
    before: str
    after: str
    reason: str


def fix(verdicts, *, include_unsure=False, include_locked=False):
    fixes = []
    for verdict in verdicts:
        if not verdict.target or (not verdict.sure and not include_unsure):
            continue
        if verdict.locked and not include_locked:
            continue
        image = verdict.image
        before = image.colorspace_settings.name
        after = set_colorspace(image, verdict.target)
        if after and after != before:
            image[AUTO_KEY] = after
            if LOCK_KEY in image:
                del image[LOCK_KEY]
            fixes.append(Fix(image.name, before, after, verdict.reason))
    invalidate()
    return fixes


def live_fix(known):
    """Fix textures as they are added or connected. Images that were already in the file when
    it was opened (`known` session ids) and images the user set by hand are left alone."""
    fixes = []
    for verdict in analyze():
        image = verdict.image
        if image.get(LOCK_KEY):
            continue
        auto = image.get(AUTO_KEY)
        current = image.colorspace_settings.name
        if auto is None:
            if image.session_uid in known:
                continue
        elif auto != current:
            image[LOCK_KEY] = True   # the user changed it since; respect that
            del image[AUTO_KEY]
            continue
        if verdict.target and verdict.sure:
            after = set_colorspace(image, verdict.target)
            if after and after != current:
                image[AUTO_KEY] = after
                fixes.append(Fix(image.name, current, after, verdict.reason))
                continue
        if auto is None:
            image[AUTO_KEY] = current   # start tracking it, in case it gets connected later
    if fixes:
        invalidate()
    return fixes
