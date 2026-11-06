"""Operators behind every button of Yasmini ECM."""

import os
import struct

import bpy
from bpy.props import CollectionProperty, EnumProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement

from . import icc, profiles, textures
from .props import get_prefs, monitor_display


def redraw_all(context=None):
    wm = (context or bpy.context).window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


class ECM_OT_select_profile(Operator):
    bl_idname = "ecm.select_profile"
    bl_label = "Choose Profile"
    bl_options = {'INTERNAL'}

    profile_id: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        profile = profiles.get(properties.profile_id)
        if profile is None:
            return ""
        text = f"{profile.summary}\nBest for {profile.best_for}"
        if not profile.available:
            text += f"\nNeeds Blender {profile.min_version[0]}.{profile.min_version[1]} or newer"
        return text

    def execute(self, context):
        if profiles.get(self.profile_id) is None:
            return {'CANCELLED'}
        context.scene.ecm_settings.selected = self.profile_id
        return {'FINISHED'}


class ECM_OT_apply_profile(Operator):
    bl_idname = "ecm.apply_profile"
    bl_label = "Apply Profile"
    bl_options = {'REGISTER', 'UNDO'}

    profile_id: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        profile = profiles.get(properties.profile_id) or profiles.get(context.scene.ecm_settings.selected)
        return f"Set up this project for {profile.name}: display, view transform, output and textures"

    def execute(self, context):
        scene = context.scene
        settings = scene.ecm_settings
        profile = profiles.get(self.profile_id or settings.selected)
        if profile is None:
            return {'CANCELLED'}
        if not profile.available:
            self.report({'ERROR'}, f"{profile.name} needs Blender {profile.min_version[0]}.{profile.min_version[1]} or newer")
            return {'CANCELLED'}

        result = profiles.apply(scene, profile, with_output=settings.apply_output, monitor=monitor_display())
        settings.selected = profile.id
        fixed = textures.fix(textures.analyze()) if settings.apply_textures else []

        message = f"{profile.name} applied"
        if fixed:
            message += f", {_plural(len(fixed), 'texture')} fixed"
        if result.notes:
            self.report({'WARNING'}, message + ". " + ". ".join(result.notes))
        else:
            self.report({'INFO'}, message)
        redraw_all(context)
        return {'FINISHED'}


class ECM_OT_reset_defaults(Operator):
    bl_idname = "ecm.reset_defaults"
    bl_label = "Reset to Blender Defaults"
    bl_description = "Go back to Blender's factory color management (sRGB display, AgX view, no look)"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        profiles.reset_to_defaults(context.scene)
        self.report({'INFO'}, "Color management reset to Blender's defaults")
        redraw_all(context)
        return {'FINISHED'}


class ECM_OT_scan_textures(Operator):
    bl_idname = "ecm.scan_textures"
    bl_label = "Review Textures"
    bl_description = "List every texture whose color space should change, and why"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        wm.ecm_scan.clear()
        textures.invalidate()
        verdicts = [v for v in textures.analyze() if v.target]
        for verdict in sorted(verdicts, key=lambda v: (not v.sure, v.image.name.lower())):
            item = wm.ecm_scan.add()
            item.name = verdict.image.name
            item.current = verdict.current
            item.target = verdict.target
            item.reason = verdict.reason
            item.sure = verdict.sure
            item.locked = verdict.locked
            item.include = verdict.sure and not verdict.locked
        wm.ecm_scan_index = 0
        wm.ecm_scan_done = True
        self.report({'INFO'}, f"{_plural(len(verdicts), 'texture')} to review" if verdicts else "Every texture looks right")
        redraw_all(context)
        return {'FINISHED'}


class ECM_OT_fix_textures(Operator):
    bl_idname = "ecm.fix_textures"
    bl_label = "Fix Textures"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(items=[
        ('SURE', "Automatic", "Fix every texture whose use is clear from the node setup"),
        ('LISTED', "Checked", "Fix the textures checked in the review list"),
    ], default='SURE', options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if properties.mode == 'LISTED':
            return "Change the color space of the checked textures"
        return "Give every texture the right color space, based on how it is connected in the node setup"

    def execute(self, context):
        wm = context.window_manager
        if self.mode == 'LISTED':
            chosen = {item.name for item in wm.ecm_scan if item.include}
            verdicts = [v for v in textures.analyze() if v.image.name in chosen]
            fixes = textures.fix(verdicts, include_unsure=True, include_locked=True)
        else:
            fixes = textures.fix(textures.analyze())
        fixed = {f.image_name for f in fixes}
        for i in reversed(range(len(wm.ecm_scan))):
            if wm.ecm_scan[i].name in fixed:
                wm.ecm_scan.remove(i)
        self.report({'INFO'}, f"Fixed {_plural(len(fixes), 'texture')}" if fixes else "Nothing needed fixing")
        redraw_all(context)
        return {'FINISHED'}


IMAGE_KINDS = (
    ('COLOR', "Color (sRGB)", "Photos, painted textures, base color, emission", textures.SRGB_SPACES, None),
    ('DATA', "Data (Non-Color)", "Normal, roughness, metallic, height and mask maps", textures.DATA_SPACES, None),
    ('LINEAR', "Linear (HDR)", "HDRIs and EXR images", textures.LINEAR_SPACES, None),
    ('P3', "Display P3", "Wide-gamut images from iPhones, Macs or P3 workflows", ("Display P3", "sRGB Encoded P3-D65"), None),
    ('ADOBE', "Adobe RGB", "Photos edited in Adobe RGB (1998)", ("Gamma 2.2 Encoded AdobeRGB",), (5, 2, 0)),
    ('ACESCG', "ACEScg", "Linear ACES textures and VFX plates", ("ACEScg",), None),
)


def image_kinds():
    return [k for k in IMAGE_KINDS if k[4] is None or bpy.app.version >= k[4]]


def kind_of(image):
    settings = image.colorspace_settings
    if settings.is_data:
        return 'DATA'
    for key, _label, _desc, names, _version in IMAGE_KINDS:
        if key != 'DATA' and settings.name in names:
            return key
    return None


class ECM_OT_image_profile(Operator):
    bl_idname = "ecm.image_profile"
    bl_label = "Assign Image Profile"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    image_name: StringProperty(options={'SKIP_SAVE'})
    kind: EnumProperty(items=[(k, label, desc) for k, label, desc, _n, _v in IMAGE_KINDS], options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        for key, label, desc, _names, _version in IMAGE_KINDS:
            if key == properties.kind:
                return f"Tell Blender this image is {label}. Best for: {desc.lower()}"
        return ""

    def execute(self, context):
        image = bpy.data.images.get(self.image_name)
        if image is None:
            return {'CANCELLED'}
        names = next(n for k, _l, _d, n, _v in IMAGE_KINDS if k == self.kind)
        for name in names:
            try:
                image.colorspace_settings.name = name
            except TypeError:
                continue
            break
        else:
            self.report({'ERROR'}, f"Your color configuration has no {names[0]} color space")
            return {'CANCELLED'}
        image[textures.LOCK_KEY] = True
        if textures.AUTO_KEY in image:
            del image[textures.AUTO_KEY]
        textures.invalidate()
        redraw_all(context)
        return {'FINISHED'}


class ECM_OT_image_auto(Operator):
    bl_idname = "ecm.image_auto"
    bl_label = "Auto-Detect"
    bl_description = "Let Yasmini ECM pick this image's color space from how it is used, and keep managing it"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    image_name: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        image = bpy.data.images.get(self.image_name)
        if image is None:
            return {'CANCELLED'}
        if textures.LOCK_KEY in image:
            del image[textures.LOCK_KEY]
        verdict = next((v for v in textures.analyze() if v.image == image), None)
        if verdict is None or verdict.kind is None:
            image[textures.AUTO_KEY] = image.colorspace_settings.name
            self.report({'INFO'}, f"Can't tell yet how {image.name} is used; it stays {image.colorspace_settings.name}")
        elif verdict.target:
            after = textures.set_colorspace(image, verdict.target)
            image[textures.AUTO_KEY] = after
            self.report({'INFO'}, f"{image.name} is now {after} ({verdict.reason})")
        else:
            image[textures.AUTO_KEY] = image.colorspace_settings.name
            self.report({'INFO'}, f"{image.name} is already right ({verdict.reason})")
        textures.invalidate()
        redraw_all(context)
        return {'FINISHED'}


class ECM_OT_set_output(Operator):
    bl_idname = "ecm.set_output"
    bl_label = "Change Output Format"
    bl_description = "Change the render output file format or bit depth"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    file_format: StringProperty(options={'SKIP_SAVE'})
    color_depth: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        ims = context.scene.render.image_settings
        try:
            if self.file_format:
                if hasattr(ims, "media_type"):
                    ims.media_type = 'IMAGE'
                ims.file_format = self.file_format
            if self.color_depth:
                ims.color_depth = self.color_depth
        except TypeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        redraw_all(context)
        return {'FINISHED'}


class ECM_OT_sync_output(Operator):
    bl_idname = "ecm.sync_output"
    bl_label = "Match Output to Viewport"
    bl_description = "Copy the scene's view transform, look, exposure and gamma to the output color override"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def execute(self, context):
        profiles.sync_output_override(context.scene)
        redraw_all(context)
        return {'FINISHED'}


class ECM_OT_enable_embedding(Operator):
    bl_idname = "ecm.enable_embedding"
    bl_label = "Embed Profiles in Renders"
    bl_description = "Tag saved PNG, JPEG and TIFF renders with their color profile"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        prefs = get_prefs()
        if not hasattr(prefs, "bl_rna"):
            return {'CANCELLED'}
        prefs.embed_profiles = True
        redraw_all(context)
        return {'FINISHED'}


_TAG_DISPLAYS = [
    ("sRGB", "sRGB", "Standard screens and the web"),
    ("Display P3", "Display P3", "Apple devices and wide-gamut screens"),
    ("Rec.1886", "Rec.709 (BT.1886)", "HD video, gamma 2.4"),
    ("Rec.2020", "Rec.2020", "Ultra HD video, gamma 2.4"),
]


class ECM_OT_tag_files(Operator):
    bl_idname = "ecm.tag_image_files"
    bl_label = "Embed Color Profile in Files"
    bl_description = ("Tag PNG, JPEG or TIFF files on disk with a color profile, like Assign Profile in Photoshop. "
                      "Pixels are not changed")
    bl_options = {'REGISTER'}

    display: EnumProperty(name="Profile", items=_TAG_DISPLAYS, default="sRGB")
    files: CollectionProperty(type=OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.tif;*.tiff", options={'HIDDEN'})
    filter_image: bpy.props.BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        scene = context.scene
        ims = scene.render.image_settings
        display = (ims.display_settings if ims.color_management == 'OVERRIDE' else scene.display_settings).display_device
        if display in icc.DISPLAYS:
            self.display = display
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        tagged, failed = 0, []
        for entry in self.files:
            path = os.path.join(self.directory, entry.name)
            if os.path.splitext(path)[1].lower() not in icc.SUPPORTED_EXTENSIONS:
                continue
            try:
                icc.tag_file(path, self.display)
                tagged += 1
            except (OSError, ValueError, IndexError, struct.error) as error:
                failed.append(f"{entry.name}: {error}")
        if failed:
            self.report({'WARNING'}, f"Tagged {_plural(tagged, 'file')}; couldn't tag " + "; ".join(failed))
        else:
            self.report({'INFO'}, f"Tagged {_plural(tagged, 'file')} as {self.display}")
        return {'FINISHED'}


classes = (
    ECM_OT_select_profile,
    ECM_OT_apply_profile,
    ECM_OT_reset_defaults,
    ECM_OT_scan_textures,
    ECM_OT_fix_textures,
    ECM_OT_image_profile,
    ECM_OT_image_auto,
    ECM_OT_set_output,
    ECM_OT_sync_output,
    ECM_OT_enable_embedding,
    ECM_OT_tag_files,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
