"""The ``adichtion`` entry point: command line with a subcommand, the app otherwise."""
import re

import pytest

from adichtion import __main__ as entry
from adichtion import __version__


def test_wants_cli_by_first_argument():
    assert not entry.wants_cli(["adichtion"])
    assert not entry.wants_cli(["adichtion", "recording.adicht"])
    assert not entry.wants_cli(["adichtion", "/some/dir/recording.adicht", "--theme", "dark"])
    for cmd in entry.CLI_COMMANDS:
        assert entry.wants_cli(["adichtion", cmd, "recording.adicht"])
    assert entry.wants_cli(["adichtion", "--help"])
    assert entry.wants_cli(["adichtion", "-h"])
    assert entry.wants_cli(["adichtion", "--version"])


def test_cli_commands_match_the_argparse_subcommands(capsys):
    """A new CLI subcommand must be added to CLI_COMMANDS or it would start the app."""
    from adichtion.cli import main as cli_main

    with pytest.raises(SystemExit) as e:
        cli_main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    m = re.search(r"\{([a-z,]+)\}", out)
    assert m, out
    assert sorted(m.group(1).split(",")) == sorted(entry.CLI_COMMANDS)


def test_main_runs_the_command_line(synthetic, capsys):
    assert entry.main(["adichtion", "info", synthetic["path"]]) == 0
    out = capsys.readouterr().out
    assert "Flow" in out and "Pmo" in out


def test_main_version(capsys):
    with pytest.raises(SystemExit) as e:
        entry.main(["adichtion", "--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"adichtion {__version__}"


def test_briefcase_version_and_changelog_match_the_package():
    """pyproject's [tool.briefcase] version is a manual copy of __version__, and the Linux
    build needs a CHANGELOG.md entry for it (docs/RELEASING.md)."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
        pyproject = fh.read()
    briefcase = pyproject.split("[tool.briefcase]", 1)[1].split("\n[", 1)[0]
    assert re.search(r'^version = "([^"]+)"', briefcase, re.M).group(1) == __version__
    assert re.search(r'^adichtion = "adichtion\.__main__:main"', pyproject, re.M)
    with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as fh:
        assert f"## [{__version__}]" in fh.read()


def test_the_bundle_requires_what_the_ui_extra_requires():
    """[tool.briefcase.app.adichtion] requires is a manual copy of numpy + the [ui] extra; the
    OpenCV bound in particular (below 4.13: LabChart for Mac's interlaced DV movies decode
    black with FFmpeg 8) must be the same in both."""
    import os
    tomllib = pytest.importorskip("tomllib")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), "rb") as fh:
        cfg = tomllib.load(fh)
    extra = set(cfg["project"]["dependencies"]) | set(cfg["project"]["optional-dependencies"]["ui"])
    (app,) = cfg["tool"]["briefcase"]["app"].values()
    assert set(app["requires"]) == extra
    assert "opencv-python-headless>=4.8,<4.13" in extra
