"""CC BY duty: every source is credited where the data is distributed."""

from pathlib import Path

import yaml
from conftest import ROOT

from tracker.sources import REFERENCES, SOURCES


def one_line(path: Path) -> str:
    """The file text with every run of whitespace folded, so wrapped lines match."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_readme_credits_every_source():
    text = one_line(ROOT / "README.md")
    for source in SOURCES.values():
        assert source.citation in text
        assert source.homepage in text
        assert source.licence in text


def test_readme_credits_every_reference_dataset_with_its_licence_page():
    text = one_line(ROOT / "README.md")
    for reference in REFERENCES.values():
        assert reference.citation in text
        assert reference.homepage in text
        assert reference.licence in text
        assert reference.licence_url in text


def test_dataset_card_credits_every_source_and_reference_dataset():
    card = (ROOT / "data" / "events" / "README.md").read_text(encoding="utf-8")
    _, front_matter, body = card.split("---\n", 2)
    assert yaml.safe_load(front_matter)["license"] == "cc-by-4.0"
    text = " ".join(body.split())
    for source in [*SOURCES.values(), *REFERENCES.values()]:
        assert source.citation in text
        assert source.homepage in text
