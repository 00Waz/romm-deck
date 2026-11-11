"""Install GUI shown when the AppImage is run for the first time (or on request)."""
import os
import shutil
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from . import config, steam, style  # noqa: E402

HOME = os.path.expanduser("~")
INSTALL_DIR = os.path.join(HOME, "Applications")
INSTALL_PATH = os.path.join(INSTALL_DIR, "RomM-Deck.AppImage")
DESKTOP_FILE = os.path.join(HOME, ".local/share/applications/romm-deck.desktop")
ICON_FILE = os.path.join(HOME, ".local/share/icons/romm-deck.png")
ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def source_appimage():
    return os.environ.get("APPIMAGE") or ""


def is_installed():
    return os.path.exists(INSTALL_PATH)


def steam_art():
    """Library artwork built from the bundled logo and icon."""
    def png(path, w, h):
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        scale = max(w / pb.get_width(), h / pb.get_height())
        nw, nh = int(pb.get_width() * scale + .5), int(pb.get_height() * scale + .5)
        pb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
        pb = pb.new_subpixbuf((nw - w) // 2, (nh - h) // 2, w, h)
        return pb.save_to_bufferv("png", [], [])[1]
    art = {}
    try:
        logo, icon = os.path.join(ASSETS, "logo.jpg"), os.path.join(ASSETS, "icon.png")
        art["wide"] = png(logo, 920, 430)
        art["hero"] = png(logo, 1920, 620)
        art["tall"] = png(icon, 600, 900)
        art["icon"] = png(icon, 256, 256)
    except GLib.Error:
        pass
    return art


def install(copy_app, menu, add_steam):
    """Perform the chosen steps. Returns a list of result lines."""
    out = []
    target = INSTALL_PATH
    if copy_app:
        src = source_appimage()
        if not src:
            raise RuntimeError("Not running from an AppImage, so there is nothing to copy.")
        os.makedirs(INSTALL_DIR, exist_ok=True)
        if os.path.realpath(src) != os.path.realpath(target):
            shutil.copy2(src, target + ".tmp")
            os.chmod(target + ".tmp", 0o755)
            os.replace(target + ".tmp", target)
        out.append(f"Installed to {target}")
    else:
        target = source_appimage() or target
    if menu:
        os.makedirs(os.path.dirname(ICON_FILE), exist_ok=True)
        shutil.copy2(os.path.join(ASSETS, "icon.png"), ICON_FILE)
        os.makedirs(os.path.dirname(DESKTOP_FILE), exist_ok=True)
        with open(DESKTOP_FILE, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=RomM Deck\n"
                    "Comment=Browse and download games from your RomM server\n"
                    f'Exec="{target}"\nIcon={ICON_FILE}\nCategories=Game;\nTerminal=false\n')
        subprocess.run(["update-desktop-database", os.path.dirname(DESKTOP_FILE)],
                       capture_output=True)
        out.append("Added to the application menu")
    if add_steam:
        cfg = config.load()
        was_running = steam.steam_running()
        if was_running:   # Steam rewrites shortcuts.vdf on exit, so edit it while closed
            subprocess.run(["steam", "-shutdown"], capture_output=True)
            for _ in range(60):
                if not steam.steam_running():
                    break
                time.sleep(1)
        try:
            _appid, existed = steam.add_app_shortcut(cfg, "RomM Deck", target, steam_art())
        finally:
            if was_running:
                subprocess.Popen(["setsid", "nohup", "steam"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out.append("Already in your Steam library" if existed else "Added to your Steam library")
        if was_running:
            out.append("Steam was restarted so the shortcut shows up")
    return out


def uninstall():
    out = []
    cfg = config.load()
    for path in (DESKTOP_FILE, ICON_FILE):
        if os.path.exists(path):
            os.remove(path)
    try:
        if steam.remove_app_shortcut(cfg, INSTALL_PATH):
            out.append("Removed the Steam shortcut (restart Steam to refresh the library)")
    except steam.SteamError:
        pass
    if os.path.exists(INSTALL_PATH):
        os.remove(INSTALL_PATH)
        out.append("Removed RomM-Deck.AppImage")
    out.append("Your settings in ~/.config/romm-deck were kept")
    return out


class Installer(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.rommdeck.installer")
        self.win = None

    def do_activate(self):
        if self.win:
            self.win.present()
            return
        s = Gtk.Settings.get_default()
        s.set_property("gtk-theme-name", "Adwaita")
        s.set_property("gtk-application-prefer-dark-theme", True)
        prov = Gtk.CssProvider()
        prov.load_from_data(style.css(True))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.ApplicationWindow(application=self, title="RomM Deck Installer",
                                         resizable=False)
        self.win.set_default_size(560, 1)
        icon = os.path.join(ASSETS, "icon.png")
        if os.path.exists(icon):
            Gtk.Window.set_default_icon_from_file(icon)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=26)
        self.win.add(box)
        head = Gtk.Box(spacing=16)
        if os.path.exists(icon):
            head.pack_start(Gtk.Image.new_from_pixbuf(
                GdkPixbuf.Pixbuf.new_from_file_at_scale(icon, 72, 72, True)), False, False, 0)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        t = Gtk.Label(label="Install RomM Deck", xalign=0)
        t.get_style_context().add_class("popup-title")
        sub = Gtk.Label(label="Browse your RomM server and download games into EmuDeck.",
                        xalign=0, wrap=True, max_width_chars=40)
        sub.get_style_context().add_class("popup-body")
        titles.pack_start(t, False, False, 0)
        titles.pack_start(sub, False, False, 0)
        head.pack_start(titles, True, True, 0)
        box.pack_start(head, False, False, 0)

        can_copy = bool(source_appimage())
        self.c_copy = Gtk.CheckButton(label=f"Install to {INSTALL_PATH.replace(HOME, '~')}",
                                      active=can_copy, sensitive=can_copy)
        self.c_menu = Gtk.CheckButton(label="Add to the desktop application menu", active=True)
        self.c_steam = Gtk.CheckButton(
            label="Add to my Steam library (Steam restarts if it is running)", active=False)
        for c in (self.c_copy, self.c_menu, self.c_steam):
            box.pack_start(c, False, False, 0)

        self.msg = Gtk.Label(xalign=0, wrap=True, max_width_chars=56)
        self.msg.get_style_context().add_class("popup-body")
        box.pack_start(self.msg, False, False, 0)

        bar = Gtk.Box(spacing=10, halign=Gtk.Align.END)
        self.b_remove = Gtk.Button(label="Uninstall", no_show_all=True)
        self.b_remove.set_visible(is_installed())
        self.b_remove.connect("clicked", self.on_uninstall)
        self.b_run = Gtk.Button(label="Run without installing")
        self.b_run.connect("clicked", lambda *_: self.launch(source_appimage()))
        self.b_install = Gtk.Button(label="Reinstall" if is_installed() else "Install")
        self.b_install.get_style_context().add_class("suggested-action")
        self.b_install.connect("clicked", self.on_install)
        self.b_close = Gtk.Button(label="Close")
        self.b_close.connect("clicked", lambda *_: self.quit())
        for b in (self.b_remove, self.b_run, self.b_close, self.b_install):
            bar.pack_start(b, False, False, 0)
        box.pack_start(bar, False, False, 0)
        self.win.show_all()
        self.b_install.grab_focus()

    def set_busy(self, busy, text=""):
        if text:
            self.msg.set_text(text)
        for b in (self.b_install, self.b_run, self.b_remove, self.b_close):
            b.set_sensitive(not busy)

    def work(self, fn, installed):
        def run():
            try:
                lines, ok = fn(), True
            except Exception as e:  # noqa: BLE001 - shown to the user
                lines, ok = [f"Failed: {e}"], False
            GLib.idle_add(lambda: self.finish(lines, ok and installed) and False)
        threading.Thread(target=run, daemon=True).start()

    def finish(self, lines, installed):
        self.set_busy(False, "\n".join(lines))
        self.b_remove.set_visible(is_installed())
        if installed:
            self.b_install.set_label("Open RomM Deck")
            self.b_install.disconnect_by_func(self.on_install)
            self.b_install.connect("clicked", lambda *_: self.launch(
                INSTALL_PATH if is_installed() else source_appimage()))
            self.b_install.grab_focus()
        return False

    def on_install(self, *_):
        copy, menu, st = (c.get_active() for c in (self.c_copy, self.c_menu, self.c_steam))
        if not (copy or menu or st):
            self.msg.set_text("Tick at least one option.")
            return
        self.set_busy(True, "Working…")
        self.work(lambda: install(copy, menu, st), True)

    def on_uninstall(self, *_):
        self.set_busy(True, "Removing…")
        self.work(uninstall, False)

    def launch(self, path):
        if path:
            subprocess.Popen([path, "--run"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.quit()


def main():
    Installer().run([])
