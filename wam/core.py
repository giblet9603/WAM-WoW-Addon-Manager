# SPDX-License-Identifier: GPL-3.0-or-later
#!/usr/bin/env python3
"""wam - an open-source WoW addon manager (work in progress).

Milestone 1: find your game install and list the addons in it, with a
note on whether each one is marked as supporting WoW Forever.

Usage:
    wam add "<path to WoW folder, _classic_beta_ folder, or AddOns folder>"
    wam list
    wam scan
    wam latest owner/repo
    wam install owner/repo | cf:<project id> [--dry-run]
    wam adopt
    wam link <folder> owner/repo | cf:<id>
    wam check | update
    wam settings update-mode manual|notify|auto
    wam schedule on|off|status
    wam cf-key | cf-files <id or slug>

Needs Python 3.10+ and nothing else (standard library only).
"""

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from . import catalog

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wam" / "config.json"
)

DATA_DIR = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "wam"
)
STATE_PATH = DATA_DIR / "state.json"
SOURCES_PATH = CONFIG_PATH.parent / "sources.json"
BACKUP_DIR = DATA_DIR / "backups"

# The "interface number" is how an addon says which game version it supports.
# WoW Forever beta uses 16001. (Revisit this at launch if it changes.)
FOREVER_INTERFACE = 16001

# Addons can also ship a Forever-specific .toc file, e.g. MyAddon_Camelot.toc
FOREVER_TOC_SUFFIX = "_camelot"

# Friendly names for the game-version folders inside the WoW folder.
FLAVOR_NAMES = {
    "_retail_": "Retail",
    "_ptr_": "Retail PTR",
    "_classic_": "Classic",
    "_classic_era_": "Classic Era",
    "_classic_beta_": "WoW Forever (beta)",
    "_classic_ptr_": "Classic PTR",
}

# Common places Lutris puts Battle.net. We search these when you run `detect`.
LUTRIS_GLOBS = [
    "Games/*/drive_c/Program Files (x86)/World of Warcraft",
    "Games/*/drive_c/Program Files/World of Warcraft",
]


# ---------------------------------------------------------------------------
# Config file (remembers which installs you've added)
# ---------------------------------------------------------------------------

def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            print(f"Warning: couldn't read {CONFIG_PATH}, starting fresh.", file=sys.stderr)
    return {"installs": []}


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


# ---------------------------------------------------------------------------
# Finding game installs
# ---------------------------------------------------------------------------

def label_for(addons_dir: Path) -> str:
    """AddOns dir is <flavor folder>/Interface/AddOns, so the flavor is 2 levels up."""
    flavor_folder = addons_dir.parent.parent.name
    return FLAVOR_NAMES.get(flavor_folder, flavor_folder)


def find_addons_dirs(path: Path):
    """Given any of: the WoW folder, a flavor folder, or an AddOns folder,
    return the list of AddOns folders it points to."""
    path = path.expanduser()
    if path.name == "AddOns" and path.is_dir():
        return [path]
    direct = path / "Interface" / "AddOns"
    if direct.is_dir():
        return [direct]
    found = []
    if path.is_dir():
        for child in sorted(path.iterdir()):
            candidate = child / "Interface" / "AddOns"
            if child.name.startswith("_") and candidate.is_dir():
                found.append(candidate)
    return found


# ---------------------------------------------------------------------------
# Reading addons (.toc files)
# ---------------------------------------------------------------------------

def parse_toc(path: Path) -> dict:
    """A .toc file is plain text with lines like '## Title: My Addon'.
    Returns {lowercase_key: value}."""
    meta = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return meta
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("##"):
            continue
        key, sep, value = line[2:].partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
    return meta


def clean_text(s: str) -> str:
    """Remove WoW color/texture codes like |cff00ff00 ... |r from titles."""
    s = re.sub(r"\|T.*?\|t", "", s)
    s = re.sub(r"\|c[0-9a-fA-F]{8}", "", s)
    s = s.replace("|r", "")
    return s.strip()


def scan_addon(folder: Path):
    """Return a dict describing one addon folder, or None if it isn't an addon."""
    tocs = [
        f for f in folder.iterdir()
        if f.is_file()
        and f.suffix.lower() == ".toc"
        and f.stem.lower().startswith(folder.name.lower())
    ]
    if not tocs:
        return None

    has_forever_toc = any(f.stem.lower().endswith(FOREVER_TOC_SUFFIX) for f in tocs)

    interfaces = set()
    for toc in tocs:
        for key, value in parse_toc(toc).items():
            if key.startswith("interface"):
                interfaces.update(int(n) for n in re.findall(r"\d+", value))

    # Prefer the Forever toc for display info, then the plain one, then any.
    tocs.sort(key=lambda f: (
        not f.stem.lower().endswith(FOREVER_TOC_SUFFIX),
        f.stem.lower() != folder.name.lower(),
        f.name.lower(),
    ))
    meta = parse_toc(tocs[0])

    if has_forever_toc or FOREVER_INTERFACE in interfaces:
        status = "yes"
    elif interfaces:
        status = "not marked"
    else:
        status = "no info"

    website = meta.get("x-website", "")
    gh = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", website, re.I)
    cf = re.search(r"curseforge\.com/wow/addons/([^/\s#?]+)", website, re.I)

    sources = []
    if gh:
        sources.append(f"github:{gh.group(1)}/{re.sub(r'[.]git$', '', gh.group(2))}")
    if cf:
        sources.append(f"curseforge-slug:{cf.group(1)}")
    if meta.get("x-curse-project-id"):
        sources.append(f"curseforge:{meta['x-curse-project-id']}")
    if meta.get("x-wago-id"):
        sources.append(f"wago:{meta['x-wago-id']}")
    if meta.get("x-wowi-id"):
        sources.append(f"wowinterface:{meta['x-wowi-id']}")

    return {
        "folder": folder.name,
        "title": clean_text(meta.get("title", folder.name)),
        "version": clean_text(meta.get("version", "?")),
        "forever": status,
        "sources": ", ".join(dict.fromkeys(sources)) or "-",
        "github": (f"{gh.group(1)}/{re.sub(r'[.]git$', '', gh.group(2))}" if gh else ""),
        "cf_id": meta.get("x-curse-project-id", ""),
        "cf_slug": cf.group(1) if cf else "",
        "part_of": meta.get("x-part-of", ""),
        "child_of": meta.get("x-child-of", ""),
    }


def scan_install(addons_dir: Path):
    addons = []
    skipped = 0
    for folder in sorted(addons_dir.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith(".") or folder.name.startswith("Blizzard_"):
            continue
        info = scan_addon(folder)
        if info is None:
            skipped += 1
        else:
            addons.append(info)
    return addons, skipped


def group_addons(addons):
    """Merge folders that belong to one addon into a single group.

    Rules (a folder joins a group if any of these match):
      1. It names a parent with X-Part-Of or X-Child-Of in its .toc.
      2. Its folder name starts with another installed folder's name
         followed by a capital letter (SpokenQuests -> Spoken, but not
         Fishing -> Fish), if that name is 4+ letters.
    Returns a list of {"root": addon, "members": [other addons in group]}.
    """
    by_name = {a["folder"].lower(): a for a in addons}
    parent = {n: n for n in by_name}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    shared_parent = {}  # X-Part-Of value -> first folder that used it
    for name, a in by_name.items():
        for key in ("part_of", "child_of"):
            target = a[key].lower()
            if not target:
                continue
            if target in by_name:
                union(target, name)
            if key == "part_of":
                union(shared_parent.setdefault(target, name), name)
        child = a["folder"]
        for other, other_addon in by_name.items():
            parent_name = other_addon["folder"]
            if (
                other != name
                and len(parent_name) >= 4
                and child.startswith(parent_name)
                and len(child) > len(parent_name)
                and not child[len(parent_name)].islower()  # SpokenQuests yes, Fishing no
            ):
                union(other, name)

    clusters = {}
    for name in by_name:
        clusters.setdefault(find(name), []).append(by_name[name])

    groups = []
    for members in clusters.values():
        wanted = {m["part_of"].lower() for m in members if m["part_of"]}
        members.sort(key=lambda m: (m["folder"].lower() not in wanted, len(m["folder"]), m["folder"].lower()))
        groups.append({"root": members[0], "members": members[1:]})
    groups.sort(key=lambda g: g["root"]["title"].lower())
    return groups


# ---------------------------------------------------------------------------
# GitHub releases (no account or API key needed)
# ---------------------------------------------------------------------------

def github_releases(repo: str, limit: int = 10):
    """Fetch recent releases for 'owner/repo'. Drafts are skipped.
    Set the GITHUB_TOKEN environment variable to raise GitHub's rate limit
    (60 requests/hour without it, which is plenty for occasional checks)."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page={limit}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "wam-addon-manager"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(f"Repo '{repo}' not found (or it has no public releases).")
        if e.code in (403, 429):
            raise RuntimeError("GitHub rate limit hit. Wait a bit, or set GITHUB_TOKEN.")
        raise RuntimeError(f"GitHub returned an error: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach GitHub: {e.reason}")
    return [r for r in data if not r.get("draft")]


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class CFError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def cf_project_id_from_slug(slug):
    """Slug (the last part of a curseforge.com/wow/addons/<slug> URL) -> project ID, or None."""
    hits = []
    try:
        hits = cf_get("/v1/mods/search", {"gameId": CF_WOW_GAME_ID, "slug": slug})["data"]
    except CFError:
        pass  # some keys can't use the slug filter; fall back to a text search below
    if not hits:
        found = cf_get("/v1/mods/search", {"gameId": CF_WOW_GAME_ID, "searchFilter": slug, "pageSize": 20})["data"]
        hits = [h for h in found if str(h.get("slug", "")).lower() == slug.lower()]
    return str(hits[0]["id"]) if hits else None


def normalize_source(text):
    """'owner/repo' -> GitHub; 'cf:123' or 'curseforge:123' -> CurseForge. Else None."""
    t = text.strip()
    m = re.fullmatch(r"(?:cf|curseforge):(\d+)", t, re.I)
    if m:
        return f"curseforge:{m.group(1)}"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", t):
        return t
    return None


def resolve_github(repo, prerelease):
    """Returns (candidate, None) or (None, error message). A candidate is one installable build."""
    try:
        releases = github_releases(repo)
    except RuntimeError as e:
        return None, str(e)
    if not releases:
        return None, f"{repo} has no published releases."
    release, note = pick_release(releases, prerelease)
    asset, warning = pick_zip_asset(release)
    if asset is None:
        return None, f"Can't install {repo} {release['tag_name']}: {warning}"
    return {
        "name": repo.split("/")[-1],
        "tag": release["tag_name"],
        "label": release["tag_name"],
        "asset": asset["name"],
        "size": asset.get("size"),
        "url": asset["browser_download_url"],
        "prerelease": bool(release.get("prerelease")),
        "notes": [n for n in (note, warning) if n],
    }, None


def resolve_curseforge(project_id, prerelease, version_prefix):
    """Newest file labeled for WoW Forever on CurseForge. Returns (candidate, None) or (None, error)."""
    try:
        mod = cf_get(f"/v1/mods/{project_id}")["data"]
        files = cf_get(f"/v1/mods/{project_id}/files", {"pageSize": 50})["data"]
    except RuntimeError as e:
        return None, f"curseforge:{project_id}: {e}"
    name = mod.get("name", f"project {project_id}")
    page = (mod.get("links") or {}).get("websiteUrl") or f"https://www.curseforge.com/wow/addons/{mod.get('slug', '')}"
    fits = [f for f in files if any(v.startswith(version_prefix) for v in f.get("gameVersions", []))]
    if not fits:
        return None, f"{name}: no file is labeled for WoW Forever ({version_prefix}x)."
    fits.sort(key=lambda f: f.get("fileDate", ""), reverse=True)
    newest = fits[0]
    stable = next((f for f in fits if f.get("releaseType") == 1), None)
    notes = []
    if prerelease:
        chosen = newest
    elif stable is None:
        chosen = newest
        notes.append("Only beta/alpha builds exist, so using the newest one.")
    else:
        chosen = stable
        if newest is not stable:
            notes.append(f"A newer {CF_RELEASE_TYPES.get(newest.get('releaseType'), 'beta')} exists "
                         f"({newest.get('fileName')}). Turn on prereleases to take it.")
    # An author opt-out still lets us TRACK versions, but wam never downloads these.
    blocked = None
    if mod.get("allowModDistribution") is False:
        blocked = "the author has turned off third-party downloads"
    elif not chosen.get("downloadUrl"):
        blocked = "CurseForge gives no download link for this file (author restriction)"
    sha1 = next((h.get("value") for h in chosen.get("hashes", []) if h.get("algo") == 1), None)
    return {
        "name": name,
        "tag": str(chosen["id"]),
        "label": chosen.get("fileName", str(chosen["id"])),
        "asset": chosen.get("fileName", "download.zip"),
        "size": chosen.get("fileLength"),
        "verify_size": False,
        "sha1": sha1,
        "url": chosen.get("downloadUrl"),
        "blocked": blocked,
        "page": page,
        "prerelease": chosen.get("releaseType") != 1,
        "notes": notes,
    }, None


def resolve_source(key, prerelease, settings=None):
    settings = settings or DEFAULT_SETTINGS
    if key.startswith("curseforge:"):
        return resolve_curseforge(key.split(":", 1)[1], prerelease, settings["forever_version_prefix"])
    return resolve_github(key, prerelease)


# ---------------------------------------------------------------------------
# Installing and updating
# ---------------------------------------------------------------------------

def load_state():
    """State = what wam installed and from where, so it can check for updates."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            print(f"Warning: couldn't read {STATE_PATH}, starting fresh.", file=sys.stderr)
    return {"tracked": {}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _open(url, timeout=60):
    headers = {"User-Agent": "wam-addon-manager"}
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def fetch_json(url):
    with _open(url) as r:
        return json.load(r)


def download_file(url, dest: Path, expected_size=None):
    with _open(url) as r, open(dest, "wb") as out:
        shutil.copyfileobj(r, out)
    if expected_size and dest.stat().st_size != expected_size:
        raise RuntimeError(
            f"Download size mismatch (got {dest.stat().st_size}, expected {expected_size}). "
            "Try again."
        )


def pick_release(releases, allow_prerelease):
    """Returns (release, note). Skips pre-releases unless asked."""
    newest = releases[0]
    stable = next((r for r in releases if not r.get("prerelease")), None)
    if allow_prerelease:
        return newest, None
    if stable is None:
        return newest, "Only pre-releases exist, so using the newest one."
    note = None
    if newest is not stable:
        note = f"A newer pre-release exists ({newest['tag_name']}). Use --prerelease to take it."
    return stable, note


def pick_zip_asset(release):
    """Choose which zip in a release to install.
    Returns (asset, warning). asset is None if we can't decide safely."""
    assets = release.get("assets", [])
    zips = [a for a in assets if a["name"].lower().endswith(".zip")]
    if not zips:
        return None, "This release has no .zip file attached."

    # Packager's release.json says which game version each zip is built for.
    meta_asset = next((a for a in assets if a["name"].lower() == "release.json"), None)
    if meta_asset:
        try:
            meta = fetch_json(meta_asset["browser_download_url"])
            for entry in meta.get("releases", []):
                interfaces = set()
                for m in entry.get("metadata", []):
                    try:
                        interfaces.add(int(m.get("interface")))
                    except (TypeError, ValueError):
                        pass
                if FOREVER_INTERFACE in interfaces:
                    match = next((a for a in zips if a["name"] == entry.get("filename")), None)
                    if match:
                        return match, None
            warning = "release.json doesn't list a build for WoW Forever (16001)."
        except Exception:
            warning = "Couldn't read release.json to confirm this build targets Forever."
    else:
        warning = "No release.json, so can't confirm this build targets Forever."

    pool = [a for a in zips if "nolib" not in a["name"].lower()] or zips
    if len(pool) == 1:
        return pool[0], warning
    names = ", ".join(a["name"] for a in pool)
    return None, f"Several zips and no way to tell which is for Forever: {names}"


MAX_UNPACKED_BYTES = 1024 * 1024 * 1024  # refuse zips that unpack to over 1 GB


def safe_extract(zip_path: Path, dest: Path):
    """Unzip, refusing anything that would land outside `dest`."""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        total = 0
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise RuntimeError(f"Refusing zip: unsafe path '{info.filename}'")
            total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise RuntimeError("Refusing zip: unpacks to more than 1 GB.")
        z.extractall(dest)


def find_addon_folders(root: Path, max_depth=2):
    """Find folders that look like addons (contain a matching .toc). Looks one
    wrapper-folder deep, since some zips nest everything in an extra folder."""
    def is_addon(d: Path):
        return any(
            f.is_file() and f.suffix.lower() == ".toc" and f.stem.lower().startswith(d.name.lower())
            for f in d.iterdir()
        )

    found = []

    def walk(d: Path, depth: int):
        for child in sorted(d.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "__MACOSX")):
                continue
            if is_addon(child):
                found.append(child)
            elif depth < max_depth:
                walk(child, depth + 1)

    walk(root, 1)
    return found


def swap_in(new_folders, addons_dir: Path, backup_root: Path):
    """Move new addon folders into AddOns. Old copies go to backup_root.
    If anything fails midway, undo it all. Returns the list of backed-up folders."""
    backed_up = []   # (backup location, original location)
    touched = []     # targets we've written to
    try:
        for nf in new_folders:
            target = addons_dir / nf.name
            if target.exists():
                backup = backup_root / nf.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                n = 1
                while backup.exists():          # shutil.move would nest into it
                    n += 1
                    backup = backup_root / f"{nf.name}.{n}"
                shutil.move(str(target), str(backup))
                backed_up.append((backup, target))
            touched.append(target)
            shutil.move(str(nf), str(target))
    except Exception:
        for t in touched:
            if t.exists():
                shutil.rmtree(t, ignore_errors=True)
        for backup, original in backed_up:
            shutil.move(str(backup), str(original))
        raise
    return backed_up


def choose_install(config, to):
    installs = config["installs"]
    if not installs:
        return None, "No installs yet. Run: wam add \"<your _classic_beta_ folder>\""
    if to:
        matches = [i for i in installs if to.lower() in i["label"].lower() or to in i["addons_dir"]]
        if len(matches) == 1:
            return matches[0], None
        return None, f"--to '{to}' matched {len(matches)} installs; be more specific."
    if len(installs) == 1:
        return installs[0], None
    labels = ", ".join(i["label"] for i in installs)
    return None, f"Several installs ({labels}). Pick one with --to."


def confirm(question):
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def sha1_of(path: Path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def do_install(key, install, *, prerelease=False, assume_yes=False, dry_run=False, log=print):
    """Download the newest build of a source (GitHub 'owner/repo' or 'curseforge:ID')
    and install it. Returns True on success."""
    addons_dir = Path(install["addons_dir"])
    if not addons_dir.is_dir():
        log(f"AddOns folder not found: {addons_dir}")
        return False

    cand, err = resolve_source(key, prerelease, get_settings(load_config()))
    if cand is None:
        log(err)
        return False

    if cand.get("blocked"):
        log(f"{cand['name']}: {cand['blocked']}, so wam won't download it.")
        log(f"  Get it yourself from: {cand.get('page')}")
        log(f"  (afterwards, `wam link <folder> {key.replace('curseforge:', 'cf:')}` refreshes wam's notes)")
        return False

    state = load_state()
    tracked = state["tracked"].setdefault(install["addons_dir"], {})
    previous = tracked.get(key)

    log(f"{cand['name']}  [{key}]")
    log(f"  version : {cand['label']}" + (" (pre-release/beta)" if cand.get("prerelease") else ""))
    log(f"  file    : {cand['asset']}" + (f" ({human_size(cand['size'])})" if cand.get("size") else ""))
    log(f"  into    : {install['label']}")
    if previous:
        log(f"  replaces: {previous.get('label') or previous['tag']}")
    for msg in cand.get("notes", []):
        log(f"  note    : {msg}")

    if dry_run:
        log("  (dry run, nothing changed)")
        return True
    log("  Tip: close the game before updating addons.")
    if not assume_yes and not confirm("Proceed?"):
        log("Cancelled.")
        return False

    # One unique backup folder per install, so two installs never share (or nest into) one.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_root = BACKUP_DIR / f"{stamp}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', key)}"
    # Work next to AddOns (same drive, so the final move is quick), not inside it.
    work = Path(tempfile.mkdtemp(prefix=".wam-", dir=addons_dir.parent))
    try:
        zip_path = work / "download.zip"
        log("  downloading...")
        download_file(cand["url"], zip_path, cand.get("size") if cand.get("verify_size", True) else None)
        if cand.get("sha1") and sha1_of(zip_path) != cand["sha1"].lower():
            raise RuntimeError("Download doesn't match the checksum the source published. Not installing it.")
        unpack = work / "unpacked"
        unpack.mkdir()
        safe_extract(zip_path, unpack)
        folders = find_addon_folders(unpack)
        if not folders:
            raise RuntimeError("No addon folders found in that zip (no .toc files).")
        backed_up = swap_in(folders, addons_dir, backup_root)

        new_names = [f.name for f in folders]
        stale = [n for n in (previous or {}).get("folders", []) if n not in new_names]
        for name in stale:
            old = addons_dir / name
            if old.is_dir():
                dest = backup_root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                n = 1
                while dest.exists():
                    n += 1
                    dest = backup_root / f"{name}.{n}"
                shutil.move(str(old), str(dest))
                backed_up.append((dest, old))

        tracked[key] = {
            "tag": cand["tag"],
            "label": cand["label"],
            "name": cand["name"],
            "asset": cand["asset"],
            "folders": new_names,
            "installed": datetime.now().isoformat(timespec="seconds"),
        }
        save_state(state)
    except Exception as e:
        log(f"  FAILED: {e}\n  Your existing addons were left as they were.")
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)

    log(f"  installed {len(new_names)} folder(s): {', '.join(new_names)}")
    if backed_up:
        log(f"  old versions saved in: {backup_root}")
    return True


# ---------------------------------------------------------------------------
# Settings, notifications and scheduled checking
# ---------------------------------------------------------------------------

UPDATE_MODES = {
    "manual": "never check on its own; you run `check` / `update` yourself",
    "notify": "check daily and send a desktop notification when updates exist",
    "auto": "check daily and install updates by itself (skips while the game is running)",
}
DEFAULT_SETTINGS = {"update_mode": "manual", "prereleases": False, "forever_version_prefix": "1.60.",
                    "theme": "terminal", "custom_colors": {}, "catalog_url": ""}

# Color themes for the visual interface. "terminal" uses your terminal's own palette.
THEME_NAMES = {
    "terminal": "Terminal colors (default)",
    "wow": "WoW standard",
    "alliance": "Alliance",
    "horde": "Horde",
    "custom": "Custom colors",
}
THEME_PRESETS = {
    # Gold-on-dark like Blizzard's own UI; success/warning match item-quality colors.
    "wow": {"background": "#0b0a08", "surface": "#17130d", "panel": "#251d12", "foreground": "#f2e9d0",
            "primary": "#ffd100", "secondary": "#8c6d3f", "accent": "#3fa7ff",
            "success": "#1eff00", "warning": "#ff8000", "error": "#ff4040"},
    # Royal blue and gold.
    "alliance": {"background": "#08122b", "surface": "#0e1c42", "panel": "#15285a", "foreground": "#e6edff",
                 "primary": "#4c8dff", "secondary": "#c9a227", "accent": "#f2c94c",
                 "success": "#46d37a", "warning": "#f2a33a", "error": "#ff5d5d"},
    # Blood red, iron and ember.
    "horde": {"background": "#120808", "surface": "#1e0f0f", "panel": "#2d1515", "foreground": "#f3e4de",
              "primary": "#e03a2f", "secondary": "#7b6a58", "accent": "#ff8c42",
              "success": "#7ac74f", "warning": "#ffb347", "error": "#ff6b6b"},
}
SYSTEMD_USER_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"
)


def get_settings(config):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(config.get("settings", {}))
    return merged


def notify(title, body):
    """Print (shows up in the system journal) and pop a desktop notification if we can."""
    print(f"{title}: {body}")
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=wam", title, body], check=False)


def game_running():
    """True if a WoW process (Wow.exe, WowClassic.exe, ...) is running under Wine."""
    if not shutil.which("pgrep"):
        return False
    return subprocess.run(
        ["pgrep", "-if", r"wow[a-z0-9_-]*\.exe"], capture_output=True
    ).returncode == 0


def display_name(key, info):
    return info.get("name") or key.split("/")[-1].split(":")[-1]


def check_all(config, state, prerelease):
    """For every tracked addon: look up the newest installable build.
    Returns [(install, key, info, candidate_or_None, error_or_None)]."""
    settings = get_settings(config)
    return [
        (install, key, info) + resolve_source(key, prerelease, settings)
        for install, key, info in _tracked_for(config, state)
    ]


def collect_updates(config, state, prerelease):
    """Return [(install, key, info, candidate)] for tracked addons with a newer build."""
    found = []
    for install, key, info, cand, err in check_all(config, state, prerelease):
        if err:
            print(f"{display_name(key, info)}: {err}")
        elif cand["tag"] != info["tag"]:
            found.append((install, key, info, cand))
    return found


def cmd_settings(args):
    config = load_config()
    settings = get_settings(config)
    if args.key is None:
        print(f"update-mode : {settings['update_mode']}  ({UPDATE_MODES[settings['update_mode']]})")
        print(f"prereleases : {'on' if settings['prereleases'] else 'off'}")
        print(f"forever-version-prefix : {settings['forever_version_prefix']}  (which CurseForge game versions count as WoW Forever)")
        print(f"theme       : {settings['theme']}  (terminal, wow, alliance, horde, custom)")
        print(f"catalog-url : {settings['catalog_url'] or '(none: using the catalog bundled with wam)'}")
        print("\nChange with:  wam settings update-mode manual|notify|auto")
        print("              wam settings prereleases on|off")
        print("              wam settings theme terminal|wow|alliance|horde|custom")
        print("              wam settings catalog-url https://...  (or: none)")
        return 0
    if args.value is None:
        print("Give a value too, e.g.: settings update-mode notify")
        return 1
    if args.key == "update-mode":
        if args.value not in UPDATE_MODES:
            print(f"update-mode must be one of: {', '.join(UPDATE_MODES)}")
            return 1
        settings["update_mode"] = args.value
    elif args.key == "prereleases":
        if args.value not in ("on", "off"):
            print("prereleases must be on or off")
            return 1
        settings["prereleases"] = args.value == "on"
    elif args.key == "catalog-url":
        if args.value not in ("none", "off") and not args.value.startswith("https://"):
            print("The catalog address must start with https://  (or use 'none' to go back to the bundled catalog)")
            return 1
        settings["catalog_url"] = "" if args.value in ("none", "off") else args.value
    elif args.key == "theme":
        if args.value not in THEME_NAMES:
            print(f"theme must be one of: {', '.join(THEME_NAMES)}")
            return 1
        settings["theme"] = args.value
    elif args.key == "forever-version-prefix":
        if not re.fullmatch(r"\d+\.\d+\.?", args.value):
            print("Use something like 1.60 (matches 1.60.1, 1.60.2, ...)")
            return 1
        settings["forever_version_prefix"] = args.value.rstrip(".") + "."
    else:
        print("Unknown setting. Options: update-mode, prereleases, theme, catalog-url, forever-version-prefix")
        return 1
    config["settings"] = settings
    save_config(config)
    print(f"Saved. update-mode={settings['update_mode']}, prereleases={'on' if settings['prereleases'] else 'off'}")
    if settings["update_mode"] != "manual":
        print("Reminder: turn on the daily check with:  wam schedule on")
    return 0


def cmd_autocheck(args):
    """What the daily timer runs. Behavior depends on the update-mode setting."""
    config, state = load_config(), load_state()
    settings = get_settings(config)
    mode = settings["update_mode"]
    if mode == "manual":
        print("Update mode is manual; nothing to do.")
        return 0
    updates = collect_updates(config, state, settings["prereleases"])
    if not updates:
        print("All tracked addons are up to date.")
        return 0
    manual = [display_name(k, i) for _, k, i, c in updates if c.get("blocked")]
    auto_ok = [(inst, k, i) for inst, k, i, c in updates if not c.get("blocked")]
    names = ", ".join(display_name(k, i) + (" (manual download)" if c.get("blocked") else "")
                      for _, k, i, c in updates)
    if mode == "notify":
        notify(f"{len(updates)} addon update(s) available", f"{names}. Run: wam update")
        return 0
    # mode == "auto"
    if game_running():
        notify("Addon updates waiting", f"{names} (not installed: the game is running)")
        return 0
    done, failed = [], []
    for install, key, info in auto_ok:
        ok = do_install(key, install, prerelease=settings["prereleases"], assume_yes=True)
        (done if ok else failed).append(display_name(key, info))
    if done:
        notify("Addons updated", ", ".join(done))
    if manual:
        notify("Manual download needed", ", ".join(manual) + " (the authors don't allow third-party downloads)")
    if failed:
        notify("Addon update failed", ", ".join(failed) + " (old versions left in place)")
    return 0 if not failed else 1


def _unit_texts(run_time):
    calendar = f"*-*-* {run_time}:00" if run_time else "daily"
    project_root = Path(__file__).resolve().parent.parent
    service = (
        "[Unit]\nDescription=Check for WoW addon updates (wam)\n\n"
        "[Service]\nType=oneshot\n"
        f"Environment=PYTHONPATH={project_root}\n"
        f"ExecStart={sys.executable} -m wam auto-check\n"
    )
    timer = (
        "[Unit]\nDescription=Daily WoW addon update check (wam)\n\n"
        f"[Timer]\nOnCalendar={calendar}\nPersistent=true\nRandomizedDelaySec=10min\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    return service, timer


def schedule_apply(action, run_time=None):
    """Turn the daily check on/off or report its status. Returns (ok, text). Never prints."""
    service_path = SYSTEMD_USER_DIR / "wam-check.service"
    timer_path = SYSTEMD_USER_DIR / "wam-check.timer"

    def systemctl(*a):
        return subprocess.run(["systemctl", "--user", *a], check=False, capture_output=True, text=True)

    if action == "on":
        if not shutil.which("systemctl"):
            return False, "systemctl not found; this needs a systemd-based Linux (CachyOS has it)."
        if run_time and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", run_time):
            return False, "The time must look like 18:30 (24-hour clock)."
        service, timer = _unit_texts(run_time)
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        service_path.write_text(service)
        timer_path.write_text(timer)
        systemctl("daemon-reload")
        result = systemctl("enable", "--now", "wam-check.timer")
        lines = [f"Daily check enabled ({run_time or 'once a day, time chosen by systemd'})."]
        if getattr(result, "returncode", 0) != 0:
            lines.append("systemctl reported a problem: " + (getattr(result, "stderr", "") or "").strip())
        if get_settings(load_config())["update_mode"] == "manual":
            lines.append("Note: update-mode is still 'manual', so the daily check won't do anything yet.")
        return True, "\n".join(lines)
    if action == "off":
        systemctl("disable", "--now", "wam-check.timer")
        for f in (service_path, timer_path):
            f.unlink(missing_ok=True)
        systemctl("daemon-reload")
        return True, "Daily check disabled."
    if not timer_path.exists():
        return True, "Daily check is off."
    result = systemctl("list-timers", "wam-check.timer", "--no-pager")
    return True, (getattr(result, "stdout", "") or "Daily check is on.").strip()


def cmd_schedule(args):
    ok, text = schedule_apply(args.action, args.time)
    print(text)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CurseForge (needs YOUR OWN free API key; wam never ships one)
# ---------------------------------------------------------------------------

CF_API = "https://api.curseforge.com"
CF_KEY_PATH = CONFIG_PATH.parent / "curseforge.key"
CF_WOW_GAME_ID = 1
CF_RELEASE_TYPES = {1: "release", 2: "beta", 3: "alpha"}


def get_cf_key():
    """The key comes from $CURSEFORGE_API_KEY, or the file written by `cf-key`."""
    key = os.environ.get("CURSEFORGE_API_KEY", "").strip()
    if not key and CF_KEY_PATH.exists():
        key = CF_KEY_PATH.read_text().strip()
    return key or None


def cf_get(path, params=None):
    key = get_cf_key()
    if not key:
        raise CFError("No CurseForge API key yet. Run: wam cf-key")
    url = CF_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(
        url, headers={"x-api-key": key, "Accept": "application/json", "User-Agent": "wam-addon-manager"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = re.sub(r"\s+", " ", e.read().decode("utf-8", "replace"))[:200]
        except Exception:
            pass
        if e.code == 401:
            raise CFError("CurseForge rejected the API key (HTTP 401). Re-run: wam cf-key", 401)
        if e.code == 403:
            raise CFError(
                f"CurseForge refused this request (HTTP 403)" + (f": {detail}" if detail else "")
                + ". The key itself may be fine but not allowed to use this endpoint.", 403)
        if e.code == 404:
            raise CFError("CurseForge says that project doesn't exist (HTTP 404).", 404)
        if e.code == 429:
            raise CFError("CurseForge rate limit hit. Wait a bit and retry.", 429)
        raise CFError(f"CurseForge returned an error: HTTP {e.code}", e.code)
    except urllib.error.URLError as e:
        raise CFError(f"Couldn't reach CurseForge: {e.reason}")


def cmd_cf_key(args):
    print("Get a free key at https://console.curseforge.com and read the terms shown there.")
    print("Your key is stored only on this computer, in a file only you can read.")
    key = getpass.getpass("Paste your CurseForge API key (typing is hidden): ").strip()
    if not key:
        print("No key entered; nothing saved.")
        return 1
    CF_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CF_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.chmod(CF_KEY_PATH, 0o600)
    try:
        game = cf_get(f"/v1/games/{CF_WOW_GAME_ID}")["data"]
        print(f"Key works. CurseForge says game #{CF_WOW_GAME_ID} is: {game.get('name', '?')}")
    except RuntimeError as e:
        print(f"Saved, but the test call failed: {e}")
        return 1
    return 0


def cmd_cf_files(args):
    """Read-only probe: show a project's recent files and how they're labeled."""
    try:
        target = args.project
        if not target.isdigit():
            pid = cf_project_id_from_slug(target)
            if not pid:
                print(f"No WoW addon with slug '{target}'.")
                return 1
            target = pid
        mod = cf_get(f"/v1/mods/{target}")["data"]
        files = cf_get(f"/v1/mods/{target}/files", {"pageSize": args.count})["data"]
    except RuntimeError as e:
        print(e)
        return 1

    print(f"{mod.get('name', '?')}  (project {target}, slug {mod.get('slug', '?')})")
    dist = mod.get("allowModDistribution")
    if dist is False:
        print("  NOTE: the author has turned OFF third-party distribution; wam must not auto-download this.")
    for f in files:
        rtype = CF_RELEASE_TYPES.get(f.get("releaseType"), "?")
        blocked = "" if f.get("downloadUrl") else "   [no third-party download link]"
        print(f"  file {f['id']}  {str(f.get('fileDate', ''))[:10]}  {rtype:<7} {f.get('fileName', '?')}{blocked}")
        print(f"      game versions: {', '.join(f.get('gameVersions', [])) or '-'}")
    return 0


# ---------------------------------------------------------------------------
# Backups and restore
# ---------------------------------------------------------------------------

def list_backups():
    """Every backup wam has made, newest first:
    [{"name", "path", "when", "source", "folders": [...]}]"""
    out = []
    if not BACKUP_DIR.is_dir():
        return out
    for d in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        m = re.match(r"(\d{8})-(\d{6})-\d+_(.*)", d.name)
        when, source = d.name, "?"
        if m:
            try:
                when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
            source = m.group(3)
        folders = sorted(c.name for c in d.iterdir() if c.is_dir())
        if folders:
            out.append({"name": d.name, "path": d, "when": when, "source": source, "folders": folders})
    return out


def restore_backup(backup_path, install, log=print):
    """Put a backup's folders back into AddOns. Whatever they replace is itself backed up
    first, so a restore can be undone. Returns True on success."""
    addons_dir = Path(install["addons_dir"])
    backup_path = Path(backup_path)
    if not addons_dir.is_dir():
        log(f"AddOns folder not found: {addons_dir}")
        return False
    sources = [c for c in sorted(backup_path.iterdir()) if c.is_dir()] if backup_path.is_dir() else []
    if not sources:
        log("That backup is empty or missing.")
        return False

    safety = BACKUP_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}_before-restore"
    work = Path(tempfile.mkdtemp(prefix=".wam-", dir=addons_dir.parent))
    try:
        # Copy (not move), so the backup stays intact if anything below fails.
        copies = []
        for src in sources:
            dest = work / src.name
            shutil.copytree(src, dest)
            copies.append(dest)
        replaced = swap_in(copies, addons_dir, safety)
    except Exception as e:
        log(f"FAILED: {e}\nYour addons were left as they were; the backup is untouched.")
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)

    names = {c.name for c in sources}
    state = load_state()
    for info in state["tracked"].get(install["addons_dir"], {}).values():
        if set(info["folders"]) & names:
            info["tag"] = "unverified"      # we no longer know which build is installed
            info["label"] = "restored from backup"
    save_state(state)
    shutil.rmtree(backup_path, ignore_errors=True)   # its contents are live again
    log(f"Restored {len(names)} folder(s): {', '.join(sorted(names))}")
    if replaced:
        log(f"What they replaced was saved in: {safety}")
    return True


# ---------------------------------------------------------------------------
# Discovering addons (GitHub search)
# ---------------------------------------------------------------------------

SEARCH_TOPICS = ["wow-addon", "wow-addons"]   # repos tag themselves with these
SEARCH_SORTS = {"best": "Best match", "popular": "Most popular (stars)", "updated": "Recently updated"}
SEARCH_CATEGORIES = {"Any category": ""}      # label -> keyword added to a GitHub search
SEARCH_CATEGORIES.update({c["label"]: c["keyword"] for c in catalog.CATEGORIES.values()})


def github_api(path, params=None):
    url = "https://api.github.com" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "wam-addon-manager"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise RuntimeError("GitHub's search limit hit (about 10 searches a minute). Wait a moment, "
                               "or set GITHUB_TOKEN for a higher limit.")
        if e.code == 422:
            raise RuntimeError("GitHub couldn't make sense of that search. Try simpler words.")
        raise RuntimeError(f"GitHub returned an error: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach GitHub: {e.reason}")


def search_github(query, sort="best", forever_only=False, limit=30):
    """Find GitHub-hosted WoW addons. Returns [{"key", "name", "desc", "stars", "updated", "url", "archived"}].
    Coverage depends on repos tagging themselves with a wow-addon topic."""
    words = " ".join(w for w in (query.strip(), "forever" if forever_only else "") if w)
    merged = {}
    for topic in SEARCH_TOPICS:
        params = {"q": f"{words} topic:{topic}".strip(), "per_page": min(limit, 50)}
        if sort == "popular":
            params.update(sort="stars", order="desc")
        elif sort == "updated":
            params.update(sort="updated", order="desc")
        for item in github_api("/search/repositories", params).get("items", []):
            merged.setdefault(item["full_name"], item)
    results = [{
        "key": it["full_name"],
        "name": it.get("name", it["full_name"]),
        "desc": (it.get("description") or "").strip(),
        "stars": it.get("stargazers_count", 0),
        "updated": str(it.get("pushed_at") or it.get("updated_at") or "")[:10],
        "url": it.get("html_url", ""),
        "archived": bool(it.get("archived")),
        # Only a hint: does the repo itself say it's for Forever? (Costs no extra API calls.)
        "forever": bool(re.search(r"forever", " ".join([it.get("name", ""), it.get("description") or "",
                                                         " ".join(it.get("topics") or [])]), re.I)),
    } for it in merged.values()]
    if sort == "popular":
        results.sort(key=lambda r: r["stars"], reverse=True)
    elif sort == "updated":
        results.sort(key=lambda r: r["updated"], reverse=True)
    return results[:limit]


# ---------------------------------------------------------------------------
# The addon catalog (a curated list of addons and where they come from)
# ---------------------------------------------------------------------------

CATALOG_CACHE = DATA_DIR / "catalog-index.json"
CATALOG_MAX_AGE = 24 * 3600          # re-download at most once a day
BUNDLED_INDEX = Path(__file__).resolve().parent / "data" / "index.json"
CATEGORY_IDS = {c["label"]: cid for cid, c in catalog.CATEGORIES.items()}   # label -> id


def _read_index(path):
    try:
        index, _ = catalog.normalize_index(json.loads(Path(path).read_text(encoding="utf-8")))
        return index
    except (OSError, ValueError):
        return None


def load_catalog(settings=None, refresh=False):
    """Returns (index, origin). Never raises: online -> cached copy -> the copy bundled with wam."""
    settings = settings or get_settings(load_config())
    url = (settings.get("catalog_url") or "").strip()
    if url.startswith("https://"):
        fresh = CATALOG_CACHE.exists() and time.time() - CATALOG_CACHE.stat().st_mtime < CATALOG_MAX_AGE
        if fresh and not refresh:
            cached = _read_index(CATALOG_CACHE)
            if cached:
                return cached, "cached"
        try:
            with _open(url, timeout=10) as r:
                body = r.read(catalog.MAX_INDEX_BYTES + 1)
            if len(body) > catalog.MAX_INDEX_BYTES:
                raise ValueError("catalog index is too large")
            index, _ = catalog.normalize_index(json.loads(body))
            CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
            CATALOG_CACHE.write_text(json.dumps(index), encoding="utf-8")
            return index, "online"
        except Exception:
            cached = _read_index(CATALOG_CACHE)
            if cached:
                return cached, "cached (couldn't reach it)"
    return _read_index(BUNDLED_INDEX) or {"schema": 1, "categories": {}, "addons": []}, "bundled"


def catalog_results(index, query="", category_id=None, sort="best", verified_only=False):
    """Catalog search, shaped like GitHub search results so one screen can show both."""
    out = []
    for e in catalog.search(index, query, category_id, sort, verified_only):
        sources = e["sources"]
        out.append({
            "key": catalog.source_key(e),
            "name": e["name"],
            "desc": e["description"],
            "stars": e.get("stars"),
            "updated": e.get("updated", ""),
            "forever": {"yes": "yes", "unknown": "?", "no": "no"}[e["forever"]],
            "source": ("GitHub" if sources.get("github") else "CurseForge") + (" (manual)" if e["download"] == "manual" else ""),
            "archived": False,
            "url": e["homepage"],
        })
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def detect_wow_roots():
    """WoW folders found in the usual Lutris locations."""
    found = []
    for pattern in LUTRIS_GLOBS:
        found.extend(Path.home().glob(pattern))
    return found


def add_install_path(path_text):
    """Remember a game install. Returns (ok, [message lines]). Never prints."""
    dirs = find_addons_dirs(Path(path_text))
    if not dirs:
        return False, [f"Couldn't find an Interface/AddOns folder under: {path_text}",
                       "Try pointing at the _classic_beta_ folder (or the AddOns folder itself)."]
    config = load_config()
    known = {i["addons_dir"] for i in config["installs"]}
    lines = []
    for d in dirs:
        if str(d) in known:
            lines.append(f"Already added: {label_for(d)}  ({d})")
            continue
        config["installs"].append({"label": label_for(d), "addons_dir": str(d)})
        lines.append(f"Added: {label_for(d)}  ({d})")
    save_config(config)
    return True, lines


def cmd_add(args):
    ok, lines = add_install_path(args.path)
    print("\n".join(lines))
    return 0 if ok else 1


def cmd_detect(args):
    roots = detect_wow_roots()
    if not roots:
        print("Didn't find a Lutris WoW folder automatically. Use `add` with your path.")
        return 1
    for wow_root in roots:
        for d in find_addons_dirs(wow_root):
            print(f"Found: {label_for(d)}\n  wam add \"{d.parent.parent}\"")
    return 0


def cmd_list(args):
    config = load_config()
    if not config["installs"]:
        print("No installs yet. Run: wam add \"<your _classic_beta_ folder>\"")
        return 1
    for i in config["installs"]:
        print(f"{i['label']:<22} {i['addons_dir']}")
    return 0


def print_table(groups, expand=False):
    headers = ("Addon", "Version", "Forever?", "Found on")
    rows = []
    for g in groups:
        root, members = g["root"], g["members"]
        title = root["title"] + (f" (+{len(members)})" if members else "")
        statuses = {m["forever"] for m in [root] + members}
        forever = root["forever"] if len(statuses) == 1 else "mixed"
        sources = ", ".join(dict.fromkeys(
            x for m in [root] + members for x in m["sources"].split(", ") if x != "-"
        )) or "-"
        rows.append((title, root["version"], forever, sources, members))
    widths = [
        max(len(headers[c]), *(len(r[c]) for r in rows)) if rows else len(headers[c])
        for c in range(4)
    ]
    widths[0] = min(widths[0], 40)
    widths[1] = min(widths[1], 16)
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for title, version, forever, sources, members in rows:
        print(fmt.format(title[: widths[0]], version[: widths[1]], forever, sources))
        if expand:
            for m in members:
                print(f"    - {m['folder']}  ({m['version']})")


def cmd_scan(args):
    config = load_config()
    if not config["installs"]:
        print("No installs yet. Run: wam add \"<your _classic_beta_ folder>\"")
        return 1
    for install in config["installs"]:
        addons_dir = Path(install["addons_dir"])
        print(f"\n== {install['label']}  ({addons_dir})")
        if not addons_dir.is_dir():
            print("   Folder not found. Is the drive mounted / is the path still right?")
            continue
        addons, skipped = scan_install(addons_dir)
        if not addons:
            print("   No addons installed here yet.")
            continue
        groups = group_addons(addons)
        print_table(groups, expand=args.expand)
        ready = sum(1 for a in addons if a["forever"] == "yes")
        print(
            f"\n{len(addons)} addon folders in {len(groups)} addons, {ready} folders marked for WoW Forever."
            + (f" ({skipped} folders had no .toc and were skipped.)" if skipped else "")
        )
    return 0


def cmd_latest(args):
    try:
        releases = github_releases(args.repo)
    except RuntimeError as e:
        print(e)
        return 1
    if not releases:
        print(f"{args.repo} has no published releases.")
        return 1
    rel = releases[0]
    kind = "pre-release" if rel.get("prerelease") else "release"
    print(f"{args.repo}: latest is {rel['tag_name']} ({kind}, published {rel['published_at'][:10]})")
    stable = next((r for r in releases if not r.get("prerelease")), None)
    if rel.get("prerelease") and stable:
        print(f"  newest non-pre-release: {stable['tag_name']}")
    assets = rel.get("assets", [])
    if not assets:
        print("  No downloadable files attached to this release.")
    for a in assets:
        note = ""
        if a["name"].lower().endswith(".zip"):
            note = "  <- addon zip"
        elif a["name"].lower() == "release.json":
            note = "  <- packager metadata"
        print(f"  {a['name']}  ({human_size(a['size'])}){note}")
    return 0


def version_matches(installed, label):
    """Does a file/tag label (e.g. 'GearQuestForever-v0.5.4-beta.zip') name this installed version?"""
    v = installed.strip().lstrip("vV")
    if not v or v == "?":
        return False
    base = re.sub(r"\.(zip|rar|7z)$", "", label, flags=re.I)
    return bool(re.search(r"(?<![0-9A-Za-z.])[vV]?" + re.escape(v) + r"(?![0-9A-Za-z.])", base))


def load_sources():
    """User-maintained map of addon folder -> source (e.g. {"Auctionator": "curseforge:6124"})."""
    if SOURCES_PATH.exists():
        try:
            return json.loads(SOURCES_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            print(f"Warning: couldn't read {SOURCES_PATH}, ignoring it.", file=sys.stderr)
    return {}


def save_sources(sources):
    SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_PATH.write_text(json.dumps(sources, indent=2, sort_keys=True))


def track_group(tracked, group, key, settings):
    """Look up `key`, then record the installed addon group as tracked by it.
    Returns (ok, message). Changes only wam's notes, never your addon files."""
    members = [group["root"]] + group["members"]
    folders = [m["folder"] for m in members]
    name = group["root"]["title"]
    cand, err = resolve_source(key, settings["prereleases"], settings)
    if cand is None:
        return False, f"{name}: skipped ({err})"
    installed_version = group["root"]["version"]
    matched = version_matches(installed_version, cand["label"])
    for other in [k for k, info in tracked.items() if set(info["folders"]) & set(folders)]:
        del tracked[other]  # re-linking: replace the old record
    tracked[key] = {
        "tag": cand["tag"] if matched else "unverified",
        "label": installed_version,
        "name": name,
        "asset": None,
        "folders": folders,
        "installed": datetime.now().isoformat(timespec="seconds"),
        "adopted": True,
    }
    status = ("up to date" if matched
              else "can't confirm it matches the newest build; next `update` will reinstall it (with backup)")
    return True, f"{name}: now tracked via {key}, installed {installed_version} ({status})"


def adopt_installed(install, log=print):
    """Bring addons you installed by hand under wam's tracking (changes only wam's notes, not your files).
    Sources, in order: your own `link`s, links inside the addon's own files, the catalog.
    Returns (number adopted, number with no known source)."""
    config = load_config()
    settings = get_settings(config)
    sources = load_sources()
    index, _ = load_catalog(settings)
    state = load_state()
    tracked = state["tracked"].setdefault(install["addons_dir"], {})
    tracked_folders = {f for info in tracked.values() for f in info["folders"]}
    addons, _ = scan_install(Path(install["addons_dir"]))
    have_cf_key = bool(get_cf_key())
    adopted = skipped_no_source = 0

    for g in group_addons(addons):
        members = [g["root"]] + g["members"]
        folders = [m["folder"] for m in members]
        if any(f in tracked_folders for f in folders):
            continue
        name = g["root"]["title"]
        mapped = next((sources[f] for f in folders if f in sources), None)
        github = next((m["github"] for m in members if m["github"]), "")
        cf_id = next((m["cf_id"] for m in members if m["cf_id"]), "")
        cf_slug = next((m["cf_slug"] for m in members if m["cf_slug"]), "")
        key = mapped or github or None
        via_catalog = False
        if not key and (cf_id or cf_slug):
            if not have_cf_key:
                log(f"- {name}: lives on CurseForge; run `cf-key` first to track it.")
                continue
            try:
                pid = cf_id or cf_project_id_from_slug(cf_slug)
            except RuntimeError as e:
                log(f"- {name}: couldn't look up its CurseForge project ({e})")
                log(f"    Workaround: find its 'Project ID' on its CurseForge page, then run:"
                    f"  wam link {folders[0]} cf:<ID>")
                continue
            key = f"curseforge:{pid}" if pid else None
        if not key:
            entry = catalog.match_installed(index, folders)
            catalog_key = catalog.source_key(entry) if entry else None
            if catalog_key:
                if catalog_key.startswith("curseforge:") and not have_cf_key:
                    log(f"- {name}: the catalog says it lives on CurseForge; run `cf-key` first to track it.")
                    continue
                key, via_catalog = catalog_key, True
        if not key:
            skipped_no_source += 1
            continue
        ok, msg = track_group(tracked, g, key, settings)
        log(f"- {msg}" + ("  [matched in the catalog]" if via_catalog and ok else ""))
        adopted += ok

    save_state(state)
    log(f"\nTracked {adopted} addon(s)." + (f" {skipped_no_source} addon(s) have no known source yet "
        f"(attach one with: wam link <folder> owner/repo  or  cf:<ID>)." if skipped_no_source else ""))
    return adopted, skipped_no_source


def cmd_adopt(args):
    install, err = choose_install(load_config(), args.to)
    if err:
        print(err)
        return 1
    adopt_installed(install)
    return 0


def link_addon(folder, source_text, install):
    """Tell wam where an installed addon comes from. Returns (ok, message). Never prints."""
    key = normalize_source(source_text)
    if key is None:
        return False, "Source must be a GitHub repo like owner/repo, or a CurseForge project like cf:6124"
    addons, _ = scan_install(Path(install["addons_dir"]))
    wanted = folder.lower()
    group = next((g for g in group_addons(addons)
                  if any(m["folder"].lower() == wanted for m in [g["root"]] + g["members"])), None)
    if group is None:
        return False, f"No installed addon folder named '{folder}'. See folder names with: wam scan --expand"
    state = load_state()
    tracked = state["tracked"].setdefault(install["addons_dir"], {})
    ok, msg = track_group(tracked, group, key, get_settings(load_config()))
    if not ok:
        return False, msg
    save_state(state)
    sources = load_sources()
    sources[group["root"]["folder"]] = key
    save_sources(sources)
    return True, msg


def cmd_link(args):
    """Tell wam where an addon comes from: link <folder> owner/repo  or  link <folder> cf:<project id>."""
    if normalize_source(args.source) is None:
        print("Source must be a GitHub repo like owner/repo, or a CurseForge project like cf:6124")
        return 1
    install, err = choose_install(load_config(), args.to)
    if err:
        print(err)
        return 1
    ok, msg = link_addon(args.folder, args.source, install)
    print(msg)
    return 0 if ok else 1


def cmd_install(args):
    key = normalize_source(args.source)
    if key is None:
        print("Give a GitHub repo like owner/repo, or a CurseForge project like cf:1698950")
        return 1
    config = load_config()
    install, err = choose_install(config, args.to)
    if err:
        print(err)
        return 1
    ok = do_install(key, install,
                    prerelease=args.prerelease or get_settings(config)["prereleases"],
                    assume_yes=args.yes, dry_run=args.dry_run)
    return 0 if ok else 1


def cmd_installed(args):
    state = load_state()
    any_rows = False
    for addons_dir, entries in state["tracked"].items():
        for key, info in entries.items():
            any_rows = True
            shown = info.get("label") or info["tag"]
            adopted = "  (adopted)" if info.get("adopted") else ""
            print(f"{display_name(key, info):<32} {shown:<34} {len(info['folders'])} folder(s){adopted}")
    if not any_rows:
        print("Nothing tracked yet. Try: wam install owner/repo   or   wam adopt")
    return 0


def _tracked_for(config, state):
    for install in config["installs"]:
        for repo, info in state["tracked"].get(install["addons_dir"], {}).items():
            yield install, repo, info


def cmd_check(args):
    config, state = load_config(), load_state()
    prerelease = args.prerelease or get_settings(config)["prereleases"]
    rows = check_all(config, state, prerelease)
    if not rows:
        print("Nothing to check yet. Install something (wam install ...) or run: wam adopt")
        return 1
    behind = 0
    for install, key, info, cand, err in rows:
        name = display_name(key, info)
        if err:
            print(f"{name}: {err}")
        elif cand["tag"] != info["tag"]:
            behind += 1
            manual = f"   [manual download: {cand.get('page')}]" if cand.get("blocked") else ""
            print(f"{name}: update available  {info.get('label') or info['tag']} -> {cand['label']}{manual}")
        else:
            print(f"{name}: up to date ({cand['label']})")
    if behind:
        print(f"\n{behind} update(s) available. Run: wam update")
    return 0


def cmd_update(args):
    config, state = load_config(), load_state()
    prerelease = args.prerelease or get_settings(config)["prereleases"]
    rows = check_all(config, state, prerelease)
    if not rows:
        print("Nothing to update yet.")
        return 1
    for install, key, info, cand, err in rows:
        name = display_name(key, info)
        if err:
            print(f"{name}: {err}")
        elif cand["tag"] == info["tag"]:
            print(f"{name}: up to date ({cand['label']})")
        elif cand.get("blocked"):
            print(f"{name}: update available ({cand['label']}) but {cand['blocked']}.")
            print(f"  Get it from: {cand.get('page')}")
        else:
            do_install(key, install, prerelease=prerelease, assume_yes=args.yes, dry_run=args.dry_run)
    return 0


def cmd_backups(args):
    backups = list_backups()
    if not backups:
        print("No backups yet. wam makes one whenever it replaces an addon.")
        return 0
    for b in backups:
        print(f"{b['name']}\n    {b['when']}  from {b['source']}  ->  {', '.join(b['folders'])}")
    print("\nPut one back with:  wam restore <name or the start of the name>")
    return 0


def cmd_restore(args):
    matches = [b for b in list_backups() if b["name"].startswith(args.name)]
    if len(matches) != 1:
        print("No backup matches that name." if not matches
              else f"{len(matches)} backups match; give more of the name.")
        return 1
    install, err = choose_install(load_config(), args.to)
    if err:
        print(err)
        return 1
    b = matches[0]
    print(f"Restore {', '.join(b['folders'])} from {b['when']} into {install['label']}?")
    if not args.yes and not confirm("Proceed?"):
        print("Cancelled.")
        return 1
    return 0 if restore_backup(b["path"], install) else 1


def cmd_catalog(args):
    settings = get_settings(load_config())
    index, origin = load_catalog(settings, refresh=(args.action == "refresh"))
    print(f"Catalog: {len(index['addons'])} addon(s), from the {origin} copy.")
    if settings.get("catalog_url"):
        print(f"Online address: {settings['catalog_url']}")
    else:
        print("No online address set, so wam uses the catalog bundled with it. To use a published one:")
        print("  wam settings catalog-url https://<you>.github.io/<repo>/index.json")
    if args.action == "refresh" and origin != "online":
        print("(Couldn't download a fresh copy; check the address and your connection.)")
    return 0


def cmd_search(args):
    query = " ".join(args.terms)
    if args.github:
        try:
            results = search_github(query, args.sort, args.forever)
        except RuntimeError as e:
            print(e)
            return 1
        if not results:
            print("Nothing found. This only covers GitHub-hosted addons that tag themselves 'wow-addon'.")
            return 0
        for r in results:
            print(f"{r['key']:<45} {r['stars']:>5} stars  updated {r['updated']}"
                  + ("   [mentions Forever]" if r["forever"] else ""))
            if r["desc"]:
                print(f"    {r['desc'][:100]}")
        print("\nInstall one with:  wam install <owner/repo>")
        return 0
    index, origin = load_catalog()
    rows = catalog_results(index, query, args.category, args.sort, args.forever)
    if not rows:
        print("Nothing in the catalog matches. For a wider (rougher) search of GitHub, add --github.")
        return 0
    for r in rows:
        print(f"{r['key']:<45} Forever: {r['forever']:<4} {r['source']}")
        print(f"    {r['name']}" + (f" - {r['desc'][:90]}" if r["desc"] else ""))
    print(f"\n{len(rows)} from the {origin} catalog. Install one with:  wam install <the first column>")
    return 0


def main():
    if len(sys.argv) == 1:
        try:
            from .tui import run
        except ImportError as e:
            print(f"The visual interface needs the 'textual' package ({e}).")
            print("Install wam with:  pipx install -e <the wam project folder>")
            print("Meanwhile, `wam --help` lists the typed commands.")
            sys.exit(1)
        run()
        return
    parser = argparse.ArgumentParser(prog="wam", description="WoW addon manager (work in progress)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="remember a game install")
    p_add.add_argument("path")
    p_add.set_defaults(func=cmd_add)

    sub.add_parser("detect", help="look for Lutris WoW installs").set_defaults(func=cmd_detect)
    sub.add_parser("list", help="show remembered installs").set_defaults(func=cmd_list)
    p_scan = sub.add_parser("scan", help="list addons in each install")
    p_scan.add_argument("--expand", action="store_true", help="also list each addon's folders")
    p_scan.set_defaults(func=cmd_scan)

    p_latest = sub.add_parser("latest", help="show the newest release of a GitHub repo")
    p_latest.add_argument("repo", help="owner/repo, e.g. sanjaygbhat/wowforever-waypoint-tracker")
    p_latest.set_defaults(func=cmd_latest)

    p_install = sub.add_parser("install", help="install an addon from a GitHub repo")
    p_install.add_argument("source", help="owner/repo (GitHub) or cf:1234 (CurseForge project ID)")
    p_install.add_argument("--to", help="which install (part of its label), if you have several")
    p_install.add_argument("--prerelease", action="store_true", help="allow pre-releases")
    p_install.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p_install.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    p_install.set_defaults(func=cmd_install)

    p_set = sub.add_parser("settings", help="show or change settings")
    p_set.add_argument("key", nargs="?", help="update-mode or prereleases")
    p_set.add_argument("value", nargs="?")
    p_set.set_defaults(func=cmd_settings)

    p_sched = sub.add_parser("schedule", help="turn the daily background check on or off")
    p_sched.add_argument("action", choices=["on", "off", "status"])
    p_sched.add_argument("--time", help="time of day, e.g. 18:30 (default: once a day, any time)")
    p_sched.set_defaults(func=cmd_schedule)

    sub.add_parser("auto-check", help="(used by the timer) act according to update-mode").set_defaults(func=cmd_autocheck)

    sub.add_parser("cf-key", help="save your CurseForge API key").set_defaults(func=cmd_cf_key)
    p_cff = sub.add_parser("cf-files", help="show a CurseForge project's recent files (read-only)")
    p_cff.add_argument("project", help="project ID (e.g. 1698950) or slug (e.g. auctionator)")
    p_cff.add_argument("--count", type=int, default=8)
    p_cff.set_defaults(func=cmd_cf_files)

    p_adopt = sub.add_parser("adopt", help="start tracking addons you installed by hand")
    p_adopt.add_argument("--to", help="which install (part of its label), if you have several")
    p_adopt.set_defaults(func=cmd_adopt)

    p_link = sub.add_parser("link", help="tell wam where an addon comes from")
    p_link.add_argument("folder", help="an addon folder name, e.g. Auctionator")
    p_link.add_argument("source", help="owner/repo (GitHub) or cf:<CurseForge project ID>")
    p_link.add_argument("--to", help="which install (part of its label), if you have several")
    p_link.set_defaults(func=cmd_link)

    sub.add_parser("backups", help="list backups wam has made").set_defaults(func=cmd_backups)
    p_restore = sub.add_parser("restore", help="put a backup back into AddOns")
    p_restore.add_argument("name", help="backup name (or its start), from `wam backups`")
    p_restore.add_argument("--to", help="which install (part of its label), if you have several")
    p_restore.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p_restore.set_defaults(func=cmd_restore)

    p_search = sub.add_parser("search", help="find addons (in the catalog, or on GitHub with --github)")
    p_search.add_argument("terms", nargs="*", help="e.g. fishing")
    p_search.add_argument("--sort", choices=["best", "popular", "updated"], default="best")
    p_search.add_argument("--forever", action="store_true", help="only things the author says support Forever")
    p_search.add_argument("--category", choices=sorted(catalog.CATEGORIES), help="catalog category")
    p_search.add_argument("--github", action="store_true", help="search GitHub instead of the catalog")
    p_search.set_defaults(func=cmd_search)

    p_catalog = sub.add_parser("catalog", help="show or refresh the addon catalog")
    p_catalog.add_argument("action", nargs="?", choices=["info", "refresh"], default="info")
    p_catalog.set_defaults(func=cmd_catalog)

    sub.add_parser("installed", help="show addons installed through wam").set_defaults(func=cmd_installed)

    for name, fn, helptext in (("check", cmd_check, "look for updates"), ("update", cmd_update, "install updates")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--prerelease", action="store_true", help="allow pre-releases")
        if name == "update":
            sp.add_argument("--yes", action="store_true", help="don't ask for confirmation")
            sp.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
        sp.set_defaults(func=fn)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
