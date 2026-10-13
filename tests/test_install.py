import io, json, os, sys, tempfile, zipfile, shutil, importlib.util
from pathlib import Path

root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root / "data")
os.environ["XDG_CONFIG_HOME"] = str(root / "cfg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core as wam

addons = root / "WoW" / "_classic_beta_" / "Interface" / "AddOns"
addons.mkdir(parents=True)
install = {"label": "WoW Forever (beta)", "addons_dir": str(addons)}

def toc(name, ver="1.0", iface=16001):
    return f"## Interface: {iface}\n## Title: {name}\n## Version: {ver}\n"

def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)

ZIPS = {}
def fake_release(tag, zipname, assets_extra=(), pre=False):
    assets = [{"name": zipname, "size": ZIPS[zipname].stat().st_size,
               "browser_download_url": f"fake://{zipname}"}]
    assets += list(assets_extra)
    return {"tag_name": tag, "prerelease": pre, "draft": False,
            "published_at": "2026-10-10T00:00:00Z", "assets": assets}

RELEASES = {}
wam.github_releases = lambda repo, limit=10: RELEASES[repo]
wam.download_file = lambda url, dest, expected_size=None: shutil.copy(ZIPS[url[len("fake://"):]], dest)
wam.fetch_json = lambda url: JSON[url]
JSON = {}

def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)

def listing():
    return sorted(p.name for p in addons.iterdir())

# ---- 1. fresh install over an existing manual copy -> backup made ----
(addons / "Foo").mkdir(); (addons / "Foo" / "Foo.toc").write_text(toc("Foo old", "0.1")); (addons / "Foo" / "mysettings.txt").write_text("keep me in backup")
(addons / "Unrelated").mkdir(); (addons / "Unrelated" / "Unrelated.toc").write_text(toc("Unrelated"))
ZIPS["foo-1.zip"] = root / "foo-1.zip"
make_zip(ZIPS["foo-1.zip"], {"Foo/Foo.toc": toc("Foo", "1.0"), "Foo/core.lua": "x", "Foo_Data/Foo_Data.toc": toc("Foo Data")})
RELEASES["me/foo"] = [fake_release("v1.0", "foo-1.zip")]
r = wam.do_install("me/foo", install, assume_yes=True)
ok(r, "install succeeds")
ok(listing() == ["Foo", "Foo_Data", "Unrelated"], f"folders present: {listing()}")
ok((addons / "Foo" / "core.lua").exists(), "new files in place")
bk = list(wam.BACKUP_DIR.rglob("mysettings.txt"))
ok(len(bk) == 1 and bk[0].read_text() == "keep me in backup", "old copy preserved in backup")
ok(wam.load_state()["tracked"][install["addons_dir"]]["me/foo"]["tag"] == "v1.0", "state records tag")
ok(not [p for p in addons.parent.iterdir() if p.name.startswith(".wam-")], "temp dir cleaned up")

# ---- 2. update drops a folder -> stale one backed up, not left behind ----
ZIPS["foo-2.zip"] = root / "foo-2.zip"
make_zip(ZIPS["foo-2.zip"], {"Foo/Foo.toc": toc("Foo", "2.0"), "Foo/core.lua": "y"})
RELEASES["me/foo"] = [fake_release("v2.0", "foo-2.zip")]
ok(wam.do_install("me/foo", install, assume_yes=True), "update succeeds")
ok(listing() == ["Foo", "Unrelated"], f"stale Foo_Data removed from AddOns: {listing()}")
ok((addons / "Foo" / "core.lua").read_text() == "y", "updated content")
ok(any(p.name == "Foo_Data" for p in wam.BACKUP_DIR.rglob("Foo_Data")), "stale folder kept in backup")

# ---- 3. malicious zip (path traversal) is refused, nothing changes ----
before = {p.name: (p / next(p.glob("*.toc")).name).read_text() for p in addons.iterdir()}
ZIPS["evil.zip"] = root / "evil.zip"
make_zip(ZIPS["evil.zip"], {"Evil/Evil.toc": toc("Evil"), "../../../escaped.txt": "pwned"})
RELEASES["me/evil"] = [fake_release("v1", "evil.zip")]
ok(not wam.do_install("me/evil", install, assume_yes=True), "evil zip refused")
ok(not (root / "escaped.txt").exists() and not (addons.parent.parent / "escaped.txt").exists(), "nothing written outside")
ok(listing() == ["Foo", "Unrelated"], "AddOns untouched by evil zip")

# ---- 4. zip wrapped in an extra outer folder still works ----
ZIPS["wrapped.zip"] = root / "wrapped.zip"
make_zip(ZIPS["wrapped.zip"], {"Wrapper-1.0/Bar/Bar.toc": toc("Bar"), "Wrapper-1.0/README.md": "hi"})
RELEASES["me/bar"] = [fake_release("v1", "wrapped.zip")]
ok(wam.do_install("me/bar", install, assume_yes=True) and (addons / "Bar" / "Bar.toc").exists(), "wrapped zip unpacked correctly")
ok(not (addons / "Wrapper-1.0").exists(), "wrapper folder not installed")

# ---- 5. failure midway -> full rollback ----
ZIPS["multi.zip"] = root / "multi.zip"
make_zip(ZIPS["multi.zip"], {"Foo/Foo.toc": toc("Foo", "3.0"), "Foo/core.lua": "z", "Zed/Zed.toc": toc("Zed")})
RELEASES["me/multi"] = [fake_release("v3", "multi.zip")]
real_move, calls = shutil.move, {"n": 0}
def flaky(src, dst):
    calls["n"] += 1
    if calls["n"] == 3:        # blow up partway: after Foo was backed up and replaced
        raise OSError("disk exploded")
    return real_move(src, dst)
wam.shutil.move = flaky
import io as _io, contextlib as _cl
buf = _io.StringIO()
with _cl.redirect_stdout(buf):
    res = wam.do_install("me/multi", install, assume_yes=True)
ok(not res and "disk exploded" in buf.getvalue(), "mid-install failure reported (the simulated one, not some other error)")
wam.shutil.move = real_move
ok((addons / "Foo" / "core.lua").read_text() == "y", "Foo rolled back to previous good version")
ok("Zed" not in listing(), "half-installed Zed not left behind")
ok("me/multi" not in wam.load_state()["tracked"][install["addons_dir"]], "failed install not recorded")

nested = [p for p in wam.BACKUP_DIR.rglob("*") if p.is_dir() and p.parent.name == p.name]
ok(not nested, f"no nested Foo/Foo backups: {nested}")
ok(len({p.parent for p in wam.BACKUP_DIR.glob("*/Foo*")}) >= 2, "each install got its own backup folder")

# ---- 6. dry run changes nothing ----
snap = listing()
ok(wam.do_install("me/multi", install, dry_run=True) and listing() == snap, "dry run changes nothing")

# ---- 7. release.json picks the zip built for 16001 ----
ZIPS["a-classic.zip"] = root / "a-classic.zip"; ZIPS["a-forever.zip"] = root / "a-forever.zip"
make_zip(ZIPS["a-classic.zip"], {"Qux/Qux.toc": toc("Qux", "classic", 11509)})
make_zip(ZIPS["a-forever.zip"], {"Qux/Qux.toc": toc("Qux", "forever", 16001)})
rj = {"name": "release.json", "size": 10, "browser_download_url": "fake://release.json"}
JSON["fake://release.json"] = {"releases": [
    {"filename": "a-classic.zip", "metadata": [{"flavor": "classic", "interface": 11509}]},
    {"filename": "a-forever.zip", "metadata": [{"flavor": "classic", "interface": 16001}]}]}
rel = fake_release("v1", "a-classic.zip", [rj]); rel["assets"].append({"name": "a-forever.zip", "size": ZIPS["a-forever.zip"].stat().st_size, "browser_download_url": "fake://a-forever.zip"})
asset, warn = wam.pick_zip_asset(rel)
ok(asset["name"] == "a-forever.zip" and warn is None, "release.json selects the Forever zip")

# ---- 8. ambiguous zips w/o metadata -> refuse to guess ----
rel2 = fake_release("v1", "a-classic.zip"); rel2["assets"].append({"name": "a-forever.zip", "size": 1, "browser_download_url": "x"})
asset, warn = wam.pick_zip_asset(rel2)
ok(asset is None, "ambiguous zips refused instead of guessed")

# ---- 9. prerelease handling ----
rels = [fake_release("v2-beta", "foo-2.zip", pre=True), fake_release("v1", "foo-1.zip")]
rel, note = wam.pick_release(rels, False)
ok(rel["tag_name"] == "v1" and "pre-release" in note, "stable preferred, pre-release mentioned")
ok(wam.pick_release(rels, True)[0]["tag_name"] == "v2-beta", "--prerelease takes newest")
print("\nALL TESTS PASSED")
