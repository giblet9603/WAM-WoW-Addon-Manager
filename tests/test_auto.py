import os, sys, tempfile, importlib.util, io, contextlib
from pathlib import Path
root = Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(root/"data"); os.environ["XDG_CONFIG_HOME"] = str(root/"cfg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wam import core as wam

def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)

AD = str(root/"AddOns")
wam.save_config({"installs": [{"label": "Forever", "addons_dir": AD}]})
wam.save_state({"tracked": {AD: {"me/alpha": {"tag": "v1", "folders": ["Alpha"], "installed": "x"},
                                  "me/beta":  {"tag": "v5", "folders": ["Beta"],  "installed": "x"}}}})
rel = lambda tag, pre=False: {"tag_name": tag, "prerelease": pre, "draft": False,
                              "assets": [{"name": f"a-{tag}.zip", "size": 1, "browser_download_url": f"x://{tag}"}]}
RELS = {"me/alpha": [rel("v2")], "me/beta": [rel("v5")]}
wam.github_releases = lambda repo, limit=10: RELS[repo]

notes, installs = [], []
wam.notify = lambda t, b: notes.append((t, b))
wam.do_install = lambda repo, install, **kw: installs.append((repo, kw)) or True
running = {"v": False}
wam.game_running = lambda: running["v"]

def run(argv):
    buf = io.StringIO()
    args = None
    import argparse
    sys.argv = ["wam"] + argv
    with contextlib.redirect_stdout(buf):
        try: wam.main()
        except SystemExit as e: code = e.code
    return code, buf.getvalue()

# default mode is manual -> does nothing, not even a network call
RELS_calls = []
code, out = run(["auto-check"])
ok(code == 0 and not notes and not installs and "manual" in out, "manual mode: does nothing")

# invalid settings rejected
code, out = run(["settings", "update-mode", "yolo"]); ok(code == 1 and "must be one of" in out, "bad mode rejected")
code, out = run(["settings", "prereleases", "maybe"]); ok(code == 1, "bad prereleases value rejected")

# notify mode
code, out = run(["settings", "update-mode", "notify"]); ok(code == 0 and "schedule on" in out, "set notify (reminds about schedule)")
code, out = run(["auto-check"])
ok(len(notes) == 1 and "1 addon update" in notes[0][0] and "alpha" in notes[0][1], f"notify mode notifies once: {notes}")
ok(not installs, "notify mode installs nothing")

# auto mode, game running -> notify but do not install
notes.clear(); run(["settings", "update-mode", "auto"]); running["v"] = True
run(["auto-check"])
ok(not installs and notes and "game is running" in notes[0][1], "auto mode waits while game is running")

# auto mode, game closed -> installs, with confirmation skipped
notes.clear(); running["v"] = False
run(["auto-check"])
ok(len(installs) == 1 and installs[0][0] == "me/alpha" and installs[0][1]["assume_yes"] is True, "auto mode installs only the outdated addon")
ok(notes and notes[-1][0] == "Addons updated", "auto mode announces what it did")

# nothing outdated -> silent
notes.clear(); installs.clear(); RELS["me/alpha"] = [rel("v1")]
code, out = run(["auto-check"]); ok(not notes and not installs and "up to date" in out, "no updates -> no noise")

# prerelease setting is honored
RELS["me/alpha"] = [rel("v3-beta", pre=True), rel("v1")]
run(["settings", "prereleases", "off"]); notes.clear(); run(["auto-check"])
ok(not installs and not notes, "prereleases off: beta ignored when stable == installed")
run(["settings", "prereleases", "on"]); notes.clear(); run(["auto-check"])
ok(len(installs) == 1, "prereleases on: beta picked up")

# failure is reported and exit code nonzero
installs.clear(); notes.clear(); wam.do_install = lambda *a, **k: False
code, out = run(["auto-check"]); ok(code == 1 and any("failed" in n[0] for n in notes), "failed update reported")

# systemd unit generation + calls (systemctl mocked)
calls = []
wam.shutil.which = lambda n: "/usr/bin/" + n
wam.subprocess.run = lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0})()
code, out = run(["schedule", "on", "--time", "18:30"])
t = (wam.SYSTEMD_USER_DIR / "wam-check.timer").read_text(); sv = (wam.SYSTEMD_USER_DIR / "wam-check.service").read_text()
ok(code == 0 and "OnCalendar=*-*-* 18:30:00" in t and "Persistent=true" in t, "timer file has chosen time + catch-up")
ok("auto-check" in sv and "-m wam" in sv and sv.count("ExecStart=") == 1, "service runs auto-check")
ok(["systemctl", "--user", "enable", "--now", "wam-check.timer"] in calls, "timer enabled via systemctl --user")
code, out = run(["schedule", "on", "--time", "25:99"]); ok(code == 1, "invalid time rejected")
run(["schedule", "on"]); ok("OnCalendar=daily" in (wam.SYSTEMD_USER_DIR / "wam-check.timer").read_text(), "default is daily")
run(["schedule", "off"]); ok(not (wam.SYSTEMD_USER_DIR / "wam-check.timer").exists(), "schedule off removes units")
print("\nALL TESTS PASSED")
