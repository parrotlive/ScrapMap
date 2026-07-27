"""Where the packaged program starts.

Which of the two front ends you get is decided by whether you passed anything:
no arguments means it was double-clicked and it opens the window, and any
argument at all means the command line. The executable is built windowed so
that double-clicking it leaves no console box sitting behind the window, and
the only complication that causes is finding somewhere for the command line to
print -- see borrow_console.
"""

import sys

ATTACH_PARENT_PROCESS = -1


def borrow_console():
    """Make sure the command line has somewhere to print. True if it has.

    A shell that ran us normally, or redirected us to a file, hands its own
    handles down and Python opens them as usual -- in which case there is
    nothing to do, and taking the console instead would send output to the
    screen that the user asked to go into a file. Only when there are no
    handles at all is there a terminal worth borrowing.
    """
    if sys.stdout is not None:
        return True
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False
    except Exception:
        return False
    for name, path, mode in (("stdout", "CONOUT$", "w"),
                             ("stderr", "CONOUT$", "w"),
                             ("stdin", "CONIN$", "r")):
        try:
            setattr(sys, name, open(path, mode, buffering=1, encoding="utf-8",
                                    errors="replace"))
        except OSError:
            pass
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        from . import gui
        if gui.run():
            return 0
        # No tkinter in this build: fall back to drawing the newest world.
    borrow_console()
    from .__main__ import main as command_line
    return command_line(argv)


if __name__ == "__main__":
    sys.exit(main())
