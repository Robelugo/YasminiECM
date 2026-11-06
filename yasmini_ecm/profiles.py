"""The project profiles users pick from, and the code that applies one to a scene.

Every setting is a list of candidates tried in order. A profile still applies cleanly on an
older Blender, or with a studio OCIO config that lacks some views; whatever had to fall
back is reported to the user.
"""

import json
from dataclasses import dataclass

import bpy

V5 = (5, 0, 0)
LINEAR_FORMATS = {'OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'HDR'}


@dataclass(frozen=True)
class Output:
    label: str
    file_format: str
    color_mode: str
    color_depth: str
    media_type: str = 'IMAGE'
    codec: str = ''


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    icon: str
    group: str
    summary: str
    best_for: str
    display: tuple
    view: tuple
    look: tuple = ("None",)
    working_space: tuple = ()
    output: Output = None
    neutral: bool = False   # also reset exposure, gamma, curves and white balance
    hdr: bool = False
    min_version: tuple = (4, 2, 0)

    @property
    def available(self):
        return bpy.app.version >= self.min_version


def _png(depth, alpha=True, label=""):
    mode = 'RGBA' if alpha else 'RGB'
    label = label or f"PNG · {depth}-bit" + (" · transparency" if alpha else "")
    return Output(label, 'PNG', mode, depth)


PROFILES = (
    Profile(
        'WEB', "Web & Social", 'WORLD', "Images",
        "Natural, photographic look in sRGB, the color space every browser, phone and "
        "social network expects.",
        "websites, social media, portfolios, client previews",
        display=("sRGB",), view=("AgX", "Filmic", "Standard"),
        output=_png('8'),
    ),
    Profile(
        'PRODUCT', "Product & Brand", 'MATSHADERBALL', "Images",
        "Keeps base colors true to the real product or brand swatch, while bright "
        "highlights still roll off gently (Khronos PBR Neutral).",
        "product shots, e-commerce, packaging, 3D and AR viewers",
        display=("sRGB",), view=("Khronos PBR Neutral", "Standard"),
        output=_png('8'),
    ),
    Profile(
        'GRAPHICS', "Exact Colors", 'FONT_DATA', "Images",
        "The color you pick is the color you get, matching hex codes 1:1. There is no "
        "tone mapping, so very bright lights clip to white.",
        "motion graphics, logos, UI, toon and flat shading, 2D animation",
        display=("sRGB",), view=("Standard",), neutral=True,
        output=_png('8'),
    ),
    Profile(
        'PRINT', "Print & Photoshop", 'FILE_IMAGE', "Images",
        "16-bit TIFF in sRGB, with plenty of precision for retouching in Photoshop and "
        "converting to your printer's CMYK profile.",
        "posters, print ads, photo prints, retouching",
        display=("sRGB",), view=("AgX", "Filmic", "Standard"),
        output=Output("TIFF · 16-bit · transparency", 'TIFF', 'RGBA', '16', codec='LZW'),
    ),
    Profile(
        'WIDE', "Wide Gamut (P3)", 'WINDOW', "Images",
        "Richer, more saturated colors for Apple devices and modern wide-gamut screens. "
        "Saved files are tagged so they display correctly everywhere.",
        "iPhone, iPad and Mac viewing, modern phones, premium web",
        display=("Display P3",), view=("AgX", "Standard"),
        output=_png('16'),
    ),
    Profile(
        'CINEMATIC', "Cinematic", 'CAMERA_DATA', "Film & Video",
        "Film-like contrast with smooth highlight roll-off. 16-bit frames keep "
        "gradients clean for editing and grading.",
        "animation, short films, moody and artistic renders",
        display=("sRGB",), view=("AgX", "Filmic"),
        look=("AgX - Medium High Contrast", "Medium High Contrast"),
        output=_png('16', alpha=False, label="PNG frames · 16-bit"),
    ),
    Profile(
        'VIDEO', "Video & TV", 'FILE_MOVIE', "Film & Video",
        "Broadcast-standard Rec.709 with BT.1886 (gamma 2.4) encoding, which is what TVs, "
        "DaVinci Resolve and Premiere Pro expect.",
        "TV and client video, editing in Resolve or Premiere",
        display=("Rec.1886",), view=("AgX", "Standard"),
        output=Output("H.264 MP4 video", 'FFMPEG', 'RGB', '8', media_type='VIDEO', codec='H264'),
    ),
    Profile(
        'HDR', "HDR Video", 'LIGHT_SUN', "Film & Video",
        "True high dynamic range for HDR TVs, phones and YouTube HDR. Preview it on an HDR "
        "display for the best results.",
        "HDR video and stills, YouTube HDR",
        display=("Rec.2100-PQ",), view=("AgX - HDR 1000 nits", "ACES 2.0 - HDR 1000 nits"),
        hdr=True, min_version=V5,
        output=_png('16', alpha=False, label="PNG frames · 16-bit HDR"),
    ),
    Profile(
        'VFX', "VFX & Compositing", 'NODE_COMPOSITING', "Production",
        "Scene-linear multilayer EXR for Nuke, Fusion or Resolve. On Blender 5 and newer "
        "it also switches the file to the industry-standard ACES (ACEScg) workflow.",
        "compositing, VFX plates, studio pipelines",
        display=("sRGB",), view=(("ACES 2.0", V5), "AgX", "Filmic"),
        working_space=(("ACEScg", V5),),
        output=Output("Multilayer EXR · half float", 'OPEN_EXR_MULTILAYER', 'RGBA', '16',
                      media_type='MULTI_LAYER_IMAGE', codec='ZIP'),
    ),
    Profile(
        'TEXTURE', "Texture & Baking", 'TPAINT_HLT', "Production",
        "No tone mapping, so the colors you paint and bake are exactly what ends up in "
        "texture files and game engines.",
        "texture painting, baking, game assets, glTF and FBX export",
        display=("sRGB",), view=("Standard",), neutral=True,
        output=_png('8'),
    ),
    Profile(
        'DATA', "Raw Data", 'IMAGE_ZDEPTH', "Production",
        "Shows and saves the exact numbers the scene produces, with no transform at all. "
        "Images look flat and dark on purpose.",
        "depth and normal passes, masks, datasets, machine learning",
        display=("sRGB",), view=("Raw",), neutral=True,
        output=Output("OpenEXR · full float", 'OPEN_EXR', 'RGBA', '32', codec='ZIP'),
    ),
)

GROUPS = ("Images", "Film & Video", "Production")
_BY_ID = {p.id: p for p in PROFILES}

DISPLAY_LABELS = {
    "sRGB": "sRGB",
    "Display P3": "Display P3",
    "Rec.1886": "Rec.709 (BT.1886)",
    "Rec.2020": "Rec.2020",
    "Rec.2100-PQ": "Rec.2100 PQ (HDR)",
    "Rec.2100-HLG": "Rec.2100 HLG (HDR)",
}

FORMAT_LABELS = {
    'PNG': "PNG", 'JPEG': "JPEG", 'TIFF': "TIFF", 'OPEN_EXR': "EXR",
    'OPEN_EXR_MULTILAYER': "Multilayer EXR", 'FFMPEG': "Video", 'WEBP': "WebP",
    'AVIF': "AVIF", 'HDR': "Radiance HDR", 'BMP': "BMP", 'TARGA': "Targa",
}


def get(profile_id):
    return _BY_ID.get(profile_id)


def in_group(group):
    return [p for p in PROFILES if p.group == group]


def enum_items():
    return [(p.id, p.name, p.summary, p.icon, i) for i, p in enumerate(PROFILES)]


def usable(candidates):
    """Candidate values this Blender version is expected to offer, in order."""
    values = []
    for candidate in candidates:
        value, min_version = (candidate, None) if isinstance(candidate, str) else candidate
        if min_version is None or bpy.app.version >= min_version:
            values.append(value)
    return tuple(values)


def look_label(look):
    return look.split(" - ", 1)[1] if " - " in look else look


def output_label(image_settings):
    fmt = image_settings.file_format
    label = FORMAT_LABELS.get(fmt, fmt.title())
    if fmt == 'FFMPEG':
        return label
    depth = image_settings.color_depth
    if fmt in LINEAR_FORMATS:
        return f"{label} · {'half' if depth == '16' else 'full'} float"
    return f"{label} · {depth or 8}-bit"


# --- Applying --------------------------------------------------------------------------

def _set(target, attr, candidates):
    """Assign the first candidate Blender accepts and return it, or None."""
    for value in candidates:
        try:
            setattr(target, attr, value)
        except (TypeError, ValueError, AttributeError):
            continue
        if getattr(target, attr) == value:
            return value
    return None


def _set_all(targets, attr, candidates):
    """Like _set, but the value must work on every target (viewport and file output)."""
    for value in candidates:
        if all(_set(t, attr, (value,)) == value for t in targets):
            return value
    results = [_set(t, attr, candidates) for t in targets]
    return results[0]


def _apply_output(scene, output, notes):
    ims = scene.render.image_settings
    if hasattr(ims, "media_type"):
        _set(ims, "media_type", (output.media_type,))
    if _set(ims, "file_format", (output.file_format,)) is None:
        notes.append(f"{output.label} isn't available, so the output format was left as is")
        return
    _set(ims, "color_mode", (output.color_mode, 'RGB'))
    _set(ims, "color_depth", (output.color_depth,))
    if output.file_format in LINEAR_FORMATS and output.codec:
        _set(ims, "exr_codec", (output.codec, 'ZIP'))
    elif output.file_format == 'TIFF' and output.codec:
        _set(ims, "tiff_codec", (output.codec,))
    elif output.file_format == 'FFMPEG':
        ffmpeg = scene.render.ffmpeg
        _set(ffmpeg, "format", ('MPEG4',))
        _set(ffmpeg, "codec", (output.codec,))
        _set(ffmpeg, "constant_rate_factor", ('HIGH',))
        _set(ffmpeg, "ffmpeg_preset", ('GOOD',))


def _set_working_space(candidates, notes):
    colorspace = getattr(bpy.data, "colorspace", None)
    if colorspace is None or not candidates:
        return None
    for value in candidates:
        if colorspace.working_space == value:
            return value
        try:
            bpy.ops.wm.set_working_color_space(working_space=value, convert_colors=True)
        except (TypeError, RuntimeError):
            continue
        if colorspace.working_space == value:
            notes.append(f"Working space is now {value}; existing colors were converted, so they look the same")
            return value
    return None


@dataclass
class Result:
    profile: Profile
    display: str
    view: str
    notes: list


def apply(scene, profile, *, with_output=True, monitor=None):
    """Apply a profile to a scene. `monitor` is the user's own display, when it differs
    from what the profile delivers (the output then gets its own color override)."""
    notes = []
    ds, vs = scene.display_settings, scene.view_settings
    ims = scene.render.image_settings

    if with_output and profile.output:
        _apply_output(scene, profile.output, notes)
    linear_output = ims.file_format in LINEAR_FORMATS

    wanted = usable(profile.display)
    delivery = _set(ds, "display_device", wanted)
    if delivery is None:
        delivery = ds.display_device
        notes.append(f"The {DISPLAY_LABELS.get(wanted[0], wanted[0])} display isn't available, so {delivery} is used")

    # Viewport on the user's monitor, files in the delivery display.
    override = bool(monitor) and monitor != delivery and not linear_output and not profile.hdr
    if override and _set(ds, "display_device", (monitor,)) is None:
        override = False
    targets = [vs]
    if override:
        ims.color_management = 'OVERRIDE'
        _set(ims.display_settings, "display_device", (delivery,))
        targets.append(ims.view_settings)
    else:
        if monitor and linear_output:
            _set(ds, "display_device", (monitor,))
        ims.color_management = 'FOLLOW_SCENE'

    views = usable(profile.view)
    view = _set_all(targets, "view_transform", views)
    if view is None:
        view = vs.view_transform
    if views and view != views[0]:
        notes.append(f"{views[0]} isn't available here, so {view} is used instead")

    looks = usable(profile.look) + ("None",)
    look = _set_all(targets, "look", looks)
    if profile.look[0] != "None" and look == "None":
        notes.append(f"The {look_label(profile.look[0])} look isn't available with {view}")

    if profile.neutral:
        for t in targets:
            t.exposure = 0.0
            t.gamma = 1.0
            t.use_curve_mapping = False
            if hasattr(t, "use_white_balance"):
                t.use_white_balance = False
    elif override:
        sync_output_override(scene)

    _set(scene.sequencer_colorspace_settings, "name", ("sRGB",))
    _set_working_space(usable(profile.working_space), notes)

    settings = scene.ecm_settings
    settings.applied_profile = profile.id
    settings.applied_state = json.dumps(read_state(scene))
    return Result(profile, delivery, view, notes)


def sync_output_override(scene):
    """Copy the scene's view settings to the output override so files match the viewport.
    Returns True when something changed."""
    ims = scene.render.image_settings
    if ims.color_management != 'OVERRIDE':
        return False
    src, dst = scene.view_settings, ims.view_settings
    changed = False
    for attr in ("view_transform", "look", "exposure", "gamma", "use_curve_mapping"):
        value = getattr(src, attr)
        if getattr(dst, attr) != value:
            try:
                setattr(dst, attr, value)
            except (TypeError, ValueError):
                continue
            changed = True
    return changed


def output_override_differs(scene):
    ims = scene.render.image_settings
    if ims.color_management != 'OVERRIDE':
        return False
    src, dst = scene.view_settings, ims.view_settings
    return any(
        getattr(src, a) != getattr(dst, a)
        for a in ("view_transform", "look", "exposure", "gamma", "use_curve_mapping")
    )


def reset_to_defaults(scene):
    """Blender's factory color management."""
    ds, vs = scene.display_settings, scene.view_settings
    _set(ds, "display_device", ("sRGB",))
    _set(vs, "view_transform", ("AgX", "Filmic", "Standard"))
    _set(vs, "look", ("None",))
    vs.exposure = 0.0
    vs.gamma = 1.0
    vs.use_curve_mapping = False
    if hasattr(vs, "use_white_balance"):
        vs.use_white_balance = False
    _set(scene.sequencer_colorspace_settings, "name", ("sRGB",))
    scene.render.image_settings.color_management = 'FOLLOW_SCENE'
    notes = []
    _set_working_space(("Linear Rec.709",), notes)
    scene.ecm_settings.applied_profile = ""
    scene.ecm_settings.applied_state = ""
    return notes


# --- Describing ------------------------------------------------------------------------

def plan_rows(profile, *, with_output, monitor=None):
    """What applying a profile will set, as (label, value) rows for the UI."""
    display = usable(profile.display)[0]
    view = usable(profile.view)[0]
    rows = [("Delivers in", DISPLAY_LABELS.get(display, display)), ("View", view)]
    look = usable(profile.look)[0] if usable(profile.look) else "None"
    if look != "None":
        rows.append(("Look", look_label(look)))
    if profile.neutral:
        rows.append(("Exposure", "0 (neutral)"))
    working = usable(profile.working_space)
    if working and hasattr(bpy.data, "colorspace"):
        rows.append(("Working space", working[0]))
    if with_output and profile.output:
        rows.append(("Output", profile.output.label))
    if monitor and monitor != display and not profile.hdr:
        rows.append(("Preview on", DISPLAY_LABELS.get(monitor, monitor) + " monitor"))
    return rows


def describe_current(scene):
    ims = scene.render.image_settings
    ds, vs = scene.display_settings, scene.view_settings
    display = ds.display_device
    if ims.color_management == 'OVERRIDE' and ims.file_format not in LINEAR_FORMATS:
        display = ims.display_settings.display_device
    parts = [DISPLAY_LABELS.get(display, display), vs.view_transform]
    if vs.look != "None":
        parts.append(look_label(vs.look))
    parts.append(output_label(ims))
    return " · ".join(parts)


# --- Drift -----------------------------------------------------------------------------

_LABELS = {
    "display": "Display", "view": "View", "look": "Look", "exposure": "Exposure",
    "gamma": "Gamma", "curves": "Curves", "white_balance": "White balance",
    "sequencer": "Sequencer", "out_display": "Output display", "out_view": "Output view",
}
_CREATIVE = {"look", "exposure", "gamma", "curves", "white_balance"}


def read_state(scene):
    vs, ds = scene.view_settings, scene.display_settings
    ims = scene.render.image_settings
    state = {
        "display": ds.display_device,
        "view": vs.view_transform,
        "look": vs.look,
        "exposure": round(vs.exposure, 3),
        "gamma": round(vs.gamma, 3),
        "curves": vs.use_curve_mapping,
        "white_balance": bool(getattr(vs, "use_white_balance", False)),
        "sequencer": scene.sequencer_colorspace_settings.name,
        "out_display": None,
        "out_view": None,
    }
    if ims.color_management == 'OVERRIDE':
        state["out_display"] = ims.display_settings.display_device
        state["out_view"] = ims.view_settings.view_transform
    return state


@dataclass
class Change:
    key: str
    label: str
    before: object
    after: object
    creative: bool

    def __str__(self):
        def fmt(v):
            if isinstance(v, bool):
                return "on" if v else "off"
            if v is None:
                return "none"
            if isinstance(v, str):
                return look_label(v) if self.key == "look" else v
            return f"{v:g}"
        return f"{self.label}: {fmt(self.before)} → {fmt(self.after)}"


def drift(scene):
    """Settings changed since the profile was applied."""
    try:
        applied = json.loads(scene.ecm_settings.applied_state or "{}")
    except ValueError:
        return []
    if not applied:
        return []
    now = read_state(scene)
    return [
        Change(key, _LABELS[key], applied.get(key), value, key in _CREATIVE)
        for key, value in now.items()
        if key in applied and applied[key] != value
    ]
