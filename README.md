# WoW Addon Manager

Disclaimer: This app is 100% vibe coded, this disclaimer is literally the only thing I've written myself for this whole project so far. I just wanted a fun TUI Addon Manager and didn't like using CurseForge. I know nothing about coding but it seems to be working really well so wish me luck and god help us all as the robots take over.

An open-source WoW addon manager for Linux, built for WoW Forever first.
No ads, no account, no Overwolf.

Run `wam` with no arguments for the full-screen interface. Every action is also a typed command
(`wam --help`), which is what the daily background check uses.

## Keys (main screen)

| key | does |
|-----|------|
| c | check for updates |
| u | update the highlighted addon (shows a plan and asks first) |
| a | update everything that has an update |
| i | install by GitHub `owner/repo` or CurseForge `cf:<project id>` |
| l | link an addon to where it comes from |
| d | discover: browse the catalog, or search GitHub |
| o | auto-link: attach sources to addons wam recognises (from the catalog) |
| r | restore an addon from a backup |
| s | settings: update mode, betas, colors, daily check |
| f | filter your addon list |
| q | quit |

## How it keeps your game safe

- Anything it replaces is moved to `~/.local/share/wam/backups/`, never deleted.
- Installs are all-or-nothing: if one fails halfway, everything is put back.
- Zips that try to write outside the AddOns folder are refused.
- CurseForge downloads are checksum-verified, and authors who turn off third-party downloads are respected:
  wam will tell you an update exists but never downloads it.
- A restore is itself backed up first, so it can be undone.

## Colors

Settings has five choices: your terminal's own colors (the default), WoW standard, Alliance, Horde, and
custom (pick your own four colors; hex or names like `gold`). Stuck with unreadable colors?
`wam settings theme terminal` puts it back.

The WoW standard, Alliance and Horde themes also show their emblem in a box in the bottom-right corner of the main
screen. It picks the biggest size that fits your window and hides itself when the window is too small.

## The catalog

`catalog/addons/` holds one small file per addon: where it comes from, which folders it installs, whether the author
says it supports Forever, and whether downloads are allowed. A GitHub Action validates every pull request and
publishes the merged `index.json` with GitHub Pages. wam uses it for Discover and for `wam adopt`/`o`, and ships
with a copy so it works offline. See [catalog/README.md](catalog/README.md) for the format and how to add an addon.

```
wam catalog                      # where is it reading from?
wam search fishing               # search the catalog (add --github for a wider search)
wam settings catalog-url https://<you>.github.io/<repo>/index.json
```

## Sources

- **GitHub releases**: no key needed.
- **CurseForge**: needs your own free API key (`wam cf-key`). wam never ships a shared key.

## Where things live

- Settings and your links: `~/.config/wam/`
- What wam installed, and backups: `~/.local/share/wam/`

## Tests

```
python3 tests/test_catalog.py        # and the other test_*.py files
python3 tests/test_tui.py            # drives every screen with simulated key presses (needs textual)
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). Anyone who distributes this program or a modified version of it
has to do so under the same license, with the source.
