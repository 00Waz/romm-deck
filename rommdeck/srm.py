"""Find the games in the roms folder the way Steam ROM Manager does.

SRM keeps one "parser" per emulator in userConfigurations.json: a roms folder,
a glob such as ``**/${title}@(.gba|.7z)`` and the command that launches a match.
Reading those parsers means a game counts as installed exactly when SRM would
add it, and gets the same launch command, title and Steam app id SRM would give
it. Without SRM, built-in per-system rules are used instead.
"""
import json
import os
import re
import unicodedata
import urllib.parse

from . import emudeck

SRM_DIRS = ["~/.config/steam-rom-manager/userData",
            "~/.var/app/com.steamgriddb.steam-rom-manager/config/steam-rom-manager/userData"]

# Fallback when SRM is not set up: rom extensions per EmuDeck system folder.
_CART = {"7z", "zip"}
FALLBACK_EXTS = {
    "nes": {"nes", "fds", "unf"}, "famicom": {"nes", "fds"}, "fds": {"fds"},
    "snes": {"sfc", "smc", "fig", "swc", "bs"}, "sfc": {"sfc", "smc"},
    "n64": {"n64", "z64", "v64", "ndd"}, "gb": {"gb"}, "gbc": {"gbc", "gb"},
    "gba": {"gba"}, "virtualboy": {"vb"}, "pokemini": {"min"},
    "genesis": {"md", "gen", "smd", "bin"}, "megadrive": {"md", "gen", "smd", "bin"},
    "mastersystem": {"sms"}, "gamegear": {"gg"}, "sg-1000": {"sg"}, "sega32x": {"32x"},
    "atari2600": {"a26", "bin"}, "atari7800": {"a78"}, "atarilynx": {"lnx"},
    "ngp": {"ngp"}, "ngpc": {"ngc", "ngpc"}, "wonderswan": {"ws"},
    "wonderswancolor": {"wsc"}, "tg16": {"pce"}, "pcengine": {"pce"},
    "nds": {"nds"}, "n3ds": {"3ds", "cci", "cia", "cxi", "3dsx", "app"},
    "gc": {"iso", "gcm", "gcz", "rvz", "ciso", "wia", "m3u"},
    "wii": {"iso", "wbfs", "rvz", "gcz", "ciso", "wia", "wad", "m3u"},
    "wiiu": {"wua", "wux", "wud", "rpx"},
    "switch": {"nsp", "xci", "nsz", "xcz", "nro"},
    "psx": {"cue", "chd", "pbp", "m3u", "iso", "img", "ecm", "mds"},
    "ps2": {"iso", "chd", "cso", "zso", "bin", "mdf", "nrg", "gz"},
    "psp": {"iso", "cso", "pbp", "chd"},
    "saturn": {"cue", "chd", "m3u", "ccd"}, "segacd": {"cue", "chd", "m3u"},
    "dreamcast": {"gdi", "cdi", "chd", "m3u"},
    "xbox": {"iso", "xiso"}, "xbox360": {"iso", "xex", "zar"},
}
CART_SYSTEMS = {"nes", "famicom", "snes", "sfc", "n64", "gb", "gbc", "gba", "virtualboy",
                "pokemini", "genesis", "megadrive", "mastersystem", "gamegear", "sg-1000",
                "sega32x", "atari2600", "atari7800", "atarilynx", "ngp", "ngpc",
                "wonderswan", "wonderswancolor", "tg16", "pcengine", "nds"}
ARCHIVE_MIN = 50 * 1024 * 1024
SKIP_DIRS = {"media", "downloaded_media", "images", "videos", "manuals", "gamelists",
             "homebrew", "xbla", "mlc01", "content", "patches", "updates", "dlc"}


class Game:
    """One launchable game file found on disk."""

    def __init__(self, path, folder, title, launch=None, category=None):
        self.path = path            # file Steam launches
        self.folder = folder        # EmuDeck system folder, e.g. "snes"
        self.title = title          # cleaned display title
        self.launch = launch        # (exe, launch options, start dir) or None
        self.category = category    # SRM's Steam category for the parser

    def __repr__(self):
        return f"Game({self.folder}/{os.path.basename(self.path)} -> {self.title!r})"


# -- SRM files -------------------------------------------------------------------
def _srm_dir():
    for d in SRM_DIRS:
        d = os.path.expanduser(d)
        if os.path.exists(os.path.join(d, "userConfigurations.json")):
            return d
    return None


def _load(d, name, default):
    try:
        with open(os.path.join(d, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _title_cache(d):
    """SRM's own titles: raw file title -> the title it matched on SteamGridDB.
    Unmatched entries are skipped; clean_title does better on those."""
    out = {}
    for key, val in _load(d, "fuzzyCache.json", {}).items():
        try:
            raw = json.loads(key)["0"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        if isinstance(val, dict) and val.get("matched") and val.get("output"):
            out[raw] = val["output"]
    return out


def _exceptions(d):
    """User exceptions keyed by title or file name (SRM appends ' ${id:...}')."""
    out = {}
    for key, val in (_load(d, "userExceptions.json", {}).get("titles") or {}).items():
        out[re.sub(r"\s*\$\{id:[^}]*\}\s*$", "", key)] = val
    return out


# -- SRM variables -----------------------------------------------------------------
def _split_top(s, sep="|"):
    parts, depth, cur = [], 0, ""
    i = 0
    while i < len(s):
        if s.startswith("${", i):
            depth += 1
            cur += "${"
            i += 2
            continue
        c = s[i]
        if c == "}" and depth:
            depth -= 1
        if c == sep and not depth:
            parts.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    parts.append(cur)
    return parts


def substitute(text, values):
    """Expand SRM variables: ${name}, ${/} and ${os:linux|then|else}."""
    out, i = "", 0
    while i < len(text):
        start = text.find("${", i)
        if start < 0:
            return out + text[i:]
        out += text[i:start]
        depth, j = 1, start + 2
        while j < len(text) and depth:
            if text.startswith("${", j):
                depth += 1
                j += 2
                continue
            if text[j] == "}":
                depth -= 1
            j += 1
        inner = text[start + 2:j - 1]
        if inner.startswith("os:"):
            parts = _split_top(inner[3:])
            then = parts[1] if len(parts) > 1 else ""
            other = parts[2] if len(parts) > 2 else ""
            out += substitute(then if parts[0] == "linux" else other, values)
        elif inner == "/":
            out += "/"
        else:
            out += values.get(inner.lower(), "")
        i = j
    return out


# -- glob matching (the micromatch subset SRM parsers use) ----------------------------
def glob_regex(pattern):
    """Turn an SRM glob into a regex with a named 'title' group. Supports **, *,
    ?, {a,b}, @(a|b), !(a|b) and ${title}."""
    def conv(p):
        out, i = "", 0
        while i < len(p):
            if p.startswith("${title}", i):
                out += r"(?P<title>[^/]+?)" if "(?P<title>" not in out else r"[^/]+?"
                i += len("${title}")
            elif p.startswith("**/", i):
                out += r"(?:[^/]+/)*"
                i += 3
            elif p.startswith("**", i):
                out += r".*"
                i += 2
            elif p[i] in "@!+*?" and p[i + 1:i + 2] == "(":
                op = p[i]
                body, i = _group(p, i + 1, "(", ")")
                alts = "|".join(conv(a) for a in _split_top(body))
                if op == "!":
                    out += rf"(?!(?:{alts})(?:/|$))[^/]*"
                else:
                    out += f"(?:{alts})" + {"@": "", "+": "+", "*": "*", "?": "?"}[op]
            elif p[i] == "{":
                body, i = _group(p, i, "{", "}")
                out += "(?:" + "|".join(conv(a) for a in _split_top(body, ",")) + ")"
            elif p[i] == "*":
                out += r"[^/]*"
                i += 1
            elif p[i] == "?":
                out += r"[^/]"
                i += 1
            else:
                out += re.escape(p[i])
                i += 1
        return out
    return re.compile(conv(pattern) + r"\Z", re.IGNORECASE)


def _group(p, i, opener, closer):
    """Return (body, index after closer) for a bracket starting at p[i]."""
    depth, j = 0, i
    while j < len(p):
        if p[j] == opener:
            depth += 1
        elif p[j] == closer:
            depth -= 1
            if not depth:
                return p[i + 1:j], j + 1
        j += 1
    return p[i + 1:], len(p)


def _walk(root):
    """Relative paths of every file under root (following symlinks once)."""
    seen = set()
    for base, dirs, files in os.walk(root, followlinks=True):
        real = os.path.realpath(base)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel = os.path.relpath(base, root)
        for name in files:
            if not name.startswith("."):
                yield name if rel == "." else os.path.join(rel, name)


# -- titles -----------------------------------------------------------------------------
def clean_title(raw):
    """Roughly what SRM's fuzzy matcher does with removeBrackets/removeCharacters:
    'Legend of Zelda, The - A Link to the Past (U) [!]' -> 'The Legend of Zelda - A
    Link to the Past'."""
    t = urllib.parse.unquote(raw).replace("_", " ")
    t = re.sub(r"\s#\s.*$", "", t)                       # "Mario Kart 7 # 3DS(Trimmed)"
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -.,")
    m = re.match(r"^(.*?), (The|A|An)\b(.*)$", t)
    if m:
        t = f"{m.group(2)} {m.group(1)}{m.group(3)}"
    return t or raw


def norm(title):
    """Comparison key for titles: no accents, case, punctuation or brackets."""
    t = unicodedata.normalize("NFKD", clean_title(title))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"\b(the|a|an)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


# -- referenced files (cue/m3u/gdi) ---------------------------------------------------------
def _referenced(path):
    """Files a playlist or cue sheet points at, so they are not added again."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".m3u", ".cue", ".gdi"):
        return set()
    base = os.path.dirname(path)
    out = set()
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if ext == ".cue":
                    m = re.match(r'FILE\s+"?(.+?)"?\s+\w+$', line, re.IGNORECASE)
                    name = m.group(1) if m else None
                elif ext == ".gdi":
                    m = re.search(r'"([^"]+)"|(\S+\.(?:bin|raw))', line, re.IGNORECASE)
                    name = (m.group(1) or m.group(2)) if m else None
                else:
                    name = line if line and not line.startswith("#") else None
                if name:
                    out.add(os.path.normpath(os.path.join(base, name)))
    except OSError:
        pass
    return out


def _drop_referenced(games):
    refs = set()
    for g in games:
        refs |= _referenced(g.path)
    return [g for g in games if os.path.normpath(g.path) not in refs]


# -- scanning --------------------------------------------------------------------------------
def _env(cfg, settings):
    env = dict(settings.get("environmentVariables") or {})
    return {
        # RomM Deck's roms folder wins over the one SRM was set up with.
        "romsdirglobal": cfg["roms_path"].rstrip("/") or env.get("romsDirectory") or "",
        "steamdirglobal": env.get("steamDirectory") or "",
        "retroarchpath": env.get("retroarchPath") or os.path.join(cfg["launchers_path"],
                                                                  "retroarch.sh"),
        "racores": env.get("raCoresDirectory") or "",
        "localimagesdir": env.get("localImagesDirectory") or "",
    }


def _folder_of(path, roots):
    for root in roots:
        rel = os.path.relpath(path, root)
        if not rel.startswith(".."):
            return rel.split(os.sep)[0]
    return ""


def _srm_games(cfg, d):
    parsers = [p for p in _load(d, "userConfigurations.json", [])
               if isinstance(p, dict) and not p.get("disabled")
               and p.get("parserType") == "Glob" and (p.get("parserInputs") or {}).get("glob")]
    if not parsers:
        return None
    env = _env(cfg, _load(d, "userSettings.json", {}))
    titles, exceptions = _title_cache(d), _exceptions(d)
    roots = {cfg["roms_path"].rstrip("/"), os.path.realpath(cfg["roms_path"])}
    # A parser with a title suffix, e.g. "${fuzzyTitle} (Ryujinx)", is an alternative
    # emulator for the same files; prefer the plain one when both match a file.
    parsers.sort(key=lambda p: (p.get("titleModifier") or "${fuzzyTitle}").strip()
                 != "${fuzzyTitle}")
    found = {}
    for p in parsers:
        rom_dir = substitute(p.get("romDirectory") or "", env).rstrip("/")
        if not os.path.isdir(rom_dir):
            continue
        rx = glob_regex(p["parserInputs"]["glob"])
        exe_path = substitute((p.get("executable") or {}).get("path") or "", env)
        games = []
        for rel in _walk(rom_dir):
            m = rx.match(rel)
            if not m:
                continue
            path = os.path.join(rom_dir, rel)
            raw = m.groupdict().get("title") or os.path.splitext(os.path.basename(rel))[0]
            exc = (exceptions.get(raw) or exceptions.get(os.path.basename(rel)) or {})
            if exc.get("exclude"):
                continue
            fuzzy = exc.get("newTitle") or titles.get(raw) or clean_title(raw)
            vals = dict(env, title=raw, fuzzytitle=fuzzy, filepath=path,
                        exepath=exe_path, exedir=os.path.dirname(exe_path),
                        filename=os.path.basename(path),
                        extension=os.path.splitext(path)[1],
                        filedir=os.path.dirname(path), dir=rom_dir, romdir=rom_dir)
            title = substitute(p.get("titleModifier") or "${fuzzyTitle}", vals).strip()
            vals["finaltitle"] = title
            exe = substitute(p.get("executableModifier") or '"${exePath}"', vals)
            args = substitute(p.get("executableArgs") or "", vals).strip()
            if exc.get("commandLineArguments"):
                args = exc["commandLineArguments"]
            if (p.get("executable") or {}).get("appendArgsToExecutable") and args:
                exe, args = f"{exe} {args}", ""
            start = substitute(p.get("startInDirectory") or "", vals) or os.path.dirname(exe_path)
            cats = p.get("steamCategories") or []
            folder = _folder_of(path, roots)
            games.append(Game(path, folder, title, (exe, args, f'"{start}"'),
                              substitute(cats[0], vals) if cats else None))
        for g in _drop_referenced(games):
            found.setdefault(os.path.normpath(g.path), g)
    return list(found.values())


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _fallback_games(cfg):
    roms = cfg["roms_path"].rstrip("/")
    out = []
    for folder in emudeck.list_folders(roms):
        exts = FALLBACK_EXTS.get(folder)
        if not exts:
            continue
        exts = exts | _CART
        base = os.path.join(roms, folder)
        games = []
        for rel in _walk(base):
            parts = rel.split(os.sep)
            if any(p.lower() in SKIP_DIRS for p in parts[:-1]):
                continue
            stem, ext = os.path.splitext(parts[-1])
            ext = ext[1:].lower()
            if ext not in exts:
                continue
            path = os.path.join(base, rel)
            # An archive in a disc system is a game only if it is game-sized; small
            # ones are emulator patches and the like (xbox360/game-patches.zip).
            if ext in _CART and folder not in CART_SYSTEMS and _size(path) < ARCHIVE_MIN:
                continue
            games.append(Game(path, folder, clean_title(stem)))
        out += _drop_referenced(games)
    return out


def scan(cfg):
    """Every game in RomM Deck's roms folder as a list of Game. SRM's parsers are
    used where they match (same launch command and title as SRM); anything they
    miss, such as a system with no enabled parser or an extension the parser
    leaves out, is found by the built-in rules and launched the EmuDeck way."""
    d = _srm_dir()
    games = (_srm_games(cfg, d) if d else None) or []
    taken = {os.path.normpath(g.path) for g in games}
    # A game in its own sub-folder (switch/Game/Game.nsp) is one game, however
    # many files are in it.
    roms = cfg["roms_path"].rstrip("/")
    owned = {_game_dir(g.path, roms) for g in games} - {None}
    for g in _fallback_games(cfg):
        path = os.path.normpath(g.path)
        if path in taken or _game_dir(path, roms) in owned:
            continue
        taken.add(path)
        games.append(g)
    return games


def _game_dir(path, roms):
    """roms/<system>/<game>/... -> that game folder; None for a top-level file."""
    parts = os.path.relpath(path, roms).split(os.sep)
    return os.path.join(roms, parts[0], parts[1]) if len(parts) > 2 else None


def using_srm():
    return _srm_dir() is not None


def roms_dir():
    """The roms folder SRM is set up with, if any (may differ from ours)."""
    d = _srm_dir()
    env = _load(d, "userSettings.json", {}).get("environmentVariables") or {} if d else {}
    return env.get("romsDirectory") or None
