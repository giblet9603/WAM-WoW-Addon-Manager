import os, sys, tempfile, importlib.util, io, contextlib
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c"); os.environ["CURSEFORGE_API_KEY"] = "k"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core as wam
wam.load_catalog = lambda settings=None, refresh=False: ({"schema": 1, "categories": {}, "addons": []}, "test")
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
def run(argv):
    buf = io.StringIO(); sys.argv = ["wam"] + argv; code = 0
    with contextlib.redirect_stdout(buf):
        try: wam.main()
        except SystemExit as e: code = e.code
    return code, buf.getvalue()
addons = root/"AddOns"; addons.mkdir()
for folder, ver, extra in [("Auctionator", "340", "## X-Website: https://www.curseforge.com/wow/addons/auctionator"),
                           ("Mystery", "2.0", "")]:
    (addons/folder).mkdir(); (addons/folder/f"{folder}.toc").write_text(f"## Interface: 16001\n## Title: {folder}\n## Version: {ver}\n{extra}\n")
wam.save_config({"installs": [{"label": "Forever", "addons_dir": str(addons)}]})
f = lambda id, name, **k: dict({"id": id, "fileDate": "2026-10-02", "releaseType": 1, "fileName": name, "gameVersions": ["1.60.1"], "downloadUrl": "u", "hashes": []}, **k)
def fake(path, params=None):
    if path == "/v1/mods/search": raise wam.CFError("Forbidden: API Key missing or invalid", 403)
    if path == "/v1/mods/6124": return {"data": {"name": "Auctionator", "allowModDistribution": True}}
    if path == "/v1/mods/6124/files": return {"data": [f(9037876, "Auctionator-340.zip")]}
    if path == "/v1/mods/999": return {"data": {"name": "Mystery", "allowModDistribution": True}}
    if path == "/v1/mods/999/files": return {"data": [f(5, "Mystery-3.0.zip")]}
    raise wam.CFError("nope", 404)
wam.cf_get = fake

code, out = run(["adopt"])
ok("link Auctionator cf:<ID>" in out and "Project ID" in out, "adopt: slug lookup blocked -> tells you the workaround")
code, out = run(["link", "auctionator", "cf:6124"])
st = wam.load_state()["tracked"][str(addons)]
ok(code == 0 and st["curseforge:6124"]["tag"] == "9037876" and "up to date" in out, "link by (case-insensitive) folder name; matches installed 340")
ok(wam.load_sources() == {"Auctionator": "curseforge:6124"}, "mapping remembered in sources.json")
code, out = run(["link", "Mystery", "cf:999"])
ok(code == 0 and wam.load_state()["tracked"][str(addons)]["curseforge:999"]["tag"] == "unverified", "outdated manual addon linked as unverified")
code, out = run(["link", "Nope", "cf:1"]); ok(code == 1 and "scan --expand" in out, "unknown folder explained")
code, out = run(["link", "Mystery", "banana"]); ok(code == 1, "bad source rejected")
code, out = run(["link", "Mystery", "cf:424242"]); ok(code == 1 and "skipped" in out and "curseforge:999" in wam.load_state()["tracked"][str(addons)], "failed link leaves existing tracking alone")
# relink replaces, doesn't duplicate
wam.github_releases = lambda repo, limit=10: [{"tag_name": "v2.0", "prerelease": False, "draft": False, "assets": [{"name": "m.zip", "size": 1, "browser_download_url": "x"}]}]
code, out = run(["link", "Mystery", "me/mystery"])
tr = wam.load_state()["tracked"][str(addons)]
ok("me/mystery" in tr and "curseforge:999" not in tr and tr["me/mystery"]["tag"] == "v2.0", "relinking replaces the old record")
# mapping is honored by adopt after state reset
wam.save_state({"tracked": {}}); code, out = run(["adopt"])
ok("curseforge:6124" in out and "me/mystery" in out and wam.load_state()["tracked"][str(addons)]["curseforge:6124"]["tag"] == "9037876", "adopt uses your saved links")
print("\nALL TESTS PASSED")
