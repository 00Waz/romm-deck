"""Add non-Steam shortcuts by editing Steam's binary shortcuts.vdf."""
import base64
import glob
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib

from . import config, emudeck, srm

STEAM_ROOTS = ["~/.local/share/Steam", "~/.steam/steam"]
PENDING_FILE = os.path.join(config.CONFIG_DIR, "pending-shortcuts.json")


class SteamError(Exception):
    pass


# -- binary VDF --------------------------------------------------------------
def vdf_load(data):
    pos = 0

    def cstr():
        nonlocal pos
        end = data.index(b"\0", pos)
        s = data[pos:end].decode("utf-8", "surrogateescape")
        pos = end + 1
        return s

    def node():
        nonlocal pos
        out = {}
        while True:
            t = data[pos]
            pos += 1
            if t == 8:
                return out
            key = cstr()
            if t == 0:
                out[key] = node()
            elif t == 1:
                out[key] = cstr()
            elif t == 2:
                out[key] = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                raise SteamError(f"Unsupported shortcuts.vdf field type {t}")
    return node()


def vdf_dump(obj):
    out = bytearray()
    enc = lambda s: s.encode("utf-8", "surrogateescape") + b"\0"

    def node(d):
        for k, v in d.items():
            if isinstance(v, dict):
                out.extend(b"\x00" + enc(k))
                node(v)
            elif isinstance(v, str):
                out.extend(b"\x01" + enc(k) + enc(v))
            else:
                out.extend(b"\x02" + enc(k) + struct.pack("<I", v & 0xFFFFFFFF))
        out.extend(b"\x08")
    node(obj)
    return bytes(out)


# -- locating Steam ------------------------------------------------------------
def steam_users():
    """Return userdata folder names, most recently used first."""
    for root in STEAM_ROOTS:
        base = os.path.join(os.path.expanduser(root), "userdata")
        dirs = [d for d in glob.glob(os.path.join(base, "*"))
                if os.path.isdir(os.path.join(d, "config")) and os.path.basename(d) != "0"]
        if dirs:
            dirs.sort(key=lambda d: os.path.getmtime(os.path.join(d, "config")), reverse=True)
            return [os.path.basename(d) for d in dirs]
    return []


def config_dir(cfg):
    users = steam_users()
    user = cfg.get("steam_user") or (users[0] if users else "")
    if not user:
        raise SteamError("Could not find a Steam user folder (userdata).")
    for root in STEAM_ROOTS:
        d = os.path.join(os.path.expanduser(root), "userdata", user, "config")
        if os.path.isdir(d):
            return d
    raise SteamError(f"Steam user folder '{user}' not found.")


def _get(entry, name, default=""):
    for k, v in entry.items():
        if k.lower() == name.lower():
            return v
    return default


def app_id(exe, name):
    return zlib.crc32((exe + name).encode("utf-8")) | 0x80000000


# -- shortcut creation ----------------------------------------------------------
def _template_from_existing(shortcuts, folder_path, rom_path):
    """Copy the launch command of an existing shortcut for a rom in the same
    folder (e.g. one made by Steam ROM Manager), swapping in the new rom."""
    pat = re.compile(r'"' + re.escape(folder_path.rstrip("/")) + r'/[^"]*"')
    for entry in reversed(list(shortcuts.values())):
        exe, opts = _get(entry, "exe"), _get(entry, "LaunchOptions")
        if pat.search(exe) or pat.search(opts):
            new = lambda s: pat.sub(lambda _m: f'"{rom_path}"', s, count=1)
            return new(exe), new(opts), _get(entry, "StartDir"), dict(_get(entry, "tags", {}))
    return None


ART_FILES = {"tall": "{}p.png", "wide": "{}.png", "hero": "{}_hero.png",
             "logo": "{}_logo.png", "icon": "{}_icon.png"}
LOGO_POSITION = {"nVersion": 1, "logoPosition": {
    "pinnedPosition": "BottomLeft", "nWidthPct": 50, "nHeightPct": 50, "nVersion": 1}}


def write_artwork(cdir, appid, art):
    """Write artwork the way Steam ROM Manager does: one file per slot, the logo
    position file, and the 64-bit-id link Big Picture uses for the wide image.
    Slots that already have an image are left alone. Returns the icon path."""
    grid = os.path.join(cdir, "grid")
    os.makedirs(grid, exist_ok=True)
    for slot, pattern in ART_FILES.items():
        target = os.path.join(grid, pattern.format(appid))
        stem = os.path.splitext(target)[0]
        if not art.get(slot) or glob.glob(stem + ".*"):
            continue
        with open(target, "wb") as f:
            f.write(art[slot])
    wide = os.path.join(grid, f"{appid}.png")
    if os.path.exists(wide):
        link = os.path.join(grid, f"{(appid << 32) | 0x02000000}.png")
        if not os.path.lexists(link):
            os.symlink(wide, link)
    if os.path.exists(os.path.join(grid, f"{appid}_logo.png")):
        position = os.path.join(grid, f"{appid}.json")
        if not os.path.exists(position):
            with open(position, "w") as f:
                json.dump(LOGO_POSITION, f, indent="\t")
    icons = glob.glob(os.path.join(grid, f"{appid}_icon.*"))
    return icons[0] if icons else ""


def collection_name(folder):
    """Steam collection for an EmuDeck roms folder: n64 -> Nintendo 64."""
    return emudeck.console_name(folder)


def add_to_collections(cfg, groups, exclusive=False):
    """Put appids in Steam collections. groups is {collection name: [appid, ...]};
    collections are created as needed. Returns how many (game, collection)
    memberships changed. With exclusive, a game in groups is also taken out of
    any other collection RomM Deck made (e.g. after a console is renamed), and a
    RomM Deck collection left empty is deleted. Collections made by Steam, by
    SRM or by hand are never touched. Collections live in cloud-storage-namespace-1.json,
    which Steam rewrites while it runs, so this only sticks reliably while Steam
    is closed (the restart flow re-applies it)."""
    path = os.path.join(config_dir(cfg), "cloudstorage", "cloud-storage-namespace-1.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        if os.path.exists(path):
            raise SteamError("Steam's collections file is unreadable; left alone.")
        data = []
    now = int(time.time())
    changed = 0
    for name, appids in groups.items():
        cid = "rommdeck-" + base64.b64encode(name.encode()).decode()
        key = "user-collections." + cid
        entry = next((e[1] for e in data if e[0] == key), None)
        coll = (json.loads(entry["value"]) if entry
                else {"id": cid, "name": name, "added": [], "removed": []})
        new = [a for a in dict.fromkeys(appids)
               if a not in coll.get("added", []) or a in coll.get("removed", [])]
        if not new:
            continue
        if entry is None:
            entry = {"key": key, "conflictResolutionMethod": "custom",
                     "strMethodId": "union-collections"}
            data.append([key, entry])
        coll["added"] = list(coll.get("added", [])) + [a for a in new if a not in coll["added"]]
        coll["removed"] = [a for a in coll.get("removed", []) if a not in new]
        entry.update(value=json.dumps(coll, separators=(",", ":")),
                     timestamp=now, version=str(now))
        changed += len(new)
    if exclusive:
        changed += _drop_from_other_collections(data, groups, now)
    if not changed:
        return 0
    if os.path.exists(path):
        shutil.copy2(path, path + ".rommdeck-bak")
    tmp = path + ".rommdeck-tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)
    return changed


def _drop_from_other_collections(data, groups, now):
    target = {a: name for name, appids in groups.items() for a in appids}
    changed = 0
    for i, (key, entry) in enumerate(data):
        if not key.startswith("user-collections.rommdeck-") or not entry.get("value"):
            continue
        coll = json.loads(entry["value"])
        name = coll.get("name")
        drop = [a for a in coll.get("added", []) if a in target and target[a] != name]
        if not drop:
            continue
        changed += len(drop)
        coll["added"] = [a for a in coll["added"] if a not in drop]
        if not coll["added"] and name not in groups:
            data[i] = [key, {"key": key, "timestamp": now, "is_deleted": True,
                             "version": str(now)}]
        else:
            entry.update(value=json.dumps(coll, separators=(",", ":")),
                         timestamp=now, version=str(now))
    return changed


def add_to_collection(cfg, name, appid):
    add_to_collections(cfg, {name: [appid]})


def _roms_roots(cfg):
    roms = cfg["roms_path"].rstrip("/")
    roots = {roms, os.path.realpath(roms)}
    srm_roms = srm.roms_dir()
    if srm_roms:
        roots |= {srm_roms.rstrip("/"), os.path.realpath(srm_roms)}
    return sorted(roots, key=len, reverse=True)


def rom_shortcut_entries(cfg):
    """Steam shortcuts that launch something under the roms folder, as
    [(appid, folder, path, name)]: the system folder (e.g. "snes"), the
    normalised path of the rom file or folder it opens, and its Steam name."""
    path = os.path.join(config_dir(cfg), "shortcuts.vdf")
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "rb") as f:
        root = vdf_load(f.read())
    roots = _roms_roots(cfg)
    out = []
    for entry in next(iter(root.values()), {}).values():
        for quoted in re.findall(r'"([^"]+)"', _get(entry, "exe") + " "
                                 + _get(entry, "LaunchOptions")):
            rom = next((r for r in roots if quoted.startswith(r + "/")), None)
            if not rom:
                continue
            parts = quoted[len(rom) + 1:].split("/")
            if len(parts) >= 2:
                out.append((_get(entry, "appid", 0), parts[0], os.path.normpath(quoted),
                            _get(entry, "appname")))
                break
    return out


def rom_shortcuts(cfg):
    """As rom_shortcut_entries, without the name: [(appid, folder, path)]."""
    return [(a, f, p) for a, f, p, _n in rom_shortcut_entries(cfg)]


def grid_image(cfg, appid, slot="tall"):
    """Path of the artwork Steam has for a shortcut in one slot, or None."""
    stem = os.path.join(config_dir(cfg), "grid", ART_FILES[slot].format(appid))
    stem = os.path.splitext(stem)[0]
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if os.path.exists(stem + ext):
            return stem + ext
    return None


def missing_games(cfg, shortcuts=None):
    """Games on disk (see srm.scan) that no Steam shortcut launches."""
    shortcuts = rom_shortcuts(cfg) if shortcuts is None else shortcuts
    launched = set()
    for _a, _f, p in shortcuts:
        launched |= {p, os.path.realpath(p)}
    return [g for g in srm.scan(cfg)
            if os.path.normpath(g.path) not in launched
            and os.path.realpath(g.path) not in launched]


def add_shortcut(cfg, name, rom_path, art=None, collection=None, launch=None, tags=None):
    """Add a shortcut and, if enabled, file it in a collection (normally the
    RomM platform name; falls back to the roms folder in capitals).
    launch is (exe, launch options, start dir) from an SRM parser, used as is.
    Returns (appid, already_existed). See _add_shortcut."""
    appid, existed = _add_shortcut(cfg, name, rom_path, art, launch, tags)
    if cfg.get("steam_collections", True) and appid:
        target = emudeck.launch_target(rom_path)
        folder = os.path.relpath(target, cfg["roms_path"].rstrip("/")).split(os.sep)[0]
        try:
            add_to_collection(cfg, collection or collection_name(folder), appid)
        except (SteamError, OSError) as e:
            print("collection update failed:", e, file=sys.stderr)
    return appid, existed


def _add_shortcut(cfg, name, rom_path, art=None, launch=None, tags=None):
    """Add a shortcut for a downloaded rom. Returns (appid, already_existed).
    art is {slot: png bytes} (see artwork.py). An existing shortcut keeps its
    entry but gets any artwork it is missing.
    Raises SteamError if the system has no known launch command."""
    rom_path = emudeck.launch_target(rom_path)
    roms = next((r for r in _roms_roots(cfg) if rom_path.startswith(r + "/")),
                cfg["roms_path"].rstrip("/"))
    rel = os.path.relpath(rom_path, roms)
    folder = rel.split(os.sep)[0]
    cdir = config_dir(cfg)
    path = os.path.join(cdir, "shortcuts.vdf")

    root = {"shortcuts": {}}
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            root = vdf_load(f.read())
    top_key = next(iter(root), "shortcuts")
    shortcuts = root.setdefault(top_key, {})

    tmpl = None if launch else _template_from_existing(
        shortcuts, os.path.join(roms, folder), rom_path)
    if launch:
        (exe, opts, start_dir), tags = launch, dict(tags or {"0": "RomM"})
    elif tmpl:
        exe, opts, start_dir, tags = tmpl
    else:
        cmd = emudeck.launch_command(cfg, folder, rom_path)
        if not cmd:
            raise SteamError(
                f"I don't know which emulator launches '{folder}' games. Add one game "
                "from that system with Steam ROM Manager and I'll copy its settings.")
        (exe, start_dir), opts, tags = cmd, "", {"0": "RomM"}
        launcher = exe.split('"')[1]
        if not os.path.exists(launcher):
            raise SteamError(f"EmuDeck launcher not found: {launcher}")

    for entry in shortcuts.values():
        if _get(entry, "exe") == exe and _get(entry, "LaunchOptions") == opts:
            write_artwork(cdir, _get(entry, "appid", 0), art or {})
            return _get(entry, "appid", 0), True

    appid = app_id(exe, name)
    icon = write_artwork(cdir, appid, art or {})

    index = str(max([int(k) for k in shortcuts if k.isdigit()] + [-1]) + 1)
    shortcuts[index] = {
        "appid": appid, "appname": name, "exe": exe, "StartDir": start_dir,
        "icon": icon, "ShortcutPath": "", "LaunchOptions": opts, "IsHidden": 0,
        "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0,
        "DevkitGameID": "", "DevkitOverrideAppID": 0, "LastPlayTime": 0,
        "FlatpakAppID": "", "sortas": "", "tags": tags,
    }

    if os.path.exists(path):
        shutil.copy2(path, path + ".rommdeck-bak")
    tmp = path + ".rommdeck-tmp"
    with open(tmp, "wb") as f:
        f.write(vdf_dump(root))
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)
    return appid, False


def add_app_shortcut(cfg, name, exe, art=None, launch_options=""):
    """Add a shortcut for an application (not a rom), e.g. RomM Deck itself.
    Returns (appid, already_existed)."""
    cdir = config_dir(cfg)
    path = os.path.join(cdir, "shortcuts.vdf")
    root = {"shortcuts": {}}
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            root = vdf_load(f.read())
    shortcuts = root.setdefault(next(iter(root), "shortcuts"), {})
    quoted = f'"{exe}"'
    for entry in shortcuts.values():
        if _get(entry, "exe") == quoted:
            write_artwork(cdir, _get(entry, "appid", 0), art or {})
            return _get(entry, "appid", 0), True
    appid = app_id(quoted, name)
    icon = write_artwork(cdir, appid, art or {})
    index = str(max([int(k) for k in shortcuts if k.isdigit()] + [-1]) + 1)
    shortcuts[index] = {
        "appid": appid, "appname": name, "exe": quoted,
        "StartDir": f'"{os.path.dirname(exe)}"', "icon": icon, "ShortcutPath": "",
        "LaunchOptions": launch_options, "IsHidden": 0, "AllowDesktopConfig": 1,
        "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0, "DevkitGameID": "",
        "DevkitOverrideAppID": 0, "LastPlayTime": 0, "FlatpakAppID": "", "sortas": "",
        "tags": {"0": "RomM"},
    }
    if os.path.exists(path):
        shutil.copy2(path, path + ".rommdeck-bak")
    tmp = path + ".rommdeck-tmp"
    with open(tmp, "wb") as f:
        f.write(vdf_dump(root))
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)
    return appid, False


def remove_app_shortcut(cfg, exe):
    """Remove the shortcut for exe, if present. Returns True if one was removed."""
    path = os.path.join(config_dir(cfg), "shortcuts.vdf")
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        root = vdf_load(f.read())
    top = next(iter(root), "shortcuts")
    shortcuts = root.get(top, {})
    keep = [e for e in shortcuts.values() if _get(e, "exe") != f'"{exe}"']
    if len(keep) == len(shortcuts):
        return False
    root[top] = {str(i): e for i, e in enumerate(keep)}
    shutil.copy2(path, path + ".rommdeck-bak")
    with open(path + ".rommdeck-tmp", "wb") as f:
        f.write(vdf_dump(root))
    os.replace(path + ".rommdeck-tmp", path)
    return True


def remove_shortcuts(cfg, appids):
    """Remove the shortcuts with these appids (e.g. ones whose rom is gone) and
    take them out of RomM Deck's collections. Returns how many were removed."""
    appids = set(appids)
    path = os.path.join(config_dir(cfg), "shortcuts.vdf")
    if not appids or not os.path.exists(path):
        return 0
    with open(path, "rb") as f:
        root = vdf_load(f.read())
    top = next(iter(root), "shortcuts")
    shortcuts = root.get(top, {})
    keep = [e for e in shortcuts.values() if _get(e, "appid", 0) not in appids]
    removed = len(shortcuts) - len(keep)
    if removed:
        root[top] = {str(i): e for i, e in enumerate(keep)}
        shutil.copy2(path, path + ".rommdeck-bak")
        tmp = path + ".rommdeck-tmp"
        with open(tmp, "wb") as f:
            f.write(vdf_dump(root))
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    try:
        _remove_from_collections(cfg, appids)
    except (SteamError, OSError, ValueError) as e:
        print("collection clean-up failed:", e, file=sys.stderr)
    return removed


def _remove_from_collections(cfg, appids):
    path = os.path.join(config_dir(cfg), "cloudstorage", "cloud-storage-namespace-1.json")
    if not os.path.exists(path):
        return
    with open(path) as f:
        data = json.load(f)
    now, changed = int(time.time()), False
    for i, (key, entry) in enumerate(data):
        if not key.startswith("user-collections.rommdeck-") or not entry.get("value"):
            continue
        coll = json.loads(entry["value"])
        if not appids & set(coll.get("added", [])):
            continue
        changed = True
        coll["added"] = [a for a in coll["added"] if a not in appids]
        if coll["added"]:
            entry.update(value=json.dumps(coll, separators=(",", ":")),
                         timestamp=now, version=str(now))
        else:
            data[i] = [key, {"key": key, "timestamp": now, "is_deleted": True,
                             "version": str(now)}]
    if changed:
        shutil.copy2(path, path + ".rommdeck-bak")
        tmp = path + ".rommdeck-tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)


# -- restart handling -------------------------------------------------------------
# Steam only reads shortcuts.vdf when it starts. In case a running Steam rewrites
# the file as it exits, shortcuts are remembered and re-applied during a restart.
def remember_pending(name, rom_path, collection=None, launch=None, tags=None):
    pending = _read_pending()
    pending.append({"name": name, "rom_path": rom_path, "collection": collection,
                    "launch": list(launch) if launch else None, "tags": tags})
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f)


def _read_pending():
    try:
        with open(PENDING_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def remember_pending_collections(groups, exclusive=False):
    """Remember collection memberships to re-apply after a Steam restart."""
    pending = _read_pending()
    pending.append({"collections": groups, "exclusive": exclusive})
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f)


def remember_pending_removal(appids, rom_paths):
    """Remember shortcuts to remove again after a Steam restart, and forget any
    pending re-add of those roms so the restart does not bring them back."""
    appids, rom_paths = set(appids), {os.path.normpath(p) for p in rom_paths}
    pending = []
    for item in _read_pending():
        rom = item.get("rom_path")
        if rom and {os.path.normpath(rom),
                    os.path.normpath(emudeck.launch_target(rom))} & rom_paths:
            continue
        if "collections" in item:
            item["collections"] = {n: [a for a in ids if a not in appids]
                                   for n, ids in item["collections"].items()}
        pending.append(item)
    pending.append({"remove": sorted(appids)})
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f)


def apply_pending():
    cfg = config.load()
    for item in _read_pending():
        try:
            if "remove" in item:
                remove_shortcuts(cfg, item["remove"])
                continue
            if "collections" in item:
                add_to_collections(cfg, item["collections"], item.get("exclusive", False))
                continue
            add_shortcut(cfg, item["name"], item["rom_path"],
                         collection=item.get("collection"),
                         launch=tuple(item["launch"]) if item.get("launch") else None,
                         tags=item.get("tags"))
        except (SteamError, OSError) as e:
            print("pending shortcut failed:", e, file=sys.stderr)
    try:
        os.remove(PENDING_FILE)
    except OSError:
        pass


def steam_running():
    return subprocess.run(["pgrep", "-x", "steam"], capture_output=True).returncode == 0


def in_game_mode():
    return ("gamescope" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            or os.environ.get("SteamGamepadUI") == "1"
            or bool(os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")))


def restart_steam():
    """Shut Steam down, re-apply shortcuts once it has exited, start it again.
    In Game Mode the session brings Steam back by itself."""
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    relaunch = "" if in_game_mode() else "nohup steam >/dev/null 2>&1 &"
    script = (
        "steam -shutdown >/dev/null 2>&1; "
        "for i in $(seq 90); do pgrep -x steam >/dev/null || break; sleep 1; done; "
        f'cd "{pkg_parent}" && "{sys.executable}" -m rommdeck --apply-pending; '
        + relaunch
    )
    subprocess.Popen(["setsid", "bash", "-c", script], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
