"""
NFA Shot Context — see your shot in the cut.

Playblasts the current shot (never submitted to ShotGrid) and plays it between
the shot before and the shot after. Only works in a Maya started through
ShotGrid, with a shot work file open.

    import nfa_shot_context
    nfa_shot_context.show()
"""

import os
import time
import traceback

__version__ = "1.2.0"
__author__ = "Luuk Kamphuis"

_WINDOW = None

SHELF_NAME = "NFA"
SHELF_LABEL = "ShotContext"
SHELF_COMMAND = (
    "import sys, traceback\n"
    "scripts_dir = r'C:/pipeline/maya/scripts'\n"
    "if scripts_dir not in sys.path:\n"
    "    sys.path.append(scripts_dir)\n"
    "# Drop the old version so every click loads the newest code\n"
    "for _m in [m for m in list(sys.modules) if m == 'nfa_shot_context' or m.startswith('nfa_shot_context.')]:\n"
    "    sys.modules.pop(_m, None)\n"
    "try:\n"
    "    import nfa_shot_context\n"
    "    nfa_shot_context.show()\n"
    "except Exception as e:\n"
    "    traceback.print_exc()\n"
    "    import maya.cmds as cmds\n"
    "    cmds.warning('Could not start Shot Context: %s' % e)\n"
)


def ensure_shelf_button():
    """
    Called from userSetup.mel. Maya keeps its own copy of the NFA shelf in each
    user's prefs, so a button added to shelf_NFA.mel never reaches artists who
    loaded the shelf before. This adds it to the NFA shelf when it's missing.
    """
    import maya.cmds as cmds

    def _add():
        try:
            if not cmds.shelfLayout(SHELF_NAME, exists=True):
                return
            for child in cmds.shelfLayout(SHELF_NAME, query=True, childArray=True) or []:
                if (cmds.objectTypeUI(child) == "shelfButton"
                        and cmds.shelfButton(child, query=True, label=True) == SHELF_LABEL):
                    return
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_context.png").replace("\\", "/")
            cmds.shelfButton(
                parent=SHELF_NAME, label=SHELF_LABEL, image=icon, image1=icon, style="iconOnly",
                annotation="Shot Context - playblast your shot and play it between the previous and next shot",
                command=SHELF_COMMAND, sourceType="python", flat=True,
            )
            print("[NFA Shot Context] Added the Shot Context button to the %s shelf" % SHELF_NAME)
        except Exception as e:
            print("[NFA Shot Context] Could not add the shelf button: %s" % e)

    cmds.evalDeferred(_add, lowestPriority=True)


def _maya_main_window():
    try:
        from maya import OpenMayaUI
        try:
            from shiboken6 import wrapInstance
            from PySide6 import QtWidgets
        except ImportError:
            from shiboken2 import wrapInstance
            from PySide2 import QtWidgets
        ptr = OpenMayaUI.MQtUtil.mainWindow()
        return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
    except Exception:
        return None


def _message(title, text, error=False):
    from .viewer import QtWidgets
    box = QtWidgets.QMessageBox(_maya_main_window())
    box.setObjectName("ShotContext")
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QtWidgets.QMessageBox.Warning if error else QtWidgets.QMessageBox.Information)
    box.exec_() if hasattr(box, "exec_") else box.exec()


class _Progress(object):
    """Maya progress window; Esc cancels between steps."""

    def __init__(self, steps):
        import maya.cmds as cmds
        self.cmds = cmds
        self.steps = max(1, steps)
        self.done = 0
        cmds.progressWindow(title="NFA Shot Context", progress=0, maxValue=self.steps,
                            status="Starting…", isInterruptable=True)

    def step(self, text):
        self.cmds.progressWindow(edit=True, progress=self.done, status=text)
        self.done += 1
        if self.cmds.progressWindow(query=True, isCancelled=True):
            raise KeyboardInterrupt()

    def close(self):
        self.cmds.progressWindow(endProgress=True)


def _status_for(pb):
    if pb.shared:
        return "Your playblast: %s" % pb.folder
    return ("Your playblast was saved locally (the shot folder wasn't writable), "
            "so artists on the neighbouring shots won't see it: %s" % pb.folder)


def show():
    """Shelf entry point."""
    global _WINDOW
    from . import sg_lookup

    try:
        sc = sg_lookup.get_context()
    except sg_lookup.NotAvailable as e:
        _message("NFA Shot Context", str(e), error=True)
        return

    from . import playblast, viewer

    if _WINDOW is not None:
        try:
            _WINDOW.close()
            _WINDOW.deleteLater()
        except Exception:
            pass
        _WINDOW = None

    progress = _Progress(5)
    try:
        progress.step("Reading the cut from ShotGrid…")
        shots = sg_lookup.list_shots(sc)
        window = sg_lookup.pick_window(shots, sc.shot["id"])
        fps = playblast.scene_fps()
        current = next(s for s, role in window if role == "current")

        progress.step("Playblasting %s…" % current.get("code"))
        pb = playblast.make_playblast(sc, shot_record=current)

        infos = []
        for shot, role in window:
            if role == "current":
                info = sg_lookup.ShotInfo(shot, role)
                info.kind = "playblast"
                info.media = sg_lookup.Media("frames", pb.folder, time.time(), "This playblast",
                                             frames=pb.frames, source="fresh")
                # Older takes and other playblasts of this shot, plus footage, to switch to
                fresh_key = os.path.normcase(os.path.normpath(pb.folder))
                older = [m for m in sg_lookup.list_playblasts(sc, shot)
                         if not (m.path and os.path.normcase(os.path.normpath(m.path)) == fresh_key)]
                footage, _ = sg_lookup.find_footage(sc, shot)
                info.options = [info.media] + older + ([footage] if footage else [])
                infos.append(info)
            else:
                progress.step("Looking up %s…" % shot.get("code"))
                infos.append(sg_lookup.resolve_neighbour(sc, shot, role, fps))
        progress.step("Opening…")
    except KeyboardInterrupt:
        progress.close()
        return
    except sg_lookup.NotAvailable as e:
        progress.close()
        _message("NFA Shot Context", str(e), error=True)
        return
    except Exception as e:
        progress.close()
        traceback.print_exc()
        _message("NFA Shot Context", "Something went wrong:\n\n%s" % e, error=True)
        return
    progress.close()

    slots = []
    for info in infos:
        slot = viewer.Slot(info, fps)
        m = info.media
        if info.role == "current":
            slot.frames = list(pb.frames)
            slot.first_frame = pb.first_frame
            slot.fresh = m
            if pb.audio_path:
                slot.audio_path = pb.audio_path
                slot.audio_start_frame = pb.audio_offset - pb.first_frame
                slot.scene_audio = (slot.audio_path, slot.audio_start_frame)
            slot.description = "Playblast of %s" % (pb.camera or "the viewport")
        elif m is not None and m.kind == "frames":
            slot.frames = list(m.frames)
        if m is not None and info.role != "current":
            when = viewer._age(m.timestamp) if m.timestamp else ""
            slot.description = "%s%s" % (m.label, (" · " + when) if when else "")
        slots.append(slot)
        print("[NFA Shot Context] %s (%s): %s %s" % (info.code, info.role, info.kind,
                                                      m.path if m else "; ".join(info.notes)))

    step = (sc.step or {}).get("name") or ""
    context_text = "  ·  ".join(x for x in ((sc.project or {}).get("name"), current.get("code"), step) if x)
    _WINDOW = viewer.ShotContextWindow(slots, fps, context_text=context_text, status=_status_for(pb),
                                       resolve_media=lambda m: sg_lookup.ensure_local(sc, m),
                                       parent=_maya_main_window())
    _WINDOW.setAttribute(viewer.QtCore.Qt.WA_DeleteOnClose, True)
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW
