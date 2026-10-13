# The wam addon catalog

A list of WoW Forever addons and where each one comes from. wam uses it to find addons
(Discover screen) and to work out where your already-installed addons came from (`wam adopt`).

Each addon is one small file in `addons/`. Adding one is a pull request.

## An entry

`addons/gearquest-forever.json`:

```json
{
  "id": "gearquest-forever",
  "name": "GearQuest - Forever",
  "description": "Leveling companion for WoW Forever...",
  "categories": ["gear", "leveling-quests"],
  "folders": ["GearQuestForever*"],
  "sources": { "curseforge": 1698950 },
  "forever": "yes",
  "download": "allowed",
  "homepage": "https://www.curseforge.com/wow/addons/gearquest-forever"
}
```

| field | required | meaning |
|-------|----------|---------|
| `id` | yes | lowercase words joined by dashes; must match the file name |
| `name` | yes | what people see |
| `description` | no | one or two sentences, up to 200 characters. Only say what the addon's own page says |
| `categories` | no | any of the ids in `wam/catalog.py` (fishing, professions, ...) |
| `folders` | no | the addon folders it installs, so wam can recognise it. `"Foo*"` matches `Foo`, `Foo_Data`, ... |
| `sources` | yes | `github`: `owner/repo`. `curseforge`: the numeric Project ID from the project's Details box. `wago` is informational. Needs a github or curseforge source |
| `forever` | yes | `yes` if the author says it supports WoW Forever, `unknown` if you can't tell, `no` if not |
| `download` | no | `manual` if the author turned off third-party downloads (wam will track it but never download it). Default `allowed` |
| `homepage` | no | an `https://` link |
| `featured` | no | maintainers only |

`stars` and `updated` are added automatically from GitHub when the index is built. Don't write them.

## Rules of thumb

- Only add things you've actually looked at: the repo or CurseForge page exists, and it's for Forever.
- Leave a field out instead of guessing.
- Each GitHub repo and each CurseForge project can appear once.
- The catalog tells wam where to download code from, so entries are reviewed by a maintainer before they're merged.
  wam also shows the source and asks before it installs anything.

## Checking your entry

```
python -m wam.catalog validate
python -m wam.catalog build --out wam/data/index.json   # refresh the copy bundled with the app
```

The pull request check runs both and fails if either complains.

## How it's published

A GitHub Action validates every pull request. When changes reach `main` it builds `index.json`
(adding star counts and update dates) and publishes it with GitHub Pages. Point wam at it with:

```
wam settings catalog-url https://<you>.github.io/<repo>/index.json
```

Without a URL, wam uses the copy bundled with it.
