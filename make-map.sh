#!/bin/sh
# Map your Scrap Mechanic worlds. With no arguments it opens a window with your
# saves in it; press Make map and the rest -- finding the game, reading the
# save, opening the result -- happens on its own.
#
# Pass any argument and it runs on the command line instead:
#     ./make-map.sh --list
#     ./make-map.sh --all --px 64
#
# Scrap Mechanic is a Windows game, so on Linux Steam runs it under Proton. Its
# saves live inside that Proton prefix and this knows where to look, so there is
# still nothing to find and nothing to configure.

set -u
cd "$(dirname "$0")"

say() { printf '%s\n' "$@"; }

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys; raise SystemExit(sys.version_info < (3, 8))' 2>/dev/null
    then
        PY="$c"
        break
    fi
done
if [ -z "$PY" ]; then
    say "" \
        "  Python 3.8 or newer was not found." \
        "  Install it with your package manager, for example:" \
        "      sudo apt install python3 python3-tk        # Debian, Ubuntu" \
        "      sudo dnf install python3 python3-tkinter   # Fedora" \
        "      sudo pacman -S python tk                   # Arch" \
        ""
    exit 1
fi

# A virtual environment from an earlier run wins, because that is where the
# dependencies were put.
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import numpy, PIL' >/dev/null 2>&1
then
    PY=".venv/bin/python"
elif ! "$PY" -c 'import numpy, PIL' >/dev/null 2>&1; then
    say "" "  First run: installing numpy and Pillow (one time)..."
    if ! "$PY" -m pip install --quiet --disable-pip-version-check --user \
            numpy Pillow >/dev/null 2>&1
    then
        # Most distributions now refuse to install into the system Python at
        # all (PEP 668), so the fallback is a virtual environment of our own
        # rather than an error the user has to go and read about.
        say "  The system Python is managed by your distribution," \
            "  so a virtual environment is being made in .venv instead."
        if ! "$PY" -m venv .venv >/dev/null 2>&1; then
            say "" \
                "  Could not create it. Install the venv package and retry:" \
                "      sudo apt install python3-venv" \
                ""
            exit 1
        fi
        if ! .venv/bin/python -m pip install --quiet --upgrade pip \
                numpy Pillow >/dev/null 2>&1
        then
            say "" "  Could not install numpy and Pillow." ""
            exit 1
        fi
        PY=".venv/bin/python"
    fi
fi

# With no arguments, open the window -- if this Python has tkinter at all. On
# Linux it is very often a separate package, so say which one.
if [ "$#" -eq 0 ]; then
    if "$PY" -c 'import tkinter' >/dev/null 2>&1; then
        exec "$PY" -m smmap --gui
    fi
    say "" \
        "  This Python has no tkinter, so there is no window to open." \
        "  Either install it:" \
        "      sudo apt install python3-tk" \
        "  or use the command line:" \
        "      ./make-map.sh --list" \
        "      ./make-map.sh --3d" \
        ""
fi

exec "$PY" -m smmap "$@"
