# SPDX-License-Identifier: GPL-3.0-or-later
"""Full-screen terminal interface for wam, built with Textual.

Run it by typing `wam` with no arguments. Everything here is a thin layer over
wam.core: the same code the typed commands use does the real work, and anything
that changes your game folder asks first.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.color import Color, ColorParseError
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, RadioButton, RadioSet,
    RichLog, Select, Static, Switch,
)

from . import catalog, core

UPDATE_MODES = ["manual", "notify", "auto"]
THEME_ORDER = ["terminal", "wow", "alliance", "horde", "custom"]
CUSTOM_FIELDS = [("background", "Background"), ("foreground", "Text"),
                 ("primary", "Highlight"), ("accent", "Accent")]


def build_theme(name: str, colors: dict) -> Theme:
    """Turn a handful of colors into a full Textual theme. Anything not given is derived."""
    bg, fg = Color.parse(colors["background"]), Color.parse(colors["foreground"])
    primary, accent = Color.parse(colors["primary"]), Color.parse(colors["accent"])
    return Theme(
        name=name,
        primary=primary.hex6,
        secondary=colors.get("secondary") or accent.hex6,
        accent=accent.hex6,
        foreground=fg.hex6,
        background=bg.hex6,
        surface=colors.get("surface") or bg.blend(fg, 0.07).hex6,
        panel=colors.get("panel") or bg.blend(fg, 0.14).hex6,
        success=colors.get("success") or "#3fb950",
        warning=colors.get("warning") or "#e3a008",
        error=colors.get("error") or "#e5534b",
        dark=bg.brightness < 0.5,
    )


def too_similar(a: Color, b: Color) -> bool:
    """True when two colors are too close in brightness to read one on the other."""
    return abs(a.brightness - b.brightness) < 0.3


def short_source(key: str | None) -> str:
    """curseforge:6124 -> cf:6124; long GitHub names are trimmed so the table never scrolls sideways."""
    if not key:
        return "-"
    key = key.replace("curseforge:", "cf:")
    return key if len(key) <= 24 else key[:23] + "…"


# ---------------------------------------------------------------------------
# Small pop-up dialogs
# ---------------------------------------------------------------------------

class MessageScreen(ModalScreen[None]):
    """Show some text; any of Enter / Esc / the button closes it."""
    BINDINGS = [Binding("escape,enter", "close", "Close")]

    def __init__(self, title: str, body: str):
        super().__init__()
        self._title, self._body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._body, markup=False, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question. y / n / Esc work too."""
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]

    def __init__(self, title: str, body: str, yes_label: str = "Yes (y)"):
        super().__init__()
        self._title, self._body, self._yes = title, body, yes_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._body, markup=False, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self._yes, variant="success", id="yes")
                yield Button("No (n)", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class PromptScreen(ModalScreen[str | None]):
    """Ask for one line of text. Enter submits, Esc cancels."""
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, value: str = "", placeholder: str = ""):
        super().__init__()
        self._title, self._body, self._value, self._placeholder = title, body, value, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._body, markup=False, classes="dialog-body")
            yield Input(value=self._value, placeholder=self._placeholder, id="prompt-input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#prompt-input", Input).value.strip() or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProgressScreen(ModalScreen[bool]):
    """Live log of a running job. Closes only once the job has finished."""
    BINDINGS = [Binding("escape,enter", "close", "Close")]

    def __init__(self, title: str):
        super().__init__()
        self._title = title
        self.done = False
        self.ok = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(self._title, classes="dialog-title")
            yield RichLog(id="job-log", wrap=True, markup=False, highlight=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Working...", id="close", disabled=True)

    def write(self, text: str) -> None:
        self.query_one("#job-log", RichLog).write(text)

    def finish(self, ok: bool) -> None:
        self.done, self.ok = True, ok
        self.write("")
        self.write("Done." if ok else "Finished with problems (see above).")
        button = self.query_one("#close", Button)
        button.label = "Close"
        button.disabled = False
        button.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_close()

    def action_close(self) -> None:
        if self.done:
            self.dismiss(self.ok)


# ---------------------------------------------------------------------------
# Main screen: your addons
# ---------------------------------------------------------------------------

class MainScreen(Screen):
    BINDINGS = [
        Binding("c", "check", "Check"),
        Binding("u", "update", "Update"),
        Binding("a", "update_all", "Update all"),
        Binding("i", "install", "Install"),
        Binding("l", "link", "Link source"),
        Binding("o", "adopt", "Auto-link"),
        Binding("d", "discover", "Discover"),
        Binding("r", "restore", "Restore"),
        Binding("s", "settings", "Settings"),
        Binding("f", "focus_filter", "Filter"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.results: dict[str, tuple] = {}   # source key -> (candidate, error) from the last check
        self.checking = False

    # -- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Input(placeholder="Filter your addons (press f)", id="filter")
                yield DataTable(id="addons", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="right"):
                yield Static("", id="detail", markup=False)
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#addons", DataTable)
        table.add_columns("Addon", "Version", "Status", "Source")
        table.focus()
        if self.app.install is None:
            self.ask_for_install()
        else:
            self.reload()

    # -- data -------------------------------------------------------------
    def build_rows(self) -> list[dict]:
        install = self.app.install
        addons, _ = core.scan_install(Path(install["addons_dir"]))
        tracked = core.load_state()["tracked"].get(install["addons_dir"], {})
        folder_to_key = {f: k for k, info in tracked.items() for f in info["folders"]}
        rows = []
        for g in core.group_addons(addons):
            members = [g["root"]] + g["members"]
            folders = [m["folder"] for m in members]
            key = next((folder_to_key[f] for f in folders if f in folder_to_key), None)
            rows.append({
                "id": g["root"]["folder"],
                "title": g["root"]["title"],
                "version": g["root"]["version"],
                "forever": g["root"]["forever"],
                "folders": folders,
                "key": key,
                "info": tracked.get(key) if key else None,
            })
        return rows

    def catalog_entry(self, row: dict):
        index = self.app.catalog_index
        return catalog.match_installed(index, row["folders"]) if index else None

    def row_status(self, row: dict) -> str:
        if row["key"] is None:
            return "· in catalog" if self.catalog_entry(row) else "· no source"
        res = self.results.get(row["key"])
        if res is None:
            return "· tracked"
        cand, err = res
        if err:
            return "! error"
        if cand["tag"] == row["info"]["tag"]:
            return "✓ up to date"
        return "⇩ manual dl" if cand.get("blocked") else "⬆ update"

    def reload(self) -> None:
        self.rows = self.build_rows()
        self.fill_table()
        self.update_status_bar()

    def fill_table(self) -> None:
        table = self.query_one("#addons", DataTable)
        wanted = self.query_one("#filter", Input).value.strip().lower()
        previous = self.current_row()
        table.clear()
        for row in self.rows:
            hay = f"{row['title']} {' '.join(row['folders'])}".lower()
            if wanted and wanted not in hay:
                continue
            title = row["title"] + (f" (+{len(row['folders']) - 1})" if len(row["folders"]) > 1 else "")
            table.add_row(Text(title), Text(row["version"]), Text(self.row_status(row)),
                          Text(short_source(row["key"])), key=row["id"])
        if previous is not None:
            try:
                table.move_cursor(row=table.get_row_index(previous["id"]))
            except Exception:
                pass
        self.show_detail(self.current_row())

    def current_row(self) -> dict | None:
        table = self.query_one("#addons", DataTable)
        if table.row_count == 0:
            return None
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None
        return next((r for r in self.rows if r["id"] == key), None)

    def show_detail(self, row: dict | None) -> None:
        detail = self.query_one("#detail", Static)
        if row is None:
            detail.update("No addons to show.")
            return
        lines = [row["title"], "", f"Installed : {row['version']}",
                 f"Forever   : {row['forever']}"]
        if len(row["folders"]) > 1:
            lines.append(f"Folders   : {len(row['folders'])}")
            lines += [f"   - {f}" for f in row["folders"][:12]]
            if len(row["folders"]) > 12:
                lines.append(f"   ... and {len(row['folders']) - 12} more")
        else:
            lines.append(f"Folder    : {row['folders'][0]}")
        if row["key"]:
            lines.append(f"Source    : {row['key']}")
        else:
            entry = self.catalog_entry(row)
            if entry:
                lines += ["Source    : none yet, but the catalog knows it:",
                          f"   {entry['name']}",
                          f"   ({catalog.source_key(entry)})",
                          "",
                          "Press o to link everything the catalog recognises."]
            else:
                lines.append("Source    : none yet (press l to link one)")
        res = self.results.get(row["key"]) if row["key"] else None
        if res:
            cand, err = res
            if err:
                lines += ["", f"Check failed: {err}"]
            else:
                lines.append(f"Newest    : {cand['label']}")
                if cand.get("blocked"):
                    lines += ["", f"The author doesn't allow third-party downloads. Get it from:",
                              f"   {cand.get('page')}"]
                for note in cand.get("notes", []):
                    lines += ["", f"Note: {note}"]
        detail.update("\n".join(lines))

    def update_status_bar(self, text: str | None = None) -> None:
        if text is None:
            n_update = sum(1 for r in self.rows if self.row_status(r) in ("⬆ update", "⇩ manual dl"))
            text = (f"{len(self.rows)} addons"
                    + (f"  ·  {n_update} update(s) available" if self.results else "  ·  press c to check for updates"))
        self.query_one("#status", Static).update(text)

    # -- events -----------------------------------------------------------
    @on(DataTable.RowHighlighted, "#addons")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value is not None:
            self.show_detail(next((r for r in self.rows if r["id"] == event.row_key.value), None))

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self.fill_table()

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#addons", DataTable).focus()

    # -- first run: find the game ----------------------------------------
    def ask_for_install(self) -> None:
        roots = core.detect_wow_roots()
        guess = str(roots[0]) if roots else ""

        def done(text):
            if not text:
                self.app.push_screen(MessageScreen(
                    "No game folder yet",
                    "wam needs to know where WoW is. Restart it and enter the path, or run:\n  wam add \"<path>\""))
                return
            ok, lines = core.add_install_path(text)
            if not ok:
                self.app.push_screen(MessageScreen("Couldn't use that path", "\n".join(lines)),
                                     lambda _: self.ask_for_install())
                return
            self.app.install = core.load_config()["installs"][0]
            self.reload()

        self.app.push_screen(PromptScreen(
            "Where is World of Warcraft?",
            "Paste the path to your WoW folder (or the _classic_beta_ folder inside it)."
            + ("\nI found this one for you:" if guess else ""),
            value=guess), done)

    # -- actions ----------------------------------------------------------
    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_check(self) -> None:
        if self.checking or self.app.install is None:
            return
        self.checking = True
        self.update_status_bar("Checking for updates...")
        self.run_check()

    @work(thread=True)
    def run_check(self) -> None:
        try:
            config, state = core.load_config(), core.load_state()
            rows = core.check_all(config, state, core.get_settings(config)["prereleases"])
            self.app.call_from_thread(self.finish_check, rows, None)
        except Exception as e:  # network trouble etc.
            self.app.call_from_thread(self.finish_check, [], str(e))

    def finish_check(self, rows, error) -> None:
        self.checking = False
        if error:
            self.update_status_bar(f"Check failed: {error}")
            return
        mine = self.app.install["addons_dir"]
        self.results = {key: (cand, err) for install, key, info, cand, err in rows
                        if install["addons_dir"] == mine}
        self.fill_table()
        self.update_status_bar()

    def action_update(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if row["key"] is None:
            self.app.push_screen(MessageScreen(
                "No update source yet",
                f"wam doesn't know where {row['title']} comes from.\n\n"
                "Press l to link it to a GitHub repo (owner/repo) or a CurseForge project (cf:<ID>)."))
            return
        self.app.begin_install(row["key"], after=self.after_job)

    def after_job(self, *_) -> None:
        self.results = {}
        self.reload()

    def action_update_all(self) -> None:
        if not self.results:
            self.app.push_screen(MessageScreen("Check first", "Press c to check for updates, then a to update them all."))
            return
        todo = []
        for row in self.rows:
            res = self.results.get(row["key"]) if row["key"] else None
            if res and res[1] is None and res[0]["tag"] != row["info"]["tag"] and not res[0].get("blocked"):
                todo.append((row["key"], row["title"], row["info"].get("label") or row["info"]["tag"], res[0]["label"]))
        if not todo:
            self.app.push_screen(MessageScreen("Nothing to update", "Everything wam can update is already up to date."))
            return
        body = "\n".join(f"{title}: {old}  ->  {new}" for _, title, old, new in todo)
        body += "\n\nOld versions are backed up first. Close the game before continuing."
        self.app.push_screen(ConfirmScreen(f"Update {len(todo)} addon(s)?", body),
                             lambda yes: self.app.run_installs([k for k, *_ in todo], self.after_job) if yes else None)

    def action_install(self) -> None:
        def got(text):
            key = core.normalize_source(text) if text else None
            if text and key is None:
                self.app.push_screen(MessageScreen("Not understood", "Use owner/repo for GitHub, or cf:<project ID> for CurseForge."))
            elif key:
                self.app.begin_install(key, after=self.after_job)
        self.app.push_screen(PromptScreen(
            "Install an addon",
            "GitHub: owner/repo     CurseForge: cf:<project ID>\n(Or press d on the main screen to search.)",
            placeholder="owner/repo  or  cf:1698950"), got)

    def action_link(self) -> None:
        row = self.current_row()
        if row is None:
            return

        def got(text):
            if text:
                self.run_link(row["id"], text)
        self.app.push_screen(PromptScreen(
            f"Link {row['title']}",
            "Where does this addon come from?\nGitHub: owner/repo     CurseForge: cf:<project ID>",
            placeholder="owner/repo  or  cf:6124"), got)

    @work(thread=True)
    def run_link(self, folder: str, text: str) -> None:
        ok, msg = core.link_addon(folder, text, self.app.install)
        self.app.call_from_thread(self.finish_link, ok, msg)

    def finish_link(self, ok: bool, msg: str) -> None:
        self.app.push_screen(MessageScreen("Linked" if ok else "Couldn't link", msg))
        self.after_job()

    def action_adopt(self) -> None:
        if self.app.install is None:
            return
        screen = ProgressScreen("Linking your addons to their sources")
        self.app.push_screen(screen, self.after_job)
        self.run_adopt(screen)

    @work(thread=True)
    def run_adopt(self, screen: "ProgressScreen") -> None:
        def log(text):
            self.app.call_from_thread(screen.write, text)
        try:
            core.adopt_installed(self.app.install, log=log)
            ok = True
        except Exception as e:
            log(f"FAILED: {e}")
            ok = False
        self.app.call_from_thread(screen.finish, ok)

    def action_discover(self) -> None:
        self.app.push_screen(DiscoverScreen())

    def action_restore(self) -> None:
        self.app.push_screen(RestoreScreen(), lambda _: self.after_job())

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())


# ---------------------------------------------------------------------------
# Discover: search for addons
# ---------------------------------------------------------------------------

class DiscoverScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("slash", "focus_query", "Search box")]

    def __init__(self):
        super().__init__()
        self.index = None
        self.origin = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Curated catalog: addons the maintainers have checked. GitHub search: wider but rougher. "
                     "Type and press Enter, then pick a result and press Enter to install it.",
                     id="hint", markup=False)
        with Horizontal(id="controls"):
            yield Input(placeholder="Search by name or topic, e.g. fishing (empty = browse everything)", id="q")
        with Horizontal(id="filters"):
            yield Label("Source ")
            yield Select([("Curated catalog", "catalog"), ("GitHub search", "github")],
                         value="catalog", allow_blank=False, id="source")
            yield Select([(label, label) for label in core.SEARCH_CATEGORIES],
                         value="Any category", allow_blank=False, id="cat")
            yield Select([(label, key) for key, label in core.SEARCH_SORTS.items()],
                         value="best", allow_blank=False, id="sort")
            yield Label(" Forever only ")
            yield Switch(id="forever")
        yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
        yield Static("", id="info", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#results", DataTable).add_columns("Addon", "Forever", "Source", "Stars", "Updated", "Description")
        self.query_one("#q", Input).focus()
        if self.app.catalog_index is not None:
            self.index, self.origin = self.app.catalog_index, self.app.catalog_origin
            self.run_search()
        else:
            self.set_info("Loading the catalog...")
            self.load_index()

    @work(thread=True, group="discover-catalog")
    def load_index(self) -> None:
        index, origin = core.load_catalog()
        self.app.call_from_thread(self.index_ready, index, origin)

    def index_ready(self, index, origin) -> None:
        self.index, self.origin = index, origin
        self.app.catalog_index, self.app.catalog_origin = index, origin
        self.run_search()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_focus_query(self) -> None:
        self.query_one("#q", Input).focus()

    def set_info(self, text: str) -> None:
        self.query_one("#info", Static).update(text)

    @on(Input.Submitted, "#q")
    def _submit(self, event: Input.Submitted) -> None:
        self.run_search()

    @on(Select.Changed)
    def _select_changed(self, event: Select.Changed) -> None:
        if self.is_mounted_ready():
            self.run_search()

    @on(Switch.Changed, "#forever")
    def _switch_changed(self, event: Switch.Changed) -> None:
        self.run_search()

    def is_mounted_ready(self) -> bool:
        # Select fires Changed once while mounting; ignore until the table has its columns.
        return len(self.query_one("#results", DataTable).columns) > 0

    def run_search(self) -> None:
        source = self.query_one("#source", Select).value
        query = self.query_one("#q", Input).value.strip()
        category = self.query_one("#cat", Select).value
        sort = self.query_one("#sort", Select).value
        sort = sort if isinstance(sort, str) else "best"
        forever = self.query_one("#forever", Switch).value
        if source != "github":
            if self.index is None:
                self.set_info("Loading the catalog...")
                return
            results = core.catalog_results(self.index, query, core.CATEGORY_IDS.get(category), sort, forever)
            self.show_results(results, None, sort, "catalog")
            return
        text = " ".join(w for w in (query, core.SEARCH_CATEGORIES.get(category, "")) if w)
        if not text and not forever:
            self.query_one("#results", DataTable).clear()
            self.set_info("Type something to search for, or pick a category.")
            return
        self.set_info("Searching GitHub...")
        self.search_worker(text, sort, forever)

    @work(thread=True, exclusive=True, group="search")
    def search_worker(self, text: str, sort: str, forever: bool) -> None:
        try:
            results, err = core.search_github(text, sort, forever), None
        except RuntimeError as e:
            results, err = [], str(e)
        self.app.call_from_thread(self.show_results, results, err, sort, "github")

    def show_results(self, results, err, sort, source) -> None:
        table = self.query_one("#results", DataTable)
        table.clear()
        if err:
            self.set_info(err)
            return
        seen = set()
        for r in results:
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            name = (r["key"] if source == "github" else r["name"]) + ("  [archived]" if r.get("archived") else "")
            forever = r.get("forever", False)
            forever = ("claims" if forever else "-") if isinstance(forever, bool) else forever
            stars = "" if r.get("stars") is None else str(r["stars"])
            table.add_row(Text(name), Text(forever), Text(r.get("source") or "GitHub"), Text(stars),
                          Text(r.get("updated") or ""), Text((r.get("desc") or "")[:80]), key=r["key"])
        label = core.SEARCH_SORTS.get(sort, sort).lower()
        if source == "catalog":
            self.set_info(f"{len(seen)} addon(s) from the {self.origin} catalog, sorted by {label}.  Enter installs the highlighted one."
                          if seen else "Nothing in the catalog matches. Switch Source to GitHub search for a wider (rougher) search.")
        elif seen:
            self.set_info(f"{len(seen)} result(s), sorted by {label}.  Enter installs the highlighted one.")
        else:
            self.set_info("Nothing found. This only covers GitHub addons that tag themselves 'wow-addon'; "
                          "try other words.")

    @on(DataTable.RowSelected, "#results")
    def _chosen(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value:
            self.app.begin_install(event.row_key.value)


# ---------------------------------------------------------------------------
# Restore from backup
# ---------------------------------------------------------------------------

class RestoreScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self):
        super().__init__()
        self.backups: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Every time wam replaces an addon it saves the old copy. Pick one and press Enter "
                     "to put it back. What it replaces is backed up too, so a restore can be undone.",
                     id="hint", markup=False)
        yield DataTable(id="backups", cursor_type="row", zebra_stripes=True)
        yield Static("", id="info", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#backups", DataTable)
        table.add_columns("When", "Replaced by", "Folders")
        self.fill()
        table.focus()

    def fill(self) -> None:
        table = self.query_one("#backups", DataTable)
        table.clear()
        self.backups = core.list_backups()
        for b in self.backups:
            names = ", ".join(b["folders"])
            table.add_row(Text(b["when"]), Text(b["source"]), Text(names[:80]), key=b["name"])
        self.query_one("#info", Static).update(
            f"{len(self.backups)} backup(s)." if self.backups else "No backups yet.")

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(DataTable.RowSelected, "#backups")
    def _chosen(self, event: DataTable.RowSelected) -> None:
        b = next((x for x in self.backups if x["name"] == event.row_key.value), None)
        if b is None:
            return
        body = (f"Put back: {', '.join(b['folders'])}\nFrom: {b['when']} (before {b['source']} was installed)\n\n"
                "Close the game first.")

        def go(yes):
            if yes:
                self.app.run_restore(b["path"], after=lambda *_: self.fill())
        self.app.push_screen(ConfirmScreen("Restore this backup?", body, "Restore (y)"), go)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        settings = core.get_settings(core.load_config())
        custom = self.custom_values(settings)
        yield Header()
        with VerticalScroll(id="settings"):
            yield Label("Update mode", classes="section")
            yield Static("What wam does by itself once the daily check is on.", markup=False)
            yield RadioSet(
                RadioButton("manual  - never check on its own", value=settings["update_mode"] == "manual"),
                RadioButton("notify  - check daily, send a desktop notification", value=settings["update_mode"] == "notify"),
                RadioButton("auto    - check daily and install updates (waits while the game runs)",
                            value=settings["update_mode"] == "auto"),
                id="mode")
            yield Label("Betas", classes="section")
            with Horizontal(classes="row"):
                yield Switch(value=settings["prereleases"], id="prereleases")
                yield Label("  Allow pre-release / beta builds")
            yield Label("Colors", classes="section")
            yield RadioSet(*[RadioButton(core.THEME_NAMES[name], value=settings["theme"] == name)
                             for name in THEME_ORDER], id="theme")
            with Vertical(id="custom-colors"):
                yield Static("Pick your own. Type #rrggbb or a color name like gold or royalblue.", markup=False)
                for key, label in CUSTOM_FIELDS:
                    with Horizontal(classes="row"):
                        yield Label(f"{label:<11}")
                        yield Input(value=custom[key], id=f"c-{key}", classes="custom-input")
                        yield Static("", id=f"sw-{key}", classes="swatch", markup=False)
                yield Static("", id="custom-hint", markup=False)
            yield Label("Daily check", classes="section")
            yield Static("Runs in the background through systemd, even when this window is closed.", markup=False)
            with Horizontal(classes="row"):
                yield Input(placeholder="time, e.g. 18:00 (optional)", id="time")
                yield Button("Turn on", id="sched-on", variant="success")
                yield Button("Turn off", id="sched-off")
            yield Static("", id="sched-status", markup=False)
        yield Footer()

    def custom_values(self, settings: dict) -> dict:
        """The four custom colors: yours if saved, otherwise copied from the theme you're on now."""
        base = core.THEME_PRESETS.get(settings["theme"], core.THEME_PRESETS["wow"])
        values = {key: base[key] for key, _ in CUSTOM_FIELDS}
        values.update({k: v for k, v in (settings.get("custom_colors") or {}).items() if k in values})
        return values

    def on_mount(self) -> None:
        self.refresh_schedule()
        self.query_one("#custom-colors").display = core.get_settings(core.load_config())["theme"] == "custom"
        self.update_swatches()

    def action_back(self) -> None:
        self.app.pop_screen()

    def save(self, **changes) -> None:
        config = core.load_config()
        settings = core.get_settings(config)
        settings.update(changes)
        config["settings"] = settings
        core.save_config(config)

    @on(RadioSet.Changed, "#mode")
    def _mode(self, event: RadioSet.Changed) -> None:
        mode = UPDATE_MODES[event.index]
        if mode != core.get_settings(core.load_config())["update_mode"]:
            self.save(update_mode=mode)
            self.notify(f"Update mode: {mode}")

    @on(Switch.Changed, "#prereleases")
    def _pre(self, event: Switch.Changed) -> None:
        if event.value != core.get_settings(core.load_config())["prereleases"]:
            self.save(prereleases=event.value)
            self.notify("Betas " + ("allowed" if event.value else "skipped"))

    # -- colors -------------------------------------------------------------
    @on(RadioSet.Changed, "#theme")
    def _theme(self, event: RadioSet.Changed) -> None:
        choice = THEME_ORDER[event.index]
        settings = core.get_settings(core.load_config())
        if choice == settings["theme"]:
            return
        if choice == "custom" and not settings.get("custom_colors"):
            # First time: start from the colors you're looking at right now.
            seed = self.custom_values(settings)
            self.save(theme=choice, custom_colors=seed)
            for key, _ in CUSTOM_FIELDS:
                self.query_one(f"#c-{key}", Input).value = seed[key]
        else:
            self.save(theme=choice)
        self.query_one("#custom-colors").display = choice == "custom"
        self.update_swatches()
        self.app.apply_theme()

    def parsed_custom(self) -> dict | None:
        out = {}
        for key, _ in CUSTOM_FIELDS:
            try:
                out[key] = Color.parse(self.query_one(f"#c-{key}", Input).value.strip())
            except ColorParseError:
                return None
        return out

    def update_swatches(self) -> None:
        for key, _ in CUSTOM_FIELDS:
            swatch = self.query_one(f"#sw-{key}", Static)
            try:
                swatch.styles.background = Color.parse(self.query_one(f"#c-{key}", Input).value.strip())
            except ColorParseError:
                swatch.styles.background = None

    @on(Input.Changed, ".custom-input")
    def _custom_changed(self, event: Input.Changed) -> None:
        self.update_swatches()
        hint = self.query_one("#custom-hint", Static)
        for key, label in CUSTOM_FIELDS:
            text = self.query_one(f"#c-{key}", Input).value.strip()
            try:
                Color.parse(text)
            except ColorParseError:
                hint.update(f"{label}: '{text}' isn't a color yet. Use #rrggbb or a name like gold.")
                return
        parsed = self.parsed_custom()
        if parsed is None:
            return
        if too_similar(parsed["foreground"], parsed["background"]):
            hint.update("Text and background are too close to read. Pick colors that differ more.")
            return
        new = {key: color.hex6 for key, color in parsed.items()}
        settings = core.get_settings(core.load_config())
        effective = {k: Color.parse(v).hex6 for k, v in self.custom_values(settings).items()}
        if new == effective:
            return          # nothing actually changed (e.g. the fields were just filled in); don't touch settings
        self.save(custom_colors=new)
        if settings["theme"] == "custom":
            self.app.apply_theme()
        hint.update("Saved." if settings["theme"] == "custom"
                    else "Saved. Choose \"Custom colors\" above to use them.")

    # -- daily check --------------------------------------------------------
    def refresh_schedule(self) -> None:
        _, text = core.schedule_apply("status")
        self.query_one("#sched-status", Static).update(text)

    @on(Button.Pressed, "#sched-on")
    def _on(self, event: Button.Pressed) -> None:
        run_time = self.query_one("#time", Input).value.strip() or None
        ok, text = core.schedule_apply("on", run_time)
        self.notify(text, severity="information" if ok else "error")
        self.refresh_schedule()

    @on(Button.Pressed, "#sched-off")
    def _off(self, event: Button.Pressed) -> None:
        ok, text = core.schedule_apply("off")
        self.notify(text)
        self.refresh_schedule()


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

class WamApp(App):
    TITLE = "wam"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    ModalScreen { align: center middle; }
    .dialog { width: 72; max-width: 95%; height: auto; max-height: 90%;
              border: round $primary; background: $surface; padding: 1 2; }
    .dialog.wide { width: 100; height: 80%; }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    .dialog-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    .dialog-buttons Button { margin-left: 2; }
    #job-log { height: 1fr; }
    #body { height: 1fr; }
    #left { width: 64%; }
    #right { width: 36%; border-left: solid $primary; padding: 0 2; }
    #status { height: 1; padding: 0 1; background: $panel; }
    #hint, #info { padding: 0 1; }
    #controls { height: auto; }
    #controls Input { width: 1fr; }
    #filters { height: auto; }
    #filters Select { width: 26; }
    #filters #source { width: 30; }
    .section { text-style: bold; margin-top: 1; }
    .row { height: auto; }
    #time { width: 34; }
    .swatch { width: 10; height: 3; margin-left: 1; outline: solid $foreground 40%; }
    /* Only show the keyboard cursor while you're actually in the list; the green dot marks what's selected. */
    RadioSet:blur > RadioButton.-selected > .toggle--label { background: transparent; }
    #custom-colors { height: auto; padding-left: 2; }
    .custom-input { width: 24; }
    #settings { padding: 1 2; }
    DataTable { scrollbar-size-horizontal: 0; }
    /* Use only the terminal's own default colors for table headers and the selected row,
       so they stay readable whatever palette the terminal has (the built-in header color
       is ANSI bright blue, which can match the text color). */
    DataTable:ansi > .datatable--header { background: ansi_default; color: ansi_default; text-style: bold underline; }
    DataTable:ansi > .datatable--cursor,
    DataTable:ansi:focus > .datatable--cursor { background: ansi_default; color: ansi_default; text-style: reverse bold; }
    #controls Label, #filters Label, .row Label { height: 3; content-align: left middle; }
    """

    def __init__(self):
        super().__init__()
        config = core.load_config()
        self.install = config["installs"][0] if config["installs"] else None
        self._theme_n = 0
        self.catalog_index = None
        self.catalog_origin = ""

    def apply_theme(self) -> None:
        """Apply the saved color choice. Anything unusable falls back to the terminal's own colors."""
        settings = core.get_settings(core.load_config())
        choice = settings["theme"]
        try:
            if choice == "custom":
                colors = dict(core.THEME_PRESETS["wow"])
                for key in ("secondary", "surface", "panel"):
                    colors.pop(key)                      # derive these from the user's colors
                colors.update(settings.get("custom_colors") or {})
            elif choice in core.THEME_PRESETS:
                colors = dict(core.THEME_PRESETS[choice])
            else:
                colors = None
            if colors is not None:
                self._theme_n += 1                       # a fresh name makes Textual re-apply it
                name = f"wam-{choice}-{self._theme_n}"
                self.register_theme(build_theme(name, colors))
                self.theme = name
                return
        except Exception:
            pass
        self.theme = "ansi-dark"      # the terminal's own colors

    @work(thread=True, group="catalog")
    def load_catalog_worker(self) -> None:
        index, origin = core.load_catalog()
        self.call_from_thread(self._catalog_ready, index, origin)

    def _catalog_ready(self, index, origin) -> None:
        self.catalog_index, self.catalog_origin = index, origin
        for screen in self.screen_stack:
            if isinstance(screen, MainScreen) and screen.is_mounted:
                screen.fill_table()

    def on_mount(self) -> None:
        self.apply_theme()
        self.load_catalog_worker()
        self.sub_title = self.install["label"] if self.install else "setup"
        self.push_screen(MainScreen())

    # -- shared install / restore flows, used by several screens -----------
    def begin_install(self, key: str, after=None) -> None:
        """Look the build up, show the plan, ask, then install."""
        if self.install is None:
            return
        self.notify(f"Looking up {key}...")
        self._plan_worker(key, after)

    @work(thread=True, group="plan")
    def _plan_worker(self, key: str, after) -> None:
        settings = core.get_settings(core.load_config())
        cand, err = core.resolve_source(key, settings["prereleases"], settings)
        self.call_from_thread(self._plan_ready, key, cand, err, after)

    def _plan_ready(self, key, cand, err, after) -> None:
        if err:
            self.push_screen(MessageScreen("Can't install that", err))
            return
        if cand.get("blocked"):
            self.push_screen(MessageScreen(
                f"{cand['name']}: manual download",
                f"{cand['blocked'].capitalize()}, so wam won't download it.\n\nGet it from:\n{cand.get('page')}"))
            return
        previous = core.load_state()["tracked"].get(self.install["addons_dir"], {}).get(key)
        lines = [f"{cand['name']}", f"Version : {cand['label']}" + ("  (beta)" if cand.get("prerelease") else "")]
        if cand.get("size"):
            lines.append(f"Size    : {core.human_size(cand['size'])}")
        if previous:
            lines.append(f"Replaces: {previous.get('label') or previous['tag']}")
            if previous["tag"] == cand["tag"]:
                lines.append("(You already have this version; this just refreshes the files.)")
        lines += [f"Into    : {self.install['label']}"] + [f"Note    : {n}" for n in cand.get("notes", [])]
        lines += ["", "Anything it replaces is backed up first. Close the game before continuing."]
        self.push_screen(ConfirmScreen("Install this?", "\n".join(lines), "Install (y)"),
                         lambda yes: self.run_installs([key], after) if yes else None)

    def run_installs(self, keys: list[str], after=None) -> None:
        screen = ProgressScreen("Installing" if len(keys) == 1 else f"Updating {len(keys)} addons")
        self.push_screen(screen, after)
        self._install_worker(keys, screen)

    @work(thread=True, group="install")
    def _install_worker(self, keys: list[str], screen: ProgressScreen) -> None:
        def log(text):
            self.call_from_thread(screen.write, text)
        settings = core.get_settings(core.load_config())
        all_ok = True
        for key in keys:
            if len(keys) > 1:
                log(f"--- {key} ---")
            try:
                ok = core.do_install(key, self.install, prerelease=settings["prereleases"],
                                     assume_yes=True, log=log)
            except Exception as e:
                log(f"FAILED: {e}")
                ok = False
            all_ok = all_ok and ok
        self.call_from_thread(screen.finish, all_ok)

    def run_restore(self, backup_path, after=None) -> None:
        screen = ProgressScreen("Restoring backup")
        self.push_screen(screen, after)
        self._restore_worker(backup_path, screen)

    @work(thread=True, group="restore")
    def _restore_worker(self, backup_path, screen: ProgressScreen) -> None:
        def log(text):
            self.call_from_thread(screen.write, text)
        try:
            ok = core.restore_backup(backup_path, self.install, log=log)
        except Exception as e:
            log(f"FAILED: {e}")
            ok = False
        self.call_from_thread(screen.finish, ok)


def run() -> None:
    WamApp().run()
