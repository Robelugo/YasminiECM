"""Add-on preferences, per-scene settings, and the texture review list."""

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

from . import profiles

MONITOR_DISPLAYS = {'MATCH': None, 'SRGB': "sRGB", 'P3': "Display P3"}


def _redraw(self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


class ECM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    default_profile: EnumProperty(
        name="Profile for New Projects",
        description="Applied automatically to every new, unsaved file",
        items=[('NONE', "None", "Leave new files with Blender's defaults", 'BLANK1', 100)] + profiles.enum_items(),
        default='NONE',
    )
    monitor: EnumProperty(
        name="My Monitor",
        description="The screen you look at while working",
        items=[
            ('MATCH', "Same as the Profile",
             "Preview in the display the profile delivers to. Right for most setups, and always on macOS, "
             "where the system already adapts colors to your screen", 0),
            ('SRGB', "Standard Monitor (sRGB)",
             "Your screen is a regular sRGB monitor; wide-gamut deliveries get a separate output override", 1),
            ('P3', "Wide-Gamut Monitor (Display P3)",
             "Your screen shows P3 colors natively (Windows or Linux in the monitor's native mode); "
             "sRGB deliveries get a separate output override", 2),
        ],
        default='MATCH',
    )
    live_textures: BoolProperty(
        name="Fix New Textures Automatically",
        description="When you add or connect an image texture, give it the right color space "
                    "(normal and roughness maps become Non-Color, and so on). "
                    "Images you set by hand are never changed",
        default=True,
    )
    embed_profiles: BoolProperty(
        name="Embed Color Profiles in Renders",
        description="Blender 4 saves images without a color profile, so other apps assume sRGB. "
                    "Tag saved PNG, JPEG and TIFF renders with the right profile "
                    "(Blender 5 and newer do this on their own)",
        default=True,
    )
    show_statusbar: BoolProperty(
        name="Show Profile in Status Bar",
        description="Show the current profile at the bottom right of the window, like Photoshop's document profile",
        default=True,
        update=_redraw,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.prop(self, "default_profile")
        col.prop(self, "monitor")
        col.separator()
        col.prop(self, "live_textures")
        row = col.row()
        row.enabled = bpy.app.version < (5, 0, 0)
        row.prop(self, "embed_profiles")
        col.prop(self, "show_statusbar")


class _DefaultPrefs:
    """Stand-in used while the add-on isn't fully registered."""
    default_profile = 'NONE'
    monitor = 'MATCH'
    live_textures = True
    embed_profiles = True
    show_statusbar = True


def get_prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon and addon.preferences else _DefaultPrefs


def monitor_display():
    return MONITOR_DISPLAYS.get(get_prefs().monitor)


class ECM_SceneSettings(bpy.types.PropertyGroup):
    selected: EnumProperty(name="Profile", items=profiles.enum_items(), default='WEB')
    applied_profile: StringProperty()
    applied_state: StringProperty()
    apply_output: BoolProperty(
        name="Set Up the Output Format",
        description="Also choose the file format and bit depth that suit this profile",
        default=True,
    )
    apply_textures: BoolProperty(
        name="Fix Texture Color Spaces",
        description="Also give every image texture the right color space (sRGB, Non-Color or Linear)",
        default=True,
    )


class ECM_TextureItem(bpy.types.PropertyGroup):
    # `name` holds the image name.
    current: StringProperty()
    target: StringProperty()
    reason: StringProperty()
    sure: BoolProperty()
    locked: BoolProperty()
    include: BoolProperty(name="Fix", description="Change this image's color space", default=True)


classes = (ECM_Preferences, ECM_SceneSettings, ECM_TextureItem)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ecm_settings = PointerProperty(type=ECM_SceneSettings)
    bpy.types.WindowManager.ecm_scan = CollectionProperty(type=ECM_TextureItem)
    bpy.types.WindowManager.ecm_scan_index = IntProperty()
    bpy.types.WindowManager.ecm_scan_done = BoolProperty()


def unregister():
    del bpy.types.WindowManager.ecm_scan_done
    del bpy.types.WindowManager.ecm_scan_index
    del bpy.types.WindowManager.ecm_scan
    del bpy.types.Scene.ecm_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
