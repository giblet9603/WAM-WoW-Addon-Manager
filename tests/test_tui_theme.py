import asyncio, io, os, sys, tempfile, contextlib, json
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"d"); os.environ["XDG_CONFIG_HOME"] = str(root/"c")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core, tui
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)

addons = root/"WoW"/"_classic_beta_"/"Interface"/"AddOns"; (addons/"Foo").mkdir(parents=True)
(addons/"Foo"/"Foo.toc").write_text("## Interface: 16001\n## Title: Foo\n## Version: 1\n")
core.add_install_path(str(root/"WoW"))
core.schedule_apply = lambda a, t=None: (True, "Daily check is off.")

async def settle(p): await p.pause(); await p.app.workers.wait_for_complete(); await p.pause()
saved = lambda: core.get_settings(core.load_config())
def hexof(app): return app.current_theme.primary.lower()

async def scenario():
    app = tui.WamApp()
    async with app.run_test(size=(130, 50)) as pilot:
        await settle(pilot)
        ok(app.theme == "ansi-dark", "default is the terminal's own colors")
        await pilot.press("s"); await settle(pilot)
        radios = list(app.screen.query("#theme RadioButton"))
        ok(len(radios) == 5 and not app.screen.query_one("#custom-colors").display, "five choices; custom editor hidden until chosen")
        ok(not saved().get("custom_colors"), "opening settings changes nothing (no custom colors saved behind your back)")

        for idx, name in ((1, "wow"), (2, "alliance"), (3, "horde")):
            radios[idx].value = True; await settle(pilot)
            ok(saved()["theme"] == name and app.theme.startswith(f"wam-{name}") and hexof(app) == core.THEME_PRESETS[name]["primary"], f"{name}: saved and applied ({app.theme})")
        radios[0].value = True; await settle(pilot)
        ok(saved()["theme"] == "terminal" and app.theme == "ansi-dark", "back to terminal colors")

        # --- custom: first use copies the colors you were just looking at
        radios[3].value = True; await settle(pilot)
        radios[4].value = True; await settle(pilot)
        ok(app.screen.query_one("#custom-colors").display, "custom editor appears")
        seeded = saved()["custom_colors"]
        ok(seeded["primary"].lower() == core.THEME_PRESETS["horde"]["primary"] and app.screen.query_one("#c-primary").value.lower() == seeded["primary"].lower(), "custom starts from the horde colors you had")
        ok(app.theme.startswith("wam-custom"), "custom applied")

        # --- live editing
        app.screen.query_one("#c-primary").value = "gold"; await settle(pilot)
        ok(saved()["custom_colors"]["primary"] == "#FFD700" and hexof(app) == "#ffd700", "color names work and re-theme live")
        app.screen.query_one("#c-accent").value = "#00ff88"; await settle(pilot)
        ok(saved()["custom_colors"]["accent"] == "#00FF88", "hex works")
        before = dict(saved()["custom_colors"]); theme_before = app.theme
        app.screen.query_one("#c-primary").value = "banan"; await settle(pilot)
        hint = str(app.screen.query_one("#custom-hint").render())
        ok("isn't a color" in hint and saved()["custom_colors"] == before and app.theme == theme_before, "typo: explained, nothing saved, theme unchanged")
        app.screen.query_one("#c-primary").value = "#ffd700"; await settle(pilot)
        app.screen.query_one("#c-foreground").value = app.screen.query_one("#c-background").value; await settle(pilot)
        hint = str(app.screen.query_one("#custom-hint").render())
        ok("too close" in hint and saved()["custom_colors"]["foreground"] == before["foreground"], "text == background is refused (can't lock yourself out)")
        app.screen.query_one("#c-foreground").value = "#ffffff"; await settle(pilot)
        ok(saved()["custom_colors"]["foreground"] == "#FFFFFF", "fixing it saves again")

        # --- custom colors survive switching away and back
        radios[1].value = True; await settle(pilot); radios[4].value = True; await settle(pilot)
        ok(saved()["custom_colors"]["primary"] == "#FFD700" and hexof(app) == "#ffd700", "custom colors are remembered")

        # --- light backgrounds work too
        app.screen.query_one("#c-background").value = "#f5f0e6"; app.screen.query_one("#c-foreground").value = "#1a1a1a"; await settle(pilot)
        ok(saved()["custom_colors"]["background"] == "#F5F0E6" and app.current_theme.dark is False, "light custom theme flips to light mode")
        await pilot.press("escape"); await settle(pilot)
        ok(type(app.screen).__name__ == "MainScreen", "settings -> back with the theme intact")
asyncio.run(scenario())

# --- startup applies the saved theme; broken configs fall back instead of crashing
async def startup(settings, expect):
    cfg = core.load_config(); cfg["settings"] = settings; core.save_config(cfg)
    app = tui.WamApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot)
        ok(expect(app) and type(app.screen).__name__ == "MainScreen", f"startup with {settings}")
asyncio.run(startup({"theme": "horde"}, lambda a: a.theme.startswith("wam-horde")))
asyncio.run(startup({"theme": "custom", "custom_colors": {"primary": "not-a-color"}}, lambda a: a.theme == "ansi-dark"))
asyncio.run(startup({"theme": "something-old"}, lambda a: a.theme == "ansi-dark"))
asyncio.run(startup({"theme": "custom", "custom_colors": {}}, lambda a: a.theme.startswith("wam-custom")))

# --- the typed escape hatch
def run(argv):
    buf = io.StringIO(); sys.argv = ["wam"] + argv; code = 0
    with contextlib.redirect_stdout(buf):
        try: core.main()
        except SystemExit as e: code = e.code
    return code, buf.getvalue()
code, out = run(["settings", "theme", "alliance"]); ok(code == 0 and saved()["theme"] == "alliance", "wam settings theme alliance")
code, out = run(["settings", "theme", "neon"]); ok(code == 1 and "must be one of" in out and saved()["theme"] == "alliance", "bad theme name rejected")
code, out = run(["settings", "theme", "terminal"]); ok(saved()["theme"] == "terminal", "wam settings theme terminal restores the default")
code, out = run(["settings"]); ok("theme" in out, "settings listing shows the theme")
print("\nALL TESTS PASSED")
