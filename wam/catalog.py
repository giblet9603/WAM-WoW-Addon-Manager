# SPDX-License-Identifier: GPL-3.0-or-later
"""wam's addon catalog: entry format, validation, index building and searching.

Standard library only, so the same file runs inside the app and in the catalog's CI job:

    python -m wam.catalog validate
    python -m wam.catalog build --out site/index.json --stamp --github-stats

An *entry* is one small JSON file in catalog/addons/. The *index* is all entries merged into one
file that the app downloads. Everything in a downloaded index is treated as untrusted data and
re-validated before use.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# id -> label shown in the app, and the keyword used when the same category is applied to a GitHub search
CATEGORIES = {
    "auction-economy": {"label": "Auction & economy", "keyword": "auction"},
    "audio": {"label": "Audio & voice", "keyword": "audio"},
    "bags-inventory": {"label": "Bags & inventory", "keyword": "bag"},
    "chat-social": {"label": "Chat & social", "keyword": "chat"},
    "combat": {"label": "Combat", "keyword": "combat"},
    "dungeons-raids": {"label": "Dungeons & raids", "keyword": "dungeon"},
    "fishing": {"label": "Fishing", "keyword": "fishing"},
    "gear": {"label": "Gear & upgrades", "keyword": "gear"},
    "leveling-quests": {"label": "Quests & leveling", "keyword": "quest"},
    "maps-navigation": {"label": "Maps & navigation", "keyword": "map"},
    "nameplates-unit-frames": {"label": "Nameplates & unit frames", "keyword": "nameplate"},
    "professions": {"label": "Professions", "keyword": "profession"},
    "tooltips": {"label": "Tooltips", "keyword": "tooltip"},
    "utilities": {"label": "Utilities", "keyword": "utility"},
}
FOREVER_VALUES = ("yes", "unknown", "no")      # does the author say it supports WoW Forever?
DOWNLOAD_VALUES = ("allowed", "manual")        # manual = the author doesn't allow third-party downloads
SOURCE_KEYS = ("github", "curseforge", "wago")
ENTRY_FIELDS = {"id", "name", "description", "categories", "folders", "sources",
                "forever", "download", "homepage", "featured"}
INDEX_EXTRA_FIELDS = {"stars", "updated"}      # filled in by the build, never written by hand

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FOLDER_RE = re.compile(r"^[A-Za-z0-9_.!'-]+\*?$")   # a WoW addon folder name, optional trailing *
MAX_INDEX_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_entry(data, stem=None):
    """Return a list of problems with one catalog entry (empty list = valid)."""
    if not isinstance(data, dict):
        return ["entry must be a JSON object"]
    errors = []
    for key in data:
        if key not in ENTRY_FIELDS:
            errors.append(f"unknown field '{key}' (typo?)")

    def need(key, kind):
        value = data.get(key)
        if not isinstance(value, kind):
            errors.append(f"'{key}' is required and must be a {kind.__name__}")
            return None
        return value

    ident = need("id", str)
    if ident is not None:
        if not ID_RE.match(ident):
            errors.append("'id' must be lowercase letters/digits separated by single dashes, e.g. gearquest-forever")
        if stem is not None and ident != stem:
            errors.append(f"'id' ({ident}) must match the file name ({stem}.json)")
    name = need("name", str)
    if name is not None and not (1 <= len(name) <= 80):
        errors.append("'name' must be 1-80 characters")
    if "description" in data:
        if not isinstance(data["description"], str) or len(data["description"]) > 200:
            errors.append("'description' must be text of at most 200 characters")
    cats = data.get("categories", [])
    if not isinstance(cats, list) or any(c not in CATEGORIES for c in cats):
        errors.append("'categories' must be a list of: " + ", ".join(CATEGORIES))
    folders = data.get("folders", [])
    if not isinstance(folders, list) or any(
            not isinstance(f, str) or not FOLDER_RE.match(f) or f.startswith(".") for f in folders):
        errors.append("'folders' must be addon folder names; only a trailing * is allowed, e.g. \"Foo*\"")
    sources = need("sources", dict)
    if sources is not None:
        for k in sources:
            if k not in SOURCE_KEYS:
                errors.append(f"unknown source '{k}' (use: {', '.join(SOURCE_KEYS)})")
        gh, cf = sources.get("github"), sources.get("curseforge")
        if gh is not None and not (isinstance(gh, str) and REPO_RE.match(gh)):
            errors.append("sources.github must look like owner/repo")
        if cf is not None and not (isinstance(cf, int) and not isinstance(cf, bool) and cf > 0):
            errors.append("sources.curseforge must be the numeric CurseForge project ID")
        if "wago" in sources and not isinstance(sources["wago"], str):
            errors.append("sources.wago must be text")
        if gh is None and cf is None:
            errors.append("sources needs a github repo or a curseforge project ID (wago alone can't be installed from)")
    if data.get("forever") not in FOREVER_VALUES:
        errors.append("'forever' must be one of: " + ", ".join(FOREVER_VALUES))
    if data.get("download", "allowed") not in DOWNLOAD_VALUES:
        errors.append("'download' must be one of: " + ", ".join(DOWNLOAD_VALUES))
    home = data.get("homepage")
    if home not in (None, "") and not (isinstance(home, str) and home.startswith("https://")):
        errors.append("'homepage' must be an https:// link")
    if "featured" in data and not isinstance(data["featured"], bool):
        errors.append("'featured' must be true or false")
    return errors


def normalize_entry(data):
    """Fill in defaults so every consumer sees the same shape."""
    out = {
        "id": data["id"],
        "name": data["name"],
        "description": data.get("description", ""),
        "categories": list(data.get("categories", [])),
        "folders": list(data.get("folders", [])),
        "sources": dict(data["sources"]),
        "forever": data["forever"],
        "download": data.get("download", "allowed"),
        "homepage": data.get("homepage", ""),
        "featured": bool(data.get("featured", False)),
    }
    for key in INDEX_EXTRA_FIELDS:
        if key in data:
            out[key] = data[key]
    return out


def load_entries(directory):
    """Read every entry file. Returns (entries, errors). Also catches duplicate sources."""
    entries, errors = [], []
    seen_repo, seen_cf = {}, {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{path.name}: can't read as JSON ({e})")
            continue
        problems = validate_entry(data, path.stem)
        errors.extend(f"{path.name}: {p}" for p in problems)
        if problems:
            continue
        entry = normalize_entry(data)
        gh = entry["sources"].get("github", "").lower()
        cf = entry["sources"].get("curseforge")
        if gh and gh in seen_repo:
            errors.append(f"{path.name}: github repo {gh} is already used by {seen_repo[gh]}")
        if cf and cf in seen_cf:
            errors.append(f"{path.name}: curseforge project {cf} is already used by {seen_cf[cf]}")
        seen_repo.setdefault(gh, path.name)
        seen_cf.setdefault(cf, path.name)
        entries.append(entry)
    return entries, errors


# ---------------------------------------------------------------------------
# Building the index
# ---------------------------------------------------------------------------

def github_stats(entries, token=None, get_json=None, warn=print):
    """{entry id: {"stars": n, "updated": "YYYY-MM-DD"}} for entries that have a GitHub repo.
    Failures are skipped (the index just won't have numbers for them)."""
    def default_get(url):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "wam-catalog-build"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            return json.load(r)

    get_json = get_json or default_get
    stats = {}
    for e in entries:
        repo = e["sources"].get("github")
        if not repo:
            continue
        try:
            info = get_json(f"https://api.github.com/repos/{repo}")
            stats[e["id"]] = {"stars": int(info.get("stargazers_count", 0)),
                              "updated": str(info.get("pushed_at") or "")[:10]}
        except Exception as ex:  # a missing repo shouldn't block the whole build
            warn(f"warning: couldn't read stats for {repo}: {ex}")
    return stats


def build_index(entries, stamp=False, stats=None):
    out_entries = []
    for e in sorted(entries, key=lambda x: x["id"]):
        e = dict(e)
        e.update((stats or {}).get(e["id"], {}))
        out_entries.append(e)
    index = {
        "schema": SCHEMA_VERSION,
        "categories": {cid: c["label"] for cid, c in CATEGORIES.items()},
        "addons": out_entries,
    }
    if stamp:
        index["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return index


def normalize_index(raw):
    """Check an index we didn't build ourselves (downloaded or cached). Returns (index, warnings).
    Raises ValueError if it's unusable. Bad entries are dropped, never trusted."""
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"not a schema-{SCHEMA_VERSION} catalog index")
    if not isinstance(raw.get("addons"), list):
        raise ValueError("index has no addon list")
    good, warnings = [], []
    for item in raw["addons"]:
        extras = {k: item[k] for k in INDEX_EXTRA_FIELDS if isinstance(item, dict) and k in item}
        base = {k: v for k, v in item.items() if k not in INDEX_EXTRA_FIELDS} if isinstance(item, dict) else item
        problems = validate_entry(base)
        if not (isinstance(extras.get("stars", 0), int) and isinstance(extras.get("updated", ""), str)):
            problems.append("bad stars/updated")
        if problems:
            warnings.append(f"dropped an entry ({problems[0]})")
            continue
        good.append(normalize_entry({**base, **extras}))
    index = {"schema": SCHEMA_VERSION, "categories": {c: v["label"] for c, v in CATEGORIES.items()}, "addons": good}
    if isinstance(raw.get("generated"), str):
        index["generated"] = raw["generated"][:20]
    return index, warnings


# ---------------------------------------------------------------------------
# Using the index
# ---------------------------------------------------------------------------

def folder_matches(pattern, folder):
    """Entry folders are exact names, or 'Prefix*'. Case-insensitive, like WoW itself."""
    pattern, folder = pattern.lower(), folder.lower()
    return folder.startswith(pattern[:-1]) if pattern.endswith("*") else folder == pattern


def match_installed(index, folders):
    """The catalog entry that claims the most of these installed folders, or None."""
    best, best_n = None, 0
    for entry in index["addons"]:
        n = sum(1 for f in folders if any(folder_matches(p, f) for p in entry["folders"]))
        if n > best_n:
            best, best_n = entry, n
    return best


def source_key(entry):
    """How wam should fetch this addon: 'owner/repo' (no key needed) or 'curseforge:<id>'."""
    if entry["sources"].get("github"):
        return entry["sources"]["github"]
    if entry["sources"].get("curseforge"):
        return f"curseforge:{entry['sources']['curseforge']}"
    return None


def search(index, query="", category=None, sort="best", verified_only=False, limit=200):
    """Search the catalog. category is a category id. sort: best, popular, updated, name."""
    words = query.lower().split()
    scored = []
    for e in index["addons"]:
        if verified_only and e["forever"] != "yes":
            continue
        if category and category not in e["categories"]:
            continue
        name = e["name"].lower()
        hay = " ".join([e["name"], e["description"], e["id"], " ".join(e["categories"]),
                        " ".join(e["folders"])]).lower()
        if words and not all(w in hay for w in words):
            continue
        scored.append((sum(3 if w in name else 1 for w in words), e))
    if sort == "popular":
        scored.sort(key=lambda t: (not t[1]["featured"], -t[1].get("stars", -1), t[1]["name"].lower()))
    elif sort == "updated":
        scored.sort(key=lambda t: (t[1].get("updated", "") == "", _neg(t[1].get("updated", "")), t[1]["name"].lower()))
    elif sort == "name":
        scored.sort(key=lambda t: t[1]["name"].lower())
    else:
        scored.sort(key=lambda t: (-t[0], not t[1]["featured"], t[1]["name"].lower()))
    return [e for _, e in scored][:limit]


def _neg(text):
    """Sort key that puts later dates first when used in ascending order."""
    return tuple(-ord(c) for c in text)


# ---------------------------------------------------------------------------
# Command line (used by CI and by contributors)
# ---------------------------------------------------------------------------

def _repo_root():
    return Path(__file__).resolve().parent.parent


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m wam.catalog", description="Work with wam's addon catalog")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "build"):
        sp = sub.add_parser(name)
        sp.add_argument("--dir", default=str(_repo_root() / "catalog" / "addons"))
        if name == "build":
            sp.add_argument("--out", required=True)
            sp.add_argument("--stamp", action="store_true", help="add a generated-at time")
            sp.add_argument("--github-stats", action="store_true", help="add stars/updated from GitHub")
    args = parser.parse_args(argv)

    if not Path(args.dir).is_dir():
        print(f"No catalog folder at {args.dir}. Run this from a checkout of the repo, or pass --dir.")
        return 1
    entries, errors = load_entries(args.dir)
    if errors:
        print("Catalog problems:")
        for e in errors:
            print(f"  - {e}")
        return 1
    if args.cmd == "validate":
        print(f"OK: {len(entries)} catalog entries are valid.")
        return 0
    stats = github_stats(entries, os.environ.get("GITHUB_TOKEN")) if args.github_stats else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_index(entries, args.stamp, stats), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(entries)} entries).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
