import datetime, os, sys, shutil, tempfile, time, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from nfa_shot_context import sg_lookup as L

ROOT = tempfile.mkdtemp()
def touch(p, age=0):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(b"x")
    t = time.time() - age; os.utime(p, (t, t)); return p

def seq(folder, n, age=0, name="img"):
    for i in range(1001, 1001 + n): touch(os.path.join(folder, "%s.%04d.jpg" % (name, i)), age)

class FakeSG:
    def __init__(self, shots, versions=(), pubs=()):
        self.shots, self.versions, self.pubs = shots, list(versions), list(pubs)
    def schema_field_read(self, t): return {f: {} for f in ["code","sg_sequence","sg_status_list","sg_cut_in","sg_cut_out","sg_cut_duration"]}
    def find_one(self, *a, **k): return {"sg_projectcode": "p123"}
    def find(self, t, filters, fields, order=None):
        if t == "Shot": return [dict(s) for s in self.shots]
        self.calls = getattr(self, "calls", 0) + 1
        val = filters[0][2]
        ids = [x["id"] for x in val] if isinstance(val, list) else [val["id"]]
        src = self.versions if t == "Version" else self.pubs
        return [dict(v, entity={"type": "Shot", "id": v["_shot"]}) for v in src if v["_shot"] in ids]

class FakeTK:
    roots = {"primary": ROOT}
    templates = {}

def mk(shots, **kw):
    sc = L.ShotContext.__new__(L.ShotContext)
    sc.tk, sc.sg = FakeTK(), FakeSG(shots, **kw)
    sc.project = {"type": "Project", "id": 1, "name": "test"}
    sc._shot_fields = None; sc._project_code = None; sc.shot = {"type": "Shot", "id": -1}
    return sc

def shot(i, code, seq="A", **f):
    d = {"type": "Shot", "id": i, "code": code, "sg_sequence": {"name": seq}, "sg_status_list": "ip"}
    d.update(f); return d

# ---- ordering: across sequences, natural sort, omit skipped
shots = [shot(1,"0010","B"), shot(2,"0020","A"), shot(3,"0010","A"), shot(4,"0100","A"), shot(5,"0030","A", sg_status_list="omt"), shot(6,"0020","B")]
sc = mk(shots)
order = [s["id"] for s in L.list_shots(sc)]
assert order == [3, 2, 4, 1, 6], order

def roles(cur):
    return [(s["code"], s["sg_sequence"]["name"], r) for s, r in L.pick_window(L.list_shots(sc), cur)]
print("middle", roles(2))
assert [r for *_, r in roles(2)] == ["previous", "current", "next"]
assert roles(4)[0][0] == "0020" and roles(4)[2][:2] == ("0010", "B")    # crosses into seq B
assert [r for *_, r in roles(3)] == ["current", "next", "next"]          # first shot: shifted
assert [r for *_, r in roles(6)] == ["previous", "previous", "current"]  # last shot: shifted
assert len(L.pick_window([shots[0]], 1)) == 1
# variable sizes: 1 = only yours; even sizes show one more after; None = all; custom numbers
cut = L.list_shots(sc)          # ids 3, 2, 4, 1, 6
def ids(cur, size): return [s["id"] for s, r in L.pick_window(cut, cur, size)]
assert ids(4, 1) == [4]
assert ids(4, 4) == [3, 2, 4, 1]          # 2 before, 1 after
assert ids(4, None) == [3, 2, 4, 1, 6]
assert ids(4, 99) == [3, 2, 4, 1, 6]
assert ids(3, 4) == [3, 2, 4, 1]          # first shot: shifted
assert ids(6, 4) == [2, 4, 1, 6]          # last shot: shifted
assert ids(6, 2) == [1, 6]                # last shot
assert [r for s, r in L.pick_window(cut, 4, None)] == ["previous", "previous", "current", "next", "next"]
assert len(L.pick_window(shots[:2], 1)) == 2

# ---- durations
assert L.shot_duration({"sg_cut_duration": 48}) == (48, "cut duration")
assert L.shot_duration({"sg_cut_in": 1001, "sg_cut_out": 1050}) == (50, "cut in/out")
assert L.shot_duration({}) == (None, "")

# ---- neighbour resolution on disk
S = lambda i, c, **f: shot(i, c, "WFT", **f)
ws = os.path.join(ROOT, "03_workfiles", "shots")
src = os.path.join(ROOT, "02_source")

# A: SG playblast newer than folder playblast -> SG wins
a = S(10, "0010", sg_cut_duration=40)
old_mov = touch(os.path.join(ws, "WFT", "0010", "animation", "maya", "previews", "main", "x_v001.mov"), age=7200)
sg_mov = touch(os.path.join(ws, "WFT", "0010", "layout", "maya", "previews", "main", "p_WFT_0010_main_sRGB_v004.mov"), age=7200)
now = datetime.datetime.now(datetime.timezone.utc)
versions = [{"_shot": 10, "id": 1, "code": "p_WFT_0010_main_v004", "created_at": now, "sg_path_to_movie": sg_mov},
            {"_shot": 10, "id": 2, "code": "comp_v010", "created_at": now, "sg_path_to_movie": "/x/nuke/comp.mov"}]
# B: folder playblast (our tool's jpg sequence) newer than the SG version
b = S(11, "0020")
bv = touch(os.path.join(ws, "WFT", "0020", "animation", "maya", "previews", "main", "v002.mov"), age=86400)
versions.append({"_shot": 11, "id": 3, "code": "anim_v002", "created_at": now - datetime.timedelta(days=1), "sg_path_to_movie": bv})
seq(os.path.join(ws, "WFT", "0020", "animation", "maya", "previews", "shot_context", "0020_shotcontext_x_user"), 30, name="0020_shotcontext")
# C: no playblast, reference mov in scWFT, highest p version
c = S(12, "0030")
touch(os.path.join(src, "scWFT", "0030", "p001", "plates", "ref", "r_p001.mov"))
touch(os.path.join(src, "scWFT", "0030", "p002", "plates", "ref", "r_p002.mov"))
# D: only EXR plates -> slate with plate frame count
d = S(13, "0040")
for i in range(1001, 1061): touch(os.path.join(src, "scWFT", "0040", "p001", "plates", "exr", "pl.%04d.exr" % i))
# E: nothing, duration from cut in/out
e = S(14, "0050", sg_cut_in=1001, sg_cut_out=1024)
# F: nothing at all -> 2 s
f = S(15, "0060")
# G: published plate movie
g = S(16, "0070")
gm = touch(os.path.join(ROOT, "elsewhere", "plate.mov"))
pubs = [{"_shot": 16, "code": "plate", "path": {"local_path": gm}, "created_at": now, "published_file_type": {"name": "Plate"}}]

sc = mk([a,b,c,d,e,f,g], versions=versions, pubs=pubs)
L.prefetch(sc, [a,b,c,d,e,f,g])
assert sc.sg.calls == 2, sc.sg.calls      # one Version + one PublishedFile query for all shots
def res(s): return L.resolve_neighbour(sc, s, "next", 25.0)
ra, rb, rc, rd, re_, rf, rg = map(res, [a,b,c,d,e,f,g])
for r in (ra, rb, rc, rd, re_, rf, rg): print(r, r.duration, r.notes)
assert ra.kind == "playblast" and ra.media.source == "shotgrid" and ra.media.path == sg_mov
assert rb.kind == "playblast" and rb.media.kind == "frames" and len(rb.media.frames) == 30
assert rc.kind == "footage" and rc.media.path.endswith("r_p002.mov")
assert rd.kind == "slate" and rd.duration == 60 and rd.slate_text == "NO FOOTAGE FOUND"
assert re_.kind == "slate" and re_.duration == 24 and re_.slate_text == "NO FOOTAGE FOUND"
assert rf.kind == "slate" and rf.duration == 50 and rf.slate_text == "NO FOOTAGE"
assert rg.kind == "footage" and rg.media.path == gm
assert sc.sg.calls == 2, sc.sg.calls      # resolving used the prefetched rows, no extra queries
assert [m.label for m in ra.options][:1] == ["p_WFT_0010_main_v004"] and len(ra.options) == 2
assert not L.is_playblast_version({"sg_path_to_movie": "/x/nuke/comp.mov", "code": "comp"})
shutil.rmtree(ROOT)
print("ALL OK")
