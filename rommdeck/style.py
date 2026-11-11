"""Look and feel: colour palettes, CSS, and cover-art rendering."""
import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Pango, PangoCairo  # noqa: E402

COVER_W, COVER_H, RADIUS = 168, 224, 14

PALETTES = {
    "dark": dict(bg="#0f1017", surface="#191b26", raised="#252838", hover="#30344a",
                 text="#eceef6", dim="#9498ad", accent="#8b5cf6", accent_hi="#a78bfa",
                 on_accent="#ffffff", good="#22c55e", warn="#f59e0b", bad="#ef4444", border="#2a2d3f"),
    "light": dict(bg="#f1f2f7", surface="#ffffff", raised="#e4e6f0", hover="#d6d9e8",
                  text="#1b1c26", dim="#646a80", accent="#7c3aed", accent_hi="#8b5cf6",
                  on_accent="#ffffff", good="#16a34a", warn="#f59e0b", bad="#dc2626", border="#d5d8e4"),
}

CSS = """
* { outline: none; -gtk-icon-shadow: none; text-shadow: none; }
window, dialog, messagedialog, .background { background-color: %(bg)s; color: %(text)s; }
label { color: %(text)s; }
dialog .dialog-vbox, messagedialog .dialog-vbox { background-color: %(surface)s; }
dialog .dialog-action-area, messagedialog .dialog-action-area {
    background-color: %(surface)s; padding: 12px; }
dialog .dialog-action-area button, messagedialog .dialog-action-area button { margin: 0 4px; }
messagedialog .titlebar { background: %(surface)s; border: none; box-shadow: none; }

dialog.popup { border: 2px solid %(border)s; border-radius: 18px; }
.popup-title { font-size: 17pt; font-weight: 800; }
.popup-body { font-size: 12pt; color: %(dim)s; }

/* header */
headerbar { background: %(surface)s; border: none; box-shadow: none;
            border-bottom: 1px solid %(border)s; min-height: 64px; padding: 4px 14px; }
.app-title { font-size: 17pt; font-weight: 800; }
.app-title-accent { color: %(accent_hi)s; }

/* controls */
button { background-image: none; background-color: %(raised)s; color: %(text)s;
         border: 2px solid transparent; border-radius: 14px; box-shadow: none;
         min-height: 44px; min-width: 44px; padding: 2px 18px;
         font-size: 12pt; font-weight: 600; transition: all 120ms ease; }
button label { color: inherit; }
button:hover { background-color: %(hover)s; }
button:active { background-color: %(accent)s; color: %(on_accent)s; }
button:focus { border-color: %(accent_hi)s; box-shadow: 0 0 12px alpha(%(accent)s, 0.7); }
button.suggested-action { background-color: %(accent)s; color: %(on_accent)s; }
button.suggested-action:hover { background-color: %(accent_hi)s; }
button.round { border-radius: 24px; padding: 2px; min-width: 48px; min-height: 48px; }
button.titlebutton { background: transparent; min-width: 36px; min-height: 36px; padding: 0; }

entry { background-image: none; background-color: %(raised)s; color: %(text)s;
        caret-color: %(accent_hi)s; border: 2px solid transparent; border-radius: 14px;
        box-shadow: none; min-height: 44px; padding: 0 14px; font-size: 12pt; }
entry:focus { border-color: %(accent)s; box-shadow: 0 0 10px alpha(%(accent)s, 0.5); }
entry image { color: %(dim)s; }
entry.search { border-radius: 24px; }
entry selection { background-color: %(accent)s; color: %(on_accent)s; }

checkbutton { min-height: 40px; font-size: 12pt; }
checkbutton check { min-width: 22px; min-height: 22px; border-radius: 7px;
                    background-image: none; background-color: %(raised)s;
                    border: 2px solid %(border)s; color: %(on_accent)s; }
checkbutton check:checked { background-color: %(accent)s; border-color: %(accent)s; }
checkbutton:focus check { border-color: %(accent_hi)s; }
separator { background-color: %(border)s; min-height: 1px; }

/* console tabs */
.tabbar { padding: 10px 12px 6px; }
button.tab { background-color: %(surface)s; color: %(dim)s; border: none; border-radius: 22px;
             padding: 0 18px; min-height: 44px; font-size: 11.5pt; font-weight: 600; }
button.tab:hover { background-color: %(raised)s; color: %(text)s; }
button.tab.tab-selected { background-color: %(accent)s; color: %(on_accent)s;
                          box-shadow: 0 2px 10px alpha(%(accent)s, 0.55); }

/* game grid */
scrolledwindow, viewport, flowbox, list { background: transparent; border: none; }
flowboxchild { padding: 0; background: none; }
flowboxchild:selected { background: none; }
.card { padding: 8px; border-radius: 18px; border: 3px solid transparent;
        transition: all 120ms ease; }
flowboxchild:hover .card { background: %(surface)s; }
flowboxchild:focus .card { background: alpha(%(accent)s, 0.22); border-color: %(accent_hi)s;
                           box-shadow: 0 0 22px alpha(%(accent)s, 0.65); }
.card-title { font-size: 11pt; font-weight: 600; }
.card-sub { font-size: 9pt; color: %(dim)s; }
.badge { background: %(good)s; color: #ffffff; border-radius: 11px; padding: 2px 9px;
         margin: 8px; font-size: 9pt; font-weight: 800; }
.badge-bad { background: %(bad)s; color: #ffffff; border-radius: 11px; padding: 2px 9px;
             margin: 8px; font-size: 9pt; font-weight: 800; }
.badge-warn { background: %(warn)s; color: #1b1c26; border-radius: 11px; padding: 2px 9px;
              margin: 8px; font-size: 9pt; font-weight: 800; }
.console-card { padding: 18px 10px 12px; border-radius: 20px; border: 3px solid transparent;
                background: %(surface)s; transition: all 120ms ease; }
flowboxchild:hover .console-card { background: %(raised)s; }
flowboxchild:focus .console-card { background: alpha(%(accent)s, 0.22);
    border-color: %(accent_hi)s; box-shadow: 0 0 22px alpha(%(accent)s, 0.65); }
.console-card.current { border-color: alpha(%(accent)s, 0.6); }
.console-name { font-size: 12pt; font-weight: 700; }
.console-count { font-size: 10pt; color: %(dim)s; }
.page-title { font-size: 16pt; font-weight: 800; padding: 16px 22px 4px; }
button.pill { border-radius: 24px; padding: 2px 18px; }
button.pill.on { background-color: %(accent)s; color: %(on_accent)s; }
.empty { font-size: 15pt; color: %(dim)s; }

/* details */
.detail-title { font-size: 19pt; font-weight: 800; }
.detail-meta { color: %(accent_hi)s; font-weight: 700; font-size: 11pt; }
.detail-small { color: %(dim)s; font-size: 10pt; }
.detail-summary { font-size: 11pt; }

/* folder list */
row { border-radius: 12px; margin: 2px 6px; background: transparent; }
row:hover { background: %(raised)s; }
row:selected, row:focus { background: %(accent)s; }
row:selected label, row:focus label { color: %(on_accent)s; }
.job-row { padding: 12px 14px; }
.job-title { font-size: 12.5pt; font-weight: 700; }
.job-state { font-size: 10pt; color: %(dim)s; }
list.jobs row { background: %(surface)s; margin: 4px 6px; border-radius: 14px; }
list.jobs row:hover { background: %(raised)s; }
list.jobs row:focus { background: alpha(%(accent)s, 0.22);
                      box-shadow: inset 0 0 0 3px %(accent_hi)s; }
list.jobs row:focus label { color: %(text)s; }
list.jobs row:focus .job-state { color: %(dim)s; }
.folder-row { padding: 12px; font-size: 12pt; }

/* bottom bar */
.status { background: %(surface)s; border-top: 1px solid %(border)s; padding: 8px 14px; }
.status-text { color: %(dim)s; font-size: 10.5pt; }
.chip { background: %(raised)s; color: %(dim)s; border-radius: 11px; padding: 3px 10px;
        font-size: 9.5pt; font-weight: 600; }
.chip-key { color: %(accent_hi)s; font-weight: 800; }
progressbar trough { background: %(raised)s; border: none; border-radius: 6px; min-height: 10px; }
progressbar progress { background: %(accent)s; border: none; border-radius: 6px; min-height: 10px; }

scrollbar { background: transparent; border: none; }
scrollbar slider { background: %(hover)s; border-radius: 8px; min-width: 6px; min-height: 40px; }
tooltip { background: %(raised)s; border-radius: 8px; }
tooltip label { color: %(text)s; }
"""


def css(dark):
    return (CSS % PALETTES["dark" if dark else "light"]).encode()


def _rounded(cr, w, h, r):
    cr.new_sub_path()
    cr.arc(w - r, r, r, -math.pi / 2, 0)
    cr.arc(w - r, h - r, r, 0, math.pi / 2)
    cr.arc(r, h - r, r, math.pi / 2, math.pi)
    cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def cover_surface(pixbuf, w=COVER_W, h=COVER_H):
    """Scale-and-crop a pixbuf to fill w x h with rounded corners."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    _rounded(cr, w, h, RADIUS)
    cr.clip()
    scale = max(w / pixbuf.get_width(), h / pixbuf.get_height())
    cr.translate((w - pixbuf.get_width() * scale) / 2, (h - pixbuf.get_height() * scale) / 2)
    cr.scale(scale, scale)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    return surf


_HUES = [(0.55, 0.36, 0.96), (0.13, 0.83, 0.93), (0.96, 0.45, 0.71), (0.98, 0.62, 0.20),
         (0.20, 0.77, 0.50), (0.38, 0.55, 0.98)]


def placeholder_surface(title, w=COVER_W, h=COVER_H):
    """A tinted tile with the game's initial, for games without cover art."""
    r, g, b = _HUES[sum(map(ord, title)) % len(_HUES)]
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    _rounded(cr, w, h, RADIUS)
    cr.clip()
    grad = cairo.LinearGradient(0, 0, w, h)
    grad.add_color_stop_rgb(0, r * 0.55, g * 0.55, b * 0.55)
    grad.add_color_stop_rgb(1, r * 0.18, g * 0.18, b * 0.22)
    cr.set_source(grad)
    cr.paint()
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription("Sans Bold 64"))
    layout.set_text((title.strip()[:1] or "?").upper(), -1)
    tw, th = layout.get_pixel_size()
    cr.move_to((w - tw) / 2, (h - th) / 2)
    cr.set_source_rgba(1, 1, 1, 0.85)
    PangoCairo.show_layout(cr, layout)
    return surf
