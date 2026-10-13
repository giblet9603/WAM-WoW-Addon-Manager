import contextlib, io, json, os, sys, tempfile, time
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c"); os.environ["CURSEFORGE_API_KEY"] = "k"
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from wam import catalog, core

def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
def run(argv):
    buf = io.StringIO(); sys.argv = ["wam"] + argv; code = 0
    with contextlib.redirect_stdout(buf):
        try: core.main()
        except SystemExit as e: code = e.code
    return code, buf.getvalue()

good = {"id": "my-addon", "name": "My Addon", "description": "Does things.", "categories": ["fishing"],
        "folders": ["MyAddon", "MyAddon_*"], "sources": {"github": "me/my-addon", "curseforge": 123},
        "forever": "yes", "download": "allowed", "homepage": "https://example.com/x"}
def bad(**changes):
    d = json.loads(json.dumps(good)); d.update(changes); return d
def errs(data, stem="my-addon"): return catalog.validate_entry(data, stem)

# ---------------------------------------------------------------- the format
ok(errs(good) == [], "a good entry validates")
ok(errs({"id": "x", "name": "X", "sources": {"curseforge": 5}, "forever": "unknown"}, "x") == [], "minimal entry (no description/categories/folders) validates")
cases = [
  (bad(nmae="typo"), "unknown field", "typo'd field name is caught"),
  (bad(id="My_Addon"), "'id' must be lowercase", "bad id"),
  (bad(id="other"), "must match the file name", "id must equal the file name"),
  (bad(categories=["fishing", "cooking"]), "'categories'", "unknown category"),
  (bad(folders=["../etc"]), "'folders'", "path-like folder rejected"),
  (bad(folders=["Foo*bar"]), "'folders'", "wildcard only at the end"),
  (bad(folders=[".hidden"]), "'folders'", "dot-folders rejected"),
  (bad(sources={}), "sources needs", "no usable source"),
  (bad(sources={"wago": "abc"}), "sources needs", "wago alone can't be installed from"),
  (bad(sources={"github": "not a repo"}), "owner/repo", "bad github repo"),
  (bad(sources={"curseforge": "6124"}), "numeric", "curseforge id must be a number"),
  (bad(sources={"curseforge": True}), "numeric", "booleans aren't ids"),
  (bad(sources={"curseforge": -4}), "numeric", "negative id"),
  (bad(sources={"gitlab": "a/b", "github": "a/b"}), "unknown source", "unknown source type"),
  (bad(forever="maybe"), "'forever'", "bad forever value"),
  (bad(download="sometimes"), "'download'", "bad download value"),
  (bad(homepage="http://insecure.example"), "https", "http links refused"),
  (bad(featured="yes"), "'featured'", "featured must be a boolean"),
  (bad(description="x" * 201), "200", "description length"),
  ([1, 2], "JSON object", "non-object"),
]
for data, needle, label in cases:
    got = errs(data)
    ok(any(needle in e for e in got), f"{label}: {got[:1]}")

# ---------------------------------------------------------------- loading entries, duplicates, CLI
d = root/"entries"; d.mkdir()
(d/"my-addon.json").write_text(json.dumps(good))
(d/"dup.json").write_text(json.dumps(bad(id="dup", folders=[], sources={"github": "ME/My-Addon"})))
(d/"broken.json").write_text("{not json")
(d/"mismatch.json").write_text(json.dumps(bad(id="something-else")))
entries, problems = catalog.load_entries(d)
joined = "\n".join(problems)
ok("already used by dup.json" in joined and "can't read as JSON" in joined and "must match the file name" in joined, "duplicate repos (case-insensitive), broken JSON and id mismatch all reported")
ok([e["id"] for e in entries] == ["dup", "my-addon"] or [e["id"] for e in entries] == ["my-addon"], "valid entries are still loaded")
buf = io.StringIO()
with contextlib.redirect_stdout(buf): code = catalog.main(["validate", "--dir", str(d)])
ok(code == 1 and "Catalog problems" in buf.getvalue(), "validate exits 1 on problems (fails the pull request)")
with contextlib.redirect_stdout(io.StringIO()): code = catalog.main(["validate"])
ok(code == 0, "the shipped catalog validates")

# ---------------------------------------------------------------- index building
real_entries, real_problems = catalog.load_entries(PROJECT/"catalog"/"addons")
ok(real_problems == [] and len(real_entries) >= 7, f"{len(real_entries)} shipped entries")
built = catalog.build_index(real_entries)
ok("generated" not in built and [a["id"] for a in built["addons"]] == sorted(a["id"] for a in built["addons"]), "index is sorted and unstamped by default (deterministic)")
ok("generated" in catalog.build_index(real_entries, stamp=True), "--stamp adds a time")
bundled_text = (PROJECT/"wam"/"data"/"index.json").read_text()
tmp_out = root/"fresh.json"
with contextlib.redirect_stdout(io.StringIO()): catalog.main(["build", "--out", str(tmp_out)])
ok(tmp_out.read_text() == bundled_text, "the copy bundled with the app is up to date with catalog/addons (run `python -m wam.catalog build --out wam/data/index.json` if this fails)")

warns = []
def fake_get(url):
    if "bad/repo" in url: raise RuntimeError("404")
    return {"stargazers_count": 42, "pushed_at": "2026-10-01T12:00:00Z"}
ents = [dict(real_entries[0], id="a", sources={"github": "good/repo"}), dict(real_entries[0], id="b", sources={"github": "bad/repo"}), dict(real_entries[0], id="c", sources={"curseforge": 9})]
stats = catalog.github_stats(ents, get_json=fake_get, warn=warns.append)
ok(stats == {"a": {"stars": 42, "updated": "2026-10-01"}} and any("bad/repo" in w for w in warns), "star stats: failures are skipped with a warning, CurseForge-only entries ignored")
withstats = catalog.build_index(ents, stats=stats)
ok(withstats["addons"][0]["stars"] == 42 and "stars" not in withstats["addons"][2], "stats merged into the index")

# ---------------------------------------------------------------- untrusted index handling
good_index = catalog.build_index(real_entries)
idx, w = catalog.normalize_index(json.loads(json.dumps(good_index)))
ok(len(idx["addons"]) == len(real_entries) and w == [], "a good index passes unchanged")
for raw, label in [({"schema": 2, "addons": []}, "future schema"), ([], "not an object"), ({"schema": 1}, "no addon list"), ("x", "a string")]:
    try: catalog.normalize_index(raw); ok(False, f"{label} should be rejected")
    except ValueError: ok(True, f"{label}: rejected")
tainted = json.loads(json.dumps(good_index))
tainted["addons"] += ["junk", {"id": "evil", "name": "Evil", "sources": {"github": "../../etc/passwd"}, "forever": "yes"},
                      dict(good_index["addons"][0], id="Bad Id"), dict(good_index["addons"][0], stars="lots")]
idx, w = catalog.normalize_index(tainted)
ok(len(idx["addons"]) == len(real_entries) and len(w) == 4, f"bad entries dropped, good ones kept ({len(w)} dropped)")
ok("../../etc/passwd" not in json.dumps(idx), "a malicious source never survives")

# ---------------------------------------------------------------- search
I = good_index
names = lambda rows: [e["id"] for e in rows]
ok("fishcraft" in names(catalog.search(I, "fishing")), "free text finds by description/category")
ok(names(catalog.search(I, "", category="professions")) == ["profession-master"], "category filter")
ok(names(catalog.search(I, "AUCTION")) == ["auctionator"], "case-insensitive")
ok(names(catalog.search(I, "gear quest")) == ["gearquest-forever"], "every word must match")
ok(catalog.search(I, "zzzz") == [], "no match -> empty")
ok(len(catalog.search(I, "", limit=3)) == 3, "limit")
test = {"schema": 1, "addons": [
  catalog.normalize_entry({"id": "a", "name": "Alpha", "sources": {"github": "x/a"}, "forever": "yes", "stars": 5, "updated": "2026-01-01"}),
  catalog.normalize_entry({"id": "b", "name": "Beta", "sources": {"github": "x/b"}, "forever": "unknown", "stars": 50, "updated": "2026-09-01"}),
  catalog.normalize_entry({"id": "c", "name": "Gamma", "sources": {"curseforge": 3}, "forever": "yes"}),
  catalog.normalize_entry({"id": "d", "name": "Delta", "description": "mentions alpha", "sources": {"curseforge": 4}, "forever": "yes", "featured": True})]}
ok(names(catalog.search(test, sort="popular")) == ["d", "b", "a", "c"], "popular: featured first, then stars, unknown last")
ok(names(catalog.search(test, sort="updated")) == ["b", "a", "d", "c"], "updated: newest first, undated last (by name)")
ok(names(catalog.search(test, sort="name")) == ["a", "b", "d", "c"], "name sort")
ok(names(catalog.search(test, "alpha")) == ["a", "d"], "best match: name hit before description hit")
ok(names(catalog.search(test, verified_only=True, sort="name")) == ["a", "d", "c"], "verified-only drops 'unknown'")

# ---------------------------------------------------------------- matching installed folders
ok(catalog.match_installed(I, ["GearQuestForever", "GearQuestForever_DRUID", "GearQuestForever_MAGE"])["id"] == "gearquest-forever", "wildcard matches module folders")
ok(catalog.match_installed(I, ["gearquestforever"])["id"] == "gearquest-forever", "case-insensitive folder match")
ok(catalog.match_installed(I, ["WaypointTracker", "WaypointTracker_Data"])["id"] == "waypoint-tracker", "multi-folder exact match")
ok(catalog.match_installed(I, ["SomethingElse"]) is None, "no false matches")
ok(catalog.source_key(next(e for e in real_entries if e["id"] == "shortest-path-forever")) == "cjber/shortest-path-forever", "GitHub preferred when both exist (no key needed)")
ok(catalog.source_key(next(e for e in real_entries if e["id"] == "auctionator")) == "curseforge:6124", "CurseForge key otherwise")

# ---------------------------------------------------------------- loading: online / cache / bundled fallbacks
class Resp:
    def __init__(s, body): s.body = body if isinstance(body, bytes) else body.encode()
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s, n=-1): return s.body[:n] if n >= 0 else s.body
NET = {"calls": 0, "body": json.dumps(good_index), "fail": False}
def fake_open(url, timeout=60):
    NET["calls"] += 1
    if NET["fail"]: raise OSError("offline")
    return Resp(NET["body"])
core._open = fake_open
URL = "https://example.github.io/wam/index.json"
idx, origin = core.load_catalog({"catalog_url": ""}); ok(origin == "bundled" and NET["calls"] == 0, "no URL: bundled copy, no network")
idx, origin = core.load_catalog({"catalog_url": "http://insecure.example/i.json"}); ok(origin == "bundled" and NET["calls"] == 0, "non-https URL is never contacted")
idx, origin = core.load_catalog({"catalog_url": URL}); ok(origin == "online" and NET["calls"] == 1 and core.CATALOG_CACHE.exists(), "first load downloads and caches")
idx, origin = core.load_catalog({"catalog_url": URL}); ok(origin == "cached" and NET["calls"] == 1, "second load within a day uses the cache (no network)")
idx, origin = core.load_catalog({"catalog_url": URL}, refresh=True); ok(origin == "online" and NET["calls"] == 2, "refresh forces a download")
old = time.time() - 3 * 86400; os.utime(core.CATALOG_CACHE, (old, old))
NET["fail"] = True
idx, origin = core.load_catalog({"catalog_url": URL}); ok(origin.startswith("cached") and "couldn't reach" in origin and len(idx["addons"]) == len(real_entries), "stale cache + offline: still works from the cache")
cache_before = core.CATALOG_CACHE.read_text()
NET["fail"] = False; NET["body"] = json.dumps({"schema": 99, "addons": []})
idx, origin = core.load_catalog({"catalog_url": URL}, refresh=True); ok(origin.startswith("cached") and core.CATALOG_CACHE.read_text() == cache_before, "garbage from the server never overwrites a good cache")
NET["body"] = "x" * (catalog.MAX_INDEX_BYTES + 10)
idx, origin = core.load_catalog({"catalog_url": URL}, refresh=True); ok(origin.startswith("cached"), "oversized response refused")
core.CATALOG_CACHE.unlink()
idx, origin = core.load_catalog({"catalog_url": URL}, refresh=True); ok(origin == "bundled" and len(idx["addons"]) == len(real_entries), "no cache + bad server: falls back to the bundled copy")
NET["body"] = json.dumps(tainted)
idx, origin = core.load_catalog({"catalog_url": URL}, refresh=True); ok(origin == "online" and "../../etc/passwd" not in json.dumps(idx) and "../../etc/passwd" not in core.CATALOG_CACHE.read_text(), "malicious entries are stripped before they're used or cached")

# ---------------------------------------------------------------- result shaping
rows = core.catalog_results(I, "auction")
ok(rows[0]["key"] == "curseforge:6124" and rows[0]["source"] == "CurseForge (manual)" and rows[0]["forever"] == "yes", f"result shape: {rows[0]['source']}")
ok(core.catalog_results(I, "", "fishing")[0]["source"] == "GitHub", "GitHub-sourced results labeled")
ok(core.CATEGORY_IDS["Fishing"] == "fishing" and core.SEARCH_CATEGORIES["Fishing"] == "fishing", "category label/id/keyword tables agree")

# ---------------------------------------------------------------- adopt uses the catalog
addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
def mk(folder, title, ver):
    (addons/folder).mkdir(exist_ok=True)
    (addons/folder/f"{folder}.toc").write_text(f"## Interface: 16001\n## Title: {title}\n## Version: {ver}\n")
mk("GearQuestForever", "GearQuest Forever", "0.5.4-beta"); mk("GearQuestForever_DRUID", "GearQuest Druid", "0.3.6")
mk("Auctionator", "Auctionator", "340"); mk("Mystery", "Mystery", "1.0"); mk("WaypointTracker", "Waypoint Tracker", "1.1.1")
core.add_install_path(str(root/"WoW")); install = core.load_config()["installs"][0]
f = lambda i, n, **k: dict({"id": i, "fileDate": "2026-10-12", "releaseType": 2, "fileName": n, "gameVersions": ["1.60.1"], "downloadUrl": "u", "hashes": []}, **k)
def fake_cf(path, params=None):
    if path == "/v1/mods/1698950": return {"data": {"name": "GearQuest", "allowModDistribution": True}}
    if path == "/v1/mods/1698950/files": return {"data": [f(5, "GearQuestForever-v0.5.4-beta.zip")]}
    if path == "/v1/mods/6124": return {"data": {"name": "Auctionator", "allowModDistribution": False, "links": {"websiteUrl": "https://cf/a"}}}
    if path == "/v1/mods/6124/files": return {"data": [f(9, "Auctionator-340.zip", releaseType=1, downloadUrl=None)]}
    raise core.CFError("nope", 404)
core.cf_get = fake_cf
core.github_releases = lambda repo, limit=10: [{"tag_name": "v1.1.1", "prerelease": False, "draft": False, "assets": [{"name": "w.zip", "size": 1, "browser_download_url": "u"}]}]
core.load_catalog = lambda settings=None, refresh=False: (I, "test")
lines = []
adopted, skipped = core.adopt_installed(install, log=lines.append)
out = "\n".join(lines)
tr = core.load_state()["tracked"][install["addons_dir"]]
ok(set(tr) == {"curseforge:1698950", "curseforge:6124", "me/never" if False else "sanjaygbhat/wowforever-waypoint-tracker"}, f"adopted via catalog: {sorted(tr)}")
ok(sorted(tr["curseforge:1698950"]["folders"]) == ["GearQuestForever", "GearQuestForever_DRUID"], "module folders grouped under one catalog match")
ok(tr["curseforge:1698950"]["tag"] == "5" and "matched in the catalog" in out, "version confirmed against the catalog's source")
ok(skipped == 1 and "Mystery" not in out.replace("1 addon(s) have no known source", ""), "addon the catalog doesn't know is reported as unsourced, not guessed")
os.environ.pop("CURSEFORGE_API_KEY"); core.CF_KEY_PATH = root/"nokey"; core.save_state({"tracked": {}})
lines.clear(); core.adopt_installed(install, log=lines.append)
ok(any("catalog says it lives on CurseForge" in l and "cf-key" in l for l in lines), "CurseForge-only match without a key: explains what to do")
os.environ["CURSEFORGE_API_KEY"] = "k"

# ---------------------------------------------------------------- command line
core.load_catalog = lambda settings=None, refresh=False: (I, "bundled")
code, out = run(["search", "fishing"]); ok(code == 0 and "github" not in out.lower().replace("github.com", "") or "Fishcraft" in out, "wam search uses the catalog by default")
ok("critolis/Fishcraft" in out and "Forever: yes" in out, "catalog search output")
code, out = run(["search", "--category", "professions"]); ok("curseforge:574050" in out and "Fishcraft" not in out, "--category filter")
code, out = run(["search", "zzzz"]); ok("--github" in out, "empty result points at --github")
code, out = run(["settings", "catalog-url", "http://nope"]); ok(code == 1 and "https://" in out, "catalog-url must be https")
code, out = run(["settings", "catalog-url", URL]); ok(code == 0 and core.get_settings(core.load_config())["catalog_url"] == URL, "catalog-url saved")
code, out = run(["settings", "catalog-url", "none"]); ok(core.get_settings(core.load_config())["catalog_url"] == "", "catalog-url none clears it")
core.load_catalog = lambda settings=None, refresh=False: (I, "online" if refresh else "cached")
code, out = run(["catalog"]); ok("7 addon(s)" in out and "No online address" in out, "wam catalog explains where it's reading from")

import re
for f in (PROJECT/"wam"/"core.py", PROJECT/"wam"/"tui.py", PROJECT/"wam"/"catalog.py"):
    ok(not re.search(r'\\\\n', f.read_text()), f"no double-escaped newlines in {f.name}")
print("\nALL TESTS PASSED")
