"""Steam library artwork for a game, in the five slots Steam ROM Manager fills:

    tall  600x900   library capsule        -> <appid>p.png
    wide  920x430   recent-games tile      -> <appid>.png
    hero  1920x620  banner on the game page -> <appid>_hero.png
    logo  transparent title art            -> <appid>_logo.png
    icon  256x256                          -> <appid>_icon.png

With a SteamGridDB API key the images come from SteamGridDB, as SRM does.
Anything still missing is built from what the RomM server has (cover and
screenshots), so every slot except the logo is always filled.
"""
import io
import json
import urllib.error
import urllib.parse
import urllib.request

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib  # noqa: E402

from .romm import RommError  # noqa: E402

SGDB = "https://www.steamgriddb.com/api/v2"
SGDB_QUERIES = {
    "tall": "/grids/game/%s?dimensions=600x900&types=static&nsfw=false&humor=false",
    "wide": "/grids/game/%s?dimensions=920x430,460x215&types=static&nsfw=false&humor=false",
    "hero": "/heroes/game/%s?types=static&nsfw=false&humor=false",
    "logo": "/logos/game/%s?types=static&nsfw=false&humor=false",
    "icon": "/icons/game/%s?types=static&nsfw=false&humor=false",
}
UA = {"User-Agent": "romm-deck"}


# -- image helpers ---------------------------------------------------------------
def _pixbuf(data):
    try:
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        return GdkPixbuf.Pixbuf.new_from_stream(stream, None)
    except GLib.Error:
        return None


def _png(data):
    """Re-encode any image as PNG (Steam's grid does not read webp/ico)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    pb = _pixbuf(data)
    if not pb:
        return None
    ok, buf = pb.save_to_bufferv("png", [], [])
    return bytes(buf) if ok else None


def _surface_png(surface):
    out = io.BytesIO()
    surface.write_to_png(out)
    return out.getvalue()


def _paint_fill(cr, pb, w, h, anchor_y=0.5):
    """Paint pb scaled to cover the w x h area."""
    scale = max(w / pb.get_width(), h / pb.get_height())
    cr.save()
    cr.translate((w - pb.get_width() * scale) / 2, (h - pb.get_height() * scale) * anchor_y)
    cr.scale(scale, scale)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BILINEAR)
    cr.paint()
    cr.restore()


def _blurred(pb):
    """Cheap blur: shrink to a few pixels and let the upscale smear it."""
    small = pb.scale_simple(max(2, pb.get_width() // 40), max(2, pb.get_height() // 40),
                            GdkPixbuf.InterpType.BILINEAR)
    return small.scale_simple(pb.get_width() // 4 or 1, pb.get_height() // 4 or 1,
                              GdkPixbuf.InterpType.BILINEAR)


def _compose(w, h, background, darken, foreground=None):
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    _paint_fill(cr, background, w, h)
    cr.set_source_rgba(0, 0, 0, darken)
    cr.paint()
    if foreground:                       # the cover itself, centred at full height
        scale = h * 0.92 / foreground.get_height()
        fw = foreground.get_width() * scale
        cr.translate((w - fw) / 2, h * 0.04)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, foreground, 0, 0)
        cr.paint()
    return _surface_png(surf)


def _square_icon(pb, size=256):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    _paint_fill(cr, pb, size, size, anchor_y=0.2)
    return _surface_png(surf)


# -- sources -------------------------------------------------------------------
def _get(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def from_steamgriddb(key, title):
    """Return {slot: png bytes} for the best SteamGridDB match, like SRM."""
    headers = dict(UA, Authorization="Bearer " + key.strip())
    art = {}
    try:
        found = json.loads(_get(f"{SGDB}/search/autocomplete/{urllib.parse.quote(title, safe='')}",
                                headers))
        games = found.get("data") or []
        if not games:
            return art
        game_id = games[0]["id"]
        for slot, query in SGDB_QUERIES.items():
            try:
                images = json.loads(_get(SGDB + query % game_id, headers)).get("data") or []
                if images:
                    png = _png(_get(images[0]["url"], UA, timeout=40))
                    if png:
                        art[slot] = png
            except (urllib.error.URLError, OSError, ValueError, KeyError):
                continue
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        pass
    return art


def from_romm(client, rom):
    """Build tall/wide/hero/icon from the RomM cover and first screenshot."""
    art = {}
    large = dict(rom, path_cover_small=rom.get("path_cover_large") or rom.get("path_cover_small"))
    data = client.cover(large)
    cover = _pixbuf(data) if data else None
    shot = None
    try:
        detail = client._json(f"/api/roms/{rom['id']}")
        for path in (detail.get("merged_screenshots") or [])[:1]:
            if not path.startswith("/assets/"):
                path = "/assets/romm/resources/" + path.lstrip("/")
            with client._open(urllib.parse.quote(path, safe="/?=&"), timeout=20) as r:
                shot = _pixbuf(r.read())
    except (RommError, OSError, ValueError, AttributeError):
        pass
    if cover:
        art["tall"] = _compose(600, 900, cover, 0.0)
        art["wide"] = _compose(920, 430, _blurred(cover), 0.35, cover)
        art["icon"] = _square_icon(cover)
    backdrop = shot or (cover and _blurred(cover))
    if backdrop:
        art["hero"] = _compose(1920, 620, backdrop, 0.25 if shot else 0.35)
    return art


def gather(client, cfg, rom, title):
    art = {}
    if cfg.get("sgdb_key"):
        art = from_steamgriddb(cfg["sgdb_key"], title)
    if len(art) < 5:
        for slot, data in from_romm(client, rom).items():
            art.setdefault(slot, data)
    return art
