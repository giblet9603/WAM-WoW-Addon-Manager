import asyncio, os, sys, tempfile
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import catalog, core, tui

def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)

addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
for folder, title in (("WaypointIsh", "Waypoint Ish"), ("Unknown", "Totally Unknown")):
    (addons/folder).mkdir(); (addons/folder/f"{folder}.toc").write_text(f"## Interface: 16001\n## Title: {title}\n## Version: 1.0\n")
core.add_install_path(str(root/"WoW"))

def entry(i, n, **k): return catalog.normalize_entry(dict({"id": i, "name": n, "sources": {"github": f"a/{i}"}, "forever": "yes"}, **k))
INDEX = {"schema": 1, "categories": {}, "addons": [
    entry("fishy", "Fishy Catalog Addon", description="Catches fish", categories=["fishing"], stars=12, updated="2026-10-01"),
    entry("manual-thing", "Manual Thing", sources={"curseforge": 77}, download="manual", forever="unknown"),
    entry("waypoint-ish", "Waypoint Ish", folders=["WaypointIsh"], stars=3, updated="2026-05-05")]}
core.load_catalog = lambda settings=None, refresh=False: (INDEX, "test")
cand = lambda **k: dict({"name": "X", "tag": "9", "label": "9.9", "asset": "x.zip", "size": 10, "url": "u", "prerelease": False, "notes": [], "blocked": None, "page": "https://p"}, **k)
core.resolve_source = lambda key, pre, settings=None: (cand(name=key, blocked="the author has turned off third-party downloads") if key == "curseforge:77" else cand(name=key), None)

async def settle(p): await p.pause(); await p.app.workers.wait_for_complete(); await p.pause()
name = lambda app: type(app.screen).__name__
text = lambda w: str(w.render())
def col(app, i, tid="#results"):
    t = app.screen.query_one(tid); return [str(t.get_row_at(r)[i]) for r in range(t.row_count)]

async def scenario():
    app = tui.WamApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot)
        t = app.screen.query_one("#addons")
        status = {str(t.get_row_at(i)[0]): str(t.get_row_at(i)[2]) for i in range(t.row_count)}
        ok(status == {"Totally Unknown": "· no source", "Waypoint Ish": "· in catalog"}, f"main list hints at what the catalog recognises: {status}")
        t.move_cursor(row=t.get_row_index("WaypointIsh")); await settle(pilot)
        detail = text(app.screen.query_one("#detail"))
        ok("catalog knows it" in detail and "Waypoint Ish" in detail and "a/waypoint-ish" in detail and "Press o" in detail, "detail panel names the catalog match and how to use it")

        # ---- o: auto-link
        calls = []
        def fake_adopt(inst, log=print):
            calls.append(inst["label"]); log("- Waypoint Ish: now tracked via a/waypoint-ish  [matched in the catalog]"); log("Tracked 1 addon(s)."); return 1, 1
        core.adopt_installed = fake_adopt
        await pilot.press("o"); await settle(pilot)
        ok(name(app) == "ProgressScreen" and calls == ["WoW Forever (beta)"], "o runs auto-link with a progress window")
        ok("matched in the catalog" in text(app.screen.query_one("#job-log")) or app.screen.done, "its log is shown")
        await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "MainScreen", "closing returns to the list")

        # ---- Discover: catalog is the default
        await pilot.press("d"); await settle(pilot)
        ok(name(app) == "DiscoverScreen" and app.screen.query_one("#source").value == "catalog", "Discover opens on the catalog")
        ok(col(app, 0) == ["Fishy Catalog Addon", "Manual Thing", "Waypoint Ish"], f"empty query browses everything: {col(app, 0)}")
        ok(col(app, 2) == ["yes", "?", "yes"] and col(app, 3) == ["GitHub", "CurseForge (manual)", "GitHub"], "Forever and Source columns come from the catalog entries")
        ok(col(app, 4) == ["12", "", "3"] and col(app, 5)[0] == "2026-10-01", "stars and dates shown when known, blank when not")
        ok("3 addon(s) from the test catalog" in text(app.screen.query_one("#info")), "info line says where results came from")

        app.screen.query_one("#q").value = "fish"; await pilot.press("enter"); await settle(pilot)
        ok(col(app, 0) == ["Fishy Catalog Addon"], "text search")
        app.screen.query_one("#q").value = ""; app.screen.query_one("#cat").value = "Fishing"; await settle(pilot)
        ok(col(app, 0) == ["Fishy Catalog Addon"], "category filter")
        app.screen.query_one("#cat").value = "Any category"; await settle(pilot)
        app.screen.query_one("#sort").value = "popular"; await settle(pilot)
        ok(col(app, 0) == ["Fishy Catalog Addon", "Waypoint Ish", "Manual Thing"], f"popular sort: {col(app, 0)}")
        app.screen.query_one("#sort").value = "updated"; await settle(pilot)
        ok(col(app, 0) == ["Fishy Catalog Addon", "Waypoint Ish", "Manual Thing"], "updated sort")
        app.screen.query_one("#forever").value = True; await settle(pilot)
        ok("Manual Thing" not in col(app, 0), "Forever-only drops entries that aren't confirmed")
        app.screen.query_one("#forever").value = False; await settle(pilot)

        # ---- installing from the catalog
        table = app.screen.query_one("#results"); table.focus()
        table.move_cursor(row=table.get_row_index("curseforge:77")); await settle(pilot)
        await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "AddonInfoScreen" and "third-party" in text(app.screen.query_one(".dialog-body")), "a manual-download entry's details say it can't be installed")
        await pilot.press("i"); await settle(pilot)
        ok(name(app) == "MessageScreen" and "third-party" in text(app.screen.query_one(".dialog-body")), "and trying to install it explains instead of installing")
        await pilot.press("escape"); await settle(pilot)
        table.move_cursor(row=table.get_row_index("a/fishy")); await settle(pilot)
        await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "AddonInfoScreen", "Enter on a catalog result shows its details")
        await pilot.press("i"); await settle(pilot)
        ok(name(app) == "ConfirmScreen" and "a/fishy" in text(app.screen.query_one(".dialog-body")), "Install from the details starts the install flow")
        await pilot.press("n"); await settle(pilot)

        # ---- switching to GitHub search
        app.screen.query_one("#source").value = "github"; await settle(pilot)
        ok(app.screen.query_one("#results").row_count == 0 and "Type something" in text(app.screen.query_one("#info")), "GitHub mode waits for a search term")
        app.screen.query_one("#source").value = "catalog"; await settle(pilot)
        ok(app.screen.query_one("#results").row_count == 3, "and back to the catalog")
        await pilot.press("escape"); await settle(pilot)

    # ---- an empty catalog
    core.load_catalog = lambda settings=None, refresh=False: ({"schema": 1, "categories": {}, "addons": []}, "bundled")
    app = tui.WamApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot); await pilot.press("d"); await settle(pilot)
        ok("GitHub search" in text(app.screen.query_one("#info")) and app.screen.query_one("#results").row_count == 0, "empty catalog points you at GitHub search")

    # ---- slow catalog: Discover waits for it instead of crashing
    import threading, time
    gate = threading.Event()
    def slow(settings=None, refresh=False):
        gate.wait(5); return INDEX, "online"
    core.load_catalog = slow
    app = tui.WamApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); await pilot.press("d"); await pilot.pause()
        ok(name(app) == "DiscoverScreen" and "Loading the catalog" in text(app.screen.query_one("#info")), "while the catalog loads, Discover says so")
        app.screen.query_one("#q").value = "x"; await pilot.press("enter"); await pilot.pause()
        ok("Loading" in text(app.screen.query_one("#info")), "searching early doesn't crash")
        gate.set(); await settle(pilot); await settle(pilot)
        ok(app.screen.query_one("#results").row_count == 0 or "catalog" in text(app.screen.query_one("#info")), "results appear once it arrives")
asyncio.run(scenario())
print("\nALL TESTS PASSED")
