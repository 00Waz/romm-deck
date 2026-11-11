"""Settings stored in ~/.config/romm-deck/config.json (mode 600)."""
import json
import os
import re

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "romm-deck"
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "server_url": "",
    "username": "",
    "password": "",
    "sgdb_key": "",        # optional SteamGridDB API key for Steam library artwork
    "api_token": "",       # optional RomM client token (rmm_...), used instead of password
    "verify_tls": True,
    "dark_mode": True,
    "roms_path": "",
    "launchers_path": "",
    "steam_user": "",      # userdata folder name; empty = auto-detect
    "steam_collections": True,  # put added games in a Steam collection per console
    "platform_map": {},    # RomM platform fs_slug -> EmuDeck roms sub-folder
}


def detect_emudeck():
    """Return (roms_path, launchers_path) from EmuDeck's settings, or guesses."""
    roms = ""
    try:
        with open(os.path.expanduser("~/emudeck/settings.sh")) as f:
            for line in f:
                m = re.match(r"\s*romsPath=(.+)", line)
                if m:
                    roms = m.group(1).strip().replace('"', "").replace("'", "")
    except OSError:
        pass
    if not roms or not os.path.isdir(roms):
        guess = os.path.expanduser("~/Emulation/roms")
        roms = guess if os.path.isdir(guess) else roms
    launchers = os.path.join(os.path.dirname(roms), "tools", "launchers") if roms else ""
    return roms, launchers


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    if not cfg["roms_path"] or not cfg["launchers_path"]:
        roms, launchers = detect_emudeck()
        cfg["roms_path"] = cfg["roms_path"] or roms
        cfg["launchers_path"] = cfg["launchers_path"] or launchers
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
