import os, sys, tempfile, importlib.util, io, contextlib, stat
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
os.environ.pop("CURSEFORGE_API_KEY", None)
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

# no key -> clear message, no crash
code, out = run(["cf-files", "123"]); ok(code == 1 and "cf-key" in out, "no key: tells you what to do")

# key saved privately, never echoed
wam.getpass.getpass = lambda prompt: "SECRET-KEY-123"
wam.cf_get = lambda path, params=None: {"data": {"name": "World of Warcraft"}}
code, out = run(["cf-key"])
mode = stat.S_IMODE(wam.CF_KEY_PATH.stat().st_mode)
ok(code == 0 and mode == 0o600, f"key file permissions are {oct(mode)}")
ok("SECRET-KEY-123" not in out, "key never printed")
ok(wam.get_cf_key() == "SECRET-KEY-123", "key read back")
os.environ["CURSEFORGE_API_KEY"] = "FROM-ENV"; ok(wam.get_cf_key() == "FROM-ENV", "env var overrides file"); del os.environ["CURSEFORGE_API_KEY"]

# probe output, slug lookup, blocked downloads, distribution flag
calls = []
def fake(path, params=None):
    calls.append((path, params))
    if path == "/v1/mods/search": return {"data": [{"id": 321}]}
    if path == "/v1/mods/321": return {"data": {"name": "Auctionator", "slug": "auctionator", "allowModDistribution": False}}
    if path == "/v1/mods/321/files":
        return {"data": [
            {"id": 9, "fileDate": "2026-10-02T09:21:00Z", "releaseType": 1, "fileName": "Auctionator-340.zip",
             "downloadUrl": "https://edge.forgecdn.net/x.zip", "gameVersions": ["1.60.1"]},
            {"id": 8, "fileDate": "2026-09-01T00:00:00Z", "releaseType": 2, "fileName": "Auctionator-339.zip",
             "downloadUrl": None, "gameVersions": []}]}
wam.cf_get = fake
code, out = run(["cf-files", "auctionator"])
ok(code == 0 and calls[0] == ("/v1/mods/search", {"gameId": 1, "slug": "auctionator"}), "slug resolved via search")
ok("OFF third-party distribution" in out, "distribution-off warning shown")
ok("1.60.1" in out and "[no third-party download link]" in out and "beta" in out, "file labels + missing link flagged")

# errors surface cleanly
def boom(path, params=None): raise RuntimeError("CurseForge rejected the API key. Re-run: python3 wam.py cf-key")
wam.cf_get = boom
code, out = run(["cf-files", "1"]); ok(code == 1 and "rejected" in out, "API errors shown cleanly")
print("\nALL TESTS PASSED")
