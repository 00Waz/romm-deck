#!/usr/bin/env python3
"""Generate the double-click installer launcher from installer-launcher.sh.
Usage: make-launcher.py OUTPUT.desktop"""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
lines = [l.rstrip() for l in open(os.path.join(here, "installer-launcher.sh"))
         if l.strip() and not l.lstrip().startswith("#")]
script = "; ".join(lines)
# Exec quoting: escape " ` $ \ for the argument, then double the backslash for
# the .desktop string; a literal % becomes %%.
esc = "".join("\\\\" + c if c in '"`$\\' else "%%" if c == "%" else c for c in script)
with open(sys.argv[1], "w") as f:
    f.write("[Desktop Entry]\nType=Application\nName=Install RomM Deck\n"
            "Comment=Open the RomM Deck installer\n"
            f'Exec=sh -c "{esc}" sh %k\n'
            "Icon=system-software-install\nTerminal=false\nCategories=Game;\n")
os.chmod(sys.argv[1], 0o755)
