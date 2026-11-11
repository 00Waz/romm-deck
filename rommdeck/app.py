"""GTK3 interface for RomM Deck."""
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import artwork, config, emudeck, srm, steam, style  # noqa: E402
from .gamepad import Gamepads  # noqa: E402
from .romm import Cancelled, RommClient, RommError, platform_name  # noqa: E402

PAGE = 60

def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def pixbuf_from_bytes(data, w=None, h=None):
    try:
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        if w:
            return GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, w, h, True, None)
        return GdkPixbuf.Pixbuf.new_from_stream(stream, None)
    except GLib.Error:
        return None


def to_png(data):
    """Steam's grid wants png/jpg; convert whatever RomM sent (often webp)."""
    pb = pixbuf_from_bytes(data)
    if not pb:
        return None
    ok, buf = pb.save_to_bufferv("png", [], [])
    return bytes(buf) if ok else None


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.rommdeck")
        self.cfg = config.load()
        self.client = RommClient(self.cfg)
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.gen = 0                 # bumps on every reload; stale results are dropped
        self.platform = None         # selected platform dict, None = all
        self.platforms = {}          # id -> platform
        self.loaded = 0
        self.total = 0
        self.loading = False
        self.covers = {}             # rom id -> pixbuf
        self.jobs = []               # downloads: queued, running and finished
        self.steam_busy = False
        self.steam_backlog = []
        self.steam_added = 0
        self.win = None

    # -- helpers -----------------------------------------------------------------
    def run_bg(self, fn, done=None, fail=None):
        """Run fn() in a thread; deliver result/exception on the GTK thread."""
        def work():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - reported to the user
                GLib.idle_add(lambda err=e: (fail or self.show_error)(err) and False)
            else:
                if done:
                    GLib.idle_add(lambda: done(res) and False)
        threading.Thread(target=work, daemon=True).start()

    def show_error(self, err):
        self.status.set_text(str(err))
        dlg = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.OK, text=str(err))
        dlg.run()
        dlg.destroy()

    def choose(self, title, body, buttons, default, icon="dialog-question-symbolic"):
        """Consistently sized popup. buttons: [(label, response)], default = response id."""
        dlg = Gtk.Dialog(title="RomM Deck", transient_for=self.win, modal=True)
        dlg.set_default_size(620, 1)
        dlg.set_resizable(False)
        dlg.get_style_context().add_class("popup")
        box = Gtk.Box(spacing=18, margin=26)
        img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
        img.set_pixel_size(56)
        box.pack_start(img, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        head = Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=44)
        head.get_style_context().add_class("popup-title")
        text.pack_start(head, False, False, 0)
        if body:
            sub = Gtk.Label(label=body, xalign=0, wrap=True, max_width_chars=48,
                            selectable=False)
            sub.get_style_context().add_class("popup-body")
            text.pack_start(sub, False, False, 0)
        box.pack_start(text, True, True, 0)
        dlg.get_content_area().add(box)
        for label, resp in buttons:
            dlg.add_button(label, resp)
        dlg.set_default_response(default)
        dlg.show_all()
        dlg.resize(620, 1)                       # shrink to fit the text
        btn = dlg.get_widget_for_response(default)
        btn.get_style_context().add_class("suggested-action")
        btn.grab_focus()
        res = dlg.run()
        dlg.destroy()
        return res

    def ask(self, title, body, yes="Yes", no="No"):
        return self.choose(title, body, [(no, Gtk.ResponseType.NO),
                                         (yes, Gtk.ResponseType.YES)],
                           Gtk.ResponseType.YES) == Gtk.ResponseType.YES

    def apply_theme(self):
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-theme-name", "Adwaita")
        dark = bool(self.cfg.get("dark_mode", True))
        settings.set_property("gtk-application-prefer-dark-theme", dark)
        if not hasattr(self, "provider"):
            self.provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), self.provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.provider.load_from_data(style.css(dark))

    # -- window --------------------------------------------------------------------
    def do_activate(self):
        if self.win:
            self.win.present()
            return
        self.apply_theme()
        self.win = Gtk.ApplicationWindow(application=self, title="RomM Deck")
        self.win.set_default_size(1280, 800)

        game_mode = steam.in_game_mode()
        header = Gtk.HeaderBar(show_close_button=not game_mode)
        title = Gtk.Box(spacing=6)
        icon_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "assets", "icon.png")
        if os.path.exists(icon_file):
            Gtk.Window.set_default_icon_from_file(icon_file)
            title.pack_start(Gtk.Image.new_from_pixbuf(
                GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_file, 46, 46, True)), False, False, 4)
        for text, cls in (("RomM", "app-title"), ("Deck", "app-title-accent")):
            part = Gtk.Label(label=text)
            part.get_style_context().add_class("app-title")
            part.get_style_context().add_class(cls)
            title.pack_start(part, False, False, 0)
        header.set_custom_title(title)
        if not game_mode:
            self.win.set_titlebar(header)
        self.search = Gtk.SearchEntry(placeholder_text="Search games…", width_chars=30)
        self.search.connect("search-changed",
                            lambda *_: (self.show_page("games"), self.reload_roms()))
        self.consoles_btn = Gtk.Button()
        inner = Gtk.Box(spacing=8)
        inner.pack_start(Gtk.Image.new_from_icon_name("view-grid-symbolic",
                                                      Gtk.IconSize.BUTTON), False, False, 0)
        inner.pack_start(Gtk.Label(label="Consoles"), False, False, 0)
        self.consoles_btn.add(inner)
        self.consoles_btn.get_style_context().add_class("pill")
        self.consoles_btn.set_tooltip_text("Choose a console")
        self.consoles_btn.connect("clicked", lambda *_: self.show_page(
            "games" if self.stack.get_visible_child_name() == "consoles" else "consoles"))
        header.pack_start(self.consoles_btn)
        header.pack_start(self.search)
        self.installed_only = False
        self.steam_paths = set()     # rom files Steam shortcuts launch
        self.steam_entries = {}      # rom file -> (appid, Steam name, roms folder)
        self.offline = False         # RomM unreachable: installed view only
        self.installed_btn = Gtk.Button(label="Installed")
        self.installed_btn.get_style_context().add_class("pill")
        self.installed_btn.set_tooltip_text("Show only games installed on this device")
        self.installed_btn.connect("clicked", lambda *_: self.toggle_installed())
        header.pack_start(self.installed_btn)
        self.sort_btn = Gtk.Button(label="Sort collections")
        self.sort_btn.get_style_context().add_class("pill")
        self.sort_btn.set_tooltip_text(
            "Re-add installed games to Steam and file them in their console collections")
        self.sort_btn.connect("clicked", lambda *_: self.sort_collections())
        header.pack_start(self.sort_btn)
        self.downloads_btn = Gtk.Button(label="Downloads")
        self.downloads_btn.get_style_context().add_class("pill")
        self.downloads_btn.set_tooltip_text("Downloads and adding to Steam")
        self.downloads_btn.connect("clicked", lambda *_: self.show_page(
            "games" if self.stack.get_visible_child_name() == "downloads" else "downloads"))
        for icon, tip, cb in (("emblem-system-symbolic", "Settings", self.open_settings),
                              ("view-refresh-symbolic", "Refresh", lambda *_: self.reload_all())):
            btn = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            btn.set_tooltip_text(tip)
            btn.get_style_context().add_class("round")
            btn.connect("clicked", cb)
            header.pack_end(btn)
        header.pack_end(self.downloads_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.win.add(outer)
        if game_mode:
            # Gaming Mode: no window frame or shadow margin, fill the whole screen
            self.win.set_decorated(False)
            outer.pack_start(header, False, False, 0)
            self.win.fullscreen()
        # One tab per console. The pages are empty; the grid below is shared.
        # One pill per console in a strip that scrolls sideways (drag / swipe).
        self.tab_box = Gtk.Box(spacing=8)
        self.tab_box.get_style_context().add_class("tabbar")
        self.tab_scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                               hscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.tab_scroller.add(self.tab_box)
        self.tab_platforms = []
        self.tab_index = 0
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=140)
        outer.pack_start(self.stack, True, True, 0)
        games = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(games, "games")
        games.pack_start(self.tab_scroller, False, False, 0)

        # Console picker page: one big tile per console, with its logo
        self.console_flow = Gtk.FlowBox(valign=Gtk.Align.START, max_children_per_line=8,
                                        selection_mode=Gtk.SelectionMode.NONE,
                                        activate_on_single_click=True, homogeneous=True,
                                        row_spacing=12, column_spacing=12, margin=16)
        self.console_flow.connect("child-activated", lambda _f, child: (
            self.show_page("games"), self.select_tab(child.tab_index)))
        self.console_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.console_scroller.add(self.console_flow)
        self.console_flow.set_vadjustment(self.console_scroller.get_vadjustment())
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading = Gtk.Label(label="Choose a console", xalign=0)
        heading.get_style_context().add_class("page-title")
        page.pack_start(heading, False, False, 0)
        page.pack_start(self.console_scroller, True, True, 0)
        self.stack.add_named(page, "consoles")
        self.logos = {}

        self.flow = Gtk.FlowBox(valign=Gtk.Align.START, max_children_per_line=12,
                                selection_mode=Gtk.SelectionMode.SINGLE,
                                activate_on_single_click=True, homogeneous=True,
                                row_spacing=6, column_spacing=6, margin=10)
        self.flow.connect("child-activated", lambda _f, child: self.open_details(child.rom))
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.add(self.flow)
        vadj = self.scroller.get_vadjustment()
        self.flow.set_vadjustment(vadj)          # keep the focused card in view
        vadj.connect("value-changed", self.on_scrolled)
        # Game Mode delivers touch as mouse events, so add drag-to-scroll for those
        self.drags = [self.drag_scroll(self.scroller, False),
                      self.drag_scroll(self.tab_scroller, True),
                      self.drag_scroll(self.console_scroller, False)]
        games.pack_start(self.scroller, True, True, 0)

        # Downloads page: every download with its progress, plus batch add-to-Steam
        self.job_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                    activate_on_single_click=True, margin=10)
        self.job_list.get_style_context().add_class("jobs")
        self.job_list.connect("row-activated", lambda _l, row: self.job_toggle(row.job))
        self.job_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.job_scroller.add(self.job_list)
        self.job_list.set_adjustment(self.job_scroller.get_vadjustment())
        self.jobs_empty = Gtk.Label(label="No downloads yet.\nOpen a game and press Download, "
                                    "or press X on a game to queue it straight away.",
                                    justify=Gtk.Justification.CENTER, no_show_all=True)
        self.jobs_empty.get_style_context().add_class("empty")
        dpage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dhead = Gtk.Label(label="Downloads", xalign=0)
        dhead.get_style_context().add_class("page-title")
        dpage.pack_start(dhead, False, False, 0)
        dpage.pack_start(self.jobs_empty, True, True, 0)
        dpage.pack_start(self.job_scroller, True, True, 0)
        actions = Gtk.Box(spacing=10, margin=12, halign=Gtk.Align.END)
        self.select_all_btn = Gtk.Button(label="Tick all")
        self.select_all_btn.connect("clicked", lambda *_: self.tick_all())
        self.clear_btn = Gtk.Button(label="Clear finished")
        self.clear_btn.connect("clicked", lambda *_: self.clear_finished())
        self.steam_btn = Gtk.Button(label="Add ticked to Steam")
        self.steam_btn.get_style_context().add_class("suggested-action")
        self.steam_btn.connect("clicked", lambda *_: self.add_ticked_to_steam())
        for b in (self.select_all_btn, self.clear_btn, self.steam_btn):
            actions.pack_start(b, False, False, 0)
        dpage.pack_start(actions, False, False, 0)
        self.stack.add_named(dpage, "downloads")
        self.drags.append(self.drag_scroll(self.job_scroller, False))

        bar = Gtk.Box(spacing=10)
        bar.get_style_context().add_class("status")
        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, no_show_all=True)
        self.progress.set_size_request(200, -1)
        self.status.get_style_context().add_class("status-text")
        hints = Gtk.Box(spacing=6)
        for key, what in (("X", "Download"), ("L1 R1", "Console"),
                          ("L2 R2", "Page"), ("⧉", "Consoles"), ("R3", "Downloads"),
                          ("Y", "Search"), ("☰", "Settings")):
            chip = Gtk.Label(use_markup=True, label=f'<span weight="800">{key}</span>  {what}')
            chip.get_style_context().add_class("chip")
            hints.pack_start(chip, False, False, 0)
        bar.pack_start(self.status, True, True, 0)
        bar.pack_end(hints, False, False, 0)
        bar.pack_end(self.progress, False, False, 0)
        outer.pack_end(bar, False, False, 0)

        self.win.connect("key-press-event", self.on_key)
        self.pads = Gamepads(self.on_pad)
        self.win.show_all()
        if self.cfg["server_url"]:
            self.reload_all()
        else:
            self.status.set_text("Welcome! Enter your RomM server details to begin.")
            GLib.idle_add(lambda: self.open_settings() and False)

    # -- platforms -------------------------------------------------------------------
    def reload_all(self):
        self.client = RommClient(self.cfg)
        self.covers.clear()
        self.status.set_text("Connecting to RomM…")
        def fail(err):
            if not self.cfg.get("server_url"):
                self.show_error(err)
                self.open_settings()
                return
            self.go_offline(err)
        self.run_bg(self.client.platforms, self.set_platforms, fail)

    def go_offline(self, err):
        """RomM is unreachable: show the games on this device, with a tab per
        roms folder that has games in it."""
        counts = {}
        for g in srm.scan(self.cfg):
            counts[g.folder] = counts.get(g.folder, 0) + 1
        plats = [{"id": "folder:" + f, "_folder": f, "name": emudeck.console_name(f),
                  "rom_count": n} for f, n in sorted(
                      counts.items(), key=lambda kv: emudeck.console_name(kv[0]).lower())]
        if not self.installed_only:
            self.installed_only = True
            self.installed_btn.get_style_context().add_class("on")
        self.offline = True
        print("RomM unreachable:", err, file=sys.stderr)
        self.set_platforms(plats)

    def set_platforms(self, plats):
        if not any(p.get("_folder") for p in plats):
            self.offline = False
        self.platforms = {p["id"]: p for p in plats}
        for btn in self.tab_box.get_children():
            self.tab_box.remove(btn)
        total = sum(p.get("rom_count", 0) for p in plats)
        self.tab_platforms = [None] + plats
        for i, plat in enumerate(self.tab_platforms):
            text = (f"{platform_name(plat)}  ({plat.get('rom_count', 0)})" if plat
                    else f"All games  ({total})")
            btn = Gtk.Button(label=text, can_focus=False)
            btn.get_style_context().add_class("tab")
            btn.connect("clicked", lambda _b, i=i: self.select_tab(i))
            self.tab_box.pack_start(btn, False, False, 0)
        self.tab_box.show_all()
        self.build_console_page(total)
        self.select_tab(0)

    def build_console_page(self, total):
        for child in self.console_flow.get_children():
            self.console_flow.remove(child)
        size = 112
        for i, plat in enumerate(self.tab_platforms):
            child = Gtk.FlowBoxChild()
            child.tab_index = i
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.get_style_context().add_class("console-card")
            child.card = box
            img = Gtk.Image.new_from_icon_name("applications-games-symbolic",
                                               Gtk.IconSize.DIALOG)
            img.set_pixel_size(72)
            img.set_size_request(size, size)
            name = Gtk.Label(label=platform_name(plat) if plat else "All games", wrap=True,
                             lines=2, max_width_chars=15, ellipsize=Pango.EllipsizeMode.END,
                             justify=Gtk.Justification.CENTER)
            name.get_style_context().add_class("console-name")
            n = plat.get("rom_count", 0) if plat else total
            count = Gtk.Label(label=f"{n:,} games")
            count.get_style_context().add_class("console-count")
            for w in (img, name, count):
                box.pack_start(w, False, False, 0)
            child.add(box)
            self.console_flow.add(child)
            if plat:
                self.load_logo(plat, img, size)
        self.console_flow.show_all()

    def load_logo(self, plat, img, size):
        if plat.get("_folder"):                      # offline tab: no server logo
            return
        key = plat.get("slug")
        if key in self.logos:
            img.set_from_pixbuf(self.logos[key])
            return

        def done(data):
            pb = pixbuf_from_bytes(data, size, size) if data else None
            if pb:
                self.logos[key] = pb
                img.set_from_pixbuf(pb)
        self.pool.submit(lambda: GLib.idle_add(
            lambda d=self.client.platform_logo(plat): done(d) and False))

    def show_page(self, name):
        if self.stack.get_visible_child_name() == name:
            return
        self.stack.set_visible_child_name(name)
        for btn, page in ((self.consoles_btn, "consoles"), (self.downloads_btn, "downloads")):
            ctx = btn.get_style_context()
            (ctx.add_class if name == page else ctx.remove_class)("on")
        if name == "consoles":
            kids = self.console_flow.get_children()
            for k in kids:
                cctx = k.card.get_style_context()
                (cctx.add_class if k.tab_index == self.tab_index
                 else cctx.remove_class)("current")
            if self.tab_index < len(kids):
                kids[self.tab_index].grab_focus()
        else:
            self.focus_grid()

    def current_view(self):
        page = self.stack.get_visible_child_name()
        if page == "consoles":
            return self.console_flow, self.console_scroller
        if page == "downloads":
            return self.job_list, self.job_scroller
        return self.flow, self.scroller

    def select_tab(self, index):
        buttons = self.tab_box.get_children()
        if not buttons:
            return
        self.tab_index = index % len(buttons)
        for i, btn in enumerate(buttons):
            ctx = btn.get_style_context()
            (ctx.add_class if i == self.tab_index else ctx.remove_class)("tab-selected")
        GLib.idle_add(self.reveal_tab)
        self.platform = self.tab_platforms[self.tab_index]
        self.reload_roms()

    def reveal_tab(self):
        """Scroll the tab strip so the selected console is fully visible."""
        buttons = self.tab_box.get_children()
        if self.tab_index < len(buttons):
            alloc = buttons[self.tab_index].get_allocation()
            adj = self.tab_scroller.get_hadjustment()
            adj.clamp_page(alloc.x - 60, alloc.x + alloc.width + 60)
        return False

    # -- rom grid ----------------------------------------------------------------------
    def reload_roms(self):
        self.gen += 1
        self.loaded = self.total = 0
        self.loading = False
        for child in self.flow.get_children():
            self.flow.remove(child)
        self.load_page()

    def toggle_installed(self):
        if self.offline and self.installed_only:     # the library needs RomM: retry
            self.reload_all()
            return
        self.installed_only = not self.installed_only
        ctx = self.installed_btn.get_style_context()
        (ctx.add_class if self.installed_only else ctx.remove_class)("on")
        self.show_page("games")
        self.reload_roms()

    # -- games on disk -----------------------------------------------------------------
    def disk_roms(self, pid, term):
        """The installed view, built only from this device: the games in the roms
        folder (srm.scan) plus every Steam shortcut that launches a file in it,
        including shortcuts whose file is gone (marked _missing). No RomM access,
        so it is instant and works offline. Returns (roms, n)."""
        self.refresh_steam_paths()
        folder = self.tab_folder(pid)
        if pid is not None and not folder:
            return [], 0
        games = srm.scan(self.cfg)
        seen = {os.path.normpath(g.path) for g in games}
        missing = set()
        for path, (_appid, name, sc_folder) in self.steam_entries.items():
            if path in seen:
                continue
            seen.add(path)
            games.append(srm.Game(path, sc_folder, name))
            if not os.path.exists(path):              # in Steam, not on the drive
                missing.add(path)
        key = srm.norm(term)
        out = []
        for g in games:
            if folder and g.folder != folder:
                continue
            rom = self.disk_rom(g)
            rom["_missing"] = g.path in missing
            if term and key not in srm.norm(rom["name"]) and \
                    term.lower() not in os.path.basename(g.path).lower():
                continue
            out.append(rom)
        out.sort(key=lambda r: (r["platform_display_name"].lower(), r["name"].lower()))
        return out, len(out)

    def disk_rom(self, g):
        """A card for a game on disk. Titled as in Steam when it has a shortcut."""
        appid, name, _f = self.steam_entries.get(os.path.normpath(g.path), (None, None, None))
        return {"id": "disk:" + g.path, "name": name or g.title,
                "fs_name": os.path.basename(g.path),
                "platform_display_name": emudeck.console_name(g.folder),
                "_path": g.path, "_game": g, "_appid": appid}

    @staticmethod
    def real_rom(rom):
        """The RomM rom behind a card, or None for a card built from the disk."""
        return None if str(rom.get("id", "")).startswith("disk:") else rom

    def refresh_steam_paths(self):
        """Read Steam's shortcuts: which rom files they launch, and under what name."""
        try:
            entries = {}
            for appid, folder, path, name in steam.rom_shortcut_entries(self.cfg):
                entries.setdefault(path, (appid, name, folder))
            self.steam_entries = entries
            self.steam_paths = set(entries) | {os.path.realpath(p) for p in entries}
        except (steam.SteamError, OSError, ValueError) as e:
            print("could not read Steam shortcuts:", e, file=sys.stderr)

    def in_steam(self, path):
        path = os.path.normpath(path)
        if path in self.steam_paths or os.path.realpath(path) in self.steam_paths:
            return True
        if os.path.isdir(path):                      # a folder game: any file inside
            return any(p.startswith(path + os.sep) for p in self.steam_paths)
        return False

    def tab_folder(self, pid):
        """The roms folder behind a console tab (None for All games)."""
        if pid is None:
            return None
        plat = self.platforms.get(pid, {})
        return plat.get("_folder") or emudeck.resolve_folder(
            self.cfg, plat.get("slug", ""), plat.get("fs_slug", ""))

    def sort_collections(self):
        """Match the Steam shortcuts for installed games to their console and put
        each in that console's collection. Works from the files on disk and
        Steam's own shortcut list, so it does not depend on the RomM server."""
        if not self.cfg.get("steam_collections", True):
            self.show_error("Steam collections are turned off. Tick \"Add games to a Steam "
                            "collection per console\" in Settings first.")
            return
        if not self.ask("Sort collections",
                        "This looks at the games in your ROMs folder and puts their Steam "
                        "shortcuts in a collection named after their console. Games that "
                        "are installed but missing from Steam are added, with artwork from "
                        "SteamGridDB when a key is set. Your RomM server is not needed.",
                        yes="Sort", no="Cancel"):
            return
        self.sort_btn.set_sensitive(False)
        self.status.set_text("Sorting collections…")

        def work():
            shortcuts = steam.rom_shortcuts(self.cfg)
            groups = {}
            for appid, folder, _path in shortcuts:
                groups.setdefault(emudeck.console_name(folder), []).append(appid)
            changed = steam.add_to_collections(self.cfg, groups, exclusive=True)
            if changed and steam.steam_running():
                steam.remember_pending_collections(groups, exclusive=True)
            gone = sum(1 for _a, _f, p in shortcuts if not os.path.exists(p))
            return len(shortcuts), changed, steam.missing_games(self.cfg, shortcuts), gone

        def done(res):
            self.sort_btn.set_sensitive(True)
            total, changed, missing, gone = res
            self.status.set_text(
                f"{total} games in Steam, {changed} sorted into collections"
                + (f", {len(missing)} installed games not in Steam" if missing else "")
                + (f", {gone} with their file missing (see Installed)" if gone else ""))
            if changed and steam.steam_running():
                self.offer_restart(changed, "sorted into collections")
            if missing:
                self.add_missing(missing)

        def fail(err):
            self.sort_btn.set_sensitive(True)
            self.show_error(err)

        self.run_bg(work, done, fail)

    def add_missing(self, games):
        """Add installed games that have no Steam shortcut, straight from the disk
        scan: SRM's launch command and title, the built-in collection name, and
        SteamGridDB artwork when a key is set. Does not use RomM."""
        self.status.set_text(f"Adding {len(games)} games to Steam…")
        self.add_to_steam([(None, g.path, None, g) for g in games])

    def load_page(self):
        if self.loading:
            return
        self.loading = True
        gen, offset = self.gen, self.loaded
        pid = self.platform["id"] if self.platform else None
        term = self.search.get_text().strip()
        self.status.set_text("Loading games…")

        def done(res):
            if gen != self.gen:
                return
            items, total = res
            self.loading = False
            self.loaded += len(items)
            self.total = total if items else self.loaded
            for rom in items:
                self.add_card(rom)
            self.flow.show_all()
            if offset == 0 and not self.search.is_focus() and self.active_window() is self.win:
                self.focus_grid()
            self.status.set_text((f"Showing {self.loaded} of {self.total} games"
                                  if self.total else ("No installed games found"
                                                      if self.installed_only else "No games found"))
                                 + (" (offline: can't reach RomM)" if self.offline else ""))

        def fail(err):
            self.loading = False
            self.show_error(err)

        if self.installed_only:
            if offset:                               # the disk list comes in one go
                self.loading = False
                return
            self.status.set_text("Finding installed games…")
            self.run_bg(lambda: self.disk_roms(pid, term), done, fail)
        else:
            def page():
                if offset == 0:
                    self.refresh_steam_paths()
                return self.client.roms(pid, term, PAGE, offset)
            self.run_bg(page, done, fail)

    def on_scrolled(self, adj):
        near_end = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 600
        if near_end and self.loaded < self.total:
            self.load_page()

    def drag_scroll(self, scroller, horizontal):
        """Drag-to-scroll for mouse input. Game Mode delivers touches as mouse
        events, which GTK would not otherwise treat as a swipe."""
        adj = scroller.get_hadjustment() if horizontal else scroller.get_vadjustment()
        state = {}
        drag = Gtk.GestureDrag.new(scroller)
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def begin(gesture, _x, _y):
            state.update(start=adj.get_value(), active=False)
            event = gesture.get_last_event(gesture.get_last_updated_sequence())
            device = event.get_source_device() if event else None
            if device and device.get_source() == Gdk.InputSource.TOUCHSCREEN:
                gesture.set_state(Gtk.EventSequenceState.DENIED)  # GTK scrolls real touch

        def update(gesture, dx, dy):
            delta = dx if horizontal else dy
            if not state["active"] and abs(delta) > 14:
                state["active"] = True
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # a scroll, not a tap
            if state["active"]:
                adj.set_value(state["start"] - delta)

        drag.connect("drag-begin", begin)
        drag.connect("drag-update", update)
        return drag

    def page_move(self, direction):
        """Move the selection a whole screenful of rows up (-1) or down (+1)."""
        flow, scroller = self.current_view()
        kids = flow.get_children()
        if not kids or flow is self.job_list:
            return
        focus = self.win.get_focus()
        cur = kids.index(focus) if focus in kids else 0
        first = kids[0].get_allocation()
        cols = max(1, sum(1 for k in kids[:40] if k.get_allocation().y == first.y))
        view = scroller.get_vadjustment().get_page_size()
        rows = max(1, int(view // max(1, first.height + flow.get_row_spacing())))
        target = cur + direction * cols * rows
        if target < 0:
            target = cur % cols                        # same column, first row
        elif target >= len(kids):
            target = len(kids) - 1
        kids[target].grab_focus()

    # -- controller / keyboard ----------------------------------------------------------
    def active_window(self):
        for w in Gtk.Window.list_toplevels():
            if w.is_active() and w.get_visible():
                return w
        return None

    def focus_grid(self):
        flow = self.current_view()[0]
        child = self.win.get_focus()
        if child is not None and child.get_parent() is flow:
            return
        kids = flow.get_children()
        if kids:
            kids[0].grab_focus()
        elif flow is self.job_list:
            self.downloads_btn.grab_focus()

    def switch_tab(self, step):
        self.select_tab(self.tab_index + step)

    def on_pad(self, action):
        win = self.active_window()
        if win is None:
            return
        main = win is self.win
        focus = win.get_focus()
        dirs = {"up": Gtk.DirectionType.UP, "down": Gtk.DirectionType.DOWN,
                "left": Gtk.DirectionType.LEFT, "right": Gtk.DirectionType.RIGHT}
        if action in dirs:
            if main and (focus is None or (action == "down" and self.search.is_focus())):
                self.focus_grid()
            else:
                win.child_focus(dirs[action])
        elif action == "a":
            if main and focus is None:
                self.focus_grid()
            else:
                win.activate_focus()
        elif action == "b":
            if isinstance(win, Gtk.Dialog):
                win.response(Gtk.ResponseType.DELETE_EVENT)
            elif main and self.stack.get_visible_child_name() != "games":
                self.show_page("games")
            elif main:
                self.search.set_text("")
                self.focus_grid()
        elif action == "select" and main:
            self.consoles_btn.clicked()
        elif action in ("rs", "ls") and main:
            self.downloads_btn.clicked()
        elif action in ("lb", "rb"):
            if main:
                self.show_page("games")
                self.switch_tab(-1 if action == "lb" else 1)
            else:
                win.child_focus(Gtk.DirectionType.TAB_BACKWARD if action == "lb"
                                else Gtk.DirectionType.TAB_FORWARD)
        elif action in ("lt", "rt") and main:
            self.page_move(-1 if action == "lt" else 1)
        elif action == "y" and main:
            self.search.grab_focus()
        elif action == "x" and main:
            page = self.stack.get_visible_child_name()
            if page == "games" and getattr(focus, "rom", None):
                self.enqueue(focus.rom)             # quick download, no dialog
            elif page == "downloads" and getattr(focus, "job", None):
                self.job_action(focus.job)          # cancel / retry
        elif action == "start" and main:
            self.open_settings()

    def on_key(self, _win, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        key = event.keyval
        if ctrl and key == Gdk.KEY_f:
            self.search.grab_focus()
        elif ctrl and key in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            self.switch_tab(-1 if key == Gdk.KEY_Page_Up else 1)
        elif key == Gdk.KEY_F5:
            self.reload_all()
        elif key == Gdk.KEY_Escape and self.stack.get_visible_child_name() != "games":
            self.show_page("games")
        elif key == Gdk.KEY_Escape:
            self.search.set_text("")
            self.focus_grid()
        elif key == Gdk.KEY_Down and self.search.is_focus():
            self.focus_grid()
        else:
            return False
        return True

    def rom_title(self, rom):
        return rom.get("name") or rom.get("fs_name_no_ext") or rom.get("fs_name", "?")

    def rom_folder(self, rom):
        if rom.get("_game"):
            return rom["_game"].folder
        plat = self.platforms.get(rom.get("platform_id"), {})
        return emudeck.resolve_folder(
            self.cfg, rom.get("platform_slug") or plat.get("slug", ""),
            rom.get("platform_fs_slug") or plat.get("fs_slug", ""))

    def installed_path(self, rom):
        if rom.get("_path"):
            return rom["_path"] if os.path.exists(rom["_path"]) else None
        folder = self.rom_folder(rom)
        if not folder:
            return None
        path = os.path.join(self.cfg["roms_path"], folder, os.path.basename(rom["fs_name"]))
        return path if os.path.exists(path) else None

    def add_card(self, rom):
        child = Gtk.FlowBoxChild()
        child.rom = rom
        title_text = self.rom_title(rom)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.get_style_context().add_class("card")
        img = Gtk.Image(halign=Gtk.Align.CENTER)
        img.set_size_request(style.COVER_W, style.COVER_H)
        child.badge = Gtk.Label(label="✓ Installed", halign=Gtk.Align.END,
                                valign=Gtk.Align.START, no_show_all=True)
        child.badge.get_style_context().add_class("badge")
        path = self.installed_path(rom)
        child.badge.set_visible(bool(path))
        child.steam_badge = Gtk.Label(label="Not in Steam", halign=Gtk.Align.START,
                                      valign=Gtk.Align.END, no_show_all=True)
        child.steam_badge.get_style_context().add_class("badge-warn")
        child.steam_badge.set_visible(bool(path) and not self.in_steam(path))
        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER)
        overlay.add(img)
        overlay.add_overlay(child.badge)
        overlay.add_overlay(child.steam_badge)
        child.missing_badge = Gtk.Label(label="✗ Not on drive", halign=Gtk.Align.END,
                                        valign=Gtk.Align.START, no_show_all=True)
        child.missing_badge.get_style_context().add_class("badge-bad")
        child.missing_badge.set_visible(bool(rom.get("_missing")))
        overlay.add_overlay(child.missing_badge)
        title = Gtk.Label(label=title_text, wrap=True, lines=2, max_width_chars=16,
                          ellipsize=Pango.EllipsizeMode.END, justify=Gtk.Justification.CENTER)
        title.get_style_context().add_class("card-title")
        child.img, child.title_label = img, title
        box.pack_start(overlay, False, False, 0)
        box.pack_start(title, False, False, 0)
        if self.platform is None:   # "All games": say which console each one is for
            sub = Gtk.Label(label=rom.get("platform_display_name") or "", max_width_chars=16,
                            ellipsize=Pango.EllipsizeMode.END)
            sub.get_style_context().add_class("card-sub")
            box.pack_start(sub, False, False, 0)
        child.add(box)
        self.flow.add(child)
        img.set_from_surface(style.placeholder_surface(title_text))
        self.load_cover(rom, img)

    def load_cover(self, rom, img):
        if not self.real_rom(rom):
            if rom.get("_appid"):
                self.load_steam_cover(rom["_appid"], img)
            return
        rid = rom["id"]
        if rid in self.covers:
            img.set_from_surface(self.covers[rid])
            return
        gen = self.gen

        def fetch():
            data = self.client.cover(rom) if gen == self.gen else None
            if data:
                GLib.idle_add(self.set_cover, rid, img, data)
        self.pool.submit(fetch)

    def load_steam_cover(self, appid, img):
        """Portrait art Steam already has for a shortcut (grid/<appid>p.*)."""
        key = f"steam:{appid}"
        if key in self.covers:
            img.set_from_surface(self.covers[key])
            return

        def fetch():
            path = steam.grid_image(self.cfg, appid, "tall")
            if path:
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                except OSError:
                    return
                GLib.idle_add(self.set_cover, key, img, data)
        self.pool.submit(fetch)

    def set_cover(self, rid, img, data):
        pb = pixbuf_from_bytes(data)
        if pb:
            self.covers[rid] = style.cover_surface(pb)
            img.set_from_surface(self.covers[rid])
        return False

    # -- details / download -------------------------------------------------------------
    def open_details(self, rom):
        if rom.get("_missing"):
            self.open_missing(rom)
            return
        folder = self.rom_folder(rom)
        installed = self.installed_path(rom)
        known = self.real_rom(rom)                   # None: a file RomM does not have
        game = rom.get("_game")
        dlg = Gtk.Dialog(title=self.rom_title(rom), transient_for=self.win, modal=True)
        dlg.set_default_size(860, 440)
        box = Gtk.Box(spacing=22, margin=22)
        dlg.get_content_area().add(box)
        big_w, big_h = 240, 320
        art = Gtk.Image(valign=Gtk.Align.START)
        art.set_size_request(big_w, big_h)
        art.set_from_surface(style.placeholder_surface(self.rom_title(rom), big_w, big_h))
        box.pack_start(art, False, False, 0)

        def fetch_art():
            if not known:
                art_path = rom.get("_appid") and steam.grid_image(self.cfg, rom["_appid"])
                if art_path:
                    with open(art_path, "rb") as f:
                        return f.read()
                return None
            large = dict(rom, path_cover_small=rom.get("path_cover_large")
                         or rom.get("path_cover_small"))
            return self.client.cover(large)

        def show_art(data):
            pb = pixbuf_from_bytes(data) if data else None
            if pb and art.get_mapped():
                art.set_from_surface(style.cover_surface(pb, big_w, big_h))
        self.run_bg(fetch_art, show_art, lambda _e: None)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.pack_start(info, True, True, 0)
        dest = os.path.join(self.cfg["roms_path"], folder) if folder else "(you'll be asked to pick a folder)"
        size = rom.get("fs_size_bytes")
        if size is None and installed and os.path.isfile(installed):
            size = os.path.getsize(installed)
        steam_note = ""
        if installed:
            steam_note = "   •   In Steam" if self.in_steam(installed) else "   •   Not in Steam"
        where = (f"On this device: {installed}" if installed
                 else f"File: {rom.get('fs_name', '')}\nSaves to: {dest}")
        lines = [
            (self.rom_title(rom), "detail-title"),
            (f"{rom.get('platform_display_name') or rom.get('platform_slug', '')}"
             f"   •   {human_size(size)}"
             + ("   •   ✓ Installed" if installed else "") + steam_note, "detail-meta"),
            (where, "detail-small"),
        ]
        for text, cls in lines:
            label = Gtk.Label(xalign=0, wrap=True, label=text, max_width_chars=56)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.get_style_context().add_class(cls)
            info.pack_start(label, False, False, 0)
        if rom.get("summary"):
            summary = Gtk.Label(label=rom["summary"], xalign=0, yalign=0, wrap=True,
                                max_width_chars=56)
            summary.get_style_context().add_class("detail-summary")
            sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, can_focus=False)
            sw.add(summary)
            info.pack_start(sw, True, True, 0)

        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        if installed:
            dlg.add_button("Remove", 3)
            dlg.add_button("Add to Steam", 2)
            if known:
                dlg.add_button("Download again", 1)
        else:
            dlg.add_button("Download", 1)
            dlg.set_default_response(1)
        dlg.show_all()
        main_btn = dlg.get_widget_for_response(2 if installed else 1)
        main_btn.get_style_context().add_class("suggested-action")
        main_btn.grab_focus()
        res = dlg.run()
        dlg.destroy()
        if res == 1:
            self.enqueue(rom)
        elif res == 2:
            self.add_to_steam([(known, installed, None, game) if game
                               else (rom, installed, None)])
        elif res == 3:
            self.remove_game(rom, installed)

    def open_missing(self, rom):
        """Details for a Steam shortcut whose rom file is no longer on the drive:
        remove the shortcut, or download the game again from RomM."""
        path, title = rom["_path"], self.rom_title(rom)
        dlg = Gtk.Dialog(title=title, transient_for=self.win, modal=True)
        dlg.set_default_size(860, 440)
        box = Gtk.Box(spacing=22, margin=22)
        dlg.get_content_area().add(box)
        big_w, big_h = 240, 320
        art = Gtk.Image(valign=Gtk.Align.START)
        art.set_size_request(big_w, big_h)
        art.set_from_surface(style.placeholder_surface(title, big_w, big_h))
        art_path = rom.get("_appid") and steam.grid_image(self.cfg, rom["_appid"])
        pb = None
        if art_path:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(art_path)
            except GLib.Error:
                pass
        if pb:
            art.set_from_surface(style.cover_surface(pb, big_w, big_h))
        box.pack_start(art, False, False, 0)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.pack_start(info, True, True, 0)
        for text, cls in ((title, "detail-title"),
                          (f"{rom['platform_display_name']}   •   In Steam   •   "
                           "✗ Not on drive", "detail-meta"),
                          (f"The Steam shortcut launches:\n{path}\nThat file is no longer "
                           "on this device, so the game won't start.", "detail-small")):
            label = Gtk.Label(xalign=0, wrap=True, label=text, max_width_chars=56)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.get_style_context().add_class(cls)
            info.pack_start(label, False, False, 0)
        romm_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=56,
                               label="Offline: can't check RomM." if self.offline
                               else "Looking for it on RomM…")
        romm_label.get_style_context().add_class("detail-small")
        info.pack_start(romm_label, False, False, 0)

        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.add_button("Remove from Steam", 4)
        dlg.add_button("Download", 1)
        download = dlg.get_widget_for_response(1)
        download.set_sensitive(False)
        found = {}

        def show(res):
            rom_found, exact = res
            if not dlg.get_mapped():
                return
            if not rom_found:
                romm_label.set_text("RomM doesn't have this game.")
                return
            found.update(rom=rom_found, exact=exact)
            romm_label.set_text(
                f"RomM has it ({rom_found.get('fs_name')}). Downloading it puts the file "
                "back, so the Steam shortcut works again." if exact else
                f"RomM has it as {rom_found.get('fs_name')}. Downloading it replaces this "
                "shortcut with a new one for that file.")
            download.set_sensitive(True)
            download.get_style_context().add_class("suggested-action")
            download.grab_focus()
        if not self.offline:
            self.run_bg(lambda: self.find_on_romm(rom), show,
                        lambda _e: romm_label.set_text("Couldn't reach RomM."))
        dlg.show_all()
        dlg.get_widget_for_response(4).grab_focus()
        res = dlg.run()
        dlg.destroy()
        if res == 4:
            self.remove_from_steam(rom)
        elif res == 1 and found:
            if found["exact"]:
                self.enqueue(found["rom"], restores_shortcut=True)
            else:
                self.remove_from_steam(rom, ask=False)
                self.enqueue(found["rom"])

    def find_on_romm(self, rom):
        """RomM's copy of a missing game: (rom, True) when its file is the one the
        shortcut launches, (rom, False) for a same-titled game, else (None, False)."""
        g = rom["_game"]
        name = os.path.basename(g.path)
        stem = os.path.splitext(name)[0]
        keys = {srm.norm(t) for t in (rom["name"], stem)} - {""}
        title_match = None
        for pid, plat in self.platforms.items():
            if self.tab_folder(pid) != g.folder:
                continue
            for term in dict.fromkeys((srm.clean_title(stem), rom["name"])):
                for cand in self.client.roms(pid, term, 25, 0)[0]:
                    target = os.path.join(self.cfg["roms_path"], g.folder,
                                          os.path.basename(cand.get("fs_name") or ""))
                    if os.path.normpath(target) == os.path.normpath(g.path):
                        return cand, True
                    if not title_match and srm.norm(cand.get("name") or "") in keys:
                        title_match = cand
        return title_match, False

    def remove_from_steam(self, rom, ask=True):
        title, appid = self.rom_title(rom), rom.get("_appid")
        if not appid:
            return
        if ask and not self.ask(f"Remove {title} from Steam?",
                                "This deletes its Steam shortcut, because the game's file "
                                f"is no longer on this device:\n{rom['_path']}",
                                yes="Remove from Steam", no="Keep"):
            return
        try:
            removed = steam.remove_shortcuts(self.cfg, [appid])
            steam.remember_pending_removal([appid], [rom["_path"]])
        except (steam.SteamError, OSError, ValueError) as e:
            self.show_error(f"Could not remove {title} from Steam: {e}")
            return
        self.refresh_steam_paths()
        self.status.set_text(f"Removed {title} from Steam" if removed
                             else f"{title} was not in Steam")
        if self.installed_only:
            self.reload_roms()
        if removed and steam.steam_running():
            self.offer_restart(removed, "removed from Steam",
                               "Steam needs a restart before it disappears from your library.")

    def remove_game(self, rom, path):
        title = self.rom_title(rom)
        if not self.ask(f"Remove {title}?",
                        f"This deletes it from this device:\n{path}\n\n"
                        "It stays on your RomM server. Any Steam shortcut is not removed.",
                        yes="Remove", no="Keep"):
            return
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as e:
            self.show_error(f"Could not remove {title}: {e}")
            return
        self.status.set_text(f"Removed {title}")
        if self.installed_only:                      # it may now be "Not on drive"
            self.reload_roms()
            return
        for child in self.flow.get_children():
            if child.rom["id"] == rom["id"]:
                if self.installed_only:
                    self.flow.remove(child)
                else:
                    child.badge.hide()

    def pick_folder(self, rom):
        """Ask which EmuDeck folder a platform belongs in; remembered afterwards."""
        folders = emudeck.list_folders(self.cfg["roms_path"])
        if not folders:
            self.show_error(f"ROMs folder not found: {self.cfg['roms_path'] or '(not set)'}\n"
                            "Set it in Settings.")
            return None
        pname = rom.get("platform_display_name") or rom.get("platform_slug", "this platform")
        dlg = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                text=f"Which folder should {pname} games go in?")
        dlg.format_secondary_text("I couldn't match this RomM platform to an EmuDeck "
                                  "folder. Your choice is remembered.")
        lb = Gtk.ListBox(activate_on_single_click=True)
        for f in folders:
            label = Gtk.Label(label=f, xalign=0)
            label.get_style_context().add_class("folder-row")
            lb.add(label)
        lb.connect("row-activated", lambda *_: dlg.response(Gtk.ResponseType.OK))
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_size_request(-1, 360)
        sw.add(lb)
        lb.set_adjustment(sw.get_vadjustment())
        dlg.get_content_area().add(sw)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.show_all()
        lb.get_row_at_index(0).grab_focus()
        res = dlg.run()
        row = lb.get_selected_row()
        choice = folders[row.get_index()] if row else None
        dlg.destroy()
        if res != Gtk.ResponseType.OK or not choice:
            return None
        key = rom.get("platform_fs_slug") or rom.get("platform_slug")
        self.cfg["platform_map"][key] = choice
        config.save(self.cfg)
        return choice

    # -- downloads ----------------------------------------------------------------------
    MAX_ACTIVE = 2          # downloads running at the same time; the rest wait

    def enqueue(self, rom, restores_shortcut=False):
        title = self.rom_title(rom)
        for job in self.jobs:
            if job["rom"]["id"] == rom["id"] and job["state"] in ("queued", "downloading"):
                self.status.set_text(f"{title} is already downloading")
                return
        folder = self.rom_folder(rom) or self.pick_folder(rom)
        if not folder:
            return
        job = {"rom": rom, "title": title, "state": "queued", "cancel": False,
               "dest": os.path.join(self.cfg["roms_path"], folder), "path": None,
               "done": 0, "total": rom.get("fs_size_bytes") or 0, "error": "",
               "steam": "exists" if restores_shortcut else "",
               "offered": restores_shortcut}
        self.jobs.append(job)
        self.build_job_row(job)
        self.status.set_text(f"Queued {title}")
        self.pump()

    def build_job_row(self, job):
        row = Gtk.ListBoxRow()
        row.job = job
        box = Gtk.Box(spacing=14)
        box.get_style_context().add_class("job-row")
        job["tick"] = Gtk.CheckButton(can_focus=False, no_show_all=True,
                                      valign=Gtk.Align.CENTER)
        job["tick"].connect("toggled", lambda *_: self.refresh_downloads())
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=job["title"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.get_style_context().add_class("job-title")
        job["label"] = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        job["label"].get_style_context().add_class("job-state")
        job["bar"] = Gtk.ProgressBar(no_show_all=True)
        for w in (name, job["label"], job["bar"]):
            text.pack_start(w, False, False, 0)
        job["button"] = Gtk.Button(can_focus=False, no_show_all=True, valign=Gtk.Align.CENTER)
        job["button"].connect("clicked", lambda *_: self.job_action(job))
        box.pack_start(job["tick"], False, False, 0)
        box.pack_start(text, True, True, 0)
        box.pack_end(job["button"], False, False, 0)
        row.add(box)
        job["row"] = row
        self.job_list.add(row)
        row.show_all()
        self.refresh_job(job)

    def refresh_job(self, job):
        state = job["state"]
        platform = job["rom"].get("platform_display_name") or ""
        if state == "queued":
            text = f"{platform}  •  Waiting…"
        elif state == "downloading":
            total = f" / {human_size(job['total'])}" if job["total"] else ""
            text = f"{platform}  •  {human_size(job['done'])}{total}"
        elif state == "done":
            steam_note = {"added": "  •  Added to Steam", "exists": "  •  Already in Steam",
                          "adding": "  •  Adding to Steam…"}.get(job["steam"], "")
            text = f"{platform}  •  Downloaded to {job['dest']}{steam_note}"
        elif state == "cancelled":
            text = f"{platform}  •  Cancelled"
        else:
            text = f"{platform}  •  Failed: {job['error']}"
        job["label"].set_text(text)
        job["bar"].set_visible(state == "downloading")
        if state == "downloading":
            if job["total"]:
                job["bar"].set_fraction(min(job["done"] / job["total"], 1.0))
            else:
                job["bar"].pulse()
        can_tick = state == "done" and not job["steam"]
        job["tick"].set_visible(can_tick)
        if not can_tick:
            job["tick"].set_active(False)
        label = {"queued": "Cancel", "downloading": "Cancel", "failed": "Retry",
                 "cancelled": "Retry"}.get(state)
        job["button"].set_visible(bool(label))
        if label:
            job["button"].set_label(label)
        self.refresh_downloads()

    def refresh_downloads(self):
        """Update the Downloads button, the page buttons and the bottom bar."""
        active = [j for j in self.jobs if j["state"] == "downloading"]
        waiting = [j for j in self.jobs if j["state"] == "queued"]
        busy = len(active) + len(waiting)
        self.downloads_btn.set_label(f"Downloads  ({busy})" if busy else "Downloads")
        ticked = [j for j in self.jobs if j["tick"].get_active()]
        tickable = [j for j in self.jobs if j["tick"].get_visible()]
        self.steam_btn.set_label(f"Add {len(ticked)} to Steam" if ticked
                                 else "Add ticked to Steam")
        self.steam_btn.set_sensitive(bool(ticked))
        self.select_all_btn.set_sensitive(bool(tickable))
        self.select_all_btn.set_label("Untick all" if tickable and len(ticked) == len(tickable)
                                      else "Tick all")
        self.clear_btn.set_sensitive(any(j["state"] in ("done", "failed", "cancelled")
                                         for j in self.jobs))
        self.jobs_empty.set_visible(not self.jobs)
        self.job_scroller.set_visible(bool(self.jobs))
        if active:
            done = sum(j["done"] for j in active)
            total = sum(j["total"] for j in active)
            names = active[0]["title"] if len(active) == 1 else f"{len(active)} games"
            more = f"  (+{len(waiting)} waiting)" if waiting else ""
            self.status.set_text(f"Downloading {names}…  {human_size(done)}"
                                 + (f" / {human_size(total)}" if total else "") + more)
            self.progress.show()
            if total:
                self.progress.set_fraction(min(done / total, 1.0))
            else:
                self.progress.pulse()
        else:
            self.progress.hide()

    def pump(self):
        """Start waiting downloads while there is a free slot."""
        while sum(j["state"] == "downloading" for j in self.jobs) < self.MAX_ACTIVE:
            job = next((j for j in self.jobs if j["state"] == "queued"), None)
            if not job:
                break
            self.start_job(job)
        self.refresh_downloads()

    def start_job(self, job):
        job.update(state="downloading", cancel=False, done=0, error="")
        self.refresh_job(job)
        last = [0.0]

        def progress(done, total):
            job["done"], job["total"] = done, total or job["total"]
            now = GLib.get_monotonic_time()
            if now - last[0] > 250000:
                last[0] = now
                GLib.idle_add(lambda: self.refresh_job(job) and False)

        def done(path):
            job.update(state="done", path=path)
            self.status.set_text(f"Downloaded {job['title']}")
            self.mark_installed(job["rom"])
            self.refresh_job(job)
            self.pump()
            self.offer_steam()

        def fail(err):
            if isinstance(err, Cancelled):
                job["state"] = "cancelled"
                self.status.set_text(f"Cancelled {job['title']}")
            else:
                job.update(state="failed", error=str(err))
                self.status.set_text(f"{job['title']} failed: {err}")
            self.refresh_job(job)
            self.pump()
            self.offer_steam()

        self.run_bg(lambda: self.client.download(job["rom"], job["dest"], progress,
                                                 lambda: job["cancel"]), done, fail)

    def job_action(self, job):
        """The row's button: cancel a waiting/running download, or retry one."""
        if job["state"] == "queued":
            job["state"] = "cancelled"
            self.refresh_job(job)
        elif job["state"] == "downloading":
            job["cancel"] = True
        elif job["state"] in ("failed", "cancelled"):
            job["state"] = "queued"
            self.refresh_job(job)
            self.pump()

    def job_toggle(self, job):
        """Tapping / pressing A on a finished download ticks it for Steam."""
        if job["tick"].get_visible():
            job["tick"].set_active(not job["tick"].get_active())

    def tick_all(self):
        tickable = [j for j in self.jobs if j["tick"].get_visible()]
        target = not all(j["tick"].get_active() for j in tickable)
        for job in tickable:
            job["tick"].set_active(target)

    def clear_finished(self):
        for job in [j for j in self.jobs if j["state"] in ("done", "failed", "cancelled")]:
            self.jobs.remove(job)
            self.job_list.remove(job["row"])
        self.refresh_downloads()
        self.focus_grid()

    def add_ticked_to_steam(self):
        self.add_to_steam([(j["rom"], j["path"], j) for j in self.jobs
                           if j["tick"].get_active()])

    def offer_steam(self):
        """Once every download has finished, ask one question about all of them."""
        if any(j["state"] in ("queued", "downloading") for j in self.jobs):
            return
        fresh = [j for j in self.jobs
                 if j["state"] == "done" and not j["offered"] and not j["steam"]]
        if not fresh:
            return
        for job in fresh:
            job["offered"] = True
        if len(fresh) == 1:
            if self.ask(f"{fresh[0]['title']} downloaded",
                        f"Saved to {fresh[0]['path']}\n\nAdd it to your Steam library?",
                        yes="Add to Steam", no="Not now"):
                self.add_to_steam([(fresh[0]["rom"], fresh[0]["path"], fresh[0])])
            return
        names = "\n".join("•  " + j["title"] for j in fresh[:8])
        if len(fresh) > 8:
            names += f"\n…and {len(fresh) - 8} more"
        res = self.choose(f"{len(fresh)} games downloaded",
                          names + "\n\nAdd them to your Steam library?",
                          [("Not now", Gtk.ResponseType.NO), ("Choose…", 1),
                           ("Add all to Steam", Gtk.ResponseType.YES)],
                          Gtk.ResponseType.YES)
        if res == Gtk.ResponseType.YES:
            self.add_to_steam([(j["rom"], j["path"], j) for j in fresh])
        elif res == 1:
            for job in fresh:
                job["tick"].set_active(True)
            self.show_page("downloads")

    def mark_installed(self, rom):
        if self.installed_only:
            self.reload_roms()
            return
        for child in self.flow.get_children():
            if child.rom["id"] == rom["id"]:
                child.badge.show()
                path = self.installed_path(child.rom)
                child.steam_badge.set_visible(bool(path) and not self.in_steam(path))

    # -- steam ---------------------------------------------------------------------------
    def add_to_steam(self, items):
        """Add games to Steam. items is a list of (rom, path, job or None), with an
        optional fourth srm.Game for games found on disk (rom may then be None).
        Runs one batch at a time; anything requested meanwhile joins the queue."""
        for _rom, _path, job, *_game in items:
            if job:
                job["steam"] = "adding"
                self.refresh_job(job)
        self.steam_backlog.extend(items)
        if self.steam_busy or not self.steam_backlog:
            return
        self.steam_busy = True
        batch, self.steam_backlog = self.steam_backlog, []

        def work():
            results = []
            for i, (rom, path, job, *rest) in enumerate(batch, 1):
                game = rest[0] if rest else None
                title = self.rom_title(rom) if rom else game.title
                GLib.idle_add(lambda t=title, i=i: self.status.set_text(
                    f"Adding to Steam ({i} of {len(batch)}): {t}…") and False)
                try:
                    if rom:
                        art = artwork.gather(self.client, self.cfg, rom, title)
                    else:
                        key = self.cfg.get("sgdb_key")
                        art = artwork.from_steamgriddb(key, title) if key else {}
                    folder = game.folder if game else self.rom_folder(rom)
                    if folder:
                        coll = emudeck.console_name(folder)
                    else:                            # RomM rom saved to a picked folder
                        plat = self.platforms.get(rom.get("platform_id"))
                        coll = rom.get("platform_display_name") or (
                            platform_name(plat) if plat else None)
                    launch = game.launch if game else None
                    tags = {"0": game.category} if game and game.category else None
                    _appid, existed = steam.add_shortcut(self.cfg, title, path, art,
                                                         collection=coll, launch=launch,
                                                         tags=tags)
                    if not existed or self.cfg.get("steam_collections", True):
                        steam.remember_pending(title, path, coll, launch, tags)
                    results.append((job, title, "exists" if existed else "added", ""))
                except (steam.SteamError, RommError, OSError) as e:
                    results.append((job, title, "", str(e)))
            return results

        def done(results):
            self.steam_busy = False
            for job, _title, outcome, _err in results:
                if job:
                    job["steam"] = outcome
                    self.refresh_job(job)
            added = [t for _j, t, o, _e in results if o == "added"]
            existed = [t for _j, t, o, _e in results if o == "exists"]
            errors = [(t, e) for _j, t, o, e in results if not o]
            self.steam_added += len(added)
            self.refresh_steam_paths()
            for child in self.flow.get_children():
                path = self.installed_path(child.rom)
                child.steam_badge.set_visible(bool(path) and not self.in_steam(path))
            parts = []
            if added:
                parts.append(f"Added {len(added)} to Steam" if len(added) > 1
                             else f"Added {added[0]} to Steam")
            if existed:
                parts.append(f"{len(existed)} already in Steam (artwork filled in)")
            if errors:
                parts.append(f"{len(errors)} could not be added")
            self.status.set_text(" • ".join(parts))
            if errors:
                self.show_error("Could not add to Steam:\n\n" + "\n\n".join(
                    f"{t}: {e}" for t, e in errors))
            if self.steam_backlog:                  # more arrived while we were busy
                self.add_to_steam([])
                return
            if self.steam_added and steam.steam_running():
                count, self.steam_added = self.steam_added, 0
                self.offer_restart(count)

        def fail(err):
            self.steam_busy = False
            self.show_error(err)

        self.run_bg(work, done, fail)

    def offer_restart(self, count, verb="added to Steam", body=None):
        what = "game" if count == 1 else "games"
        extra = ("\n\nThis app will close and Steam will restart."
                 if steam.in_game_mode() else "")
        if not self.ask(f"{count} {what} {verb}",
                        (body or f"Steam needs a restart before the {what} show up in your "
                         "library.") + " You can keep downloading and restart later." + extra,
                        yes="Restart Steam now", no="Later"):
            return
        if any(j["state"] in ("queued", "downloading") for j in self.jobs) and not self.ask(
                "Downloads are still running",
                "Restarting Steam now may interrupt them. Restart anyway?",
                yes="Restart", no="Wait"):
            return
        steam.restart_steam()

    # -- settings ------------------------------------------------------------------------
    def open_settings(self, *_):
        dlg = Gtk.Dialog(title="Settings", transient_for=self.win, modal=True)
        dlg.set_default_size(720, -1)
        grid = Gtk.Grid(row_spacing=10, column_spacing=12, margin=16)
        dlg.get_content_area().add(grid)
        fields = {}

        def row(n, label, key, password=False, hint="", folder=False):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, n, 1, 1)
            entry = Gtk.Entry(text=str(self.cfg.get(key, "")), hexpand=True,
                              placeholder_text=hint, activates_default=True)
            if password:
                entry.set_visibility(False)
                entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            grid.attach(entry, 1, n, 1, 1)
            if folder:
                btn = Gtk.Button(label="Browse…")
                btn.connect("clicked", lambda *_: self.browse(dlg, entry))
                grid.attach(btn, 2, n, 1, 1)
            fields[key] = entry

        row(0, "RomM server URL", "server_url", hint="http://192.168.1.50:8080")
        row(1, "Username", "username")
        row(2, "Password", "password", password=True)
        row(3, "API token (optional)", "api_token", password=True,
            hint="rmm_… — used instead of the password if set")
        grid.attach(Gtk.Label(label="SteamGridDB key (optional)", xalign=1), 0, 8, 1, 1)
        fields["sgdb_key"] = Gtk.Entry(
            text=self.cfg.get("sgdb_key", ""), hexpand=True, visibility=False,
            placeholder_text="For logos and banners when adding games to Steam")
        grid.attach(fields["sgdb_key"], 1, 8, 1, 1)
        verify = Gtk.CheckButton(label="Verify HTTPS certificate (untick for self-signed)",
                                 active=self.cfg.get("verify_tls", True))
        grid.attach(verify, 1, 4, 2, 1)
        show = Gtk.CheckButton(label="Show")
        show.connect("toggled", lambda b: [fields[k].set_visibility(b.get_active())
                                           for k in ("password", "api_token", "sgdb_key")])
        grid.attach(show, 2, 2, 1, 1)
        dark = Gtk.CheckButton(label="Dark mode", active=self.cfg.get("dark_mode", True))
        grid.attach(dark, 1, 9, 2, 1)
        colls = Gtk.CheckButton(
            label="Add games to a Steam collection per console (e.g. N64)",
            active=self.cfg.get("steam_collections", True))
        grid.attach(colls, 1, 10, 2, 1)
        grid.attach(Gtk.Separator(), 0, 5, 3, 1)
        row(6, "EmuDeck ROMs folder", "roms_path", folder=True)
        row(7, "EmuDeck launchers folder", "launchers_path", folder=True)
        result = Gtk.Label(xalign=0, wrap=True)
        grid.attach(result, 1, 11, 2, 1)

        def collect():
            cfg = dict(self.cfg)
            for key, entry in fields.items():
                cfg[key] = entry.get_text().strip() if key != "password" else entry.get_text()
            cfg["verify_tls"] = verify.get_active()
            cfg["dark_mode"] = dark.get_active()
            cfg["steam_collections"] = colls.get_active()
            return cfg

        def test(*_):
            result.set_text("Testing…")
            cfg = collect()
            self.run_bg(lambda: RommClient(cfg).platforms(),
                        lambda p: result.set_text(f"✓ Connected — {len(p)} platforms found"),
                        lambda e: result.set_text(f"✗ {e}"))

        test_btn = Gtk.Button(label="Test connection")
        test_btn.connect("clicked", test)
        dlg.add_action_widget(test_btn, Gtk.ResponseType.NONE)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Save", Gtk.ResponseType.OK).get_style_context().add_class(
            "suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        while True:
            res = dlg.run()
            if res != Gtk.ResponseType.NONE:
                break
        if res == Gtk.ResponseType.OK:
            self.cfg = collect()
            config.save(self.cfg)
            self.apply_theme()
            self.reload_all()
        dlg.destroy()

    def browse(self, parent, entry):
        fc = Gtk.FileChooserNative.new("Choose folder", parent,
                                       Gtk.FileChooserAction.SELECT_FOLDER, None, None)
        if os.path.isdir(entry.get_text()):
            fc.set_current_folder(entry.get_text())
        if fc.run() == Gtk.ResponseType.ACCEPT:
            entry.set_text(fc.get_filename())
        fc.destroy()


def main():
    App().run(None)
