import asyncio, os, sys, tempfile
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import art, core, tui
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)

addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; (addons/"Foo").mkdir(parents=True)
(addons/"Foo"/"Foo.toc").write_text("## Interface: 16001\n## Title: Foo\n## Version: 1\n")
core.add_install_path(str(root/"WoW"))
core.schedule_apply = lambda a, t=None: (True, "Daily check is off.")

async def settle(p): await p.pause(); await p.app.workers.wait_for_complete(); await p.pause()
def use(theme):
    cfg = core.load_config(); cfg["settings"] = {"theme": theme}; core.save_config(cfg)

# --- the picker: biggest art that fits, nothing when there's no room or no art
ok(art.pick("horde", 60, 50)[1] is art.HORDE_LARGE, "roomy column gets the large crest")
ok(art.pick("horde", 40, 50)[1] is art.HORDE_COMPACT, "narrower column gets the compact crest")
ok(art.pick("horde", 60, 32)[1] is art.HORDE_COMPACT, "short window drops to compact")
ok(art.pick("horde", 60, 24) is None and art.pick("horde", 30, 50) is None, "too small: no crest")
ok(all(art.pick(t, 80, 60) is None for t in ("terminal", "custom", None)), "themes without art show nothing")
ok(art.pick("wow", 60, 50)[1] is art.WOW_LARGE and art.pick("wow", 40, 50)[1] is art.WOW_COMPACT and art.pick("wow", 60, 32)[1] is art.WOW_LARGE, "wow gets its own art in both sizes")
ok(art.pick("alliance", 60, 50)[1] is art.ALLIANCE_LARGE and art.pick("alliance", 40, 50)[1] is art.ALLIANCE_COMPACT, "alliance gets its own art in both sizes")

async def scenario():
    use("horde")
    app = tui.WamApp()
    async with app.run_test(size=(130, 50)) as pilot:
        await settle(pilot)
        crest = app.screen.query_one("#crest")
        ok(crest.display and crest.border_title == "For the Horde!", "horde theme shows the crest box")
        ok(str(crest.render()).strip() == art.HORDE_LARGE.strip(), "large art at 130x50")
        side = app.screen.query_one("#side"); detail = app.screen.query_one("#right")
        ok(crest.region.right <= side.region.right and crest.region.bottom == side.region.bottom, "box sits in the bottom-right corner")
        ok(detail.region.height >= art.MIN_DETAIL_ROWS, "addon details keep their room above it")
        want = tui.Color.parse(core.THEME_PRESETS["horde"]["primary"]); got = crest.styles.color
        ok(max(abs(a - b) for a, b in zip(want.rgb, got.rgb)) <= 2, "art is drawn in the horde main color")
        ok("Foo" in str(app.screen.query_one("#detail").render()), "details still work")

        await pilot.resize_terminal(100, 40); await settle(pilot)
        ok(crest.display and str(crest.render()).strip() == art.HORDE_COMPACT.strip(), "smaller window switches to the compact crest")
        await pilot.resize_terminal(80, 24); await settle(pilot)
        ok(not crest.display, "tiny window hides it")
        await pilot.resize_terminal(130, 50); await settle(pilot)
        ok(crest.display, "and it comes back when there's room")

        # switching theme in settings updates the crest live
        await pilot.press("s"); await settle(pilot)
        radios = list(app.screen.query("#theme RadioButton"))
        radios[2].value = True; await settle(pilot); await pilot.press("escape"); await settle(pilot)
        c = app.screen.query_one("#crest")
        ok(c.display and c.border_title == "For the Alliance!" and str(c.render()).strip() == art.ALLIANCE_LARGE.strip(), "alliance: its own lion crest and title")
        want = tui.Color.parse(core.THEME_PRESETS["alliance"]["primary"]); got = c.styles.color
        ok(max(abs(a - b) for a, b in zip(want.rgb, got.rgb)) <= 2, "lion is drawn in the alliance blue")
        await pilot.press("s"); await settle(pilot)
        list(app.screen.query("#theme RadioButton"))[1].value = True; await settle(pilot); await pilot.press("escape"); await settle(pilot)
        c = app.screen.query_one("#crest")
        ok(c.display and c.border_title == "World of Warcraft" and str(c.render()).strip() == art.WOW_LARGE.strip(), "wow standard: its own W crest and title")
        want = tui.Color.parse(core.THEME_PRESETS["wow"]["primary"]); got = c.styles.color
        ok(max(abs(a - b) for a, b in zip(want.rgb, got.rgb)) <= 2, "W is drawn in WoW gold")
        await pilot.press("s"); await settle(pilot)
        list(app.screen.query("#theme RadioButton"))[4].value = True; await settle(pilot); await pilot.press("escape"); await settle(pilot)
        ok(not app.screen.query_one("#crest").display, "custom colors (no art): no crest")
        await pilot.press("s"); await settle(pilot)
        list(app.screen.query("#theme RadioButton"))[0].value = True; await settle(pilot); await pilot.press("escape"); await settle(pilot)
        ok(not app.screen.query_one("#crest").display, "terminal colors (no art): no crest")
        await pilot.press("s"); await settle(pilot)
        list(app.screen.query("#theme RadioButton"))[3].value = True; await settle(pilot); await pilot.press("escape"); await settle(pilot)
        ok(app.screen.query_one("#crest").display, "back to horde: crest returns")
asyncio.run(scenario())

async def startup(theme, shown):
    use(theme)
    app = tui.WamApp()
    async with app.run_test(size=(130, 50)) as pilot:
        await settle(pilot)
        ok(app.screen.query_one("#crest").display is shown, f"startup on {theme}: crest {'shown' if shown else 'hidden'}")
asyncio.run(startup("terminal", False)); asyncio.run(startup("wow", True)); asyncio.run(startup("horde", True)); asyncio.run(startup("alliance", True))
print("\nALL TESTS PASSED")
