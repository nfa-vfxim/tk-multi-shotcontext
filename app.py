# MIT License
# Copyright (c) 2026 Netherlands Film Academy

"""
Toolkit app for NFA Shot Context: adds "Shot Context..." to the ShotGrid menu
in Maya. Playblasts the current shot (never submitted) and plays it between the
previous and the next shot.
"""

import os

import sgtk


class ShotContextApp(sgtk.platform.Application):
    def init_app(self):
        self.tool = self.import_module("nfa_shot_context")

        display_name = self.get_setting("display_name")
        if not display_name:
            return
        self.engine.register_command(
            "%s..." % display_name,
            self._show,
            {
                "short_name": "shot_context",
                "description": "Playblast your shot and play it between the previous and the next shot",
                "icons": {"dark": {"png": os.path.join(self.disk_location, "icon_256_dark.png")}},
            },
        )

    def _show(self):
        self.tool.show()
