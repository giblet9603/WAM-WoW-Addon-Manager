import asyncio, os, sys, tempfile
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core, tui

addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
(addons/"Demo").mkdir(); (addons/"Demo"/"Demo.toc").write_text("## Interface: 16001\n## Title: Demo\n## Version: 1.0\n")
core.add_install_path(str(root/"WoW"))
core.load_catalog = lambda settings=None, refresh=False: ({"schema": 1, "categories": {}, "addons": []}, "test")

def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)

MODS = [
    {"id": 6124, "name": "Auctionator", "summary": "Auction house helper", "dateModified": "2026-10-02T10:00:00Z",
     "allowModDistribution": False, "links": {"websiteUrl": "https://www.curseforge.com/wow/addons/auctionator"},
     "latestFilesIndexes": [{"gameVersion": "1.60.1"}]},
    {"id": 99, "name": "Old Retail Thing", "summary": "Retail only", "dateModified": "2025-01-01T00:00:00Z",
     "links": {}, "latestFilesIndexes": [{"gameVersion": "11.0.2"}]},
]
calls = []
def fake_cf_get(path, params=None):
    calls.append((path, params)); return {"data": MODS}
core.cf_get = fake_cf_get

rows = core.search_curseforge("auction", "popular")
ok(calls[0][0] == "/v1/mods/search" and calls[0][1]["searchFilter"] == "auction" and calls[0][1]["sortField"] == 2, "queries CurseForge's search with the text and sort")
ok([r["key"] for r in rows] == ["curseforge:6124", "curseforge:99"], "returns results keyed so install-by-ID works")
ok(rows[0]["source"] == "CurseForge (manual)" and rows[0]["forever"] == "yes" and rows[1]["forever"] == "-", "marks manual-download addons and Forever claims")
ok([r["key"] for r in core.search_curseforge("x", forever_only=True)] == ["curseforge:6124"], "Forever only drops addons with no Forever build")

async def scenario():
    app = tui.WamApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
        await pilot.press("d"); await pilot.pause()
        scr = app.screen
        ok(type(scr).__name__ == "DiscoverScreen", "Discover opens")
        scr.query_one("#source").value = "curseforge"; await pilot.pause()
        scr.query_one("#q").value = "auction"
        scr.run_search(); await app.workers.wait_for_complete(); await pilot.pause()
        t = scr.query_one("#results")
        names = [str(t.get_row_at(i)[0]) for i in range(t.row_count)]
        ok(names == ["Auctionator", "Old Retail Thing"], f"CurseForge results shown in Discover: {names}")
        ok("2 result(s)" in str(scr.query_one("#info").render()), "info line summarises results")
asyncio.run(scenario())

# no key -> friendly error, not a crash
def no_key(path, params=None): raise core.CFError("No CurseForge API key yet. Run: wam cf-key")
core.cf_get = no_key
async def nokey():
    app = tui.WamApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause(); await pilot.press("d"); await pilot.pause()
        scr = app.screen
        scr.query_one("#source").value = "curseforge"; await pilot.pause()
        scr.query_one("#q").value = "auction"; scr.run_search()
        await app.workers.wait_for_complete(); await pilot.pause()
        ok("wam cf-key" in str(scr.query_one("#info").render()), "no key shows the 'wam cf-key' hint")
asyncio.run(nokey())
