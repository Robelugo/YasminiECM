"""Background behavior: fixing new textures as they are connected, applying the default
profile to new files, and tagging renders with a color profile on Blender 4."""

import collections
import os
import re
import time
import traceback

import bpy
from bpy.app.handlers import persistent

from . import icc, profiles, textures
from .props import get_prefs, monitor_display

recent = collections.deque(maxlen=6)   # latest automatic texture fixes, shown in the UI

_known = set()            # session ids of images that were in the file when it was opened
_import_before = set()
_image_count = -1
_WATCHED = (bpy.types.Material, bpy.types.NodeTree, bpy.types.World, bpy.types.Light, bpy.types.Image)
_LEGACY = bpy.app.version < (5, 0, 0)


def _redraw():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type in {'PROPERTIES', 'VIEW_3D', 'NODE_EDITOR', 'IMAGE_EDITOR', 'STATUSBAR'}:
                area.tag_redraw()


def _busy():
    return any(bpy.app.is_job_running(job) for job in ('RENDER', 'OBJECT_BAKE'))


def remember_existing_images():
    _known.clear()
    _known.update(image.session_uid for image in bpy.data.images)


# --- Live texture fixing ---------------------------------------------------------------

def _live_pass():
    global _image_count
    try:
        if _busy():
            return 1.0
        _image_count = len(bpy.data.images)
        if get_prefs().live_textures:
            for fix in textures.live_fix(_known):
                recent.appendleft(fix)
                print(f"Yasmini ECM: {fix.image_name}: {fix.before} → {fix.after} ({fix.reason})")
        textures.invalidate()
        _redraw()
    except Exception:
        traceback.print_exc()
    return None


def _sync_pass():
    try:
        for scene in bpy.data.scenes:
            if scene.ecm_settings.applied_profile and profiles.sync_output_override(scene):
                _redraw()
    except Exception:
        traceback.print_exc()
    return None


def schedule_pass(delay=0.4):
    if not bpy.app.timers.is_registered(_live_pass):
        bpy.app.timers.register(_live_pass, first_interval=delay)


@persistent
def _on_depsgraph_update(scene, depsgraph=None, *_):
    textures_changed = len(bpy.data.images) != _image_count
    scene_changed = False
    if depsgraph is not None:
        for update in depsgraph.updates:
            if isinstance(update.id, _WATCHED):
                textures_changed = True
            elif isinstance(update.id, bpy.types.Scene):
                scene_changed = True
    if textures_changed:
        textures.invalidate()
        schedule_pass()
    elif scene_changed and scene is not None and scene.render.image_settings.color_management == 'OVERRIDE':
        # Keep the output override in step with view tweaks made in the scene.
        if not bpy.app.timers.is_registered(_sync_pass):
            bpy.app.timers.register(_sync_pass, first_interval=0.2)


@persistent
def _on_undo_redo(*_):
    textures.invalidate()
    schedule_pass()


@persistent
def _on_import_pre(*_):
    _import_before.clear()
    _import_before.update(image.session_uid for image in bpy.data.images)


@persistent
def _on_import_post(*_):
    # Appended and linked images keep the color spaces their author chose.
    _known.update(image.session_uid for image in bpy.data.images if image.session_uid not in _import_before)


# --- New files -------------------------------------------------------------------------

def _apply_default_profile():
    try:
        profile = profiles.get(get_prefs().default_profile)
        if profile is None or not profile.available or bpy.data.filepath:
            return None
        for scene in bpy.data.scenes:
            if not scene.ecm_settings.applied_profile:
                profiles.apply(scene, profile, monitor=monitor_display())
                scene.ecm_settings.selected = profile.id
        _redraw()
    except Exception:
        traceback.print_exc()
    return None


@persistent
def _on_load_post(*_):
    remember_existing_images()
    recent.clear()
    textures.invalidate()
    bpy.app.timers.register(_apply_default_profile, first_interval=0.1)


# --- Embedding profiles in renders (Blender 4) -----------------------------------------

def _written_file(scene):
    """The file Blender just wrote for this frame (stills and animations name them differently)."""
    render = scene.render
    frame = scene.frame_current
    candidates = [render.frame_path(frame=frame)]
    still = bpy.path.abspath(render.filepath)
    still = re.sub(r"#+", lambda m: str(frame).zfill(len(m.group())), still)
    if render.use_file_extension:
        still = bpy.path.ensure_ext(still, render.file_extension)
    candidates.append(still)
    existing = [p for p in candidates if os.path.isfile(p)]
    if not existing:
        return None
    newest = max(existing, key=os.path.getmtime)
    return newest if time.time() - os.path.getmtime(newest) < 120 else None


def embed_render_profile(scene):
    ims = scene.render.image_settings
    if ims.file_format not in ('PNG', 'JPEG', 'TIFF') or ims.views_format == 'INDIVIDUAL' and scene.render.use_multiview:
        return None
    override = ims.color_management == 'OVERRIDE'
    display = (ims.display_settings if override else scene.display_settings).display_device
    view = (ims.view_settings if override else scene.view_settings).view_transform
    if display not in icc.DISPLAYS or view in ("Raw", "False Color") or "Log" in view:
        return None
    path = _written_file(scene)
    if path:
        icc.tag_file(path, display)
    return path


@persistent
def _on_render_write(scene, *_):
    if not _LEGACY or not get_prefs().embed_profiles:
        return
    try:
        embed_render_profile(scene)
    except Exception:
        traceback.print_exc()


# --- Registration ----------------------------------------------------------------------

_HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update),
    ("undo_post", _on_undo_redo),
    ("redo_post", _on_undo_redo),
    ("load_post", _on_load_post),
    ("render_write", _on_render_write),
    ("blend_import_pre", _on_import_pre),
    ("blend_import_post", _on_import_post),
)


def _after_register():
    remember_existing_images()
    _apply_default_profile()
    return None


def register():
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and handler not in handlers:
            handlers.append(handler)
    bpy.app.timers.register(_after_register, first_interval=0.2)


def unregister():
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and handler in handlers:
            handlers.remove(handler)
    for timer in (_live_pass, _sync_pass, _apply_default_profile, _after_register):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
