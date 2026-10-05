"""``adichtion`` / ``python -m adichtion``: the app, or the command line.

Without arguments (or with just a ``.adicht`` path) the viewer starts; with a
subcommand (``info``, ``export``, ...) or ``--help``/``--version`` the command
line runs.  This is also what the packaged app executes.
"""
from __future__ import annotations

import sys
from typing import Optional

CLI_COMMANDS = ("info", "tree", "comments", "strings", "videos", "check", "export", "verify", "infer", "macsettings", "bench")


def wants_cli(argv: list[str]) -> bool:
    """Whether the arguments address the command line rather than the app."""
    rest = argv[1:]
    if not rest:
        return False
    first = rest[0]
    return first in CLI_COMMANDS or first in ("-h", "--help", "--version")


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if wants_cli(argv):
        from .cli import main as cli_main
        cli_main(argv[1:])
        return 0
    try:
        import PySide6  # noqa: F401 - only to tell a missing viewer from a broken one
    except ImportError:
        # `pip install adichtion` brings the library and the command line only; the viewer is the `ui` extra.
        print("The adichtion app needs the viewer packages, which this installation does not have.\n"
              'Install them with:  pip install "adichtion[ui]"\n'
              "The command line works without them: adichtion --help", file=sys.stderr)
        return 1
    from .ui.app import main as app_main
    return app_main(argv)


if __name__ == "__main__":
    sys.exit(main())
