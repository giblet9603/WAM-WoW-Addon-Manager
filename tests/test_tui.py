import asyncio, os, sys, tempfile, json
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core
from wam import tui

def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)

addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
def mk(folder, title, ver, extra=""):
    (addons/folder).mkdir(exist_ok=True)
    (addons/folder/f"{folder}.toc").write_text(f"## Interface: 16001\n## Title: {title}\n## Version: {ver}\n{extra}\n")
mk("Auctionator", "Auctionator", "340")
mk("GearQuestForever", "GearQuest Forever", "0.5.4-beta")
mk("GearQuestForever_DRUID", "GearQuest Druid", "0.3.6")
mk("Mystery", "Mystery [red]Addon[/red]", "2.0")          # brackets must not break rendering
mk("Spoken", "Spoken", "3.4.0")
core.add_install_path(str(root/"WoW"))
install = core.load_config()["installs"][0]
AD = install["addons_dir"]
core.save_state({"tracked": {AD: {
    "curseforge:6124": {"tag": "1", "label": "Auctionator-340.zip", "name": "Auctionator", "folders": ["Auctionator"], "installed": "x"},
    "curseforge:100": {"tag": "5", "label": "0.5.4-beta", "name": "GearQuest Forever", "folders": ["GearQuestForever", "GearQuestForever_DRUID"], "installed": "x"},
    "me/spoken": {"tag": "v1", "label": "3.4.0", "name": "Spoken", "folders": ["Spoken"], "installed": "x"},
}}})

cand = lambda tag, label, **k: dict({"name": "X", "tag": tag, "label": label, "asset": label, "size": 1000, "url": "u", "prerelease": False, "notes": [], "blocked": None, "page": "https://cf/x"}, **k)
CHECK = {"mode": "ok"}
def fake_check_all(config, state, prerelease):
    if CHECK["mode"] == "boom": raise RuntimeError("network down")
    return [(install, "curseforge:6124", state["tracked"][AD]["curseforge:6124"], cand("2", "Auctionator-341.zip", blocked="the author has turned off third-party downloads"), None),
            (install, "curseforge:100", state["tracked"][AD]["curseforge:100"], cand("6", "0.5.5-beta"), None),
            (install, "me/spoken", state["tracked"][AD]["me/spoken"], cand("v1", "v1"), None)]
core.check_all = fake_check_all
INSTALLED = []
def fake_do_install(key, inst, *, prerelease=False, assume_yes=False, dry_run=False, log=print):
    INSTALLED.append((key, assume_yes)); log(f"  downloading {key}..."); log("  installed 1 folder(s)"); return True
core.do_install = fake_do_install
core.resolve_source = lambda key, pre, settings=None: (cand("9", "9.9.9", name=key), None)

async def settle(pilot):
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()

name = lambda app: type(app.screen).__name__
def table_titles(app):
    t = app.screen.query_one("#addons")
    return [str(t.get_row_at(i)[0]) for i in range(t.row_count)]
def text_of(widget): return str(widget.render())

async def scenario():
    app = tui.WamApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await settle(pilot)
        # ---- startup ----
        ok(name(app) == "MainScreen", "boots into the main screen")
        titles = table_titles(app)
        ok(len(titles) == 4 and "GearQuest Forever (+1)" in titles, f"addons grouped: {titles}")
        ok(any("[red]" in t for t in titles), "square brackets in names render safely")
        t = app.screen.query_one("#addons")
        statuses = {str(t.get_row_at(i)[0]): str(t.get_row_at(i)[2]) for i in range(t.row_count)}
        ok(statuses["Mystery [red]Addon[/red]"] == "· no source" and statuses["Spoken"] == "· tracked", "statuses before checking")
        ok("press c" in text_of(app.screen.query_one("#status")), "status bar nudges you to check")

        # ---- detail panel follows the cursor ----
        first = text_of(app.screen.query_one("#detail"))
        await pilot.press("down"); await settle(pilot)
        ok(text_of(app.screen.query_one("#detail")) != first, "detail panel changes as you move")
        await pilot.press("home" if False else "up"); await settle(pilot)

        # ---- filter ----
        await pilot.press("f"); await pilot.pause()
        app.screen.query_one("#filter").value = "gear"; await settle(pilot)
        ok(table_titles(app) == ["GearQuest Forever (+1)"], "filter narrows the list")
        await pilot.press("enter"); await pilot.pause()
        ok(app.screen.query_one("#addons").has_focus, "Enter in the filter returns to the list")
        app.screen.query_one("#filter").value = ""; await settle(pilot)
        ok(len(table_titles(app)) == 4, "clearing the filter restores the list")

        # ---- check for updates ----
        await pilot.press("c"); await settle(pilot)
        statuses = {str(t.get_row_at(i)[0]): str(t.get_row_at(i)[2]) for i in range(t.row_count)}
        ok(statuses["Auctionator"] == "⇩ manual dl" and statuses["GearQuest Forever (+1)"] == "⬆ update" and statuses["Spoken"] == "✓ up to date", f"check results shown: {statuses}")
        ok("2 update(s) available" in text_of(app.screen.query_one("#status")), "status bar counts updates")

        # ---- update a single addon: confirm -> progress ----
        app.screen.query_one("#addons").move_cursor(row=app.screen.query_one("#addons").get_row_index("GearQuestForever")); await settle(pilot)
        await pilot.press("u"); await settle(pilot)
        ok(name(app) == "ConfirmScreen", "u asks before changing anything")
        ok("9.9.9" in text_of(app.screen.query_one(".dialog-body")) and "backed up" in text_of(app.screen.query_one(".dialog-body")), "plan shows the version and the backup promise")
        await pilot.press("n"); await settle(pilot)
        ok(name(app) == "MainScreen" and not INSTALLED, "answering no installs nothing")
        await pilot.press("u"); await settle(pilot); await pilot.press("y"); await settle(pilot)
        ok(name(app) == "ProgressScreen" and INSTALLED == [("curseforge:100", True)], f"y runs the install: {INSTALLED}")
        ok("Done." in text_of(app.screen.query_one("#job-log")) or app.screen.done, "progress screen finishes")
        await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "MainScreen", "closing progress returns to the list")

        # ---- update with no source / manual-download addons ----
        t = app.screen.query_one("#addons")
        t.move_cursor(row=t.get_row_index("Mystery")); await settle(pilot)
        await pilot.press("u"); await settle(pilot)
        ok(name(app) == "MessageScreen" and "link" in text_of(app.screen.query_one(".dialog-body")).lower(), "u on an unlinked addon explains how to link it")
        await pilot.press("escape"); await settle(pilot)
        t.move_cursor(row=t.get_row_index("Auctionator")); await settle(pilot)
        core.resolve_source = lambda key, pre, settings=None: (cand("2", "Auctionator-341.zip", name="Auctionator", blocked="the author has turned off third-party downloads"), None)
        await pilot.press("u"); await settle(pilot)
        ok(name(app) == "MessageScreen" and "https://cf/x" in text_of(app.screen.query_one(".dialog-body")), "opted-out addon: tells you where to download, never offers install")
        await pilot.press("escape"); await settle(pilot)
        core.resolve_source = lambda key, pre, settings=None: (cand("9", "9.9.9", name=key), None)

        # ---- update all ----
        INSTALLED.clear()
        await pilot.press("a"); await settle(pilot)
        ok(name(app) == "MessageScreen", "update-all after a reload asks you to check first")
        await pilot.press("escape"); await pilot.press("c"); await settle(pilot)
        await pilot.press("a"); await settle(pilot)
        body = text_of(app.screen.query_one(".dialog-body"))
        ok(name(app) == "ConfirmScreen" and "GearQuest" in body and "Auctionator" not in body, "update-all lists only what wam may download")
        await pilot.press("y"); await settle(pilot); await pilot.press("enter"); await settle(pilot)
        ok(INSTALLED == [("curseforge:100", True)], "update-all installed just the downloadable one")

        # ---- install by repo / id ----
        INSTALLED.clear()
        await pilot.press("i"); await settle(pilot)
        ok(name(app) == "PromptScreen", "i opens the install prompt")
        app.screen.query_one("#prompt-input").value = "banana"; await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "MessageScreen", "garbage input is explained, not installed")
        await pilot.press("escape"); await pilot.press("i"); await settle(pilot)
        app.screen.query_one("#prompt-input").value = "cf:1234"; await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "ConfirmScreen", "valid id leads to a confirm step")
        await pilot.press("y"); await settle(pilot); await pilot.press("enter"); await settle(pilot)
        ok(INSTALLED == [("curseforge:1234", True)], "installed after confirming")

        # ---- link a source ----
        calls = []
        core.link_addon = lambda folder, text, inst: calls.append((folder, text)) or (True, "Mystery: now tracked")
        t = app.screen.query_one("#addons"); t.move_cursor(row=t.get_row_index("Mystery")); await settle(pilot)
        await pilot.press("l"); await settle(pilot)
        app.screen.query_one("#prompt-input").value = "me/mystery"; await pilot.press("enter"); await settle(pilot)
        ok(calls == [("Mystery", "me/mystery")] and name(app) == "MessageScreen", "link sends the right folder and source")
        await pilot.press("escape"); await settle(pilot)

        # ---- discover / search ----
        SEARCHES = []
        def fake_search(text, sort="best", forever=False, limit=30):
            SEARCHES.append((text, sort, forever))
            if text.startswith("boom"): raise RuntimeError("GitHub's search limit hit")
            return [{"key": "a/fishy", "name": "fishy", "desc": "Fishing [bold]helper[/bold]", "forever": True, "stars": 12, "updated": "2026-10-01", "url": "u", "archived": False},
                    {"key": "b/old", "name": "old", "desc": "", "stars": 3, "updated": "2025-01-01", "url": "u", "archived": True}]
        core.search_github = fake_search
        await pilot.press("d"); await settle(pilot)
        ok(name(app) == "DiscoverScreen", "d opens Discover")
        app.screen.query_one("#source").value = "github"; await settle(pilot)      # these checks are about GitHub search
        await pilot.press("enter"); await settle(pilot)
        ok(not SEARCHES and "Type something" in text_of(app.screen.query_one("#info")), "empty search is explained, not sent")
        app.screen.query_one("#q").value = "fishing"; await pilot.press("enter"); await settle(pilot)
        res = app.screen.query_one("#results")
        ok(SEARCHES[-1] == ("fishing", "best", False) and res.row_count == 2, f"search runs: {SEARCHES[-1]}")
        ok("[archived]" in str(res.get_row_at(1)[0]) and "[bold]" in str(res.get_row_at(0)[1]), "archived flag + bracketed descriptions render")
        ok(str(res.get_row_at(0)[2]) == "claims" and str(res.get_row_at(1)[2]) == "-", "Forever column shows the hint")
        app.screen.query_one("#sort").value = "popular"; await settle(pilot)
        ok(SEARCHES[-1] == ("fishing", "popular", False), f"changing sort re-runs the search: {SEARCHES[-1]}")
        app.screen.query_one("#cat").value = "Auction & economy"; await settle(pilot)
        ok(SEARCHES[-1][0] == "fishing auction", f"category adds its keyword: {SEARCHES[-1]}")
        app.screen.query_one("#forever").value = True; await settle(pilot)
        ok(SEARCHES[-1][2] is True, "Forever-only switch is passed through")
        app.screen.query_one("#q").value = "boom"; await pilot.press("enter"); await settle(pilot)
        ok("limit" in text_of(app.screen.query_one("#info")), "search errors shown in plain words")
        app.screen.query_one("#q").value = "fishing"; await pilot.press("enter"); await settle(pilot)
        INSTALLED.clear()
        app.screen.query_one("#results").focus(); await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "AddonInfoScreen" and "a/fishy" in text_of(app.screen.query_one(".dialog-body")), "Enter on a result shows its details first")
        await pilot.press("i"); await settle(pilot)
        ok(name(app) == "ConfirmScreen" and "a/fishy" in text_of(app.screen.query_one(".dialog-body")), "choosing Install there starts the install flow")
        await pilot.press("n"); await settle(pilot)
        ok(name(app) == "DiscoverScreen" and not INSTALLED, "declining returns to results, installs nothing")
        await pilot.press("escape"); await settle(pilot)
        ok(name(app) == "MainScreen", "Esc goes back to the list")

        # ---- restore ----
        BK = [{"name": "20261012-183433-703860_cjber_x", "path": root/"bk1", "when": "2026-10-12 18:34", "source": "cjber_x", "folders": ["ShortestPath", "SP_Data"]}]
        core.list_backups = lambda: list(BK)
        RESTORED = []
        core.restore_backup = lambda path, inst, log=print: RESTORED.append(path) or log("Restored 2 folder(s)") or True
        await pilot.press("r"); await settle(pilot)
        ok(name(app) == "RestoreScreen" and app.screen.query_one("#backups").row_count == 1, "r lists backups")
        await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "ConfirmScreen" and "ShortestPath" in text_of(app.screen.query_one(".dialog-body")), "restore asks first")
        await pilot.press("y"); await settle(pilot)
        ok(RESTORED == [root/"bk1"] and name(app) == "ProgressScreen", "restore runs on the right backup")
        await pilot.press("enter"); await settle(pilot); await pilot.press("escape"); await settle(pilot)
        ok(name(app) == "MainScreen", "back to the list after restoring")

        # ---- settings ----
        SCHED = []
        core.schedule_apply = lambda action, t=None: SCHED.append((action, t)) or (True, "Daily check is off." if action == "status" else f"ok {action}")
        await pilot.press("s"); await settle(pilot)
        ok(name(app) == "SettingsScreen", "s opens settings")
        radios = list(app.screen.query("#mode RadioButton"))
        radios[1].value = True; await settle(pilot)
        ok(core.get_settings(core.load_config())["update_mode"] == "notify", "choosing 'notify' is saved")
        radios[2].value = True; await settle(pilot)
        ok(core.get_settings(core.load_config())["update_mode"] == "auto", "choosing 'auto' is saved")
        app.screen.query_one("#prereleases").value = True; await settle(pilot)
        ok(core.get_settings(core.load_config())["prereleases"] is True, "beta switch saved")
        app.screen.query_one("#time").value = "18:30"
        app.screen.query_one("#sched-on").press(); await settle(pilot)
        ok(("on", "18:30") in SCHED, f"schedule on sends the time: {SCHED}")
        app.screen.query_one("#sched-off").press(); await settle(pilot)
        ok(("off", None) in SCHED, "schedule off")
        await pilot.press("escape"); await settle(pilot)
        ok(name(app) == "MainScreen", "settings -> back")

    # ---- network failure during check ----
    app = tui.WamApp()
    CHECK["mode"] = "boom"
    async with app.run_test(size=(130, 40)) as pilot:
        await settle(pilot); await pilot.press("c"); await settle(pilot)
        ok("Check failed: network down" in text_of(app.screen.query_one("#status")) and name(app) == "MainScreen", "failed check is reported, app survives")
        CHECK["mode"] = "ok"
        await pilot.press("c"); await settle(pilot)
        ok("update(s)" in text_of(app.screen.query_one("#status")), "can retry after a failure")

asyncio.run(scenario())

# ---- first run: no game folder configured ----
core.save_config({"installs": []})
async def first_run():
    app = tui.WamApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await settle(pilot)
        ok(name(app) == "PromptScreen", "no install yet -> asks where WoW is")
        app.screen.query_one("#prompt-input").value = "/definitely/not/here"; await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "MessageScreen", "bad path explained")
        await pilot.press("escape"); await settle(pilot)
        ok(name(app) == "PromptScreen", "then asks again")
        app.screen.query_one("#prompt-input").value = str(root/"WoW"); await pilot.press("enter"); await settle(pilot)
        ok(name(app) == "MainScreen" and app.screen.query_one("#addons").row_count == 4, "good path loads your addons")
asyncio.run(first_run())
print("\nALL TESTS PASSED")
