import io, os, sys, tempfile, importlib.util, contextlib, shutil, urllib.error, json, zipfile
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core as wam
REAL_API = wam.github_api
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
def run(argv):
    buf = io.StringIO(); sys.argv = ["wam"] + argv; code = 0
    with contextlib.redirect_stdout(buf):
        try: wam.main()
        except SystemExit as e: code = e.code
    return code, buf.getvalue()

addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
install = {"label": "Forever", "addons_dir": str(addons)}
wam.save_config({"installs": [install]})
toc = lambda n, v: f"## Interface: 16001\n## Title: {n}\n## Version: {v}\n"
def mkzip(path, ver, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Foo/Foo.toc", toc("Foo", ver)); z.writestr("Foo/data.lua", f"v{ver}")
        z.writestr("Foo_Extra/Foo_Extra.toc", toc("Foo Extra", ver))

# ---- build real history: v1 installed, then updated to v2 (creating a backup of v1) ----
ZIPS = {}
for v in ("1", "2"):
    ZIPS[v] = root/f"foo{v}.zip"; mkzip(ZIPS[v], v)
rel = lambda v: [{"tag_name": f"v{v}", "prerelease": False, "draft": False,
                  "assets": [{"name": f"foo{v}.zip", "size": ZIPS[v].stat().st_size, "browser_download_url": f"fake://{v}"}]}]
CUR = {"v": "1"}
wam.github_releases = lambda repo, limit=10: rel(CUR["v"])
wam.download_file = lambda url, dest, expected_size=None: shutil.copy(ZIPS[url[-1]], dest)
quiet = lambda *a: None
ok(wam.do_install("me/foo", install, assume_yes=True, log=quiet), "install v1")
CUR["v"] = "2"
ok(wam.do_install("me/foo", install, assume_yes=True, log=quiet), "update to v2")
ok((addons/"Foo"/"data.lua").read_text() == "v2", "v2 live")

# ---- list_backups ----
bks = wam.list_backups()
ok(len(bks) == 1 and bks[0]["folders"] == ["Foo", "Foo_Extra"] and bks[0]["source"] == "me_foo", f"backup listed: {bks[0]['when']} {bks[0]['source']}")
code, out = run(["backups"]); ok("Foo, Foo_Extra" in out and "wam restore" in out, "backups command lists it")

# ---- restore failure midway leaves backup + addons untouched ----
real_move = shutil.move; n = {"c": 0}
def flaky(a, b):
    n["c"] += 1
    if n["c"] == 2: raise OSError("boom")
    return real_move(a, b)
wam.shutil.move = flaky
logs = []
res = wam.restore_backup(bks[0]["path"], install, log=logs.append)
wam.shutil.move = real_move
ok(not res and any("boom" in l for l in logs), "restore failure reported")
ok((addons/"Foo"/"data.lua").read_text() == "v2", "addons unchanged after failed restore")
ok(bks[0]["path"].exists() and (bks[0]["path"]/"Foo"/"data.lua").read_text() == "v1", "backup intact after failed restore")
ok(not [p for p in addons.parent.iterdir() if p.name.startswith(".wam-")], "no temp dirs left behind")

# ---- successful restore, via the CLI (prefix match), and it is undoable ----
code, out = run(["restore", "zzz", "--yes"]); ok(code == 1 and "No backup matches" in out, "unknown backup name rejected")
code, out = run(["restore", bks[0]["name"][:15], "--yes"])
ok(code == 0 and (addons/"Foo"/"data.lua").read_text() == "v1", f"restored v1: {out.strip().splitlines()[-2:]}")
ok(not bks[0]["path"].exists(), "used backup is consumed (its files are live again)")
undo = [b for b in wam.list_backups() if b["source"] == "before-restore"]
ok(len(undo) == 1 and (undo[0]["path"]/"Foo"/"data.lua").read_text() == "v2", "the replaced v2 was saved as an undo point")
st = wam.load_state()["tracked"][str(addons)]["me/foo"]
ok(st["tag"] == "unverified", "tracking no longer claims a version we can't confirm")
CUR["v"] = "2"
code, out = run(["check"]); ok("update available" in out, "check then offers the newer version again")
ok(wam.restore_backup(undo[0]["path"], install, log=quiet) and (addons/"Foo"/"data.lua").read_text() == "v2", "undoing the restore works")
ok(not wam.restore_backup(root/"nope", install, log=quiet), "missing backup path refused")

# ---- search ----
CALLS = []
items_a = [{"full_name": "a/fish", "name": "fish", "description": "Fishing helper for WoW Forever", "stargazers_count": 5, "pushed_at": "2026-10-01T00:00:00Z", "html_url": "u1", "archived": False},
           {"full_name": "b/bigfish", "name": "bigfish", "description": None, "stargazers_count": 90, "pushed_at": "2026-01-01T00:00:00Z", "html_url": "u2", "archived": True}]
items_b = [{"full_name": "a/fish", "name": "fish", "description": "dup", "stargazers_count": 5, "pushed_at": "x", "html_url": "u1"},
           {"full_name": "c/newfish", "name": "newfish", "description": "Brand new", "stargazers_count": 1, "pushed_at": "2026-10-10T00:00:00Z", "html_url": "u3"}]
def fake_api(path, params=None):
    CALLS.append((path, dict(params)))
    return {"items": items_a if "topic:wow-addon" in params["q"] and "wow-addons" not in params["q"] else items_b}
wam.github_api = fake_api
r = wam.search_github("fishing", "popular", True)
ok([x["key"] for x in r] == ["b/bigfish", "a/fish", "c/newfish"], f"popular sort merges + dedupes: {[x['key'] for x in r]}")
ok(CALLS[0][1]["q"] == "fishing forever topic:wow-addon" and CALLS[0][1]["sort"] == "stars" and CALLS[1][1]["q"].endswith("topic:wow-addons"), "query + sort sent to GitHub correctly")
ok(r[0]["archived"] and r[0]["desc"] == "" and r[1]["updated"] == "2026-10-01", "fields normalized (null description, date, archived flag)")
ok(r[1]["forever"] is True and r[0]["forever"] is False, "Forever hint comes from the repo's own words")
r = wam.search_github("fish", "updated"); ok([x["key"] for x in r][0] == "c/newfish", "updated sort")
r = wam.search_github("fish", "best", limit=2); ok(len(r) == 2, "limit respected")
code, out = run(["search", "fishing", "--sort", "popular", "--github"]); ok(code == 0 and "b/bigfish" in out and "wam install" in out and "[mentions Forever]" in out, "search --github command output")

# GitHub errors become readable messages
class H(urllib.error.HTTPError):
    def __init__(s, code): super().__init__("u", code, "m", {}, None)
wam.github_api = REAL_API
def boom(req, timeout=0): raise H(403)
wam.urllib.request.urlopen = boom
try: wam.search_github("x"); ok(False, "should raise")
except RuntimeError as e: ok("search limit" in str(e), "rate limit explained in plain words")

# ---- schedule helper never prints, validates, reports ----
calls = []
wam.shutil.which = lambda n: "/usr/bin/" + n
wam.subprocess.run = lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0, "stderr": "", "stdout": "NEXT  LEFT  wam-check.timer"})()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    a = wam.schedule_apply("on", "07:30"); b = wam.schedule_apply("on", "99:99"); c = wam.schedule_apply("status"); d = wam.schedule_apply("off"); e = wam.schedule_apply("status")
ok(buf.getvalue() == "", "schedule_apply prints nothing (safe inside the TUI)")
ok(a[0] and not b[0] and "24-hour" in b[1] and "wam-check.timer" in c[1] and d[0] and e[1] == "Daily check is off.", "schedule on/invalid/status/off")
svc = (wam.SYSTEMD_USER_DIR / "wam-check.service")
wam.schedule_apply("on"); t = svc.read_text()
ok("-m wam auto-check" in t and "PYTHONPATH=" in t, "service runs `python -m wam auto-check`")
print("\nALL TESTS PASSED")
