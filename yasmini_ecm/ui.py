"""Panels, menus and the status bar entry."""

import textwrap

import bpy
from bpy.types import Panel, UIList

from . import health, live, operators, profiles, textures
from .props import get_prefs, monitor_display

LEVEL_ICONS = {'ERROR': 'CANCEL', 'WARNING': 'ERROR', 'INFO': 'INFO'}


def text_width(context, width=None):
    """How many characters of label text fit across a region."""
    scale = context.preferences.system.ui_scale
    if width is None:
        width = context.region.width if context.region else 300 * scale
    return max(18, int((width - 40 * scale) / (5.6 * scale)))


def wrap(layout, text, chars, icon='NONE'):
    col = layout.column(align=True)
    col.scale_y = 0.85
    for i, line in enumerate(textwrap.wrap(text, chars) or [""]):
        line_icon = icon if i == 0 else ('BLANK1' if icon != 'NONE' else 'NONE')
        col.label(text=line, icon=line_icon)


def _real_prefs():
    prefs = get_prefs()
    return prefs if hasattr(prefs, "bl_rna") else None


# --- Sections shared by every place Yasmini ECM shows up ---------------------------------

def draw_status(layout, context):
    scene = context.scene
    profile = profiles.get(scene.ecm_settings.applied_profile)
    box = layout.box()
    col = box.column(align=True)
    if profile:
        modified = any(not c.creative for c in profiles.drift(scene))
        col.label(text=profile.name + (" (modified)" if modified else ""),
                  icon='ERROR' if modified else 'CHECKMARK')
    else:
        col.label(text="No color profile yet", icon='QUESTION')
    sub = col.row()
    sub.active = False
    sub.label(text=profiles.describe_current(scene))
    problems = health.problem_count(context)
    if problems:
        col.label(text=f"{problems} issue{'s' if problems != 1 else ''} found below", icon='ERROR')


def draw_cards(layout, context, columns=0):
    settings = context.scene.ecm_settings
    for group in profiles.GROUPS:
        col = layout.column(align=True)
        col.label(text=group)
        grid = col.grid_flow(row_major=True, columns=columns, even_columns=True, even_rows=True, align=True)
        grid.scale_y = 1.3
        for profile in profiles.in_group(group):
            cell = grid.row(align=True)
            cell.enabled = profile.available
            op = cell.operator("ecm.select_profile", text=profile.name, icon=profile.icon,
                               depress=settings.selected == profile.id)
            op.profile_id = profile.id


def draw_details(layout, context, chars, compact=False):
    settings = context.scene.ecm_settings
    profile = profiles.get(settings.selected) or profiles.PROFILES[0]
    box = layout.box()
    box.label(text=profile.name, icon=profile.icon)
    wrap(box, profile.summary, chars)
    if not compact:
        wrap(box, f"Best for {profile.best_for}.", chars)
    if not profile.available:
        box.label(text=f"Needs Blender {profile.min_version[0]}.{profile.min_version[1]} or newer", icon='ERROR')
        return

    rows = box.column(align=True)
    for label, value in profiles.plan_rows(profile, with_output=settings.apply_output, monitor=monitor_display()):
        split = rows.split(factor=0.4, align=True)
        name = split.row()
        name.alignment = 'RIGHT'
        name.active = False
        name.label(text=label)
        split.label(text=value)

    if compact:
        options = box.row(align=True)
        options.prop(settings, "apply_output", text="Output Format")
        options.prop(settings, "apply_textures", text="Textures")
    else:
        options = box.column(align=True)
        options.prop(settings, "apply_output")
        options.prop(settings, "apply_textures")

    row = box.row()
    row.scale_y = 1.6
    applied = settings.applied_profile == profile.id
    op = row.operator("ecm.apply_profile", text="Re-apply Profile" if applied else "Apply Profile",
                      icon='FILE_REFRESH' if applied else 'CHECKMARK')
    op.profile_id = profile.id


def draw_textures(layout, context, chars):
    wm = context.window_manager
    verdicts = textures.cached()
    sure = [v for v in verdicts if v.target and v.sure and not v.locked]
    col = layout.column()
    if not verdicts:
        col.label(text="No image textures in this file yet", icon='INFO')
    elif sure:
        n = len(sure)
        col.label(text=f"{n} texture{'s' if n != 1 else ''} need{'' if n != 1 else 's'} a different color space",
                  icon='ERROR')
        row = col.row()
        row.scale_y = 1.4
        row.operator("ecm.fix_textures", text="Fix Automatically", icon='SHADERFX').mode = 'SURE'
    else:
        n = len(verdicts)
        col.label(text=f"{n} texture{'s' if n != 1 else ''} checked, none need fixing", icon='CHECKMARK')
    col.operator("ecm.scan_textures", text="Review One by One", icon='VIEWZOOM')

    if wm.ecm_scan_done:
        if len(wm.ecm_scan):
            col.template_list("ECM_UL_textures", "", wm, "ecm_scan", wm, "ecm_scan_index", rows=3)
            if 0 <= wm.ecm_scan_index < len(wm.ecm_scan):
                item = wm.ecm_scan[wm.ecm_scan_index]
                text = f"{item.name} {item.reason}. Suggested: {item.current} → {item.target}."
                if item.locked:
                    text += " You set this one by hand."
                elif not item.sure:
                    text += " Check it before fixing."
                wrap(col, text, chars, icon='INFO')
            col.operator("ecm.fix_textures", text="Fix Checked Textures", icon='CHECKMARK').mode = 'LISTED'
        else:
            col.label(text="Nothing left to review", icon='CHECKMARK')

    prefs = _real_prefs()
    if prefs:
        col.separator()
        col.prop(prefs, "live_textures")
    if live.recent:
        recent = col.column(align=True)
        recent.active = False
        recent.label(text="Fixed automatically:")
        for fix in list(live.recent)[:4]:
            recent.label(text=f"{fix.image_name} → {fix.after}", icon='IMAGE_DATA')


def draw_health(layout, context, chars, problems_only=False):
    issues = health.check(context)
    if not any(i.level != 'INFO' for i in issues):
        layout.label(text="No problems found", icon='CHECKMARK')
    if problems_only:
        issues = [i for i in issues if i.level != 'INFO'][:3]
    for issue in issues:
        box = layout.box()
        col = box.column(align=True)
        col.label(text=issue.title, icon=LEVEL_ICONS[issue.level])
        if issue.detail:
            wrap(col, issue.detail, chars)
        if issue.fix_op:
            op = col.operator(issue.fix_op, text=issue.fix_label)
            for key, value in issue.fix_props.items():
                setattr(op, key, value)


def draw_advanced(layout, context, chars, show_native):
    col = layout.column()
    prefs = _real_prefs()
    if prefs:
        col.label(text="My Monitor")
        col.prop(prefs, "monitor", text="")
        wrap(col, "Only change this if your screen shows wide-gamut colors natively. "
                  "Re-apply the profile afterwards.", chars)
        col.separator()

    if show_native:
        scene = context.scene
        box = col.box()
        native = box.column()
        native.use_property_split = True
        native.use_property_decorate = False
        native.label(text="Blender's Color Management")
        native.prop(scene.display_settings, "display_device", text="Display")
        native.prop(scene.view_settings, "view_transform", text="View")
        native.prop(scene.view_settings, "look")
        native.prop(scene.view_settings, "exposure")
        native.prop(scene.view_settings, "gamma")
        native.prop(scene.sequencer_colorspace_settings, "name", text="Sequencer")
        colorspace = getattr(bpy.data, "colorspace", None)
        if colorspace is not None:
            row = native.row()
            row.enabled = False
            row.prop(colorspace, "working_space")

    col.operator("ecm.tag_image_files", text="Embed Profile in Files…", icon='FILE_IMAGE')
    col.operator("ecm.reset_defaults", icon='LOOP_BACK')


def draw_image_profile(layout, context, image, chars):
    settings = image.colorspace_settings
    kind = operators.kind_of(image)
    label = next((lbl for key, lbl, *_ in operators.IMAGE_KINDS if key == kind), settings.name)
    col = layout.column(align=True)
    col.label(text=f"Profile: {label}", icon='IMAGE_DATA')
    if label != settings.name:
        sub = col.row()
        sub.active = False
        sub.label(text=settings.name)
    if image.library:
        col.label(text="Linked from another file", icon='LIBRARY_DATA_DIRECT')
        return

    verdict = textures.verdict_for(image)
    if verdict and verdict.target:
        box = layout.box()
        inner = box.column(align=True)
        inner.label(text=f"Should be {textures.friendly_target(verdict.target)}", icon='ERROR')
        wrap(inner, f"It {verdict.reason}.", chars)
        inner.operator("ecm.image_auto", text="Fix It", icon='CHECKMARK').image_name = image.name
    elif verdict and verdict.kind:
        wrap(layout, f"Looks right: it {verdict.reason}.", chars, icon='CHECKMARK')

    grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for key, kind_label, _desc, _names, _version in operators.image_kinds():
        op = grid.operator("ecm.image_profile", text=kind_label, depress=kind == key)
        op.image_name = image.name
        op.kind = key

    if image.get(textures.LOCK_KEY):
        wrap(layout, "You set this by hand, so automatic fixes leave it alone.", chars, icon='LOCKED')
        layout.operator("ecm.image_auto", text="Let Yasmini ECM Decide", icon='UNLOCKED').image_name = image.name
    elif not (verdict and verdict.target):
        layout.operator("ecm.image_auto", icon='VIEWZOOM').image_name = image.name


# --- Panels ----------------------------------------------------------------------------

class _Main:
    bl_label = "Yasmini ECM"

    def draw(self, context):
        layout = self.layout
        draw_status(layout, context)
        layout.label(text="What is this project for?")
        draw_cards(layout, context)
        draw_details(layout, context, text_width(context))


class _Textures:
    bl_label = "Textures"

    def draw(self, context):
        draw_textures(self.layout, context, text_width(context))


class _Health:
    bl_label = "Health Check"

    def draw_header(self, context):
        self.layout.label(icon='ERROR' if health.problem_count(context) else 'CHECKMARK')

    def draw(self, context):
        draw_health(self.layout, context, text_width(context))


class _Advanced:
    bl_label = "Advanced"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_advanced(self.layout, context, text_width(context), context.space_data.type != 'PROPERTIES')


class _InRender:
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"


class _InSidebar:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Color"


class ECM_PT_render(_Main, _InRender, Panel):
    bl_idname = "ECM_PT_render"
    bl_order = 99   # right above Blender's own Color Management panel


class ECM_PT_render_textures(_Textures, _InRender, Panel):
    bl_idname = "ECM_PT_render_textures"
    bl_parent_id = "ECM_PT_render"


class ECM_PT_render_health(_Health, _InRender, Panel):
    bl_idname = "ECM_PT_render_health"
    bl_parent_id = "ECM_PT_render"


class ECM_PT_render_advanced(_Advanced, _InRender, Panel):
    bl_idname = "ECM_PT_render_advanced"
    bl_parent_id = "ECM_PT_render"


class ECM_PT_view3d(_Main, _InSidebar, Panel):
    bl_idname = "ECM_PT_view3d"


class ECM_PT_view3d_textures(_Textures, _InSidebar, Panel):
    bl_idname = "ECM_PT_view3d_textures"
    bl_parent_id = "ECM_PT_view3d"


class ECM_PT_view3d_health(_Health, _InSidebar, Panel):
    bl_idname = "ECM_PT_view3d_health"
    bl_parent_id = "ECM_PT_view3d"


class ECM_PT_view3d_advanced(_Advanced, _InSidebar, Panel):
    bl_idname = "ECM_PT_view3d_advanced"
    bl_parent_id = "ECM_PT_view3d"


class ECM_PT_popover(Panel):
    """Opened from the status bar and from Edit > Color Profile."""
    bl_idname = "ECM_PT_popover"
    bl_label = "Yasmini ECM"
    bl_space_type = 'TOPBAR'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 20

    def draw(self, context):
        layout = self.layout
        scale = context.preferences.system.ui_scale
        chars = text_width(context, self.bl_ui_units_x * 20 * scale)
        draw_status(layout, context)
        draw_cards(layout, context, columns=2)
        draw_details(layout, context, chars, compact=True)
        draw_health(layout, context, chars, problems_only=True)
        hint = layout.column()
        hint.active = False
        wrap(hint, "More options in Render Properties and in the 3D View sidebar (Color tab).", chars)


class ECM_PT_node_image(Panel):
    bl_idname = "ECM_PT_node_image"
    bl_label = "Color Profile"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Node"

    @classmethod
    def poll(cls, context):
        node = getattr(context, "active_node", None)
        return (context.space_data.tree_type == 'ShaderNodeTree' and node is not None
                and node.bl_idname in textures.IMAGE_NODES and node.image is not None)

    def draw(self, context):
        draw_image_profile(self.layout, context, context.active_node.image, text_width(context))


class ECM_PT_image_editor(Panel):
    bl_idname = "ECM_PT_image_editor"
    bl_label = "Color Profile"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Image"

    @classmethod
    def poll(cls, context):
        image = context.space_data.image
        return image is not None and image.type not in {'RENDER_RESULT', 'COMPOSITING'}

    def draw(self, context):
        draw_image_profile(self.layout, context, context.space_data.image, text_width(context))


class ECM_UL_textures(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "include", text="")
        row.label(text=item.name, icon='IMAGE_DATA' if item.sure else 'QUESTION')
        target = row.row()
        target.alignment = 'RIGHT'
        target.label(text=item.target)


# --- Menus and status bar --------------------------------------------------------------

def draw_statusbar(self, context):
    prefs = get_prefs()
    scene = context.scene
    if not prefs.show_statusbar or scene is None:
        return
    profile = profiles.get(scene.ecm_settings.applied_profile)
    modified = profile is not None and any(not c.creative for c in profiles.drift(scene))
    text = profile.name + (" (modified)" if modified else "") if profile else "No color profile"
    self.layout.popover(panel="ECM_PT_popover", text=text, icon='ERROR' if modified else 'COLOR')


def draw_edit_menu(self, context):
    layout = self.layout
    layout.separator()
    op = layout.operator("wm.call_panel", text="Color Profile…", icon='COLOR')
    op.name = "ECM_PT_popover"
    op.keep_open = True


def draw_image_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator("ecm.tag_image_files", text="Embed Color Profile in Files…", icon='FILE_IMAGE')


classes = (
    ECM_UL_textures,
    ECM_PT_render,
    ECM_PT_render_textures,
    ECM_PT_render_health,
    ECM_PT_render_advanced,
    ECM_PT_view3d,
    ECM_PT_view3d_textures,
    ECM_PT_view3d_health,
    ECM_PT_view3d_advanced,
    ECM_PT_popover,
    ECM_PT_node_image,
    ECM_PT_image_editor,
)

_MENUS = (
    (bpy.types.STATUSBAR_HT_header, draw_statusbar),
    (bpy.types.TOPBAR_MT_edit, draw_edit_menu),
    (bpy.types.IMAGE_MT_image, draw_image_menu),
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    for menu, draw in _MENUS:
        menu.append(draw)


def unregister():
    for menu, draw in _MENUS:
        menu.remove(draw)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
