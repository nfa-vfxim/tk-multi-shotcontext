"""
ShotGrid side of NFA Shot Context.

Everything here is plain Python on top of sgtk / shotgun_api3 so it can be
tested without Maya:

- get_context()          -> the current Toolkit shot context (or a clear error)
- list_shots()           -> all shots of the project, in cut order (shot code)
- pick_window()          -> always three shots, shifted at the start/end
- resolve_neighbour()    -> what to show for a neighbouring shot:
                            newest playblast  >  footage  >  "no footage" slate
"""

import glob
import os
import re
import tempfile

VIDEO_EXT = (".mov", ".mp4", ".m4v", ".webm", ".avi", ".mkv")
IMAGE_EXT = (".jpg", ".jpeg", ".png")

# Shots with these statuses are left out of the cut.
EXCLUDED_STATUSES = ("omt", "omit", "na")

# A ShotGrid Version counts as a playblast when its movie lives in the Maya
# folder structure (the Review tool writes to <step>/maya/previews/...) or
# when its name says so.
PLAYBLAST_PATH_MARKERS = ("/maya/",)
PLAYBLAST_NAME_MARKERS = ("playblast",)

# Published file types that count as footage (lower case, substring match).
FOOTAGE_TYPE_MARKERS = ("plate", "footage", "reference", "scan")

DEFAULT_SECONDS = 2.0

SHOT_CONTEXT_FOLDER = "shot_context"


class NotAvailable(Exception):
    """The tool can't run here; the message is shown to the artist."""


# --------------------------------------------------------------------------- helpers
def natural_key(text):
    """'sh10' sorts after 'sh9'; case insensitive."""
    parts = re.split(r"(\d+)", (text or "").lower())
    return [int(p) if p.isdigit() else p for p in parts]


def _norm(path):
    return (path or "").replace("\\", "/")


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _ts(value):
    """ShotGrid datetime -> epoch seconds (0 when unknown)."""
    if value is None:
        return 0.0
    try:
        return value.timestamp()
    except Exception:
        return 0.0


_SEQ_RE = re.compile(r"^(?P<head>.*?)(?P<num>\d+)(?P<ext>\.(?:jpe?g|png))$", re.IGNORECASE)


def find_image_sequence(folder):
    """Biggest numbered JPG/PNG sequence directly in *folder*, as a sorted list."""
    groups = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        m = _SEQ_RE.match(name)
        if not m:
            continue
        key = (m.group("head"), m.group("ext").lower())
        groups.setdefault(key, []).append((int(m.group("num")), os.path.join(folder, name)))
    if not groups:
        return []
    best = max(groups.values(), key=len)
    if len(best) < 2:
        return []
    return [p for _, p in sorted(best)]


def count_frames(folder, extensions=(".exr", ".dpx", ".tif", ".tiff") + IMAGE_EXT):
    """Number of frames of the biggest numbered sequence in *folder* (any image type)."""
    pat = re.compile(r"^(.*?)(\d+)(\.[A-Za-z]+)$")
    groups = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        m = pat.match(name)
        if m and m.group(3).lower() in extensions:
            groups.setdefault((m.group(1), m.group(3).lower()), 0)
            groups[(m.group(1), m.group(3).lower())] += 1
    return max(groups.values()) if groups else 0


class Media(object):
    """One candidate thing to show for a shot."""

    def __init__(self, kind, path, timestamp, label, frames=None, source=""):
        self.kind = kind            # "movie" | "frames"
        self.path = path            # movie file or sequence folder
        self.timestamp = timestamp  # epoch seconds, used to pick the newest
        self.label = label          # short text for the UI
        self.frames = frames or []  # for "frames"
        self.source = source        # "shotgrid" | "folder" | "footage"

    def __repr__(self):
        return "Media(%s, %r, %s)" % (self.kind, self.path, self.label)


class ShotInfo(object):
    """What the viewer needs for one of the three slots."""

    def __init__(self, shot, role):
        self.shot = shot            # ShotGrid Shot dict
        self.code = shot.get("code") or "?"
        self.role = role            # "previous" | "current" | "next"
        self.media = None           # Media or None
        self.kind = "slate"         # "playblast" | "footage" | "slate"
        self.duration = None        # frames, when known
        self.duration_source = ""   # where the duration came from
        self.slate_text = ""
        self.notes = []
        self.options = []           # every Media the artist can switch to, newest playblast first

    def __repr__(self):
        return "ShotInfo(%s, %s, %s, %s)" % (self.code, self.role, self.kind, self.media)


# --------------------------------------------------------------------------- context
class ShotContext(object):
    def __init__(self, engine):
        self.engine = engine
        self.tk = engine.sgtk
        self.sg = engine.shotgun
        self.context = engine.context
        self.project = self.context.project
        self.shot = self.context.entity
        self.step = self.context.step
        self.user = self.context.user
        self._shot_fields = None
        self._project_code = None

    # ---- ShotGrid schema, cached per session
    def shot_schema(self):
        if self._shot_fields is None:
            try:
                self._shot_fields = set(self.sg.schema_field_read("Shot").keys())
            except Exception:
                self._shot_fields = set()
        return self._shot_fields

    def project_code(self):
        if self._project_code is None:
            code = ""
            try:
                p = self.sg.find_one("Project", [["id", "is", self.project["id"]]],
                                     ["sg_projectcode", "tank_name"])
                code = (p or {}).get("sg_projectcode") or ""
            except Exception:
                pass
            self._project_code = code
        return self._project_code

    def project_root(self):
        roots = getattr(self.tk, "roots", None) or {}
        if "primary" in roots:
            return roots["primary"]
        return next(iter(roots.values()), None)


def get_context():
    """The running Toolkit shot context, or NotAvailable with a readable reason."""
    try:
        import sgtk
    except ImportError:
        raise NotAvailable(
            "ShotGrid is not running in this Maya.\n\n"
            "Start Maya from ShotGrid Desktop (or open your shot with ShotGrid > File Open) "
            "and try again."
        )
    engine = sgtk.platform.current_engine()
    if engine is None:
        raise NotAvailable(
            "ShotGrid is not running in this Maya.\n\n"
            "Start Maya from ShotGrid Desktop and open your shot through ShotGrid > File Open."
        )
    entity = engine.context.entity
    if not entity or entity.get("type") != "Shot":
        where = entity.get("type") if entity else "a project without a shot"
        raise NotAvailable(
            "Shot Context only works inside a shot.\n\n"
            "Maya is currently in %s. Open your shot work file through "
            "ShotGrid > File Open and try again." % where
        )
    return ShotContext(engine)


# --------------------------------------------------------------------------- shot order
def duration_fields(schema):
    wanted = ["sg_cut_duration", "sg_cut_in", "sg_cut_out", "sg_working_duration"]
    return [f for f in wanted if not schema or f in schema]


def list_shots(sc):
    """All shots of the project, ordered by sequence and shot code (natural sort)."""
    schema = sc.shot_schema()
    fields = ["code", "sg_sequence", "sg_status_list"] + duration_fields(schema)
    shots = sc.sg.find("Shot", [["project", "is", sc.project]], fields)
    keep = []
    for s in shots:
        status = (s.get("sg_status_list") or "").lower()
        if status in EXCLUDED_STATUSES and s["id"] != sc.shot["id"]:
            continue
        keep.append(s)
    return sort_shots(keep)


def sequence_name(shot):
    seq = shot.get("sg_sequence") or {}
    return seq.get("name") or seq.get("code") or ""


def sort_shots(shots):
    return sorted(shots, key=lambda s: (natural_key(sequence_name(s)), natural_key(s.get("code"))))


def pick_window(shots, current_id, size=3):
    """
    *size* shots around the current one (None = the whole cut). Odd sizes put
    the current shot in the middle; even sizes show one more shot before it
    than after it. At the start or end of the cut the window shifts so there are
    still *size* shots (when the project has them).
    Returns [(shot, role)], role is "previous", "current" or "next".
    """
    ids = [s["id"] for s in shots]
    if current_id not in ids:
        raise NotAvailable("The current shot was not found in the project's shot list.")
    i = ids.index(current_id)
    n = len(shots)
    size = n if size is None else max(1, min(int(size), n))
    start = i - size // 2
    start = max(0, min(start, n - size))
    out = []
    for j in range(start, start + size):
        role = "current" if j == i else ("previous" if j < i else "next")
        out.append((shots[j], role))
    return out


def shot_duration(shot):
    """(frames, source) from ShotGrid, or (None, '')."""
    d = shot.get("sg_cut_duration")
    if d:
        return int(d), "cut duration"
    cin, cout = shot.get("sg_cut_in"), shot.get("sg_cut_out")
    if cin is not None and cout is not None and cout >= cin:
        return int(cout - cin + 1), "cut in/out"
    d = shot.get("sg_working_duration")
    if d:
        return int(d), "working duration"
    return None, ""


# --------------------------------------------------------------------------- paths
def _template_paths(sc, shot, template_name, skip=("Step", "name", "version")):
    """Existing paths of a template for *shot*, leaving the *skip* keys open."""
    tk = sc.tk
    tpl = tk.templates.get(template_name) if hasattr(tk, "templates") else None
    if tpl is None:
        return []
    fields = {}
    try:
        ctx = tk.context_from_entity("Shot", shot["id"])
        try:
            fields = ctx.as_template_fields(tpl, validate=False)
        except TypeError:
            fields = ctx.as_template_fields(tpl)
    except Exception:
        fields = {}
    keys = set(tpl.keys.keys())
    if "Shot" in keys and not fields.get("Shot"):
        fields["Shot"] = shot.get("code")
    if "Sequence" in keys and not fields.get("Sequence"):
        fields["Sequence"] = sequence_name(shot)
    if "Projectcode" in keys and not fields.get("Projectcode"):
        fields["Projectcode"] = sc.project_code()
    skip_keys = [k for k in keys if k in skip or (k not in fields)]
    try:
        return tk.paths_from_template(tpl, fields, skip_keys=skip_keys)
    except Exception:
        return []


def _sequence_folder_names(shot):
    """'WFT' and 'scWFT' both occur on disk (see Shot Manager v1.22.5)."""
    seq = sequence_name(shot)
    names = {seq}
    if re.match(r"^sc(?=[A-Z0-9])", seq):
        names.add(seq[2:])
    else:
        names.add("sc" + seq)
    return [n for n in names if n]


def maya_preview_dirs(sc, shot):
    """<shot>/<every step>/maya/previews folders that exist."""
    dirs = set()
    for area in _template_paths(sc, shot, "shot_work_area_maya"):
        dirs.add(os.path.join(area, "previews"))
    root = sc.project_root()
    if root:
        for seq in _sequence_folder_names(shot):
            pattern = os.path.join(root, "03_workfiles", "shots", seq, shot["code"], "*", "maya", "previews")
            dirs.update(glob.glob(pattern))
    return sorted(d for d in dirs if os.path.isdir(d))


def scan_preview_dir(folder):
    """Movies and image sequences anywhere below a maya/previews folder."""
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in filenames:
            if name.lower().endswith(VIDEO_EXT):
                p = os.path.join(dirpath, name)
                found.append(Media("movie", p, _mtime(p), name, source="folder"))
        seq = find_image_sequence(dirpath)
        if seq:
            newest = max(_mtime(seq[0]), _mtime(seq[-1]))
            found.append(Media("frames", dirpath, newest, os.path.basename(dirpath),
                               frames=seq, source="folder"))
    return found


def is_playblast_version(version):
    path = _norm(version.get("sg_path_to_movie")).lower()
    name = (version.get("code") or "").lower()
    if any(m in path for m in PLAYBLAST_PATH_MARKERS):
        return True
    return any(m in name for m in PLAYBLAST_NAME_MARKERS)


VERSION_FIELDS = ["code", "created_at", "sg_path_to_movie", "sg_uploaded_movie", "user", "entity"]
PUBLISH_FIELDS = ["code", "path", "created_at", "published_file_type", "entity"]


def _batch_find(sc, entity_type, shots, fields, cache_name):
    """One ShotGrid query for many shots; results cached per shot id on *sc*."""
    cache = sc.__dict__.setdefault(cache_name, {})
    todo = [s for s in shots if s["id"] not in cache]
    for start in range(0, len(todo), 100):
        chunk = todo[start:start + 100]
        try:
            rows = sc.sg.find(entity_type, [["entity", "in", [{"type": "Shot", "id": s["id"]} for s in chunk]]],
                              fields, order=[{"field_name": "created_at", "direction": "desc"}])
        except Exception:
            continue  # leave uncached; the per-shot query below tries again
        for s in chunk:
            cache[s["id"]] = []
        for r in rows:
            sid = (r.get("entity") or {}).get("id")
            if sid in cache:
                cache[sid].append(r)
    return cache


def prefetch(sc, shots):
    """Fetch Versions and PublishedFiles of many shots in a few queries."""
    _batch_find(sc, "Version", shots, VERSION_FIELDS, "_version_cache")
    _batch_find(sc, "PublishedFile", shots, PUBLISH_FIELDS, "_publish_cache")


def _rows(sc, entity_type, shot, fields, cache_name):
    cache = sc.__dict__.get(cache_name) or {}
    if shot["id"] in cache:
        return cache[shot["id"]]
    try:
        return sc.sg.find(entity_type, [["entity", "is", {"type": "Shot", "id": shot["id"]}]],
                          fields, order=[{"field_name": "created_at", "direction": "desc"}])
    except Exception:
        return []


def shotgrid_playblasts(sc, shot):
    """Playblast Versions of a shot as Media (local file, or downloaded upload)."""
    versions = _rows(sc, "Version", shot, VERSION_FIELDS, "_version_cache")
    out = []
    for v in versions:
        if not is_playblast_version(v):
            continue
        label = v.get("code") or "Version %s" % v["id"]
        path = v.get("sg_path_to_movie")
        if path and os.path.isfile(path):
            out.append(Media("movie", path, _ts(v.get("created_at")), label, source="shotgrid"))
            continue
        if v.get("sg_uploaded_movie"):
            out.append(Media("movie", None, _ts(v.get("created_at")), label, source="shotgrid"))
            out[-1].version = v
    return out


def download_uploaded_movie(sc, media):
    """Fetch an uploaded movie into a local cache once; returns the path or None."""
    v = getattr(media, "version", None)
    if not v:
        return None
    cache = os.path.join(tempfile.gettempdir(), "nfa_shot_context", "downloads")
    try:
        os.makedirs(cache, exist_ok=True)
    except OSError:
        return None
    target = os.path.join(cache, "version_%s.mov" % v["id"])
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target
    try:
        sc.sg.download_attachment(v["sg_uploaded_movie"], file_path=target)
        return target
    except Exception:
        return None


def list_playblasts(sc, shot):
    """
    Every playblast of a shot, newest first: ShotGrid playblast Versions and
    movies / image sequences in the Maya folder structure. A file that is
    reachable both ways is listed once, as its ShotGrid Version.
    """
    candidates = []
    for d in maya_preview_dirs(sc, shot):
        candidates.extend(scan_preview_dir(d))
    candidates.extend(shotgrid_playblasts(sc, shot))
    by_path = {}
    for c in candidates:
        key = _norm(c.path).lower() if c.path else id(c)
        if key not in by_path or c.source == "shotgrid":
            by_path[key] = c
    return sorted(by_path.values(), key=lambda m: m.timestamp, reverse=True)


def ensure_local(sc, media):
    """Make sure *media* has a local path (downloads an uploaded movie). True when playable."""
    if media.path is None:
        media.path = download_uploaded_movie(sc, media)
    return bool(media.path)


def newest_playblast(sc, shot, playblasts=None):
    """
    The latest form of a playblast: whichever is newer, the newest ShotGrid
    playblast Version or the newest file in the Maya folder structure.
    """
    for c in (playblasts if playblasts is not None else list_playblasts(sc, shot)):
        if ensure_local(sc, c):
            return c
    return None


def find_footage(sc, shot):
    """
    (Media or None, plate frame count). Order: newest reference .mov from
    02_source, then a published plate/reference movie, then a JPG/PNG plate
    sequence. EXR plates can't be shown, but their frame count is returned.
    """
    plate_frames = 0
    refs = list(_template_paths(sc, shot, "hiero_plate_path", skip=("version",)))
    root = sc.project_root()
    if root:
        for seq in _sequence_folder_names(shot):
            refs += glob.glob(os.path.join(root, "02_source", seq, shot["code"], "p*", "plates", "ref", "*.mov"))
    refs = sorted(set(p for p in refs if os.path.isfile(p)), key=lambda p: (natural_key(p), _mtime(p)))

    # Plate folders next to the reference (or found by glob) for duration/sequence fallback
    plate_dirs = set()
    for r in refs:
        plate_dirs.update(glob.glob(os.path.join(os.path.dirname(os.path.dirname(r)), "*")))
    if root:
        for seq in _sequence_folder_names(shot):
            plate_dirs.update(glob.glob(os.path.join(root, "02_source", seq, shot["code"], "p*", "plates", "*")))
    plate_dirs = sorted((d for d in plate_dirs if os.path.isdir(d)), key=natural_key, reverse=True)
    for d in plate_dirs:
        plate_frames = max(plate_frames, count_frames(d))

    if refs:
        p = refs[-1]
        return Media("movie", p, _mtime(p), os.path.basename(p), source="footage"), plate_frames

    pubs = _rows(sc, "PublishedFile", shot, PUBLISH_FIELDS, "_publish_cache")
    for pub in pubs:
        ptype = ((pub.get("published_file_type") or {}).get("name") or "").lower()
        if not any(m in ptype for m in FOOTAGE_TYPE_MARKERS):
            continue
        path = (pub.get("path") or {}).get("local_path")
        if path and path.lower().endswith(VIDEO_EXT) and os.path.isfile(path):
            return Media("movie", path, _ts(pub.get("created_at")), pub.get("code") or os.path.basename(path),
                         source="footage"), plate_frames

    for d in plate_dirs:
        seq = find_image_sequence(d)
        if seq:
            return Media("frames", d, _mtime(seq[0]), os.path.basename(d), frames=seq,
                         source="footage"), plate_frames
    return None, plate_frames


def resolve_neighbour(sc, shot, role, fps):
    """Decide what a neighbouring shot shows: playblast > footage > slate."""
    info = ShotInfo(shot, role)
    info.duration, info.duration_source = shot_duration(shot)

    playblasts = list_playblasts(sc, shot)
    footage, plate_frames = find_footage(sc, shot)
    info.options = playblasts + ([footage] if footage else [])

    media = newest_playblast(sc, shot, playblasts)
    if media:
        info.media, info.kind = media, "playblast"
        return info

    if footage:
        info.media, info.kind = footage, "footage"
        return info

    info.kind = "slate"
    if info.duration is None and plate_frames:
        info.duration, info.duration_source = plate_frames, "plate frames"
    if info.duration:
        info.slate_text = "NO FOOTAGE FOUND"
        info.notes.append("%d frames (%s)" % (info.duration, info.duration_source))
    else:
        info.duration = int(round(DEFAULT_SECONDS * fps))
        info.duration_source = "default"
        info.slate_text = "NO FOOTAGE"
        info.notes.append("No duration on ShotGrid, showing %g s" % DEFAULT_SECONDS)
    return info
