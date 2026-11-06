"""Plain-language checks for common color management mistakes, each with a one-click fix."""

import os
from dataclasses import dataclass, field

import bpy

from . import icc, profiles, textures
from .props import get_prefs

_ORDER = {'ERROR': 0, 'WARNING': 1, 'INFO': 2}


@dataclass
class Issue:
    level: str             # 'ERROR', 'WARNING' or 'INFO'
    title: str
    detail: str = ""
    fix_op: str = ""
    fix_label: str = ""
    fix_props: dict = field(default_factory=dict)


def _reapply(profile):
    return dict(fix_op="ecm.apply_profile", fix_label=f"Re-apply {profile.name}", fix_props={"profile_id": profile.id})


def check(context):
    scene = context.scene
    settings = scene.ecm_settings
    profile = profiles.get(settings.applied_profile)
    vs = scene.view_settings
    ims = scene.render.image_settings
    fmt = ims.file_format
    override = ims.color_management == 'OVERRIDE' and fmt not in profiles.LINEAR_FORMATS
    out_display = (ims.display_settings if override else scene.display_settings).display_device
    out_view = (ims.view_settings if override else vs).view_transform
    issues = []

    colorspace = getattr(bpy.data, "colorspace", None)
    if colorspace is not None and colorspace.is_missing_opencolorio_config:
        issues.append(Issue(
            'ERROR', "Blender can't find its color configuration",
            "Colors can't be managed correctly. Reinstall Blender, or check the OCIO "
            "environment variable if your studio sets one."))
    if os.environ.get("OCIO"):
        issues.append(Issue(
            'INFO', "Using a studio OCIO configuration",
            "Some profiles may fall back to the closest option your configuration offers."))

    if profile is None:
        issues.append(Issue('INFO', "No color profile assigned yet",
                            "Pick what this project is for and press Apply Profile."))
    else:
        changes = profiles.drift(scene)
        technical = [c for c in changes if not c.creative]
        creative = [c for c in changes if c.creative]
        if technical:
            issues.append(Issue(
                'WARNING', f"Settings changed since {profile.name} was applied",
                "; ".join(str(c) for c in technical), **_reapply(profile)))
        if creative:
            issues.append(Issue(
                'INFO', f"Creative tweaks on top of {profile.name}",
                "; ".join(str(c) for c in creative) + ". That's fine if you meant it."))

    is_data_profile = profile is not None and profile.id == 'DATA'
    if out_view == "False Color":
        issues.append(Issue(
            'WARNING', "False Color view is on",
            "This is a heat-map for checking exposure. Renders will be saved in false colors.",
            **(_reapply(profile) if profile else {})))
    elif out_view == "Raw" and not is_data_profile and fmt not in profiles.LINEAR_FORMATS:
        issues.append(Issue(
            'WARNING', "Raw view on an image output",
            "Raw skips all color conversion, so saved images look dark and flat. "
            "Use it only for technical data.",
            **(_reapply(profile) if profile else {})))
    elif "Log" in out_view:
        issues.append(Issue(
            'WARNING', f"{out_view} view is on",
            "Log views are meant for color grading later; renders look flat and gray until graded.",
            **(_reapply(profile) if profile else {})))

    if override and profiles.output_override_differs(scene):
        issues.append(Issue(
            'WARNING', "Saved files won't match the viewport",
            "The output color override uses different view settings than the scene.",
            fix_op="ecm.sync_output", fix_label="Match Output to Viewport"))

    _texture_issues(issues)
    _output_issues(issues, scene, fmt, out_display, out_view)

    if not is_data_profile and scene.sequencer_colorspace_settings.name in textures.DATA_SPACES:
        issues.append(Issue(
            'WARNING', "Video Sequencer works in Non-Color",
            "Videos and images in the sequencer will show wrong colors.",
            **(_reapply(profile) if profile else {})))

    issues.sort(key=lambda i: _ORDER[i.level])
    return issues


def _texture_issues(issues):
    verdicts = textures.cached()
    sure = [v for v in verdicts if v.target and v.sure and not v.locked]
    unsure = [v for v in verdicts if v.target and (not v.sure or v.locked)]
    both = [v for v in verdicts if v.kind is None and "and as data" in v.reason]
    if sure:
        names = ", ".join(v.image.name for v in sure[:3]) + (" …" if len(sure) > 3 else "")
        issues.append(Issue(
            'WARNING', f"{len(sure)} texture{'s' if len(sure) != 1 else ''} with the wrong color space",
            names, fix_op="ecm.fix_textures", fix_label="Fix Textures", fix_props={"mode": 'SURE'}))
    if unsure:
        issues.append(Issue(
            'INFO', f"{len(unsure)} texture{'s' if len(unsure) != 1 else ''} worth a look",
            "Masks, baked images or ones you set by hand. Review them under Textures.",
            fix_op="ecm.scan_textures", fix_label="Review Textures"))
    for verdict in both[:2]:
        issues.append(Issue('INFO', f"{verdict.image.name} is used two ways", f"It {verdict.reason}."))


def _output_issues(issues, scene, fmt, display, view):
    ims = scene.render.image_settings
    wide = display in ("Display P3", "Rec.2020")
    hdr = display.startswith("Rec.2100")

    if fmt in ('PNG', 'TIFF') and ims.color_depth == '8' and (wide or hdr):
        issues.append(Issue(
            'WARNING', "8-bit output can show banding",
            f"{profiles.DISPLAY_LABELS.get(display, display)} spreads 8 bits over a wider range; "
            "16-bit keeps gradients smooth.",
            fix_op="ecm.set_output", fix_label="Use 16-bit", fix_props={"color_depth": '16'}))
    if hdr and fmt in ('JPEG', 'WEBP', 'BMP', 'TARGA', 'TARGA_RAW'):
        issues.append(Issue(
            'WARNING', f"{profiles.FORMAT_LABELS.get(fmt, fmt)} can't hold HDR",
            "HDR needs at least 10 bits per channel.",
            fix_op="ecm.set_output", fix_label="Use PNG 16-bit",
            fix_props={"file_format": 'PNG', "color_depth": '16'}))
    if fmt in profiles.LINEAR_FORMATS and view not in ("Standard", "Raw"):
        issues.append(Issue(
            'INFO', f"EXR files are saved without the {view} look",
            "EXR stores linear scene light; the view transform is not baked in. Apply it in "
            "your compositor, or save PNG or TIFF for finished images."))

    taggable = fmt in ('PNG', 'JPEG', 'TIFF') and display in icc.DISPLAYS and display != "sRGB"
    if bpy.app.version < (5, 0, 0) and taggable and view not in ("Raw", "False Color"):
        label = profiles.DISPLAY_LABELS.get(display, display)
        if get_prefs().embed_profiles:
            issues.append(Issue(
                'INFO', f"Renders will be tagged as {label}",
                "Blender 4 doesn't embed color profiles, so Yasmini ECM adds one to each "
                "saved render. Photoshop, browsers and phones will show the right colors."))
        else:
            issues.append(Issue(
                'WARNING', "Saved images won't carry a color profile",
                f"Other apps will assume sRGB and show your {label} colors wrong.",
                fix_op="ecm.enable_embedding", fix_label="Embed Profiles in Renders"))


def problem_count(context):
    return sum(1 for i in check(context) if i.level != 'INFO')
