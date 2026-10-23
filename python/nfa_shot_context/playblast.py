"""
Playblast of the current shot for NFA Shot Context.

JPG frames, never submitted to ShotGrid. They are written into the step's
Maya previews folder (<step>/maya/previews/shot_context/...) so animators on
the neighbouring shots see this playblast as well. Only the newest few takes
per artist are kept.
"""

import datetime
import getpass
import os
import re
import shutil
import tempfile

import maya.cmds as cmds

from . import sg_lookup

KEEP_TAKES = 3
MAX_WIDTH = 1920
STARTUP_CAMERAS = ("persp", "top", "front", "side", "bottom", "back", "left")


class PlayblastResult(object):
    def __init__(self, frames, folder, first_frame, shared, audio_path=None, audio_offset=0.0, camera=""):
        self.frames = frames
        self.folder = folder
        self.first_frame = first_frame
        self.shared = shared            # False when it went to the local temp folder
        self.audio_path = audio_path
        self.audio_offset = audio_offset  # scene frame where the sound starts
        self.camera = camera


def scene_fps():
    units = {
        "game": 15.0, "film": 24.0, "pal": 25.0, "ntsc": 30.0, "show": 48.0,
        "palf": 50.0, "ntscf": 60.0,
    }
    unit = cmds.currentUnit(query=True, time=True)
    if unit in units:
        return units[unit]
    m = re.match(r"^(\d+(?:\.\d+)?)fps$", unit)
    return float(m.group(1)) if m else 25.0


def _camera_of(panel):
    try:
        cam = cmds.modelPanel(panel, query=True, camera=True)
    except RuntimeError:
        return ""
    if cmds.nodeType(cam) == "camera":
        cam = (cmds.listRelatives(cam, parent=True) or [cam])[0]
    return cam.split("|")[-1]


def pick_panel():
    """The viewport to playblast: focused one, else one looking through a shot camera."""
    focus = cmds.getPanel(withFocus=True)
    if focus and cmds.getPanel(typeOf=focus) == "modelPanel":
        return focus
    visible = [p for p in (cmds.getPanel(visiblePanels=True) or [])
               if cmds.getPanel(typeOf=p) == "modelPanel"]
    for p in visible:
        if _camera_of(p).lower() not in STARTUP_CAMERAS:
            return p
    return visible[0] if visible else None


def scene_audio():
    """(absolute path, offset frame) of the scene's sound, or (None, 0)."""
    nodes = cmds.ls(type="audio") or []
    # The timeline sound wins when it's set
    try:
        import maya.mel as mel
        slider = mel.eval("$tmp = $gPlayBackSlider")
        active = cmds.timeControl(slider, query=True, sound=True)
        if active in nodes:
            nodes = [active]
    except Exception:
        pass
    if not nodes:
        return None, 0.0
    node = nodes[-1]
    path = cmds.getAttr(node + ".filename") or ""
    offset = cmds.getAttr(node + ".offset") or 0.0
    if path and not os.path.isabs(path):
        try:
            path = cmds.workspace(expandName=path)
        except Exception:
            pass
    return (path if path and os.path.isfile(path) else None), float(offset)


def output_folder(sc):
    """(<work area>/previews/shot_context, shared?) for the current context."""
    tk, ctx = sc.tk, sc.context
    tpl = tk.templates.get("shot_work_area_maya")
    if tpl is not None:
        try:
            fields = ctx.as_template_fields(tpl, validate=True)
            area = tpl.apply_fields(fields)
            return os.path.join(area, "previews", sg_lookup.SHOT_CONTEXT_FOLDER), True
        except Exception:
            pass
    # No step in the context (or no template): use the scene's own maya folder
    scene = cmds.file(query=True, sceneName=True)
    if scene:
        maya_dir = os.path.dirname(os.path.dirname(scene))
        if os.path.basename(maya_dir).lower() == "maya":
            return os.path.join(maya_dir, "previews", sg_lookup.SHOT_CONTEXT_FOLDER), True
    return os.path.join(tempfile.gettempdir(), "nfa_shot_context", "playblasts"), False


def _cleanup(base, user_tag, keep):
    """Keep the newest *keep* takes of this artist in *base*."""
    try:
        takes = [os.path.join(base, d) for d in os.listdir(base)
                 if d.endswith("_" + user_tag) and os.path.isdir(os.path.join(base, d))]
    except OSError:
        return
    takes.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for old in takes[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def make_playblast(sc, shot_record=None, use_cut_range=False):
    """Playblast the current shot to JPG frames. Raises RuntimeError on failure."""
    panel = pick_panel()
    if not panel:
        raise RuntimeError("No viewport found to playblast. Show a perspective/camera view and try again.")
    camera = _camera_of(panel)

    start = cmds.playbackOptions(query=True, minTime=True)
    end = cmds.playbackOptions(query=True, maxTime=True)
    if use_cut_range:
        rec = shot_record or {}
        cin, cout = rec.get("sg_cut_in"), rec.get("sg_cut_out")
        if cin is not None and cout is not None:
            start, end = cin, cout

    width = cmds.getAttr("defaultResolution.width")
    height = cmds.getAttr("defaultResolution.height")
    if width > MAX_WIDTH:
        height = int(round(height * MAX_WIDTH / float(width)))
        width = MAX_WIDTH
    width -= width % 2
    height -= height % 2

    user_tag = re.sub(r"[^A-Za-z0-9]+", "", getpass.getuser()) or "user"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shot_code = re.sub(r"[^A-Za-z0-9_-]+", "_", sc.shot.get("name") or sc.shot.get("code") or "shot")

    def new_take(base_dir):
        # Unique per take, also for two playblasts within the same second
        n = 1
        while True:
            suffix = "" if n == 1 else "-%d" % n
            path = os.path.join(base_dir, "%s_shotcontext_%s%s_%s" % (shot_code, stamp, suffix, user_tag))
            if not os.path.exists(path):
                os.makedirs(path)
                return path
            n += 1

    base, shared = output_folder(sc)
    try:
        folder = new_take(base)
    except OSError:
        base = os.path.join(tempfile.gettempdir(), "nfa_shot_context", "playblasts")
        folder = new_take(base)
        shared = False

    cmds.setFocus(panel)
    prefix = os.path.join(folder, "%s_shotcontext" % shot_code)
    cmds.playblast(
        filename=prefix.replace("\\", "/"),
        format="image",
        compression="jpg",
        quality=90,
        startTime=start,
        endTime=end,
        forceOverwrite=True,
        viewer=False,
        showOrnaments=False,
        offScreen=True,
        percent=100,
        widthHeight=(width, height),
        framePadding=4,
        clearCache=True,
    )

    frames = sg_lookup.find_image_sequence(folder)
    if not frames:
        shutil.rmtree(folder, ignore_errors=True)
        raise RuntimeError("The playblast didn't write any frames to:\n%s" % folder)

    _cleanup(base, user_tag, KEEP_TAKES)
    audio_path, audio_offset = scene_audio()
    return PlayblastResult(frames, folder, int(start), shared, audio_path, audio_offset, camera)
