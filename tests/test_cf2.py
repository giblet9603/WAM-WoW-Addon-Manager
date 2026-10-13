import os, sys, tempfile, importlib.util, io, contextlib, hashlib, zipfile, shutil
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
os.environ["CURSEFORGE_API_KEY"] = "test-key"
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

P = "1.60."
f = lambda id, date, rt, name, gv=("1.60.1",), url="https://edge.forgecdn.net/f.zip", sha=None: {
    "id": id, "fileDate": date, "releaseType": rt, "fileName": name, "gameVersions": list(gv),
    "downloadUrl": url, "fileLength": 1, "hashes": ([{"value": sha, "algo": 1}] if sha else [])}
MODS = {}; FILES = {}
def fake_cf(path, params=None):
    if path.startswith("/v1/mods/") and path.endswith("/files"):
        return {"data": FILES[path.split("/")[3]]}
    if path.startswith("/v1/mods/") and path.count("/") == 3:
        return {"data": MODS[path.split("/")[3]]}
    raise AssertionError(path)
wam.cf_get = fake_cf

# --- resolver: Forever label filter, stable preference, beta fallback ---
MODS["1"] = {"name": "Multi", "allowModDistribution": True}
FILES["1"] = [f(10, "2026-10-01", 1, "Multi-v1.zip", ("1.15.9", "1.60.1")),
              f(11, "2026-10-05", 1, "Multi-v2-classic-only.zip", ("1.15.9",)),
              f(12, "2026-10-09", 2, "Multi-v3-beta.zip")]
c, e = wam.resolve_curseforge("1", False, P)
ok(c["tag"] == "10" and any("newer beta" in n for n in c["notes"]), "stable Forever build preferred; non-Forever file ignored; beta mentioned")
c, e = wam.resolve_curseforge("1", True, P); ok(c["tag"] == "12", "prereleases on -> newest beta")
MODS["2"] = {"name": "AllBeta", "allowModDistribution": True}; FILES["2"] = [f(20, "2026-10-12", 2, "AllBeta-v1-beta.zip")]
c, e = wam.resolve_curseforge("2", False, P); ok(c["tag"] == "20" and any("Only beta" in n for n in c["notes"]), "beta-only addon falls back to newest beta")
MODS["3"] = {"name": "Closed", "allowModDistribution": False}; FILES["3"] = [f(30, "2026-10-12", 1, "Closed.zip")]
c, e = wam.resolve_curseforge("3", False, P); ok(c is not None and "third-party" in c["blocked"], "author opt-out: trackable but flagged blocked")
MODS["4"] = {"name": "NoLink", "allowModDistribution": True}; FILES["4"] = [f(40, "2026-10-12", 1, "NoLink.zip", url=None)]
c, e = wam.resolve_curseforge("4", False, P); ok(c is not None and "no download link" in c["blocked"], "missing download link flagged blocked")
MODS["5"] = {"name": "Classic only", "allowModDistribution": True}; FILES["5"] = [f(50, "2026-10-12", 1, "C.zip", ("1.15.9",))]
c, e = wam.resolve_curseforge("5", False, P); ok(c is None and "WoW Forever" in e, "no Forever-labeled file -> refuse")

# --- full CF install with checksum, then update detection ---
addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
wam.save_config({"installs": [{"label": "Forever", "addons_dir": str(addons)}]})
def mkzip(path, ver):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Multi/Multi.toc", f"## Interface: 16001\n## Title: Multi\n## Version: {ver}\n")
zp = root/"m1.zip"; mkzip(zp, "1")
FILES["1"] = [f(10, "2026-10-01", 1, "Multi-v1.zip", sha=hashlib.sha1(zp.read_bytes()).hexdigest(), url="cf://m1.zip")]
wam.download_file = lambda url, dest, expected_size=None: shutil.copy(root/url[len("cf://"):], dest)
code, out = run(["install", "cf:1", "--yes"])
ok(code == 0 and (addons/"Multi"/"Multi.toc").exists(), "CurseForge install works")
st = wam.load_state()["tracked"][str(addons)]["curseforge:1"]
ok(st["tag"] == "10" and st["name"] == "Multi", "tracked by file id with project name")

# checksum mismatch blocks install and leaves things alone
zp2 = root/"m2.zip"; mkzip(zp2, "2")
FILES["1"] = [f(11, "2026-10-08", 1, "Multi-v2.zip", sha="0"*40, url="cf://m2.zip")]
code, out = run(["install", "cf:1", "--yes"])
ok(code == 1 and "checksum" in out and "Version: 1" in (addons/"Multi"/"Multi.toc").read_text(), "bad checksum refused, old version intact")

# update detection + application via check/update
FILES["1"] = [f(11, "2026-10-08", 1, "Multi-v2.zip", sha=hashlib.sha1(zp2.read_bytes()).hexdigest(), url="cf://m2.zip")]
code, out = run(["check"]); ok("update available" in out and "Multi-v1.zip -> Multi-v2.zip" in out, f"check sees update")
code, out = run(["update", "--yes"]); ok("Version: 2" in (addons/"Multi"/"Multi.toc").read_text(), "update applied")
code, out = run(["check"]); ok("up to date" in out, "then up to date")

# --- version matching ---
vm = wam.version_matches
ok(vm("0.5.4-beta", "GearQuestForever-v0.5.4-beta.zip"), "matches beta tag in filename")
ok(vm("7.0.22", "ProfessionMaster-v7.0.22.zip") and not vm("7.0.2", "ProfessionMaster-v7.0.22.zip"), "no partial-number false match")
ok(vm("340", "Auctionator-340.zip") and not vm("34", "Auctionator-340.zip"), "plain numbers")
ok(not vm("?", "x.zip") and not vm("1.0", "Thing-v1.0.1.zip"), "unknown/near versions don't match")

# --- adopt ---
wam.save_state({"tracked": {}})
for folder, ver, extra in [("GearQuestForever", "0.5.4-beta", "## X-Curse-Project-ID: 100"),
                           ("ProfessionMaster", "7.0.20", "## X-Curse-Project-ID: 200"),
                           ("Manual", "1.0", "## X-Website: https://github.com/me/manual"),
                           ("Lonely", "1.0", "")]:
    (addons/folder).mkdir(exist_ok=True)
    (addons/folder/f"{folder}.toc").write_text(f"## Interface: 16001\n## Title: {folder}\n## Version: {ver}\n{extra}\n")
(addons/"GearQuestForever_DRUID").mkdir(exist_ok=True); (addons/"GearQuestForever_DRUID"/"GearQuestForever_DRUID.toc").write_text("## Interface: 16001\n## Version: 0.3.6-beta\n")
MODS["100"] = {"name": "GearQuest - Forever", "allowModDistribution": True}
FILES["100"] = [f(1001, "2026-10-12", 2, "GearQuestForever-v0.5.4-beta.zip")]
MODS["200"] = {"name": "Profession Master", "allowModDistribution": True}
FILES["200"] = [f(2001, "2026-10-12", 1, "ProfessionMaster-v7.0.22.zip")]
wam.github_releases = lambda repo, limit=10: [{"tag_name": "v1.0", "prerelease": False, "draft": False, "assets": [{"name": "m.zip", "size": 1, "browser_download_url": "x"}]}]
code, out = run(["adopt"])
tr = wam.load_state()["tracked"][str(addons)]
ok(tr["curseforge:100"]["tag"] == "1001" and "GearQuestForever_DRUID" in tr["curseforge:100"]["folders"], "GearQuest adopted as current, with its module folders")
ok(tr["curseforge:200"]["tag"] == "unverified" and "reinstall" in out, "older-than-newest adopted as unverified")
ok(tr["me/manual"]["tag"] == "v1.0", "GitHub-linked addon adopted via its X-Website")
ok("Lonely" not in out and "no known source" in out, "addon with no source is reported, not guessed")
code, out = run(["check"])
ok("ProfessionMaster: update available" in out and "GearQuestForever: up to date" in out and "Manual: up to date" in out, "check after adopt: only the outdated one flagged")

# adopt without a key explains itself
del os.environ["CURSEFORGE_API_KEY"]; wam.CF_KEY_PATH = root/"nokey"; wam.save_state({"tracked": {}})
code, out = run(["adopt"]); ok("cf-key" in out, "no key: CurseForge addons skipped with instructions")
os.environ["CURSEFORGE_API_KEY"] = "test-key"

# --- slug fallback: slug filter refused (403) -> text search + exact slug match ---
calls = []
def fake_search(path, params=None):
    calls.append(dict(params))
    if "slug" in params: raise wam.CFError("refused", 403)
    return {"data": [{"id": 1, "slug": "auctionator-classic"}, {"id": 4, "slug": "auctionator"}]}
wam.cf_get = fake_search
ok(wam.cf_project_id_from_slug("auctionator") == "4" and "searchFilter" in calls[1], "slug lookup falls back to exact-match text search")
print("\nALL TESTS PASSED")
