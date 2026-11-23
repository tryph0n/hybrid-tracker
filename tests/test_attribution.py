"""CC BY duty: every source is credited where the data is distributed."""

from pathlib import Path

from conftest import ROOT

from tracker.sources import SOURCES


def one_line(path: Path) -> str:
    """The file text with every run of whitespace folded, so wrapped lines match."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_readme_credits_every_source():
    text = one_line(ROOT / "README.md")
    for source in SOURCES.values():
        assert source.citation in text
        assert source.homepage in text
        assert source.licence in text
