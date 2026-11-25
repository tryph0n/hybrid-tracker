import re
import tomllib

from conftest import ROOT

import tracker


def test_package_reports_the_pyproject_version():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert tracker.__version__ == pyproject["project"]["version"]


def test_changelog_lists_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
    assert "## [Unreleased]" in changelog
    assert re.search(
        rf"^## \[{re.escape(tracker.__version__)}\]", changelog, re.MULTILINE
    )
