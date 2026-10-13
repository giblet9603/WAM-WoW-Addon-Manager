import os, sys, tempfile, importlib.util, io, contextlib
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c"); os.environ["CURSEFORGE_API_KEY"] = "k"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core as wam
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
(addons/"Auctionator").mkdir(); (addons/"Auctionator"/"Auctionator.toc").write_text("## Interface: 16001\n## Title: Auctionator\n## Version: 340\n")
(addons/"Open").mkdir(); (addons/"Open"/"Open.toc").write_text("## Interface: 16001\n## Title: Open\n## Version: 1\n")
wam.save_config({"installs": [{"label": "Forever", "addons_dir": str(addons)}], "settings": {"update_mode": "notify"}})
fl = lambda id, name, url: {"id": id, "fileDate": "2026-10-02", "releaseType": 1, "fileName": name, "gameVersions": ["1.60.1"], "downloadUrl": url, "hashes": []}
STATE = {"auc_files": [fl(1, "Auctionator-340.zip", None)], "open_files": [fl(10, "Open-1.zip", "u")]}
def fake(path, params=None):
    if path == "/v1/mods/6124": return {"data": {"name": "Auctionator", "slug": "auctionator", "allowModDistribution": False,
                                                 "links": {"websiteUrl": "https://www.curseforge.com/wow/addons/auctionator"}}}
    if path == "/v1/mods/6124/files": return {"data": STATE["auc_files"]}
    if path == "/v1/mods/7": return {"data": {"name": "Open", "slug": "open", "allowModDistribution": True}}
    if path == "/v1/mods/7/files": return {"data": STATE["open_files"]}
wam.cf_get = fake

code, out = run(["link", "Auctionator", "cf:6124"])
ok(code == 0 and "up to date" in out, "opted-out addon can now be tracked (was refused before)")
run(["link", "Open", "cf:7"])

code, out = run(["install", "cf:6124", "--yes"])
ok(code == 1 and "won't download" in out and "curseforge.com/wow/addons/auctionator" in out, "install of an opted-out addon refused, with the page to get it from")
ok(not (addons/"Auctionator"/"core.lua").exists(), "nothing touched")

# a new Auctionator version appears -> reported, never downloaded
STATE["auc_files"] = [fl(2, "Auctionator-341.zip", None)]
code, out = run(["check"])
ok("Auctionator: update available" in out and "manual download" in out and "Open: up to date" in out, "check flags it as manual-download")
calls = []; wam.download_file = lambda *a, **k: calls.append(a)
code, out = run(["update", "--yes"])
ok(not calls and "turned off third-party downloads" in out and "Get it from" in out, "update skips it and says where to get it")

# notify mode wording; auto mode installs only the downloadable ones, announces the manual one
notes = []; wam.notify = lambda t, b: notes.append((t, b))
run(["auto-check"]); ok(len(notes) == 1 and "Auctionator (manual download)" in notes[0][1], "notify mode marks it manual")
notes.clear(); run(["settings", "update-mode", "auto"]); wam.game_running = lambda: False
installed = []; wam.do_install = lambda key, install, **k: installed.append(key) or True
STATE["open_files"] = [fl(11, "Open-2.zip", "u")]
run(["auto-check"])
ok(installed == ["curseforge:7"], f"auto mode installs only downloadable updates: {installed}")
ok(any(t == "Manual download needed" and "Auctionator" in b for t, b in notes), "auto mode announces the manual one")
print("\nALL TESTS PASSED")
