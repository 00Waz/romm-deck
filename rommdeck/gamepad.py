"""Controller input via the Linux joystick API (/dev/input/js*), stdlib only.

Reports high-level actions ("up", "a", "lb", ...) to a callback on the GTK
main loop. Layout is the Xbox 360 one, which is what Steam's virtual pad and
most controllers present.
"""
import glob
import os
import struct

from gi.repository import GLib

EVENT = struct.Struct("IhBB")          # time, value, type, number
BUTTONS = {0: "a", 1: "b", 2: "x", 3: "y", 4: "lb", 5: "rb", 6: "select", 7: "start",
           9: "ls", 10: "rs"}
AXES_X, AXES_Y = (0, 6), (1, 7)        # left stick + d-pad
AXES_TRIGGER = {2: "lt", 5: "rt"}
THRESHOLD = 16000
REPEAT_DELAY, REPEAT_RATE = 380, 110   # ms


class Gamepads:
    def __init__(self, callback):
        self.callback = callback
        self.devices = {}              # path -> (fd, watch id)
        self.axes = {}                 # (path, axis) -> value
        self.direction = None
        self.repeat_id = 0
        self.scan()
        GLib.timeout_add_seconds(3, self.scan)

    def scan(self):
        for path in glob.glob("/dev/input/js*"):
            if path in self.devices:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            watch = GLib.io_add_watch(fd, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                                      self.on_ready, path)
            self.devices[path] = (fd, watch)
        return True

    def drop(self, path):
        fd, _watch = self.devices.pop(path)
        try:
            os.close(fd)
        except OSError:
            pass
        for key in [k for k in self.axes if k[0] == path]:
            del self.axes[key]
        self.update_direction()

    def on_ready(self, fd, cond, path):
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            self.drop(path)
            return False
        try:
            data = os.read(fd, EVENT.size * 32)
        except BlockingIOError:
            return True
        except OSError:
            self.drop(path)
            return False
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _t, value, etype, number = EVENT.unpack_from(data, off)
            init = etype & 0x80
            etype &= 0x7F
            if etype == 1 and not init:
                if value and number in BUTTONS:
                    self.callback(BUTTONS[number])
            elif etype == 2:
                if number in AXES_TRIGGER:
                    was = self.axes.get((path, number), -32767) > 0
                    self.axes[(path, number)] = value
                    if value > 0 and not was and not init:
                        self.callback(AXES_TRIGGER[number])
                elif number in AXES_X + AXES_Y:
                    self.axes[(path, number)] = 0 if init else value
                    self.update_direction()
        return True

    def update_direction(self):
        def pick(axes, neg, pos):
            for (_p, axis), v in self.axes.items():
                if axis in axes and abs(v) > THRESHOLD:
                    return neg if v < 0 else pos
            return None
        new = pick(AXES_Y, "up", "down") or pick(AXES_X, "left", "right")
        if new == self.direction:
            return
        self.direction = new
        if self.repeat_id:
            GLib.source_remove(self.repeat_id)
            self.repeat_id = 0
        if new:
            self.callback(new)
            self.repeat_id = GLib.timeout_add(REPEAT_DELAY, self.start_repeat)

    def start_repeat(self):
        self.repeat_id = GLib.timeout_add(REPEAT_RATE, self.repeat)
        self.repeat()
        return False

    def repeat(self):
        if self.direction:
            self.callback(self.direction)
            return True
        self.repeat_id = 0
        return False
