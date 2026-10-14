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

# default source follows the key: no key -> catalog, key present -> CurseForge (browsing works with an empty box)
core.cf_get = fake_cf_get
async def default_source(has_key):
    core.get_cf_key = lambda: "k" if has_key else None
    app = tui.WamApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
        await pilot.press("d"); await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
        scr = app.screen
        return scr.query_one("#source").value, scr.query_one("#results").row_count
v, n = asyncio.run(default_source(False)); ok(v == "catalog", "no key: Discover opens on the catalog")
v, n = asyncio.run(default_source(True)); ok(v == "curseforge" and n == 2, f"key saved: Discover opens on CurseForge and lists popular addons ({n})")

# ---- details screen with the page link, and the new column order
import webbrowser
OPENED = []
webbrowser.open = lambda url: OPENED.append(url) or True
core.get_cf_key = lambda: "k"
LONG = "A very long description that goes on and on about everything this addon can do for you " * 3
core.cf_get = lambda path, params=None: {"data": [
    {"id": 6124, "name": "Auctionator With A Really Very Long Name Indeed", "summary": LONG, "slug": "auctionator",
     "dateModified": "2026-10-02T10:00:00Z", "links": {"websiteUrl": "https://www.curseforge.com/wow/addons/auctionator"},
     "latestFilesIndexes": [{"gameVersion": "1.60.1"}]},
    {"id": 7, "name": "Slugless", "summary": "x", "dateModified": "2026-01-01", "links": {}, "latestFilesIndexes": []},
    {"id": 8, "name": "Odd Link", "summary": "x", "dateModified": "2026-01-01", "links": {"websiteUrl": "javascript:alert(1)"}, "latestFilesIndexes": []}]}
rows = core.search_curseforge("x")
ok(rows[1]["url"] == "https://www.curseforge.com/projects/7", "an addon with no page link still gets a CurseForge link")

async def details():
    app = tui.WamApp()
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
        await pilot.press("d"); await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
        scr = app.screen
        t = scr.query_one("#results")
        heads = [str(c.label) for c in t.columns.values()]
        ok(heads == ["Addon", "Description", "Forever", "Source", "Stars", "Updated"], f"column order: {heads}")
        name0, desc0 = str(t.get_row_at(0)[0]), str(t.get_row_at(0)[1])
        ok(len(name0) <= 26 and name0.endswith("…") and len(desc0) <= 40 and desc0.endswith("…"), "long names and descriptions are shortened in the table")
        t.focus(); await pilot.press("enter"); await pilot.pause()
        ok(type(app.screen).__name__ == "AddonInfoScreen", "Enter opens the details")
        body = str(app.screen.query_one(".dialog-body").render())
        ok("https://www.curseforge.com/wow/addons/auctionator" in body and LONG[:60] in body, "details show the page link and the full description")
        await pilot.press("o"); await pilot.pause()
        ok(OPENED == ["https://www.curseforge.com/wow/addons/auctionator"], "o opens the page in a browser")
        await pilot.press("escape"); await pilot.pause()
        ok(type(app.screen).__name__ == "DiscoverScreen", "Esc goes back to the results")
        t.move_cursor(row=2); await pilot.press("enter"); await pilot.pause()
        OPENED.clear(); await pilot.press("o"); await pilot.pause()
        ok(OPENED == [] and "no page link" in str(app.screen.query_one(".dialog-body").render()), "a non-web link is never opened")
        await pilot.press("escape"); await pilot.pause()
asyncio.run(details())
