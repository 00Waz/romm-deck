"""EmuDeck knowledge: which roms folder a RomM platform maps to, and how
each system is launched (used when no existing Steam shortcut can be copied)."""
import os

# RomM platform slug (IGDB style) -> EmuDeck / ES-DE roms folder
SLUG_TO_FOLDER = {
    "ngc": "gc", "gamecube": "gc", "3ds": "n3ds", "new-nintendo-3ds": "n3ds",
    "sfam": "snes", "famicom": "nes", "virtualboy": "virtualboy",
    "genesis-slash-megadrive": "genesis", "megadrive": "genesis",
    "sms": "mastersystem", "sega32": "sega32x", "dc": "dreamcast",
    "sg1000": "sg-1000", "ps": "psx", "ps1": "psx", "playstation": "psx",
    "turbografx16--1": "tg16", "turbografx-16-slash-pc-engine-cd": "tg-cd",
    "neogeoaes": "neogeo", "neogeomvs": "neogeo", "neo-geo-cd": "neogeocd",
    "neo-geo-pocket": "ngp", "neo-geo-pocket-color": "ngpc",
    "lynx": "atarilynx", "jaguar": "atarijaguar", "atari-st": "atarist",
    "wonderswan-color": "wonderswancolor", "zxs": "zxspectrum",
    "acpc": "amstradcpc", "pokemon-mini": "pokemini", "nintendo-dsi": "nds",
}

# EmuDeck roms folder -> console name, as RomM names the platform (taken from
# RomM 5.4 /api/platforms/supported). Used for Steam collections and the
# installed view, so neither needs the RomM server.
FOLDER_NAMES = {
    '3do': '3DO Interactive Multiplayer',
    '3ds': 'Nintendo 3DS',
    'ags': 'Adventure Game Studio',
    'amiga': 'Amiga',
    'amiga1200': 'Amiga',
    'amiga600': 'Amiga',
    'amigacd32': 'Amiga CD32',
    'amstradcpc': 'Amstrad CPC',
    'android': 'Android',
    'apple2': 'Apple II',
    'apple2gs': 'Apple IIGS',
    'arcade': 'Arcade',
    'arcadia': 'Arcadia 2001',
    'arduboy': 'Arduboy',
    'astrocde': 'Bally Astrocade',
    'atari2600': 'Atari 2600',
    'atari5200': 'Atari 5200',
    'atari7800': 'Atari 7800',
    'atari800': 'Atari 800',
    'atarijaguar': 'Atari Jaguar',
    'atarijaguarcd': 'Atari Jaguar CD',
    'atarilynx': 'Atari Lynx',
    'atarist': 'Atari ST/STE',
    'atarixe': 'Atari XEGS',
    'atomiswave': 'Arcade',
    'bbcmicro': 'BBC Microcomputer System',
    'c16': 'Commodore 16',
    'c64': 'Commodore C64/128/MAX',
    'cavestory': 'Cave Story',
    'cdimono1': 'Philips CD-i',
    'cdtv': 'Commodore CDTV',
    'chailove': 'ChaiLove',
    'channelf': 'Fairchild Channel F',
    'coco': 'TRS-80 Color Computer',
    'colecovision': 'ColecoVision',
    'cps': 'Capcom Play System',
    'cps1': 'Capcom Play System',
    'cps2': 'Capcom Play System 2',
    'cps3': 'Capcom Play System 3',
    'crvision': 'CreatiVision',
    'daphne': 'Daphne (LaserDisc)',
    'doom': 'Doom',
    'dos': 'DOS',
    'dragon32': 'Dragon 32/64',
    'dreamcast': 'Dreamcast',
    'easyrpg': 'EasyRPG',
    'famicom': 'Family Computer',
    'fba': 'Arcade',
    'fbneo': 'Arcade',
    'fds': 'Family Computer Disk System',
    'flash': 'Flash',
    'fmtowns': 'FM Towns',
    'gameandwatch': 'Game & Watch',
    'gamecom': 'Game.com',
    'gamegear': 'Sega Game Gear',
    'gamecube': 'GameCube',
    'gb': 'Game Boy',
    'gba': 'Game Boy Advance',
    'gbc': 'Game Boy Color',
    'gc': 'GameCube',
    'genesis': 'Sega Mega Drive/Genesis',
    'genesiswide': 'Sega Mega Drive/Genesis',
    'gx4000': 'Amstrad GX4000',
    'intellivision': 'Intellivision',
    'j2me': 'J2ME',
    'lcdgames': 'Handheld Electronic LCD',
    'lutro': 'Lutro',
    'macintosh': 'Mac',
    'mame': 'Arcade',
    'mame-advmame': 'Arcade',
    'mame-mame4all': 'Arcade',
    'mastersystem': 'Sega Master System/Mark III',
    'megacd': 'Sega CD',
    'megacdjp': 'Sega CD',
    'megadrive': 'Sega Mega Drive/Genesis',
    'megadrivejp': 'Sega Mega Drive/Genesis',
    'megaduck': 'Mega Duck/Cougar Boy',
    'mess': 'MESS',
    'model2': 'Sega Model 2',
    'model3': 'Sega Model 3',
    'moto': 'Thomson MO/TO',
    'msx': 'MSX',
    'msx1': 'MSX',
    'msx2': 'MSX2',
    'msxturbor': 'MSX Turbo R',
    'mugen': 'MUGEN',
    'multivision': 'Othello Multivision',
    'n3ds': 'Nintendo 3DS',
    'n64': 'Nintendo 64',
    'n64dd': 'Nintendo 64DD',
    'naomi': 'Arcade',
    'naomi2': 'Arcade',
    'naomigd': 'Arcade',
    'nds': 'Nintendo DS',
    'neogeo': 'Neo Geo AES',
    'neogeocd': 'Neo Geo CD',
    'neogeocdjp': 'Neo Geo CD',
    'nes': 'Nintendo Entertainment System',
    'ngp': 'Neo Geo Pocket',
    'ngpc': 'Neo Geo Pocket Color',
    'odyssey2': 'Odyssey 2 / Videopac G7000',
    'openbor': 'OpenBOR',
    'oric': 'Oric 1 / Atmos',
    'palm': 'Palm OS',
    'pc': 'DOS',
    'pc88': 'PC-8800 Series',
    'pc98': 'PC-9800 Series',
    'pcengine': 'TurboGrafx-16/PC Engine',
    'pcenginecd': 'Turbografx-16/PC Engine CD',
    'pcfx': 'PC-FX',
    'pico8': 'Pico-8',
    'pokemini': 'Pokémon mini',
    'ports': 'Ports',
    'primehacks': 'Wii',
    'ps2': 'PlayStation 2',
    'ps3': 'PlayStation 3',
    'ps4': 'PlayStation 4',
    'psp': 'PlayStation Portable',
    'psvita': 'PlayStation Vita',
    'psx': 'PlayStation',
    'pv1000': 'PV-1000',
    'quake': 'Quake',
    'samcoupe': 'MGT SAM Coupé',
    'satellaview': 'Satellaview',
    'saturn': 'Sega Saturn',
    'saturnjp': 'Sega Saturn',
    'scummvm': 'ScummVM',
    'sega32x': 'Sega 32X',
    'sega32xjp': 'Sega 32X',
    'sega32xna': 'Sega 32X',
    'segacd': 'Sega CD',
    'sfc': 'Super Famicom',
    'sg-1000': 'SG-1000',
    'sgb': 'Game Boy',
    'snes': 'Super Nintendo Entertainment System',
    'sneshd': 'Super Nintendo Entertainment System',
    'snesna': 'Super Nintendo Entertainment System',
    'solarus': 'Solarus',
    'spectravideo': 'Spectravideo',
    'steam': 'Steam',
    'stratagus': 'Stratagus',
    'sufami': 'Sufami Turbo',
    'supergrafx': 'PC Engine SuperGrafx',
    'supervision': 'Watara/QuickShot Supervision',
    'switch': 'Nintendo Switch',
    'symbian': 'Symbian',
    'tanodragon': 'Tano Dragon',
    'tg-cd': 'Turbografx-16/PC Engine CD',
    'tg16': 'TurboGrafx-16/PC Engine',
    'ti99': 'Texas Instruments TI-99',
    'tic80': 'TIC-80',
    'to8': 'Thomson MO/TO',
    'trs-80': 'TRS-80',
    'uzebox': 'Uzebox',
    'vectrex': 'Vectrex',
    'vic20': 'Commodore VIC-20',
    'videopac': 'Odyssey 2 / Videopac G7000',
    'virtualboy': 'Virtual Boy',
    'vsmile': 'V.Smile',
    'wasm4': 'WASM-4',
    'wii': 'Wii',
    'wiiu': 'Wii U',
    'wonderswan': 'WonderSwan',
    'wonderswancolor': 'WonderSwan Color',
    'x1': 'Sharp X1',
    'x68000': 'Sharp X68000',
    'xbox': 'Xbox',
    'xbox360': 'Xbox 360',
    'zmachine': 'Z-Machine',
    'zx81': 'Sinclair ZX81',
    'zxspectrum': 'ZX Spectrum',
}


def console_name(folder):
    """Collection / display name for a roms folder, e.g. snes -> Super Nintendo
    Entertainment System. Unknown folders fall back to the folder in capitals."""
    return FOLDER_NAMES.get(folder) or FOLDER_NAMES.get(folder.lower()) or folder.upper()


RA = "retroarch"
# roms folder -> (launcher script, args before the rom, args after) or (RA, core)
LAUNCH = {
    "nes": (RA, "mesen"), "famicom": (RA, "mesen"), "fds": (RA, "mesen"),
    "snes": (RA, "snes9x"), "sfc": (RA, "snes9x"),
    "n64": (RA, "mupen64plus_next"),
    "gb": (RA, "gambatte"), "gbc": (RA, "gambatte"), "gba": (RA, "mgba"),
    "virtualboy": (RA, "mednafen_vb"), "pokemini": (RA, "pokemini"),
    "genesis": (RA, "genesis_plus_gx"), "megadrive": (RA, "genesis_plus_gx"),
    "mastersystem": (RA, "genesis_plus_gx"), "gamegear": (RA, "genesis_plus_gx"),
    "segacd": (RA, "genesis_plus_gx"), "megacd": (RA, "genesis_plus_gx"),
    "sg-1000": (RA, "genesis_plus_gx"), "sega32x": (RA, "picodrive"),
    "saturn": (RA, "mednafen_saturn"), "dreamcast": (RA, "flycast"),
    "atomiswave": (RA, "flycast"), "naomi": (RA, "flycast"),
    "tg16": (RA, "mednafen_pce"), "pcengine": (RA, "mednafen_pce"),
    "tg-cd": (RA, "mednafen_pce"), "pcenginecd": (RA, "mednafen_pce"),
    "ngp": (RA, "mednafen_ngp"), "ngpc": (RA, "mednafen_ngp"),
    "neogeo": (RA, "fbneo"), "neogeocd": (RA, "neocd"),
    "arcade": (RA, "mame"), "mame": (RA, "mame"), "fbneo": (RA, "fbneo"),
    "wonderswan": (RA, "mednafen_wswan"), "wonderswancolor": (RA, "mednafen_wswan"),
    "atari2600": (RA, "stella"), "atari5200": (RA, "a5200"),
    "atari7800": (RA, "prosystem"), "atarilynx": (RA, "mednafen_lynx"),
    "atarijaguar": (RA, "virtualjaguar"), "colecovision": (RA, "bluemsx"),
    "msx": (RA, "bluemsx"), "msx2": (RA, "bluemsx"), "3do": (RA, "opera"),
    "c64": (RA, "vice_x64sc"), "amiga": (RA, "puae"), "dos": (RA, "dosbox_pure"),
    "zxspectrum": (RA, "fuse"), "amstradcpc": (RA, "cap32"),
    "intellivision": (RA, "freeintv"),
    "psx": ("duckstation.sh", "-batch -fullscreen", ""),
    "ps2": ("pcsx2-qt.sh", "-batch -fullscreen -nogui", ""),
    "ps3": ("rpcs3.sh", "--no-gui", ""),
    "psp": ("ppsspp.sh", "-f -g", ""),
    "gc": ("dolphin-emu.sh", "-b -e", ""), "gamecube": ("dolphin-emu.sh", "-b -e", ""),
    "wii": ("dolphin-emu.sh", "-b -e", ""),
    "wiiu": ("cemu.sh", "-f -g", ""),
    "nds": ("melonds.sh", "", "-f"),
    "n3ds": ("azahar.sh", "", ""), "3ds": ("azahar.sh", "", ""),
    "switch": ("ryujinx.sh", "--fullscreen", ""),
    "xbox": ("xemu-emu.sh", "-full-screen -dvd_path", ""),
    "xbox360": ("xenia.sh", "", ""),
    "scummvm": ("scummvm.sh", "", ""),
}

RA_CORES = os.path.expanduser("~/.var/app/org.libretro.RetroArch/config/retroarch/cores")

# Preferred file to launch inside a multi-file rom folder
LAUNCH_EXT_ORDER = [".m3u", ".cue", ".gdi", ".chd", ".iso", ".rvz", ".wbfs", ".cso",
                    ".pbp", ".ccd", ".nsp", ".xci", ".3ds", ".cia", ".bin", ".img"]


def list_folders(roms_path):
    try:
        return sorted(d for d in os.listdir(roms_path)
                      if os.path.isdir(os.path.join(roms_path, d)))
    except OSError:
        return []


def resolve_folder(cfg, slug, fs_slug):
    """Return the EmuDeck roms sub-folder for a RomM platform, or None."""
    roms = cfg["roms_path"]
    exists = lambda d: bool(d) and os.path.isdir(os.path.join(roms, d))
    override = cfg.get("platform_map", {}).get(fs_slug or slug)
    for cand in (override, SLUG_TO_FOLDER.get(fs_slug), fs_slug,
                 SLUG_TO_FOLDER.get(slug), slug):
        if exists(cand):
            return cand
    return None


def launch_command(cfg, folder, rom_path):
    """Return (exe, start_dir) for a Steam shortcut, or None if unknown."""
    spec = LAUNCH.get(folder)
    if not spec:
        return None
    ldir = cfg["launchers_path"]
    if spec[0] == RA:
        core = os.path.join(RA_CORES, spec[1] + "_libretro.so")
        exe = f'"{os.path.join(ldir, "retroarch.sh")}" -L {core} "{rom_path}"'
    else:
        script, pre, post = spec
        parts = [f'"{os.path.join(ldir, script)}"', pre, f'"{rom_path}"', post]
        exe = " ".join(p for p in parts if p)
    return exe, ldir


def launch_target(path):
    """For a folder rom pick the file an emulator should open."""
    if not os.path.isdir(path):
        return path
    files = []
    for root, _dirs, names in os.walk(path):
        files += [os.path.join(root, n) for n in names]
    if not files:
        return path

    def rank(f):
        ext = os.path.splitext(f)[1].lower()
        order = LAUNCH_EXT_ORDER.index(ext) if ext in LAUNCH_EXT_ORDER else len(LAUNCH_EXT_ORDER)
        return (order, -os.path.getsize(f))
    return sorted(files, key=rank)[0]
