# SPDX-License-Identifier: GPL-3.0-or-later
"""Yasmini ECM (Easy Color Management): Photoshop-style color profiles for Blender projects.

Pick what a project is for (web, print, video, VFX...) and Yasmini ECM sets up the
display, view transform, look, working space, output format and texture color spaces.
"""

from . import live, operators, props, ui

_modules = (props, operators, ui, live)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
